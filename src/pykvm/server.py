"""
Server: captures physical keyboard/mouse, forwards events over TCP.

Usage:
    pykvm-server [--host HOST] [--port PORT]

Modes
-----
local  — events are written to local uinput passthrough clones (default).
remote — events are serialised and sent to the connected client over TCP.

The hotkey (default: Left-Ctrl + Left-Alt + Tab) toggles between modes.
On toggle, held keys are synthetically released on the outgoing side to
prevent stuck keys.
"""

import asyncio
import logging
import sys
from argparse import ArgumentParser

from evdev import InputDevice, ecodes

from pykvm import devices, protocol
from pykvm.config import ServerConfig

log = logging.getLogger(__name__)

_MOUSE_BTNS: frozenset[int] = frozenset(range(ecodes.BTN_MOUSE, ecodes.BTN_JOYSTICK))

# Absolute position codes accepted as touchpad X/Y (single-touch and MT protocol B).
_TP_X_CODES: frozenset[int] = frozenset(c for c in (ecodes.ABS_X, getattr(ecodes, "ABS_MT_POSITION_X", None)) if c is not None)
_TP_Y_CODES: frozenset[int] = frozenset(c for c in (ecodes.ABS_Y, getattr(ecodes, "ABS_MT_POSITION_Y", None)) if c is not None)
# ABS_MT_TRACKING_ID value -1 signals finger lift in type-B multitouch.
_ABS_MT_TRACKING_ID: int | None = getattr(ecodes, "ABS_MT_TRACKING_ID", None)

# Touchpad-only button codes that the virtual mouse doesn't understand.
_TOUCHPAD_ONLY_BTNS: frozenset[int] = frozenset(
    c
    for name in (
        "BTN_TOUCH",
        "BTN_TOOL_FINGER",
        "BTN_TOOL_DOUBLETAP",
        "BTN_TOOL_TRIPLETAP",
        "BTN_TOOL_QUADTAP",
        "BTN_TOOL_QUINTTAP",
        "BTN_TOOL_PEN",
        "BTN_TOOL_RUBBER",
        "BTN_TOOL_BRUSH",
        "BTN_TOOL_PENCIL",
        "BTN_TOOL_AIRBRUSH",
    )
    if (c := getattr(ecodes, name, None)) is not None
)

_VAL = {0: "up", 1: "dn", 2: "rp"}


def _key_name(code: int) -> str:
    name = ecodes.keys.get(code, str(code))
    return name[0] if isinstance(name, list) else name


async def run(cfg: ServerConfig) -> None:
    # ── discover and grab physical devices ──────────────────────────────────
    keyboards = devices.find_keyboards()
    mice = devices.find_mice()

    # Deduplicate by path: a device that has both EV_KEY and EV_REL (e.g. a
    # keyboard with a scroll wheel or trackpoint) appears in both lists.
    # Keep only one InputDevice per path and close the redundant file
    # descriptor, otherwise we would attempt to grab the same device twice.
    seen: dict[str, InputDevice] = {}
    for dev in keyboards + mice:
        if dev.path in seen:
            dev.close()
        else:
            seen[dev.path] = dev
    all_devs: list[InputDevice] = list(seen.values())

    if not all_devs:
        log.error("No input devices found. Is the user in the 'input' group?")
        sys.exit(1)

    for dev in all_devs:
        caps = dev.capabilities()
        has_kbd = ecodes.KEY_A in caps.get(ecodes.EV_KEY, [])
        has_rel = ecodes.EV_REL in caps
        has_abs = ecodes.EV_ABS in caps and {c for c, _ in caps[ecodes.EV_ABS]} >= {ecodes.ABS_X, ecodes.ABS_Y}
        pointer = "touchpad" if has_abs and not has_rel else ("mouse" if has_rel else "")
        kind = "+".join(filter(None, ["kbd" if has_kbd else "", pointer]))
        log.info("Found  %s  (%s)  [%s]", dev.name, dev.path, kind or "?")

    vkbd = devices.create_virtual_keyboard()
    vmouse = devices.create_virtual_mouse()

    grabbed: list[InputDevice] = []
    for dev in all_devs:
        try:
            dev.grab()
            grabbed.append(dev)
            log.info("Grabbed %s", dev.path)
        except OSError as exc:
            log.warning("Could not grab %s: %s — skipping", dev.path, exc)
            dev.close()

    if not grabbed:
        log.error("No devices could be grabbed.")
        vkbd.close()
        vmouse.close()
        sys.exit(1)

    all_devs = grabbed

    # ── shared mutable state ─────────────────────────────────────────────────
    # All device tasks run in the same event loop (single-threaded), so no
    # locking is needed for reads.  Assignments are only made at await points
    # where only one task is active, which is safe in asyncio.
    mode: str = "local"
    held_keys: set[int] = set()
    last_local_target = vkbd
    writer: asyncio.StreamWriter | None = None
    # Running mouse position for debug logging; reset each time remote mode starts.
    mouse_x: int = 0
    mouse_y: int = 0
    # Touchpad ABS tracking: last reported absolute position (None = not yet seen).
    # Accumulated deltas are emitted as EV_REL on EV_SYN.
    _tp_x: int | None = None
    _tp_y: int | None = None
    _tp_dx: int = 0
    _tp_dy: int = 0

    # ── routing helpers ──────────────────────────────────────────────────────
    def _is_mouse(ev) -> bool:
        return ev.type == ecodes.EV_REL or (ev.type == ecodes.EV_KEY and ev.code in _MOUSE_BTNS)

    def _route_local(ev) -> None:
        nonlocal last_local_target
        if ev.type == ecodes.EV_SYN:
            last_local_target.write(ev.type, ev.code, ev.value)
        else:
            target = vmouse if _is_mouse(ev) else vkbd
            last_local_target = target
            target.write(ev.type, ev.code, ev.value)

    def _write_remote(ev) -> None:
        if writer is not None:
            writer.write(protocol.pack(protocol.RawEvent(ev.type, ev.code, ev.value)))

    async def _flush_remote() -> None:
        if writer is not None:
            try:
                await writer.drain()
            except OSError:
                pass  # disconnect is handled in _handle_client

    # ── stuck-key release ────────────────────────────────────────────────────
    def _release_held_local() -> None:
        for code in list(held_keys):
            vkbd.write(ecodes.EV_KEY, code, 0)
        if held_keys:
            vkbd.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

    async def _release_held_remote() -> None:
        for code in list(held_keys):
            _write_remote(protocol.RawEvent(ecodes.EV_KEY, code, 0))
        if held_keys:
            _write_remote(protocol.RawEvent(ecodes.EV_SYN, ecodes.SYN_REPORT, 0))
            await _flush_remote()

    # ── mode toggle ──────────────────────────────────────────────────────────
    async def _toggle() -> None:
        nonlocal mode, mouse_x, mouse_y, _tp_x, _tp_y, _tp_dx, _tp_dy
        if mode == "local":
            _release_held_local()
            held_keys.clear()
            mouse_x = 0
            mouse_y = 0
            _tp_x = None
            _tp_y = None
            _tp_dx = 0
            _tp_dy = 0
            mode = "remote"
            log.info("→ remote")
        else:
            await _release_held_remote()
            held_keys.clear()
            mode = "local"
            log.info("→ local")

    # ── per-device read loop ─────────────────────────────────────────────────
    async def _read_device(dev: InputDevice) -> None:
        nonlocal mouse_x, mouse_y, _tp_x, _tp_y, _tp_dx, _tp_dy
        try:
            async for ev in dev.async_read_loop():
                # Maintain held-key set for hotkey detection and stuck-key release.
                if ev.type == ecodes.EV_KEY:
                    # Drop touchpad-only buttons before any further processing.
                    if ev.code in _TOUCHPAD_ONLY_BTNS:
                        if ev.code == ecodes.BTN_TOUCH and ev.value == 0:
                            # Finger lifted — reset touchpad position so next touch
                            # doesn't produce a large jump.
                            _tp_x = None
                            _tp_y = None
                        continue

                    if ev.value == 1:  # key down
                        held_keys.add(ev.code)
                    elif ev.value == 0:  # key up
                        held_keys.discard(ev.code)

                    # Hotkey fires when the last key of the combo is pressed.
                    if ev.value == 1 and ev.code in cfg.hotkey and cfg.hotkey.issubset(held_keys):
                        await _toggle()
                        continue  # swallow the triggering key press

                    log.debug("[%s] kbd %s %s", mode, _key_name(ev.code), _VAL.get(ev.value, ev.value))

                elif ev.type == ecodes.EV_REL:
                    if ev.code == ecodes.REL_X:
                        mouse_x += ev.value
                    elif ev.code == ecodes.REL_Y:
                        mouse_y += ev.value
                    log.debug("[%s] mouse %s %+d  pos(%d,%d)", mode, ecodes.REL.get(ev.code, ev.code), ev.value, mouse_x, mouse_y)

                elif ev.type == ecodes.EV_ABS:
                    # Touchpad: accumulate deltas; emit synthetic EV_REL on EV_SYN.
                    if ev.code in _TP_X_CODES:
                        if _tp_x is not None:
                            _tp_dx += ev.value - _tp_x
                        _tp_x = ev.value
                        log.debug("[%s] abs X=%d  dx=%+d", mode, ev.value, _tp_dx)
                    elif ev.code in _TP_Y_CODES:
                        if _tp_y is not None:
                            _tp_dy += ev.value - _tp_y
                        _tp_y = ev.value
                        log.debug("[%s] abs Y=%d  dy=%+d", mode, ev.value, _tp_dy)
                    elif ev.code == _ABS_MT_TRACKING_ID and ev.value == -1:
                        # Type-B multitouch: finger lifted — reset position.
                        _tp_x = None
                        _tp_y = None
                    continue  # never forward raw ABS events

                # SYN_MT_REPORT is an internal multitouch sync; skip it entirely.
                if ev.type == ecodes.EV_SYN and ev.code != ecodes.SYN_REPORT:
                    continue

                # EV_SYN SYN_REPORT: flush any accumulated touchpad deltas first.
                if ev.type == ecodes.EV_SYN:
                    if _tp_dx or _tp_dy:
                        mouse_x += _tp_dx
                        mouse_y += _tp_dy
                        log.debug("[%s] touchpad rel(%+d,%+d)  pos(%d,%d)", mode, _tp_dx, _tp_dy, mouse_x, mouse_y)
                        rel_x = protocol.RawEvent(ecodes.EV_REL, ecodes.REL_X, _tp_dx)
                        rel_y = protocol.RawEvent(ecodes.EV_REL, ecodes.REL_Y, _tp_dy)
                        _tp_dx = 0
                        _tp_dy = 0
                        if mode == "local":
                            _route_local(rel_x)
                            _route_local(rel_y)
                        else:
                            _write_remote(rel_x)
                            _write_remote(rel_y)

                if mode == "local":
                    _route_local(ev)
                else:
                    _write_remote(ev)
                    if ev.type == ecodes.EV_SYN:
                        await _flush_remote()

        except asyncio.CancelledError:
            pass

    # ── TCP server ───────────────────────────────────────────────────────────
    async def _handle_client(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        nonlocal writer, mode
        addr = w.get_extra_info("peername")
        log.info("Client connected from %s", addr)
        writer = w
        try:
            # Block until the client closes the connection (we never expect
            # data from the client; EOF signals disconnect).
            await r.read()
        except OSError:
            pass
        finally:
            log.info("Client disconnected (%s)", addr)
            writer = None
            if mode == "remote":
                _release_held_local()
                held_keys.clear()
                mode = "local"
                log.info("→ local (client gone)")
            w.close()
            await w.wait_closed()

    # ── main loop ────────────────────────────────────────────────────────────
    server = await asyncio.start_server(_handle_client, cfg.host, cfg.port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    hotkey_names = "+".join(_key_name(c) for c in sorted(cfg.hotkey))
    log.info("Listening on %s  —  mode: local  —  hotkey: %s", addrs, hotkey_names)
    log.info("Press %s to toggle local ↔ remote", hotkey_names)

    tasks = [asyncio.create_task(_read_device(d)) for d in all_devs]
    try:
        async with server:
            await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for dev in all_devs:
            try:
                dev.ungrab()
            except OSError:
                pass
        vkbd.close()
        vmouse.close()
        log.info("Ungrabbed all devices")


def main() -> None:
    parser = ArgumentParser(description="pykvm server — capture input devices and forward events over TCP")
    parser.add_argument("--host", default="0.0.0.0", metavar="HOST")
    parser.add_argument("--port", type=int, default=5900)
    parser.add_argument(
        "--debug",
        "-v",
        action="store_true",
        help="Log key events to /tmp/pykvm.debug.log",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.debug:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        # Keep the existing console handler at INFO so stdout stays quiet.
        for h in root.handlers:
            h.setLevel(logging.INFO)
        fh = logging.FileHandler("/tmp/pykvm.debug.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(fh)
        log.info("Debug key log → /tmp/pykvm.debug.log")

    conf = ServerConfig(host=args.host, port=args.port)
    try:
        asyncio.run(run(conf))
    except KeyboardInterrupt:
        pass
