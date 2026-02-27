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


async def run(cfg: ServerConfig) -> None:
    # ── discover and grab physical devices ──────────────────────────────────
    keyboards = devices.find_keyboards()
    mice = devices.find_mice()
    all_devs: list[InputDevice] = keyboards + mice

    if not all_devs:
        log.error("No input devices found. Is the user in the 'input' group?")
        sys.exit(1)

    for dev in all_devs:
        log.info("Found  %s  (%s)", dev.name, dev.path)

    vkbd = devices.create_virtual_keyboard()
    vmouse = devices.create_virtual_mouse()

    for dev in all_devs:
        dev.grab()
        log.info("Grabbed %s", dev.path)

    # ── shared mutable state ─────────────────────────────────────────────────
    # All device tasks run in the same event loop (single-threaded), so no
    # locking is needed for reads.  Assignments are only made at await points
    # where only one task is active, which is safe in asyncio.
    mode: str = "local"
    held_keys: set[int] = set()
    last_local_target = vkbd
    writer: asyncio.StreamWriter | None = None

    # ── routing helpers ──────────────────────────────────────────────────────
    def _is_mouse(ev) -> bool:
        return ev.type == ecodes.EV_REL or (
            ev.type == ecodes.EV_KEY and ev.code in _MOUSE_BTNS
        )

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
        nonlocal mode
        if mode == "local":
            _release_held_local()
            held_keys.clear()
            mode = "remote"
            log.info("→ remote")
        else:
            await _release_held_remote()
            held_keys.clear()
            mode = "local"
            log.info("→ local")

    # ── per-device read loop ─────────────────────────────────────────────────
    async def _read_device(dev: InputDevice) -> None:
        try:
            async for ev in dev.async_read_loop():
                # Maintain held-key set for hotkey detection and stuck-key release.
                if ev.type == ecodes.EV_KEY:
                    if ev.value == 1:  # key down
                        held_keys.add(ev.code)
                    elif ev.value == 0:  # key up
                        held_keys.discard(ev.code)

                    # Hotkey fires when the last key of the combo is pressed.
                    if (
                        ev.value == 1
                        and ev.code in cfg.hotkey
                        and cfg.hotkey.issubset(held_keys)
                    ):
                        await _toggle()
                        continue  # swallow the triggering key press

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
    log.info("Listening on %s  (hotkey key-codes: %s)", addrs, sorted(cfg.hotkey))

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
    parser = ArgumentParser(
        description="pykvm server — capture input devices and forward events over TCP"
    )
    parser.add_argument("--host", default="0.0.0.0", metavar="HOST")
    parser.add_argument("--port", type=int, default=5900)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = ServerConfig(host=args.host, port=args.port)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass
