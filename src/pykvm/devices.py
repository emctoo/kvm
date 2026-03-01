"""
Device discovery and virtual device creation.
"""

import evdev
from evdev import AbsInfo, InputDevice, UInput, ecodes


def find_keyboards() -> list[InputDevice]:
    """Return all devices that have EV_KEY with KEY_A (basic keyboard check)."""
    result = []
    for path in evdev.list_devices():
        dev = InputDevice(path)
        caps = dev.capabilities()
        keys = caps.get(ecodes.EV_KEY, [])
        if ecodes.KEY_A in keys:
            result.append(dev)
    return result


def find_mice() -> list[InputDevice]:
    """Return devices that produce pointer movement: EV_REL mice or EV_ABS touchpads.

    Accepts both single-touch (ABS_X/ABS_Y) and multitouch type-B
    (ABS_MT_POSITION_X/ABS_MT_POSITION_Y) touchpads.
    """
    _mt_x = getattr(ecodes, "ABS_MT_POSITION_X", None)
    _mt_y = getattr(ecodes, "ABS_MT_POSITION_Y", None)

    result = []
    for path in evdev.list_devices():
        dev = InputDevice(path)
        caps = dev.capabilities()
        if ecodes.EV_REL in caps:
            result.append(dev)
        elif ecodes.EV_ABS in caps:
            abs_codes = {code for code, _ in caps[ecodes.EV_ABS]}
            single = ecodes.ABS_X in abs_codes and ecodes.ABS_Y in abs_codes
            mt = _mt_x in abs_codes and _mt_y in abs_codes if (_mt_x and _mt_y) else False
            if single or mt:
                result.append(dev)
    return result


def create_virtual_touchpad(source: InputDevice) -> UInput:
    """Create a virtual touchpad that mirrors the physical device's capabilities.

    Capabilities (including ABS min/max/resolution ranges) are copied verbatim
    from the source device so that libinput can classify the virtual touchpad
    and process gestures — tap-to-click, two-finger scroll, three-finger
    gestures, etc. — identically to the real hardware.

    Used in local mode so the host desktop retains full touchpad support even
    while the server has the physical device grabbed.
    """
    # EV_SYN is added automatically by UInput; EV_MSC (scan codes) and EV_FF
    # (force feedback) are not needed for gesture processing.
    _SKIP = {ecodes.EV_SYN, ecodes.EV_MSC}
    caps = {ev_type: codes for ev_type, codes in source.capabilities(absinfo=True).items() if ev_type not in _SKIP}
    return UInput(
        caps,
        name="pykvm-touchpad",
        version=0x1,
        input_props=[ecodes.INPUT_PROP_POINTER],
    )


def create_virtual_touchpad_from_caps(caps_json: dict) -> UInput:
    """Create a virtual touchpad on the client from server-provided capabilities JSON.

    The JSON maps ev_type (decimal string) to:
      EV_ABS (3): [[code, [value, min, max, fuzz, flat, resolution]], ...]
      other types: [code, ...]

    AbsInfo ranges are reconstructed so libinput classifies the device
    identically to the physical touchpad on the server.

    Raises ValueError if any field cannot be coerced to the expected type,
    so callers can handle corrupt or tampered data without crashing.
    """
    caps: dict = {}
    try:
        for key, codes in caps_json.items():
            try:
                ev_type = int(key)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"caps: invalid ev_type key {key!r}") from exc

            if not isinstance(codes, list):
                raise ValueError(f"caps: codes for ev_type {ev_type} must be a list, got {type(codes).__name__}")

            if ev_type == ecodes.EV_ABS:
                abs_caps = []
                for entry in codes:
                    try:
                        code, absinfo = entry
                        abs_caps.append((int(code), AbsInfo(*[int(x) for x in absinfo])))
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"caps: malformed EV_ABS entry {entry!r}") from exc
                caps[ev_type] = abs_caps
            else:
                try:
                    caps[ev_type] = [int(c) for c in codes]
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"caps: invalid code in ev_type {ev_type}: {exc}") from exc
    except AttributeError as exc:
        # caps_json.items() failed — top-level object is not a dict
        raise ValueError(f"caps: expected dict, got {type(caps_json).__name__}") from exc

    return UInput(
        caps,
        name="pykvm-touchpad",
        version=0x1,
        input_props=[ecodes.INPUT_PROP_POINTER],
    )


def create_virtual_keyboard() -> UInput:
    """Create a uinput virtual keyboard mirroring a full key set."""
    return UInput(
        {ecodes.EV_KEY: list(ecodes.keys.keys())},
        name="pykvm-keyboard",
        version=0x1,
    )


def create_virtual_mouse() -> UInput:
    """Create a uinput virtual relative-pointer device.

    INPUT_PROP_POINTER tells libinput to classify this device as a pointer so
    that X11 / Wayland compositors move the cursor when EV_REL events arrive.

    REL_WHEEL_HI_RES / REL_HWHEEL_HI_RES (added in Linux 4.19) are sent by
    most modern mice in addition to REL_WHEEL / REL_HWHEEL.  Without them the
    virtual device silently drops those events, breaking scroll forwarding and
    occasionally confusing libinput's event accounting.

    BTN_SIDE / BTN_EXTRA / BTN_FORWARD / BTN_BACK cover the extra thumb buttons
    common on gaming and productivity mice.
    """
    # REL_WHEEL_HI_RES = 11, REL_HWHEEL_HI_RES = 12 (Linux 4.19+)
    hi_res = [
        c
        for c in (
            getattr(ecodes, "REL_WHEEL_HI_RES", None),
            getattr(ecodes, "REL_HWHEEL_HI_RES", None),
        )
        if c is not None
    ]

    return UInput(
        {
            ecodes.EV_KEY: [
                ecodes.BTN_LEFT,
                ecodes.BTN_RIGHT,
                ecodes.BTN_MIDDLE,
                ecodes.BTN_SIDE,
                ecodes.BTN_EXTRA,
                ecodes.BTN_FORWARD,
                ecodes.BTN_BACK,
            ],
            ecodes.EV_REL: [
                ecodes.REL_X,
                ecodes.REL_Y,
                ecodes.REL_WHEEL,
                ecodes.REL_HWHEEL,
                *hi_res,
            ],
        },
        name="pykvm-mouse",
        version=0x1,
        input_props=[ecodes.INPUT_PROP_POINTER],
    )
