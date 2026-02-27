{
  description = "Python KVM switch using evdev/uinput";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      treefmt-nix,
      git-hooks,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      overlay = workspace.mkPyprojectOverlay {
        # Prefer pre-built wheels where available; packages with no wheel
        # (e.g. evdev) fall back to sdist automatically.
        sourcePreference = "wheel";
      };

      editableOverlay = workspace.mkEditablePyprojectOverlay {
        root = "$REPO_ROOT";
      };

      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;

          # evdev ships sdist-only; setup.py has /usr/include hardcoded for the
          # header search.  Patch it to point at the Nix-provided linuxHeaders,
          # matching exactly what nixpkgs does for python3Packages.evdev.
          evdevOverlay = _final: prev: {
            evdev = prev.evdev.overrideAttrs (old: {
              nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
                _final.setuptools
              ];
              buildInputs = (old.buildInputs or [ ]) ++ [
                pkgs.linuxHeaders
              ];
              patchPhase = (old.patchPhase or "") + ''
                substituteInPlace setup.py \
                  --replace-fail /usr/include ${pkgs.linuxHeaders}/include
              '';
            });
          };
        in
        (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.wheel
              overlay
              evdevOverlay
            ]
          )
      );

      # ── Formatting (treefmt-nix) ─────────────────────────────────────────

      # Evaluate treefmt.nix once per system; the result exposes:
      #   .config.build.wrapper  — the `treefmt` binary with config baked in
      #   .config.build.check    — a derivation that fails if files are unformatted
      treefmtEval = forAllSystems (
        system: treefmt-nix.lib.evalModule nixpkgs.legacyPackages.${system} ./treefmt.nix
      );

      # ── Pre-commit hooks (git-hooks.nix) ─────────────────────────────────

      # Run treefmt as the sole pre-commit hook so formatting is the single
      # source of truth (treefmt.nix drives both `nix fmt` and git hooks).
      preCommitChecks = forAllSystems (
        system:
        git-hooks.lib.${system}.run {
          src = ./.;
          hooks.treefmt = {
            enable = true;
            packageOverrides.treefmt = treefmtEval.${system}.config.build.wrapper;
          };
        }
      );

      # ── VM support (x86_64-linux only) ──────────────────────────────────

      # Built pykvm virtualenv for embedding in VMs (non-editable).
      pykvm-pkg = pythonSets."x86_64-linux".mkVirtualEnv "pykvm-env" workspace.deps.default;

      # Settings shared by both VMs.
      vmBaseModule = {
        system.stateVersion = "25.05";

        virtualisation.memorySize = 1024;

        boot.kernelModules = [ "uinput" ];

        # Allow root (and any process in the input group) to access evdev/uinput.
        services.udev.extraRules = ''
          KERNEL=="uinput",          MODE="0660", GROUP="input"
          SUBSYSTEM=="input",        MODE="0660", GROUP="input"
        '';

        users.users.root.password = "";
        services.getty.autologinUser = "root";

        environment.systemPackages = [ pykvm-pkg ];

        # Keep the VM image small.
        documentation.enable = false;
      };

      # Server VM: has a virtual keyboard to grab; forwards port 5900 → host:15900.
      #
      # Networking diagram:
      #   client VM ──(QEMU user net)──► 10.0.2.2:15900 (host) ──(fwd)──► server VM:5900
      vmServerModule = {
        networking.hostName = "kvm-server";

        # Override the default user-networking options to splice in port forwarding.
        # hostfwd=tcp::15900-:5900 → bind host:15900, forward to guest:5900.
        virtualisation.qemu.networkingOptions = [
          "-net nic,netdev=user.0,model=virtio"
          "-netdev user,id=user.0,hostfwd=tcp::15900-:5900"
        ];

        # Give the VM a virtual keyboard so pykvm-server has a device to grab.
        virtualisation.qemu.options = [ "-device virtio-keyboard-pci" ];

        systemd.services.pykvm-server = {
          description = "pykvm server";
          wantedBy = [ "multi-user.target" ];
          after = [ "network.target" ];
          serviceConfig = {
            ExecStart = "${pykvm-pkg}/bin/pykvm-server";
            Restart = "on-failure";
            RestartSec = "2s";
          };
        };
      };

      # Client VM: creates uinput virtual devices; connects to server via host relay.
      vmClientModule = {
        networking.hostName = "kvm-client";

        systemd.services.pykvm-client = {
          description = "pykvm client";
          wantedBy = [ "multi-user.target" ];
          after = [ "network.target" ];
          serviceConfig = {
            # 10.0.2.2 is the QEMU user-net gateway (= host), which forwards :15900 → server VM.
            ExecStart = "${pykvm-pkg}/bin/pykvm-client --server 10.0.2.2 --port 15900";
            Restart = "on-failure";
            RestartSec = "2s";
          };
        };
      };

      # qemu-vm.nix provides virtualisation.{memorySize,qemu,...}; it is not
      # part of the default nixosSystem module list and must be imported explicitly.
      qemuVmModule = "${nixpkgs}/nixos/modules/virtualisation/qemu-vm.nix";

      # Build the two NixOS systems once; reuse in both nixosConfigurations and packages.
      vmServerSystem = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          qemuVmModule
          vmBaseModule
          vmServerModule
        ];
      };

      vmClientSystem = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          qemuVmModule
          vmBaseModule
          vmClientModule
        ];
      };
    in
    {
      # `nix fmt` — format all files in the repo
      formatter = forAllSystems (system: treefmtEval.${system}.config.build.wrapper);

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          # `nix flake check` — fail if any file is not formatted
          formatting = treefmtEval.${system}.config.build.check self;
          # also run the pre-commit suite in CI
          pre-commit-check = preCommitChecks.${system};
        }
        // lib.optionalAttrs (system == "x86_64-linux") (
          let
            # Minimal fake pykvm server: listens on :5900, accepts one client,
            # sends KEY_A-down + SYN_REPORT, then closes.
            fakeServerPy = pkgs.writeText "fake-kvm-server.py" ''
              import socket, struct, time
              s = socket.socket()
              s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
              s.bind(('0.0.0.0', 5900))
              s.listen(1)
              conn, _ = s.accept()
              conn.sendall(struct.pack('!HHi', 1, 30, 1))   # KEY_A down
              conn.sendall(struct.pack('!HHi', 0, 0, 0))    # SYN_REPORT
              time.sleep(0.1)
              conn.close()
              s.close()
            '';
          in
          {
            vm-integration = pkgs.nixosTest {
              name = "pykvm-integration";

              nodes = {
                server =
                  { pkgs, ... }:
                  {
                    # Allow the client VM to reach port 5900.
                    networking.firewall.allowedTCPPorts = [ 5900 ];

                    systemd.services.fake-kvm-server = {
                      description = "Fake pykvm TCP server (integration test)";
                      wantedBy = [ "multi-user.target" ];
                      after = [ "network.target" ];
                      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${fakeServerPy}";
                    };
                  };

                client =
                  { ... }:
                  {
                    boot.kernelModules = [ "uinput" ];

                    services.udev.extraRules = ''
                      KERNEL=="uinput",   MODE="0660", GROUP="input"
                      SUBSYSTEM=="input", MODE="0660", GROUP="input"
                    '';

                    environment.systemPackages = [ pykvm-pkg ];

                    systemd.services.pykvm-client = {
                      description = "pykvm client (integration test)";
                      wantedBy = [ "multi-user.target" ];
                      after = [ "network.target" ];
                      serviceConfig = {
                        ExecStart = "${pykvm-pkg}/bin/pykvm-client --server server --port 5900";
                        Restart = "on-failure";
                        RestartSec = "1s";
                      };
                    };
                  };
              };

              testScript = ''
                start_all()
                # Wait until the fake server is accepting connections before
                # checking the client log — avoids a spurious timeout if the
                # server starts slowly.
                server.wait_for_open_port(5900)
                client.wait_until_succeeds(
                    "journalctl -u pykvm-client --no-pager | grep 'Server closed'",
                    timeout=60,
                )
              '';
            };
          }
        )
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          virtualenv = pythonSet.mkVirtualEnv "pykvm-dev-env" workspace.deps.all;
          preCommit = preCommitChecks.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.uv
              pkgs.just
              pkgs.linuxHeaders # needed to build evdev from source if no wheel
            ]
            ++ preCommit.enabledPackages; # tools required by the pre-commit hooks

            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = pythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };

            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel)
              ${preCommit.shellHook}
            '';
          };
        }
      );

      nixosConfigurations = {
        vm-server = vmServerSystem;
        vm-client = vmClientSystem;
      };

      packages = forAllSystems (
        system:
        {
          default = pythonSets.${system}.mkVirtualEnv "pykvm-env" workspace.deps.default;
        }
        // lib.optionalAttrs (system == "x86_64-linux") {
          vm-server = vmServerSystem.config.system.build.vm;
          vm-client = vmClientSystem.config.system.build.vm;
        }
      );
    };
}
