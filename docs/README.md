# pykvm

A software KVM (Keyboard-Video-Mouse) switch for Linux, written in Python using
[python-evdev](https://python-evdev.readthedocs.io/).

pykvm lets you share one physical keyboard and mouse across multiple Linux
machines over a TCP connection — no special hardware required.  It operates
entirely at the kernel input layer via `evdev` and `uinput`, so it works
regardless of whether you are running X11, Wayland, a bare framebuffer, or no
display server at all.

This project is inspired by [rkvm](https://github.com/htrefil/rkvm) (written in
Rust).  The Python implementation serves as a proof-of-concept; a future Zig
port is planned for lower latency and smaller binary size.

---

## How it works

```
Physical KB/Mouse
      │
      ▼
  ┌─────────┐   evdev grab    ┌──────────────────┐
  │  kernel  │ ─────────────► │  pykvm-server    │
  │ /dev/    │                │  (machine A)     │
  │ input/*  │                │                  │
  └─────────┘                 │  local mode:     │
                              │    ↓ passthrough │
                              │  uinput clone    │
                              │                  │
                              │  remote mode:    │
                              │    ↓ TCP stream  │
                              └──────┬───────────┘
                                     │  TCP :5900
                                     ▼
                              ┌──────────────────┐
                              │  pykvm-client    │
                              │  (machine B)     │
                              │                  │
                              │  uinput inject   │
                              │    ↓             │
                              │  virtual KB+mouse│
                              └──────────────────┘
```

The server machine grabs all physical keyboard and mouse devices exclusively
(preventing the OS from processing them directly) and creates local `uinput`
passthrough clones so the local desktop continues to work normally in *local
mode*.

A configurable hotkey (default **Left-Ctrl + Left-Alt + Tab**) toggles between
**local mode** (events go to the local uinput clone) and **remote mode** (events
are serialised and sent over TCP to the client).

The client receives the event stream and injects each event into virtual `uinput`
devices, making them indistinguishable from real hardware to every application on
the client machine.

---

## Features (POC scope)

- Keyboard forwarding (all keys)
- Relative mouse forwarding (movement, buttons, scroll wheel)
- Hotkey-based switching (configurable key combo)
- Plain TCP transport (no TLS for now)
- Display-server agnostic (evdev/uinput only)
- NixOS-first: reproducible builds via Nix flakes + uv2nix

---

## Quick start

### Prerequisites

- Linux (kernel ≥ 3.1 for uinput)
- Python ≥ 3.12
- Nix with flakes enabled **or** `evdev` installed system-wide
- User must be in the `input` group on both machines, or run as root

### Development shell

```bash
git clone <repo>
cd pykvm
nix develop        # enters a shell with pykvm installed in editable mode
```

### Run the server (machine A — has the physical KB/mouse)

```bash
pykvm-server --host 0.0.0.0 --port 5900
```

### Run the client (machine B)

```bash
pykvm-client --server <IP-of-machine-A> --port 5900
```

### Switch focus

Press **Left-Ctrl + Left-Alt + Tab** on the server machine to toggle input
between local and remote.

---

## Documentation index

| Document | Description |
|---|---|
| [architecture.md](architecture.md) | Component overview and data-flow |
| [protocol.md](protocol.md) | Wire protocol specification |
| [devices.md](devices.md) | evdev device discovery and uinput injection |
| [configuration.md](configuration.md) | All configuration options |
| [development.md](development.md) | Dev environment setup (Nix, uv2nix) |
| [vm-testing.md](vm-testing.md) | Two-VM test setup with QEMU |
| [roadmap.md](roadmap.md) | What is done, what is planned |
