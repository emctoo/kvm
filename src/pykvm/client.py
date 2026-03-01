"""
Client: receives events from server, injects via uinput.

Usage:
    pykvm-client --server HOST [--port PORT]

Auto-reconnect
--------------
The client retries the connection indefinitely with exponential back-off
(1 s → 2 s → 4 s → … → 60 s cap) whenever the server is unreachable or
drops the connection.  The back-off resets to 1 s after each successful
TCP handshake so that normal server restarts reconnect quickly.

Virtual keyboard and mouse are created once and kept alive across
reconnects so that the compositor never sees them disappear.  The virtual
touchpad is recreated per connection because its ABS capability ranges
come from the server's capability handshake and may differ between
sessions.
"""

import asyncio
import logging
import socket
from argparse import ArgumentParser

from evdev import ecodes

from pykvm import devices, protocol
from pykvm.config import ClientConfig

log = logging.getLogger(__name__)

# BTN_MOUSE … BTN_JOYSTICK-1 are mouse/pointer button codes.
_MOUSE_BTNS: frozenset[int] = frozenset(range(ecodes.BTN_MOUSE, ecodes.BTN_JOYSTICK))


def _apply_keepalive(sock: socket.socket, *, idle: int = 10, interval: int = 5, count: int = 3) -> None:
    """Enable TCP keep-alive on *sock* with aggressive timeouts.

    With the defaults a half-open connection is detected in roughly
    idle + interval * count = 25 seconds, at which point the OS sends a RST
    and any pending asyncio read() raises OSError / IncompleteReadError,
    triggering the reconnect loop.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for opt, val in (
        (getattr(socket, "TCP_KEEPIDLE", None), idle),
        (getattr(socket, "TCP_KEEPINTVL", None), interval),
        (getattr(socket, "TCP_KEEPCNT", None), count),
    ):
        if opt is not None:
            sock.setsockopt(socket.IPPROTO_TCP, opt, val)


# Touchpad-specific button codes that must go to the virtual touchpad so
# libinput can process tap-to-click, gestures, etc.
_TOUCHPAD_BTNS: frozenset[int] = frozenset(
    c
    for name in (
        "BTN_TOUCH",
        "BTN_TOOL_FINGER",
        "BTN_TOOL_DOUBLETAP",
        "BTN_TOOL_TRIPLETAP",
        "BTN_TOOL_QUADTAP",
        "BTN_TOOL_QUINTTAP",
    )
    if (c := getattr(ecodes, name, None)) is not None
)

_VAL = {0: "up", 1: "dn", 2: "rp"}

_BACKOFF_INIT = 1.0  # seconds before first retry
_BACKOFF_MAX = 60.0  # ceiling for exponential back-off
_HANDSHAKE_TIMEOUT = 5.0  # seconds to wait for the server capability header


def _key_name(code: int) -> str:
    name = ecodes.keys.get(code, str(code))
    return name[0] if isinstance(name, list) else name


async def run(cfg: ClientConfig) -> None:
    # Virtual keyboard and mouse persist for the lifetime of the process so
    # the compositor never sees them flap on reconnect.
    vkbd = devices.create_virtual_keyboard()
    vmouse = devices.create_virtual_mouse()
    log.info("Virtual devices created")

    delay = _BACKOFF_INIT

    try:
        while True:
            vtouchpad = None
            writer = None
            try:
                log.info("Connecting to %s:%d …", cfg.server_host, cfg.server_port)
                reader, writer = await asyncio.open_connection(cfg.server_host, cfg.server_port)
                log.info("Connected")
                _apply_keepalive(writer.get_extra_info("socket"))
                delay = _BACKOFF_INIT  # successful connection → reset back-off

                # ── capability handshake ──────────────────────────────────
                # Server sends a 4-byte length followed by a JSON caps body
                # (or length 0 when no physical touchpad is attached).
                # Both reads are guarded by a timeout so a stalled server
                # cannot block the client indefinitely.
                hdr = await asyncio.wait_for(
                    reader.readexactly(protocol.CAPS_HDR_SIZE),
                    timeout=_HANDSHAKE_TIMEOUT,
                )
                caps_len = protocol.unpack_caps_header(hdr)
                if caps_len > 0:
                    body = await asyncio.wait_for(
                        reader.readexactly(caps_len),
                        timeout=_HANDSHAKE_TIMEOUT,
                    )
                    caps_json = protocol.unpack_caps_body(body)
                    vtouchpad = devices.create_virtual_touchpad_from_caps(caps_json)
                    log.info("Virtual touchpad created")
                else:
                    log.info("Server has no touchpad")

                # ── event loop ────────────────────────────────────────────
                # Track which virtual device received the last non-SYN event
                # so that EV_SYN / SYN_REPORT is flushed to the correct device.
                last_target = vkbd

                # Running mouse position (relative to start) for debug logging.
                mouse_x: int = 0
                mouse_y: int = 0

                while True:
                    data = await reader.readexactly(protocol.EVENT_SIZE)
                    event = protocol.unpack(data)

                    if event.type == ecodes.EV_ABS:
                        # Raw touchpad absolute-position event; route to vtouchpad
                        # so libinput on this host can process gestures.
                        if vtouchpad is not None:
                            last_target = vtouchpad
                            vtouchpad.write(event.type, event.code, event.value)

                    elif event.type == ecodes.EV_REL:
                        last_target = vmouse
                        vmouse.write(event.type, event.code, event.value)
                        if event.code == ecodes.REL_X:
                            mouse_x += event.value
                        elif event.code == ecodes.REL_Y:
                            mouse_y += event.value

                    elif event.type == ecodes.EV_KEY:
                        if vtouchpad is not None and event.code in _TOUCHPAD_BTNS:
                            # BTN_TOUCH / BTN_TOOL_FINGER / etc. — always to touchpad.
                            last_target = vtouchpad
                            vtouchpad.write(event.type, event.code, event.value)
                            log.debug("tp    %s %s", _key_name(event.code), _VAL.get(event.value, event.value))
                        elif event.code in _MOUSE_BTNS:
                            # BTN_LEFT/RIGHT/MIDDLE may come from a physical touchpad
                            # button or a regular mouse.  Use last_target as a
                            # heuristic: if the previous event was a touchpad event,
                            # route to vtouchpad; otherwise to vmouse.
                            if vtouchpad is not None and last_target is vtouchpad:
                                vtouchpad.write(event.type, event.code, event.value)
                                log.debug("tp    %s %s", _key_name(event.code), _VAL.get(event.value, event.value))
                            else:
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

            except asyncio.TimeoutError:
                log.warning("Handshake timed out (server stalled?) — retrying in %.0fs", delay)
            except asyncio.IncompleteReadError:
                log.info("Server disconnected — reconnecting in %.0fs", delay)
            except ValueError as exc:
                log.warning("Malformed capability data (%s) — retrying in %.0fs", exc, delay)
            except OSError as exc:
                log.warning("Connection failed (%s) — retrying in %.0fs", exc, delay)
            finally:
                # Always clean up the TCP connection and per-session vtouchpad.
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass
                if vtouchpad is not None:
                    vtouchpad.close()

            await asyncio.sleep(delay)
            delay = min(delay * 2, _BACKOFF_MAX)

    except asyncio.CancelledError:
        pass  # clean shutdown via Ctrl+C / task cancellation
    finally:
        vkbd.close()
        vmouse.close()


def main() -> None:
    parser = ArgumentParser(description="pykvm client — inject events received from a pykvm server")
    parser.add_argument("--server", required=True, metavar="HOST")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument("--debug", "-v", action="store_true", help="Log injected events to /tmp/pykvm.debug.log")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s")

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
