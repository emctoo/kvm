# uinput Event Routing

This document explains where injected uinput events actually go inside a Linux
guest, why they are invisible in certain VM configurations, and how to verify
that injection is working correctly.

---

## The Linux input event path

When pykvm-client calls `UInput.write(EV_KEY, KEY_A, 1)` the event travels
through the kernel's input subsystem in two parallel directions:

```
uinput device (/dev/uinput)
        │
        ▼
  kernel input core
        │
        ├──► evdev handler  ──► /dev/input/eventN   (readable by user-space)
        │
        └──► kbd handler    ──► active Virtual Terminal (VT / ttyN)
```

- **evdev handler**: exposes raw events to any process that opens
  `/dev/input/eventN` — this is what `evtest`, X11, Wayland, and pykvm-server
  itself use.
- **kbd handler**: translates key codes into characters and delivers them to
  whichever VT is currently in the foreground.  This is the path that makes
  text appear in a text-mode console without any display server.

Both handlers are attached automatically by the kernel when a uinput device
advertises `EV_KEY` capabilities.

---

## Virtual Terminals vs. serial console

A standard Linux boot creates several Virtual Terminals (`tty1`–`tty6`).
These are independent text screens; only one is "active" at a time.  The `kbd`
handler sends key events to the active VT.

In pykvm's NixOS VMs, `services.getty.autologinUser = "root"` logs root in
automatically on **tty1**.  Injected keyboard events therefore land on **tty1**.

QEMU's `-nographic` flag maps the guest's **serial port** (`ttyS0`) to the
host terminal — this is a completely separate device from the VTs.  The
result:

```
Physical keyboard
      │ (grabbed by pykvm-server)
      │ TCP
      ▼
pykvm-client (VM)
      │ UInput.write()
      ▼
  tty1  ←── kbd handler delivers events here
      │
      ✗  not visible — user is watching ttyS0 via -nographic
```

Even though events are injected correctly, the user sees nothing because their
terminal window shows `ttyS0`, not `tty1`.

This behaviour has nothing to do with the presence or absence of a window
manager.  The mismatch is purely between which console the QEMU serial bridge
exposes and which console the `kbd` handler targets.

---

## When a display is present

Running QEMU **with** a display window (`just vm-dev-gui`) changes the picture:

```
QEMU display window  ←── renders tty1
      │
      tty1  ←── kbd handler delivers events here
```

The QEMU window shows the active VT (`tty1`), so injected key events appear
as typed characters in the window, exactly as if a physical keyboard were
plugged into the VM.

---

## When a display server (X11 / Wayland) is running

A display server reads directly from `/dev/input/eventN` (the **evdev** path)
rather than relying on the `kbd` → VT path.  With a display server running:

- Injected events appear in whichever window has keyboard focus.
- The serial console / VT distinction is irrelevant.

For the current use-case (headless dev VM), a display server is not required.

---

## Verifying injection without a display

`evtest` monitors the evdev path directly, making it the easiest way to confirm
that events are being received and injected in a headless environment.

```bash
# In an SSH session inside the dev VM (just vm-ssh):
evtest /dev/input/by-name/pykvm-keyboard
```

Switch the server to remote mode (Left-Ctrl + Left-Alt + Tab) and type — you
should see output like:

```
Event: time 1700000000.123, type 1 (EV_KEY), code 30 (KEY_A), value 1
Event: time 1700000000.124, type 0 (EV_SYN), code 0 (SYN_REPORT), value 0
Event: time 1700000000.200, type 1 (EV_KEY), code 30 (KEY_A), value 0
```

If events appear here, injection is working correctly.  The only issue is that
there is no consumer forwarding those events to your terminal.

The `pykvm-client --debug` flag writes a human-readable log to
`/tmp/pykvm.debug.log` and is another quick way to confirm events are flowing
over the network:

```bash
tail -f /tmp/pykvm.debug.log
# 14:05:13 kbd   KEY_A dn
# 14:05:13 kbd   KEY_A up
```

---

## Choosing the right mode

| Goal | Recommended setup |
|---|---|
| Verify events flow end-to-end | `just vm-dev` (headless) + `evtest` over SSH |
| See characters appear in a VM shell | `just vm-dev-gui` (QEMU window) |
| Full KVM use with a graphical VM | Run a display server (X11/Wayland) inside the VM |

---

## Summary

| Component | Role |
|---|---|
| `UInput.write()` | Injects raw input events into the kernel |
| evdev handler | Exposes events on `/dev/input/eventN` |
| kbd handler | Delivers events to the active VT (tty1) |
| QEMU `-nographic` | Bridges guest serial port (ttyS0) to host terminal |
| QEMU display window | Renders the active VT (tty1) — sees injected events |
| `evtest` | Reads from evdev path — confirms injection over SSH |
