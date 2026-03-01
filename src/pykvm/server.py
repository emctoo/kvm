"""
Server: captures physical keyboard/mouse, forwards events over TCP.

Usage:
    pykvm-server [--host HOST] [--port PORT]

Slot switching
--------------
Press Left-Ctrl + Left-Win + digit to switch the active target:

  +1  →  local       (events go to the server's own uinput devices)
  +2  →  client 1    (first connected client)
  +3  →  client 2    (second connected client)
  …

Multiple clients may be connected simultaneously; only the active slot
receives events.  When the active client disconnects, the server falls
back to local mode automatically.

On every slot switch, held keys are synthetically released on the
outgoing target to prevent stuck keys.
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

# Digit key → digit value (1-9).  Used to detect slot-switch combos.
_DIGIT_TO_NUM: dict[int, int] = {getattr(ecodes, f"KEY_{i}"): i for i in range(1, 10) if hasattr(ecodes, f"KEY_{i}")}

_VAL = {0: "up", 1: "dn", 2: "rp"}


def _key_name(code: int) -> str:
    name = ecodes.keys.get(code, str(code))
    return name[0] if isinstance(name, list) else name


async def run(cfg: ServerConfig) -> None:
    # ── discover and grab physical devices ──────────────────────────────────
    keyboards = devices.find_keyboards()
    mice = devices.find_mice()

    # Deduplicate by path (a device with both EV_KEY and EV_REL appears twice).
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
    # Virtual touchpad for local-mode passthrough.  Created from the first
    # grabbed touchpad device so its ABS ranges and button layout match the
    # physical hardware; libinput then processes gestures normally.
    _tp_sources = [d for d in all_devs if ecodes.EV_ABS in d.capabilities() and ecodes.KEY_A not in d.capabilities().get(ecodes.EV_KEY, [])]
    vtouchpad = devices.create_virtual_touchpad(_tp_sources[0]) if _tp_sources else None
    # Serialise touchpad capabilities (ABS ranges + key codes) to JSON for
    # the client so it can create a matching virtual touchpad.
    touchpad_caps: dict | None = None
    if _tp_sources:
        _SKIP_EV = {ecodes.EV_SYN, ecodes.EV_MSC}
        raw_caps = _tp_sources[0].capabilities(absinfo=True)
        _caps_dict: dict = {}
        for _ev_type, _codes in raw_caps.items():
            if _ev_type in _SKIP_EV:
                continue
            if _ev_type == ecodes.EV_ABS:
                _caps_dict[str(_ev_type)] = [[c, list(ai)] for c, ai in _codes]
            else:
                _caps_dict[str(_ev_type)] = list(_codes)
        touchpad_caps = _caps_dict

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
    # current: active slot — 0 = local, N = connected client N.
    # clients: slot → StreamWriter for each connected client.
    current: int = 0
    clients: dict[int, asyncio.StreamWriter] = {}

    held_keys: set[int] = set()
    last_local_target = vkbd

    # Running pointer position for debug logging; reset on each slot switch.
    mouse_x: int = 0
    mouse_y: int = 0

    # ── routing helpers ──────────────────────────────────────────────────────
    def _is_mouse(ev) -> bool:
        return ev.type == ecodes.EV_REL or (ev.type == ecodes.EV_KEY and ev.code in _MOUSE_BTNS)

    def _route_local(ev) -> None:
        nonlocal last_local_target
        if ev.type == ecodes.EV_SYN:
            last_local_target.write(ev.type, ev.code, ev.value)
        else:
            vdev = vmouse if _is_mouse(ev) else vkbd
            last_local_target = vdev
            vdev.write(ev.type, ev.code, ev.value)

    def _write_remote(ev) -> None:
        w = clients.get(current)
        if w is not None:
            w.write(protocol.pack(protocol.RawEvent(ev.type, ev.code, ev.value)))

    async def _flush_remote() -> None:
        w = clients.get(current)
        if w is not None:
            try:
                await w.drain()
            except OSError:
                pass  # disconnect is handled in _handle_client

    # ── stuck-key release ────────────────────────────────────────────────────
    def _release_held_local() -> None:
        for code in list(held_keys):
            vkbd.write(ecodes.EV_KEY, code, 0)
        if held_keys:
            vkbd.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)

    async def _release_held_on(w: asyncio.StreamWriter) -> None:
        """Release all held keys on a specific remote writer."""
        for code in list(held_keys):
            w.write(protocol.pack(protocol.RawEvent(ecodes.EV_KEY, code, 0)))
        if held_keys:
            w.write(protocol.pack(protocol.RawEvent(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)))
            try:
                await w.drain()
            except OSError:
                pass

    # ── slot switch ──────────────────────────────────────────────────────────
    async def _switch(new_slot: int) -> None:
        nonlocal current, mouse_x, mouse_y
        if new_slot == current:
            return
        # Release held keys on the outgoing target.
        if current == 0:
            _release_held_local()
        else:
            w = clients.get(current)
            if w is not None:
                await _release_held_on(w)
        held_keys.clear()
        # Reset pointer tracking for the new target.
        mouse_x = 0
        mouse_y = 0
        current = new_slot
        if new_slot == 0:
            log.info("→ local  (clients: %s)", list(clients) or "none")
        else:
            log.info("→ client %d  (clients: %s)", new_slot, list(clients))

    # ── per-device read loop ─────────────────────────────────────────────────
    async def _read_device(dev: InputDevice) -> None:
        nonlocal mouse_x, mouse_y
        # A touchpad has EV_ABS but no alphanumeric keys (KEY_A distinguishes
        # keyboards that also happen to have ABS axes, e.g. some all-in-ones).
        dev_caps = dev.capabilities()
        is_touchpad = ecodes.EV_ABS in dev_caps and ecodes.KEY_A not in dev_caps.get(ecodes.EV_KEY, [])
        try:
            async for ev in dev.async_read_loop():
                # ── local touchpad passthrough ────────────────────────────
                # In local mode, forward every raw event to the virtual
                # touchpad so libinput processes gestures (tap-to-click,
                # two-finger scroll, etc.) exactly as on the physical device.
                if is_touchpad and current == 0:
                    if vtouchpad is not None:
                        vtouchpad.write(ev.type, ev.code, ev.value)
                    continue

                # ── remote touchpad: forward all raw events ───────────────
                # The client creates a matching virtual touchpad from the
                # capabilities sent at connect time; libinput there handles
                # all gesture processing.
                if is_touchpad:
                    _write_remote(ev)
                    if ev.type == ecodes.EV_SYN:
                        await _flush_remote()
                    continue

                # ── keyboard / mouse events ───────────────────────────────
                if ev.type == ecodes.EV_KEY:
                    if ev.value == 1:  # key down
                        held_keys.add(ev.code)
                    elif ev.value == 0:  # key up
                        held_keys.discard(ev.code)

                    # Slot-switch hotkey: switch_mods + digit 1-9.
                    # Digit 1 → local (slot 0), digit N → client slot N-1.
                    if ev.value == 1 and ev.code in _DIGIT_TO_NUM and cfg.switch_mods.issubset(held_keys):
                        new_slot = _DIGIT_TO_NUM[ev.code] - 1  # 1→0, 2→1, 3→2…
                        if new_slot == 0 or new_slot in clients:
                            await _switch(new_slot)
                        else:
                            log.warning("Slot %d: no client connected", new_slot)
                        continue  # swallow the triggering digit press

                    log.debug("[%d] kbd %s %s", current, _key_name(ev.code), _VAL.get(ev.value, ev.value))

                elif ev.type == ecodes.EV_REL:
                    if ev.code == ecodes.REL_X:
                        mouse_x += ev.value
                    elif ev.code == ecodes.REL_Y:
                        mouse_y += ev.value
                    log.debug("[%d] mouse %s %+d  pos(%d,%d)", current, ecodes.REL.get(ev.code, ev.code), ev.value, mouse_x, mouse_y)

                if current == 0:
                    _route_local(ev)
                else:
                    _write_remote(ev)
                    if ev.type == ecodes.EV_SYN:
                        await _flush_remote()

        except asyncio.CancelledError:
            pass

    # ── TCP server ───────────────────────────────────────────────────────────
    async def _handle_client(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        nonlocal current
        # Assign the smallest available slot ≥ 1 so that keys stay stable
        # across reconnects (slot 1 is always reused once it's free).
        slot = 1
        while slot in clients:
            slot += 1
        clients[slot] = w
        addr = w.get_extra_info("peername")
        mods_names = "+".join(_key_name(c) for c in sorted(cfg.switch_mods))
        log.info("Client %d connected from %s  →  press %s+%d to switch", slot, addr, mods_names, slot + 1)
        try:
            # Send touchpad capabilities so the client can create a matching
            # virtual touchpad.  Must arrive before the event stream.
            w.write(protocol.pack_caps(touchpad_caps))
            await w.drain()
            # Block until EOF (client never sends data; disconnect = EOF).
            await r.read()
        except OSError:
            pass
        finally:
            log.info("Client %d disconnected (%s)", slot, addr)
            del clients[slot]
            if current == slot:
                _release_held_local()
                held_keys.clear()
                current = 0
                log.info("→ local (client %d gone)", slot)
            w.close()
            await w.wait_closed()

    # ── main loop ────────────────────────────────────────────────────────────
    server = await asyncio.start_server(_handle_client, cfg.host, cfg.port)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    mods_names = "+".join(_key_name(c) for c in sorted(cfg.switch_mods))
    log.info("Listening on %s  —  slot: local  —  hotkey: %s+[1-9]", addrs, mods_names)
    log.info("  %s+1 = local  |  +2 = client 1  |  +3 = client 2  |  …", mods_names)

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
        if vtouchpad is not None:
            vtouchpad.close()
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
