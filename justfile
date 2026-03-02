# pykvm development shortcuts
# Run `just` or `just --list` to see all recipes.

# List available recipes
default:
    @just --list

# ── formatting & checks ───────────────────────────────────────────────────────

# Format all files (nix fmt → treefmt → nixfmt + ruff-format + just)
fmt:
    nix fmt

# Evaluate all flake outputs without building (fast)
check:
    nix flake check --no-build

# Full flake check including formatting verification (builds formatters)
check-full:
    nix flake check

# ── development ───────────────────────────────────────────────────────────────

# sync to client
sync-to-client:
    rsync -avz --progress --exclude 'result' --exclude '__pycache__' ./ root@192.168.9.33:/tmp/pykvm/

watch-and-sync:
    watchexec -w -r -- just sync-to-client

# Enter the Nix development shell
dev:
    nix develop

# Build the default package (pykvm virtualenv)
build:
    nix build

# Update locked Python dependencies
update:
    uv lock --upgrade

# ── testing (use inside `nix develop`) ───────────────────────────────────────

# Run all tests (unit + mock integration)
test *args:
    pytest {{ args }}

# Run unit tests only
test-unit:
    pytest tests/unit

# Run mock integration tests only
test-integration:
    pytest tests/integration

# Run the NixOS VM integration test (slow — builds QEMU VMs)
test-vm:
    nix build '.#checks.x86_64-linux.vm-integration' -L

# ── running (use inside `nix develop`) ───────────────────────────────────────

# Run pykvm-server; pass extra flags after --  e.g. `just server -- --port 5901`
server *args:
    pykvm-server --psk "my-secret" {{ args }}

# Run pykvm-client; pass --server HOST and any other flags after --
client *args:
    pykvm-client --psk "my-secret" {{ args }}

# ── dev client VM (SSH on localhost:2222) ─────────────────────────────────────
# Shared SSH flags: no host-key checking (VM regenerates keys each boot).

[private]
_vm_ssh := "ssh -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@localhost"

# Build and launch the dev client VM (headless; SSH available on localhost:2222)
vm-dev:
    nix build .#vm-dev-client
    QEMU_OPTS="-nographic" ./result/bin/run-kvm-dev-client-vm

# Same as vm-dev but with a QEMU display window (needed to see uinput key injection)
vm-dev-gui:
    nix build .#vm-dev-client
    ./result/bin/run-kvm-dev-client-vm

# Sync pykvm source into the dev VM (run in a second terminal while vm-dev is running)
vm-sync:
    {{ _vm_ssh }} mkdir -p /root/pykvm/src
    rsync -avz --delete -e "ssh -p 2222 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" \
        src/ root@localhost:/root/pykvm/src/

# Open an SSH shell in the dev VM
vm-ssh:
    {{ _vm_ssh }}

# Run pykvm-client in the headless dev VM.
# server defaults to 10.0.2.2 (QEMU gateway = the host running the VM).

# Override for a remote server:  just vm-run-client 192.168.9.34
vm-run-client server="10.0.2.2" *args:
    {{ _vm_ssh }} -t "pykvm-client --server {{ server }} --port 5900 --psk "my-secret" --debug {{ args }}"

# ── dev desktop VM ────────────────────────────────────────────────────────────
# VM_HOST controls where the QEMU process runs (default: localhost).
# Set it once or prefix each command:
#
#   export VM_HOST=192.168.9.33
#
#   just vm-dev-desktop-start               # build + launch
#   just vm-dev-desktop-view                # open VNC viewer (remote only)
#   just vm-dev-desktop-sync                # sync src/ into the VM
#   just vm-dev-desktop-ssh                 # shell into the VM
#   just vm-dev-desktop-run 192.168.9.34    # run pykvm-client

vm_host := env("VM_HOST", "localhost")
[private]
_d_ssh := "ssh -p 2223 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Build and launch the desktop VM.
# VM_HOST=localhost  → opens a QEMU display window here.
# VM_HOST=x.x.x.x   → copies the VM closure to that host via nix copy,

# then starts it there with VNC on port 5900.
vm-dev-desktop-start:
    #!/usr/bin/env bash
    set -e
    nix build .#vm-dev-desktop
    if [ "{{ vm_host }}" = "localhost" ]; then
        exec ./result/bin/run-kvm-dev-desktop-vm
    else
        nix copy --to ssh://maple@{{ vm_host }} .#vm-dev-desktop
        exec ssh -t maple@{{ vm_host }} \
            "QEMU_OPTS='-display vnc=0.0.0.0:0' $(readlink -f result)/bin/run-kvm-dev-desktop-vm"
    fi

# Open a VNC viewer to the desktop VM (use when VM_HOST is a remote machine).
vm-dev-desktop-view:
    nix-shell -p tigervnc --run "vncviewer {{ vm_host }}"

# Sync src/ into the running VM (works for both local and remote VM_HOST).
vm-dev-desktop-sync:
    {{ _d_ssh }} root@{{ vm_host }} mkdir -p /root/pykvm/src
    rsync -avz --delete -e "{{ _d_ssh }}" src/ root@{{ vm_host }}:/root/pykvm/src/

# Open a root shell inside the running VM.
vm-dev-desktop-ssh:
    {{ _d_ssh }} root@{{ vm_host }}

# Run pykvm-client inside the VM.
# server defaults to 10.0.2.2 (QEMU gateway = the machine running the VM).
# When VM_HOST is remote, pass the pykvm-server address explicitly:

# just vm-dev-desktop-run 192.168.9.34
vm-dev-desktop-run server="10.0.2.2" *args:
    just vm-dev-desktop-sync
    {{ _d_ssh }} root@{{ vm_host }} -t "pykvm-client --server {{ server }} --port 5900 --psk "my-secret" --debug {{ args }}"

# ── VM testing ────────────────────────────────────────────────────────────────

# Build and launch the server VM (opens a QEMU window)
vm-server:
    nix build .#vm-server
    ./result/bin/run-kvm-server-vm

# Build and launch the client VM (opens a QEMU window)
vm-client:
    nix build .#vm-client
    ./result/bin/run-kvm-client-vm

# Build and launch the server VM headless (no QEMU window, Ctrl-A X to quit)
vm-server-headless:
    nix build .#vm-server
    QEMU_OPTS="-nographic" ./result/bin/run-kvm-server-vm

# Build and launch the client VM headless
vm-client-headless:
    nix build .#vm-client
    QEMU_OPTS="-nographic" ./result/bin/run-kvm-client-vm
