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

# Enter the Nix development shell
dev:
    nix develop

# Build the default package (pykvm virtualenv)
build:
    nix build

# Update locked Python dependencies
update:
    uv lock --upgrade

# ── running (use inside `nix develop`) ───────────────────────────────────────

# Run pykvm-server; pass extra flags after --  e.g. `just server -- --port 5901`
server *args:
    pykvm-server {{ args }}

# Run pykvm-client; pass --server HOST and any other flags after --
client *args:
    pykvm-client {{ args }}

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
