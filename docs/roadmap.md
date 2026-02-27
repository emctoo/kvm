# Roadmap

## Current status

| Component | Status |
|---|---|
| Nix flake (uv2nix) | Done |
| `protocol.py` — wire format | Done |
| `devices.py` — discovery + uinput factory | Done |
| `config.py` — dataclasses + defaults | Done |
| NixOS VM configurations (2-machine test) | Done |
| `client.py` — receive + uinput injection | Not started |
| `server.py` — grab + passthrough + TCP send | Not started |
| CLI argument parsing | Not started |
| Stuck-key release on mode switch | Not started |
| Reconnection / error recovery | Not started |
| Tests | Not started |

---

## Phase 1 — Client (next)

Implement `client.py`.  The client is simpler because it only needs uinput write
access — no device grabbing, no local mode.

Tasks:
- `asyncio.open_connection` to server
- Read loop: `reader.readexactly(EVENT_SIZE)` → `protocol.unpack` → route to
  `virtual_keyboard.write` or `virtual_mouse.write` by `event.type`
- Create virtual devices using `devices.create_virtual_keyboard()` and
  `devices.create_virtual_mouse()` on startup
- Graceful shutdown on `asyncio.CancelledError` / `ConnectionResetError`

Expected LOC: ~60 lines.

---

## Phase 2 — Server

Implement `server.py`.

Tasks:
- Discover keyboards and mice with `devices.find_keyboards()` / `find_mice()`
- Create local passthrough uinput clones for each grabbed device
- `device.grab()` all discovered devices
- `asyncio.start_server` to accept a single client connection
- Asyncio read loops over all grabbed devices (`device.async_read_loop()`)
- Maintain `held_keys: set[int]` to track key state
- On each `EV_KEY` event: check against `config.hotkey` to detect mode toggle
- On mode toggle: synthesise key-release sequence for all `held_keys` on the
  outgoing side (prevents stuck keys)
- Route events: local mode → passthrough uinput; remote mode → `protocol.pack`
  + TCP write

Expected LOC: ~120 lines.

---

## Phase 3 — Robustness

- **Reconnection**: client retries connection with exponential backoff on
  disconnect; server queues or drops events while no client is connected.
- **Graceful ungrab**: `SIGTERM` / `SIGINT` handler releases all device grabs
  before exiting.
- **Multiple clients**: server currently supports one client; multi-client
  broadcasting is a stretch goal.
- **Device hotplug**: watch `udevd` (or `/sys/class/input`) for new devices and
  grab them automatically.

---

## Phase 4 — Features

### Edge-of-screen switching

When the mouse reaches the right edge of the screen, switch to remote mode;
when it reaches the left edge of the remote screen, switch back.

Requires:
- Knowledge of the server's screen resolution (query via `xrandr` / Wayland
  output protocol, or configure statically).
- Tracking the current absolute mouse X position by accumulating `REL_X`
  deltas.

### Multiple clients

Maintain a list of connected clients; hotkey cycles through them in order.

### TLS

Wrap the TCP stream in TLS using Python's `ssl` module.  The server presents a
self-signed certificate generated at first run; the client pins the certificate
hash.

This matches rkvm's security model.

---

## Phase 5 — Zig port (future)

The Python implementation serves as a specification and proof-of-concept.  A
future Zig port is planned with the goals of:

- Lower latency: Zig's event loops avoid Python GIL and interpreter overhead.
- Smaller binary: a single statically-linked executable with no runtime
  dependency on Python.
- Identical wire protocol: the 8-byte frame format is unchanged, so a Zig
  server can talk to a Python client and vice versa.
- Same NixOS flake structure: the Zig binary will be built with
  `pkgs.zig.buildZigPackage` (or similar) and embedded in the same flake.

The Python codebase will be kept as a reference implementation and test harness.

---

## Non-goals

- **Windows / macOS support**: the project uses Linux-only kernel interfaces
  (`evdev`, `uinput`).  Other platforms would require entirely different input
  stacks.
- **Video switching**: pykvm is a KM switch, not a full KVM.  Switching the
  video signal requires hardware (an HDMI/DP switch) and is out of scope.
- **Clipboard sharing**: copying text between machines is a separate concern
  (tools like `wl-paste` piped over SSH handle this well).
- **GUI**: a system-tray indicator may be added eventually, but the core tool
  is and will remain a headless daemon.
