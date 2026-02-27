# Architecture

## Overview

pykvm is split into two processes that communicate over TCP:

| Process | Role |
|---|---|
| `pykvm-server` | Runs on the machine with the physical keyboard and mouse.  Grabs input devices exclusively, optionally forwards events over the network. |
| `pykvm-client` | Runs on each target machine.  Receives the event stream and injects it into virtual `uinput` devices. |

Both processes are written in Python and use `asyncio` for their event loops.

---

## Module map

```
src/pykvm/
├── __init__.py      (empty)
├── protocol.py      Wire format — pack/unpack 8-byte event frames
├── devices.py       evdev discovery + uinput virtual-device factory
├── config.py        ServerConfig / ClientConfig dataclasses + defaults
├── server.py        Server entrypoint (TODO)
└── client.py        Client entrypoint (TODO)
```

### `protocol.py`

Defines `RawEvent` (a `NamedTuple` of `type`, `code`, `value`) and two
functions: `pack(event) -> bytes` and `unpack(data) -> RawEvent`.  Each event
occupies exactly 8 bytes on the wire.  See [protocol.md](protocol.md) for the
full specification.

### `devices.py`

Handles all kernel I/O abstraction:

- `find_keyboards()` — enumerates `/dev/input/*` and returns devices that
  advertise `EV_KEY` with `KEY_A` (a reliable proxy for "real keyboard").
- `find_mice()` — returns devices that advertise `EV_REL` (relative pointer).
- `create_virtual_keyboard()` — creates a `uinput` device advertising the full
  `EV_KEY` key set.
- `create_virtual_mouse()` — creates a `uinput` relative-pointer device with
  `BTN_LEFT/RIGHT/MIDDLE` and `REL_X/Y/WHEEL/HWHEEL`.

### `config.py`

Plain dataclasses with sensible defaults:

- `ServerConfig` — bind host/port, hotkey key-code set.
- `ClientConfig` — server host/port.

### `server.py` / `client.py`

Entry-points for the two binaries.  Currently stubs; implementation is the next
phase.

---

## Server internals (planned)

```
                    ┌─────────────────────────────────┐
                    │           pykvm-server           │
                    │                                  │
  /dev/input/eventN │  asyncio                         │
  ──────────────────►  read loop ──► hotkey check      │
  (grabbed)         │       │              │           │
                    │  local mode     remote mode      │
                    │       │              │           │
                    │  uinput clone   TCP send         │
                    │  (passthrough)  (serialised)     │
                    └─────────────────────────────────┘
```

1. **Device grab** — `device.grab()` is called on every discovered keyboard and
   mouse.  The kernel stops delivering those events to any other consumer
   (X11/Wayland/etc.) until the grab is released.

2. **Passthrough uinput** — a `uinput` clone of each grabbed device is created
   immediately.  In *local mode* every event is written to this clone, so the
   local desktop behaves normally.

3. **Event loop** — `asyncio` reads from all grabbed devices concurrently using
   `device.async_read_loop()`.  Before routing each event, the server checks
   whether the current key state matches the hotkey combo.

4. **Mode toggle** — when the hotkey fires, the server switches `mode` between
   `local` and `remote`.  It also synthesises a full key-release sequence on the
   side being left (to prevent stuck keys).

5. **TCP send** — in *remote mode* each `RawEvent` is packed to 8 bytes and
   written to the client socket.

---

## Client internals (planned)

```
                    ┌─────────────────────────────────┐
                    │           pykvm-client           │
                    │                                  │
  TCP :5900         │  asyncio                         │
  ──────────────────►  recv loop                       │
                    │       │                          │
                    │  unpack(8 bytes)                 │
                    │       │                          │
                    │  uinput.write(type, code, value) │
                    └─────────────────────────────────┘
```

1. **Connect** — the client connects to the server's TCP socket and enters a
   read loop.

2. **Receive** — exactly 8 bytes are read at a time.  Each frame is unpacked
   into a `RawEvent`.

3. **Inject** — the event is written to the appropriate `uinput` virtual device
   (keyboard or mouse, identified by the event type).

4. **EV_SYN passthrough** — `EV_SYN / SYN_REPORT` frames are forwarded
   verbatim; the kernel requires them to batch events correctly.

---

## Concurrency model

Both server and client use Python's `asyncio`.  `python-evdev` exposes
`device.async_read_loop()` which integrates natively with the event loop.
Network I/O uses `asyncio.open_connection` / `asyncio.start_server`.

No threads are used.  All I/O is non-blocking and cooperative.

---

## Privileges

| Operation | Requirement |
|---|---|
| Read `/dev/input/eventN` | User in `input` group or root |
| `device.grab()` | Same — group `input` or root |
| Write to `/dev/uinput` | User in `input` group or root, `uinput` kernel module loaded |

On NixOS the udev rules in the flake grant group `input` access to both
`/dev/input/*` and `/dev/uinput`.
