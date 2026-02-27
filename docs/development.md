# Development Setup

## Requirements

- [Nix](https://nixos.org/download) with flakes enabled
- Git

No other tools are required; everything else (`python`, `uv`, `evdev`, etc.) is
provided by the Nix dev shell.

---

## Enable Nix flakes

If flakes are not yet enabled, add to `/etc/nix/nix.conf` (NixOS) or
`~/.config/nix/nix.conf`:

```
experimental-features = nix-command flakes
```

On NixOS, alternatively add to `configuration.nix`:

```nix
nix.settings.experimental-features = [ "nix-command" "flakes" ];
```

---

## Entering the dev shell

```bash
cd pykvm
nix develop
```

This provides:

| Tool | Version |
|---|---|
| Python | 3.12 (from nixpkgs) |
| `uv` | latest from nixpkgs |
| `pykvm` | editable install (source in `src/pykvm/`) |
| `pykvm-server` | points at `src/pykvm/server.py:main` |
| `pykvm-client` | points at `src/pykvm/client.py:main` |
| `linuxHeaders` | for rebuilding evdev from source if needed |

The shell sets:

```bash
UV_NO_SYNC=1            # prevent uv from re-syncing the venv
UV_PYTHON=<nix python>  # lock uv to the Nix-provided interpreter
UV_PYTHON_DOWNLOADS=never
REPO_ROOT=$(git rev-parse --show-toplevel)
```

---

## Project structure

```
pykvm/
├── flake.nix          Nix flake — dev shell, VM configs, packages
├── flake.lock         Locked input revisions (commit this)
├── pyproject.toml     PEP 621 project metadata + entry-points
├── uv.lock            Locked Python dependencies (commit this)
├── .gitignore
├── docs/              This documentation
└── src/
    └── pykvm/
        ├── __init__.py
        ├── protocol.py
        ├── devices.py
        ├── config.py
        ├── server.py
        └── client.py
```

---

## How uv2nix works

[uv2nix](https://github.com/pyproject-nix/uv2nix) bridges the `uv` package
manager with Nix's build system.

### Key concepts

**`workspace`** — uv2nix reads `uv.lock` to learn the exact package versions
and their sources:

```nix
workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
```

**`overlay`** — converts the workspace into a Nix package overlay.  Each locked
package becomes a Nix derivation:

```nix
overlay = workspace.mkPyprojectOverlay { sourcePreference = "sdist"; };
```

`sourcePreference = "sdist"` tells uv2nix to prefer source distributions over
wheels.  This is necessary here because `evdev` does not publish wheels — only a
source tarball.

**`pythonSets`** — a per-system `python-packages` scope with the workspace
packages merged in:

```nix
pythonSets = forAllSystems (system:
  (pkgs.callPackage pyproject-nix.build.packages { inherit python; })
  .overrideScope (lib.composeManyExtensions [
    pyproject-build-systems.overlays.wheel  # build tools (setuptools, cython…)
    overlay                                  # our locked packages
    evdevOverlay                             # extra build inputs for evdev
  ])
);
```

**`evdevOverlay`** — `evdev` is a C extension that requires Cython and Linux
kernel headers at build time.  The overlay patches its derivation:

```nix
evdevOverlay = _final: prev: {
  evdev = prev.evdev.overrideAttrs (old: {
    nativeBuildInputs = (old.nativeBuildInputs or []) ++ [ pkgs.python312Packages.cython ];
    buildInputs       = (old.buildInputs       or []) ++ [ pkgs.linuxHeaders ];
  });
};
```

**Editable install** — in the dev shell, `pykvm` itself is installed as an
editable package (i.e. the live source tree is on `sys.path`):

```nix
editableOverlay = workspace.mkEditablePyprojectOverlay { root = "$REPO_ROOT"; };
pythonSet = pythonSets.${system}.overrideScope editableOverlay;
virtualenv = pythonSet.mkVirtualEnv "pykvm-dev-env" workspace.deps.all;
```

Changes to `src/pykvm/*.py` are reflected immediately without rebuilding.

---

## Adding a Python dependency

1. Inside the dev shell, run:

   ```bash
   uv add <package>
   ```

   This updates `pyproject.toml` and regenerates `uv.lock`.

2. If the package has C extensions, add a Nix overlay entry in `flake.nix`
   similar to `evdevOverlay` to supply the required build inputs.

3. Re-enter the dev shell (`exit` then `nix develop`) to pick up the new
   package.

4. Commit both `pyproject.toml` and `uv.lock`.

---

## Updating locked dependencies

```bash
uv lock --upgrade        # upgrade all packages within constraints
uv lock --upgrade-package evdev   # upgrade a single package
```

Commit the updated `uv.lock`.

---

## Building the production package

```bash
nix build           # builds .#packages.x86_64-linux.default
ls -la result/bin/  # pykvm-server  pykvm-client
```

The result is a self-contained virtualenv symlinked at `./result`.

---

## Running tests (future)

```bash
# Inside nix develop:
python -m pytest tests/
```

No tests exist yet; they will be added alongside the server/client
implementations.
