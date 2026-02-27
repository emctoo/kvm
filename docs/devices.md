# Device Handling

## Linux input subsystem primer

Linux exposes every input device as a character device under `/dev/input/`:

```
/dev/input/event0   ← first device (often power button)
/dev/input/event1   ← second device (often keyboard)
/dev/input/event2   ← …
```

Each device advertises a set of *capabilities*: a mapping from event type
(`EV_KEY`, `EV_REL`, `EV_ABS`, …) to the codes it can generate.

`python-evdev` wraps this interface:

```python
import evdev
dev = evdev.InputDevice("/dev/input/event2")
print(dev.name)          # "AT Translated Set 2 keyboard"
print(dev.capabilities()) # {1: [1, 2, 3, …], 0: [0, 1, 4]}
```

## uinput

`uinput` is a kernel module that creates *virtual* input devices.  Writing an
event to a `uinput` device is indistinguishable from a real hardware event from
the perspective of any userspace application (including Wayland compositors and
X11 servers).

The `/dev/uinput` character device must exist (requires `modprobe uinput`) and
be writable by the running user.

---

## Device discovery (`devices.py`)

### `find_keyboards()`

```python
def find_keyboards() -> list[InputDevice]:
    for path in evdev.list_devices():
        dev = InputDevice(path)
        caps = dev.capabilities()
        keys = caps.get(ecodes.EV_KEY, [])
        if ecodes.KEY_A in keys:
            result.append(dev)
```

**Heuristic**: a device is treated as a keyboard if it advertises `EV_KEY` and
includes `KEY_A` (key code 30).  This reliably excludes power buttons, media
remotes, and mice (which only have `BTN_*` codes) while catching all standard
keyboards.

**Known limitation**: devices that emit `KEY_A` but are not keyboards (e.g. some
game controllers with keyboard emulation) will be grabbed.  A future version may
consult `device.name` or check for a broader key set.

### `find_mice()`

```python
def find_mice() -> list[InputDevice]:
    for path in evdev.list_devices():
        dev = InputDevice(path)
        if ecodes.EV_REL in dev.capabilities():
            result.append(dev)
```

**Heuristic**: a device is treated as a mouse if it advertises `EV_REL` (relative
motion events).  This catches standard mice, trackballs, and trackpads in
relative mode.

**Note**: touchpads in absolute mode (`EV_ABS`) are not forwarded in the current
POC scope.

---

## Device grabbing (server)

After discovery, the server calls `device.grab()` on each device:

```python
device.grab()
```

`grab()` sends `EVIOCGRAB` to the kernel, which causes the device's events to be
delivered *exclusively* to the grabbing process.  No other process — including
the display server — receives events from the grabbed device.

**Critical**: if the server process crashes without calling `device.ungrab()`,
the grab is released automatically by the kernel when the file descriptor is
closed.

To avoid locking the user out, the server:
1. Creates local `uinput` passthrough clones **before** grabbing.
2. Starts in **local mode** (events are forwarded to the clones, not sent over
   the network).
3. Only grabs after the TCP server socket is listening and ready.

---

## Virtual device creation (client)

### `create_virtual_keyboard()`

```python
UInput(
    {ecodes.EV_KEY: list(ecodes.keys.keys())},
    name="pykvm-keyboard",
    version=0x1,
)
```

Advertises the complete set of `EV_KEY` codes known to `python-evdev`.  This
ensures the virtual keyboard can reproduce any key the physical keyboard can
generate without needing to introspect the source device's exact capabilities.

The `version=0x1` field is written into the uinput device descriptor; it has no
functional effect.

### `create_virtual_mouse()`

```python
UInput(
    {
        ecodes.EV_KEY: [BTN_LEFT, BTN_RIGHT, BTN_MIDDLE],
        ecodes.EV_REL: [REL_X, REL_Y, REL_WHEEL, REL_HWHEEL],
    },
    name="pykvm-mouse",
    version=0x1,
)
```

Advertises three buttons and four relative axes.  Scroll events (`REL_WHEEL`,
`REL_HWHEEL`) are included.

---

## Writing events to uinput

```python
virtual_dev.write(event.type, event.code, event.value)
```

`EV_SYN / SYN_REPORT` must be written after each logical event batch.  If it is
omitted, the kernel buffers events indefinitely and applications never see them.

Correct sequence:
```python
virtual_dev.write(ecodes.EV_KEY, ecodes.KEY_A, 1)   # key down
virtual_dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
virtual_dev.write(ecodes.EV_KEY, ecodes.KEY_A, 0)   # key up
virtual_dev.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
```

Because `EV_SYN` frames are forwarded over the wire alongside regular events,
the client can simply write every received event verbatim without special-casing
sync events.

---

## Privileges and udev rules

The NixOS VM modules include:

```nix
services.udev.extraRules = ''
  KERNEL=="uinput",     MODE="0660", GROUP="input"
  SUBSYSTEM=="input",   MODE="0660", GROUP="input"
'';
boot.kernelModules = [ "uinput" ];
```

For non-NixOS systems, add `/etc/udev/rules.d/99-pykvm.rules`:

```
KERNEL=="uinput",   MODE="0660", GROUP="input"
SUBSYSTEM=="input", MODE="0660", GROUP="input"
```

Then reload udev and add your user to the `input` group:

```bash
sudo udevadm control --reload-rules
sudo usermod -aG input $USER
# log out and back in
```
