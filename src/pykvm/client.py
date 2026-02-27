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

    try:
        while True:
            data = await reader.readexactly(protocol.EVENT_SIZE)
            event = protocol.unpack(data)

            if event.type == ecodes.EV_REL:
                last_target = vmouse
                vmouse.write(event.type, event.code, event.value)
            elif event.type == ecodes.EV_KEY:
                if event.code in _MOUSE_BTNS:
                    last_target = vmouse
                    vmouse.write(event.type, event.code, event.value)
                else:
                    last_target = vkbd
                    vkbd.write(event.type, event.code, event.value)
            elif event.type == ecodes.EV_SYN:
                last_target.write(event.type, event.code, event.value)

    except asyncio.IncompleteReadError:
        log.info("Server closed the connection")
    finally:
        vkbd.close()
        vmouse.close()


def main() -> None:
    parser = ArgumentParser(
        description="pykvm client — inject events received from a pykvm server"
    )
    parser.add_argument("--server", required=True, metavar="HOST")
    parser.add_argument("--port", type=int, default=5900)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = ClientConfig(server_host=args.server, server_port=args.port)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
