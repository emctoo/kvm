"""
Device discovery and virtual device creation.
"""

import evdev
from evdev import InputDevice, UInput, ecodes


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
    """Return all devices that have EV_REL (relative pointer movement)."""
    result = []
    for path in evdev.list_devices():
        dev = InputDevice(path)
        if ecodes.EV_REL in dev.capabilities():
            result.append(dev)
    return result


def create_virtual_keyboard() -> UInput:
    """Create a uinput virtual keyboard mirroring a full key set."""
    return UInput(
        {ecodes.EV_KEY: list(ecodes.keys.keys())},
        name="pykvm-keyboard",
        version=0x1,
    )


def create_virtual_mouse() -> UInput:
    """Create a uinput virtual relative-pointer device."""
    return UInput(
        {
            ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
            ecodes.EV_REL: [
                ecodes.REL_X,
                ecodes.REL_Y,
                ecodes.REL_WHEEL,
                ecodes.REL_HWHEEL,
            ],
        },
        name="pykvm-mouse",
        version=0x1,
    )
