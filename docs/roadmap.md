# Roadmap

## Current status

| Component | Status |
|---|---|
| Nix flake (uv2nix) | Done |
| `protocol.py` — wire format + auth + caps handshake | Done |
| `devices.py` — discovery + uinput factory (kbd / mouse / touchpad) | Done |
| `config.py` — dataclasses + defaults | Done |
| NixOS VM configurations (server, client, dev-client, dev-desktop) | Done |
| `client.py` — receive + uinput injection + auto-reconnect | Done |
| `server.py` — grab + passthrough + TCP send + multi-client + hotplug | Done |
| CLI argument parsing (`--host`, `--port`, `--psk`, `--debug`, `--ignore-device`, `--switch-mods`) | Done |
| Stuck-key release on slot switch | Done |
| Reconnection / error recovery (exponential back-off, TCP keep-alive) | Done |
| Multiple clients (slot-based: `switch_mods + 1–9`) | Done |
| Device hotplug monitoring | Done |
| PSK authentication (SHA-256 token, server-side reject) | Done |
| Touchpad forwarding + capability handshake | Done |
| Tests (unit: protocol, config; integration: client event routing) | Done |

---

## Phase 1 — Client ✅

`client.py` is fully implemented.

- `asyncio.open_connection` with exponential back-off reconnect (1 s → 60 s cap)
- PSK authentication token exchange before the event stream
- Capability handshake: server sends touchpad ABS ranges; client creates a
  matching virtual touchpad via `devices.create_virtual_touchpad_from_caps()`
- Read loop: `reader.readexactly(EVENT_SIZE)` → `protocol.unpack` → route
  `EV_KEY` / `EV_REL` / `EV_ABS` / `EV_SYN` to the correct virtual device
- Virtual keyboard and mouse persist across reconnects; virtual touchpad is
  recreated per connection (caps may differ)
- TCP keep-alive with 25-second dead-connection detection
- `--debug` flag writes per-event log to `/tmp/pykvm.debug.log`
- Graceful shutdown on `asyncio.CancelledError`

---

## Phase 2 — Server ✅

`server.py` is fully implemented.

- Discovers keyboards, mice, and touchpads with `devices.find_keyboards()` /
  `find_mice()`; deduplicates by path
- Creates local passthrough uinput clones (`pykvm-keyboard`, `pykvm-mouse`,
  `pykvm-touchpad`) so the server desktop keeps working while devices are grabbed
- `device.grab()` all discovered devices at startup
- `asyncio.start_server` accepting multiple simultaneous clients
- Slot-based switching: `switch_mods + digit 1` → local, `+2` → client 1, etc.
- `held_keys` tracking + synthetic key-release on every slot switch (no stuck keys)
- Route events: local mode → passthrough uinput; remote mode → `protocol.pack`
  + TCP write + `drain()`
- PSK authentication: rejects connections with wrong token before sending any data
- Sends touchpad capability JSON to each connecting client
- `--switch-mods` accepts `KEY_*` names, short names, or raw evdev codes
- `--ignore-device` excludes devices by name substring or exact `/dev/input/` path
- `--debug` flag writes per-event log to `/tmp/pykvm.debug.log`

---

## Phase 3 — Robustness ✅

- **Reconnection**: client retries with exponential back-off; virtual devices
  stay alive across reconnects so the compositor never sees them disappear.
- **Graceful ungrab**: asyncio `CancelledError` / `KeyboardInterrupt` handler
  cancels all read tasks, calls `dev.ungrab()` + `dev.close()` for every grabbed
  device, and destroys virtual devices before exiting.
- **Multiple clients**: server supports up to 9 simultaneous clients; only the
  active slot receives events; falling back to local mode when the active client
  disconnects.
- **Device hotplug**: polls `/dev/input/` every second; newly connected
  keyboards, mice, and touchpads are grabbed automatically; unplugged devices
  are released gracefully with a log message.
- **TCP keep-alive**: both server and client set `SO_KEEPALIVE` with 25-second
  dead-connection detection (`TCP_KEEPIDLE=10`, `TCP_KEEPINTVL=5`, `TCP_KEEPCNT=3`).

---

## Phase 4 — Features

### Edge-of-screen switching (planned)

When the mouse reaches the right edge of the screen, switch to remote mode;
when it reaches the left edge of the remote screen, switch back.

Requires:
- Knowledge of the server's screen resolution (query via `xrandr` / Wayland
  output protocol, or configure statically).
- Tracking the current absolute mouse X position by accumulating `REL_X`
  deltas.

### TLS (planned)

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
