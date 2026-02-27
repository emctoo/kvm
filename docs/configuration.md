# Configuration

## Overview

pykvm uses plain Python dataclasses for configuration (`src/pykvm/config.py`).
CLI argument parsing (not yet implemented) will populate these dataclasses at
startup.

---

## `ServerConfig`

```python
@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 5900
    hotkey: frozenset[int] = frozenset({29, 56, 15})
```

| Field | Default | Description |
|---|---|---|
| `host` | `"0.0.0.0"` | IP address the TCP server binds to.  `"0.0.0.0"` listens on all interfaces. |
| `port` | `5900` | TCP port the server listens on. |
| `hotkey` | `{29, 56, 15}` | Set of evdev key codes that must all be held simultaneously to toggle mode. |

### Hotkey

The hotkey is represented as a `frozenset` of evdev `EV_KEY` key codes (not
kernel scancodes, not X keysyms).

Default combo: **Left-Ctrl + Left-Alt + Tab**

| Key | evdev code |
|---|---|
| `KEY_LEFTCTRL` | 29 |
| `KEY_LEFTALT` | 56 |
| `KEY_TAB` | 15 |

The server detects the hotkey by maintaining a `set` of currently-held keys and
comparing it against `config.hotkey` on every `EV_KEY` press event.

To use a different hotkey, construct `ServerConfig` with a different `frozenset`:

```python
# Left-Ctrl + Left-Alt + F1
ServerConfig(hotkey=frozenset({29, 56, 59}))
```

Common key codes:

| Key | Code |
|---|---|
| `KEY_LEFTCTRL` | 29 |
| `KEY_RIGHTCTRL` | 97 |
| `KEY_LEFTALT` | 56 |
| `KEY_RIGHTALT` | 100 |
| `KEY_LEFTSHIFT` | 42 |
| `KEY_RIGHTSHIFT` | 54 |
| `KEY_TAB` | 15 |
| `KEY_F1` – `KEY_F12` | 59 – 70 |
| `KEY_SCROLLLOCK` | 70 |
| `KEY_PAUSE` | 119 |

Full list: `python -c "import evdev.ecodes as e; print({v:k for k,v in e.KEY.items()})"`.

---

## `ClientConfig`

```python
@dataclass
class ClientConfig:
    server_host: str = "127.0.0.1"
    server_port: int = 5900
```

| Field | Default | Description |
|---|---|---|
| `server_host` | `"127.0.0.1"` | IP or hostname of the machine running `pykvm-server`. |
| `server_port` | `5900` | TCP port to connect to. |

---

## Default port: 5900

Port 5900 is the default VNC port.  It was chosen as a recognisable "remote
desktop" port that is typically open in firewalls.  If VNC is already running on
either machine, pick a different port (e.g. `5901`).

---

## Future: CLI flags

Planned CLI interface (not yet implemented):

### `pykvm-server`

```
pykvm-server [OPTIONS]

Options:
  --host HOST        Bind address          [default: 0.0.0.0]
  --port PORT        TCP port              [default: 5900]
  --hotkey KEYS      Comma-separated key names, e.g. KEY_LEFTCTRL,KEY_LEFTALT,KEY_TAB
```

### `pykvm-client`

```
pykvm-client [OPTIONS]

Options:
  --server HOST      Server address        [required]
  --port PORT        Server TCP port       [default: 5900]
```

---

## VM test environment

In the NixOS VM configuration the client's `ExecStart` is hardcoded:

```nix
ExecStart = "${pykvm-pkg}/bin/pykvm-client --server 10.0.2.2 --port 15900";
```

`10.0.2.2` is the QEMU user-networking gateway address (the host).  The host
forwards TCP port 15900 to the server VM's port 5900.  See
[vm-testing.md](vm-testing.md) for the full diagram.
