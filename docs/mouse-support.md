# Mouse Support

This document explains how pykvm forwards physical mouse events to a virtual
machine and what you need to see the mouse cursor move.

---

## Event flow

```
Physical mouse (host)
      │  EV_REL / BTN_*
      ▼
pykvm-server   (grabs device, serialises events over TCP)
      │  TCP stream
      ▼
pykvm-client   (receives events, routes to virtual devices)
      │  UInput.write()
      ▼
pykvm-mouse   (/dev/input/eventN  — virtual relative-pointer)
      │
      ├──► evdev handler   ──►  /dev/input/eventN
      │                              ▲
      │                         libinput / X11 reads here
      │
      └──► kbd handler    ──►  (ignored for REL events)
```

The server grabs all physical mice it finds (any device with `EV_REL`
capability) in addition to keyboards.  When in **remote** mode the server sends
every event — `EV_REL` movement, `BTN_LEFT/RIGHT/MIDDLE`, and `EV_SYN` — to
the client.

The client routes events based on type:

| Event | Routed to |
|---|---|
| `EV_REL` (any axis) | `pykvm-mouse` |
| `EV_KEY` + code in `BTN_MOUSE`…`BTN_JOYSTICK` | `pykvm-mouse` |
| `EV_KEY` (all other codes) | `pykvm-keyboard` |
| `EV_SYN` | whichever device received the preceding event |

---

## Why you need a desktop environment

The mouse cursor only exists inside a display server (X11 or Wayland).  Without
one, `EV_REL` events are injected correctly into the kernel but there is
nothing consuming them — no cursor to move.

| Environment | Mouse cursor visible? | Keyboard visible? |
|---|---|---|
| Headless QEMU (`-nographic`) | No | No (see [uinput-routing.md](uinput-routing.md)) |
| QEMU display window, no DM | No cursor; VT text only | Yes — characters appear in VT |
| XFCE desktop (X11 + libinput) | **Yes** — cursor moves | Yes — characters appear in focused window |

---

## libinput device classification

pykvm creates its virtual mouse with `INPUT_PROP_POINTER` set.  libinput uses
this property to classify a device as a pointer, which causes it to move the
desktop cursor.  Without this property libinput might ignore the device or
classify it as a keyboard.

Verify inside the VM:

```bash
# List all input devices and their properties
libinput list-devices | grep -A5 pykvm-mouse
# Should show:  Capabilities:  pointer
```

---

## Desktop dev VM

`vm-dev-desktop` is a NixOS VM with XFCE that you can use to see mouse and
keyboard injection working end-to-end.

### Start the VM

```bash
# Build and launch (opens a QEMU display window — required for the cursor)
just vm-dev-desktop
```

The VM boots and auto-logs in as `user` directly into XFCE (LightDM does not
allow root autologin).  SSH as root is available on `localhost:2223` while the
VM is running.

### Sync and run

Open a second terminal on the host:

```bash
# Sync the current src/ into the VM
just vm-desktop-sync

# Start pykvm-server on the host (in a third terminal if not already running)
just server

# Run pykvm-client inside the VM
just vm-desktop-run-client
```

Press **Left-Ctrl + Left-Alt + Tab** on your physical keyboard to toggle the
server into remote mode.  The mouse cursor inside the XFCE window should now
move as you move your physical mouse, and clicking should work.

### SSH into the VM

```bash
just vm-desktop-ssh
```

### Verify injection with evtest

While pykvm-client is running (inside the VM or over SSH):

```bash
evtest /dev/input/by-name/pykvm-mouse
# Move your physical mouse → you should see REL_X / REL_Y events
```

---

## Troubleshooting

### Cursor does not move

1. Confirm pykvm-client is running and connected:
   ```bash
   just vm-desktop-ssh
   journalctl -u pykvm-client    # if running as a service
   # or check the debug log
   tail -f /tmp/pykvm.debug.log
   ```
2. Confirm the server is in remote mode (check server log for `→ remote`).
3. Confirm the virtual mouse is visible to libinput:
   ```bash
   libinput list-devices | grep pykvm
   ```
4. If `pykvm-mouse` is not listed, check udev rules:
   ```bash
   ls -la /dev/uinput
   udevadm control --reload-rules && udevadm trigger
   ```

### Mouse jumps or is sluggish

The QEMU user-net path adds latency.  This is expected in a VM-based test
environment; on real hardware over a LAN the latency is much lower.

### Clicks land in wrong position

Relative mouse events carry **delta** movement, not absolute coordinates.
The starting position of the cursor is wherever it was when pykvm-client
connected.  This is normal for a relative-mode pointer device.
