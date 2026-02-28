"""
Client: receives events from server, injects via uinput.

Usage:
    pykvm-client --server HOST [--port PORT]
"""

import asyncio
import logging
from argparse import ArgumentParser

from evdev import ecodes

from pykvm import devices, protocol
from pykvm.config import ClientConfig

log = logging.getLogger(__name__)

# BTN_MOUSE … BTN_JOYSTICK-1 are mouse/pointer button codes.
_MOUSE_BTNS: frozenset[int] = frozenset(range(ecodes.BTN_MOUSE, ecodes.BTN_JOYSTICK))

_VAL = {0: "up", 1: "dn", 2: "rp"}


def _key_name(code: int) -> str:
    name = ecodes.keys.get(code, str(code))
    return name[0] if isinstance(name, list) else name


async def run(cfg: ClientConfig) -> None:
    vkbd = devices.create_virtual_keyboard()
    vmouse = devices.create_virtual_mouse()
    log.info("Virtual devices created")

    log.info("Connecting to %s:%d …", cfg.server_host, cfg.server_port)
    reader, _ = await asyncio.open_connection(cfg.server_host, cfg.server_port)
    log.info("Connected")

    # Track which virtual device received the last non-SYN event so that
    # EV_SYN / SYN_REPORT is flushed to the correct device.
    last_target = vkbd

    # Running mouse position (relative to start) used for debug logging.
    # Updated on EV_REL; logged once per SYN_REPORT to avoid flooding the log.
    mouse_x: int = 0
    mouse_y: int = 0

    try:
        while True:
            data = await reader.readexactly(protocol.EVENT_SIZE)
            event = protocol.unpack(data)

            if event.type == ecodes.EV_REL:
                last_target = vmouse
                vmouse.write(event.type, event.code, event.value)
                if event.code == ecodes.REL_X:
                    mouse_x += event.value
                elif event.code == ecodes.REL_Y:
                    mouse_y += event.value
            elif event.type == ecodes.EV_KEY:
                if event.code in _MOUSE_BTNS:
                    last_target = vmouse
                    vmouse.write(event.type, event.code, event.value)
                    log.debug("mouse %s %s", _key_name(event.code), _VAL.get(event.value, event.value))
                else:
                    last_target = vkbd
                    vkbd.write(event.type, event.code, event.value)
                    log.debug("kbd   %s %s", _key_name(event.code), _VAL.get(event.value, event.value))
            elif event.type == ecodes.EV_SYN:
                last_target.write(event.type, event.code, event.value)
                if last_target is vmouse:
                    log.debug("mouse pos (%d, %d)", mouse_x, mouse_y)

    except asyncio.IncompleteReadError:
        log.info("Server closed the connection")
    finally:
        vkbd.close()
        vmouse.close()


def main() -> None:
    parser = ArgumentParser(description="pykvm client — inject events received from a pykvm server")
    parser.add_argument("--server", required=True, metavar="HOST")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--debug", "-v", action="store_true", help="Log injected events to /tmp/pykvm.debug.log")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.debug:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        for h in root.handlers:
            h.setLevel(logging.INFO)
        fh = logging.FileHandler("/tmp/pykvm.debug.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(fh)
        log.info("Debug event log → /tmp/pykvm.debug.log")

    cfg = ClientConfig(server_host=args.server, server_port=args.port)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
