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
  };

  outputs =
    {
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "sdist";
      };

      editableOverlay = workspace.mkEditablePyprojectOverlay {
        root = "$REPO_ROOT";
      };

      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;

          # evdev ships sdist-only; it needs Cython + kernel headers to compile.
          evdevOverlay = _final: prev: {
            evdev = prev.evdev.overrideAttrs (old: {
              nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
                pkgs.python312Packages.cython
              ];
              buildInputs = (old.buildInputs or [ ]) ++ [
                pkgs.linuxHeaders
              ];
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
        modules = [ qemuVmModule vmBaseModule vmServerModule ];
      };

      vmClientSystem = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [ qemuVmModule vmBaseModule vmClientModule ];
      };
    in
    {
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          virtualenv = pythonSet.mkVirtualEnv "pykvm-dev-env" workspace.deps.all;
        in
        {
          default = pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.uv
              pkgs.linuxHeaders # needed to build evdev from source if no wheel
            ];

            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = pythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };

            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel)
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
