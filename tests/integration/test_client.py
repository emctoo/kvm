"""
Mock-based integration tests for the client event-routing logic.

These tests feed packed event bytes directly into an asyncio.StreamReader
and verify that events are routed to the correct virtual device (vkbd or
vmouse) without requiring physical hardware or uinput access.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from evdev import ecodes

from pykvm import protocol
from pykvm.client import run
from pykvm.config import ClientConfig


# ── helpers ────────────────────────────────────────────────────────────────


def _pack(*args) -> bytes:
    """Pack a single (type, code, value) tuple into wire bytes."""
    return protocol.pack(protocol.RawEvent(*args))


def _make_reader(*events: tuple[int, int, int]) -> asyncio.StreamReader:
    """Return a StreamReader pre-loaded with the given events + EOF."""
    reader = asyncio.StreamReader()
    for ev in events:
        reader.feed_data(_pack(*ev))
    reader.feed_eof()
    return reader


async def _run_client(
    reader: asyncio.StreamReader,
    vkbd: MagicMock,
    vmouse: MagicMock,
) -> None:
    """Run client.run() with mocked devices and a pre-loaded StreamReader."""
    writer = MagicMock()
    cfg = ClientConfig(server_host="127.0.0.1", server_port=5900)
    with (
        patch("pykvm.devices.create_virtual_keyboard", return_value=vkbd),
        patch("pykvm.devices.create_virtual_mouse", return_value=vmouse),
        patch("asyncio.open_connection", new=AsyncMock(return_value=(reader, writer))),
    ):
        await run(cfg)


# ── keyboard routing ───────────────────────────────────────────────────────


async def test_key_event_goes_to_vkbd():
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_KEY, ecodes.KEY_A, 1),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vkbd.write.assert_any_call(ecodes.EV_KEY, ecodes.KEY_A, 1)
    vmouse.write.assert_not_called()


async def test_key_up_goes_to_vkbd():
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_KEY, ecodes.KEY_SPACE, 0),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vkbd.write.assert_any_call(ecodes.EV_KEY, ecodes.KEY_SPACE, 0)
    vmouse.write.assert_not_called()


# ── mouse routing ──────────────────────────────────────────────────────────


async def test_rel_event_goes_to_vmouse():
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_REL, ecodes.REL_X, 10),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vmouse.write.assert_any_call(ecodes.EV_REL, ecodes.REL_X, 10)
    vkbd.write.assert_not_called()


async def test_mouse_button_goes_to_vmouse():
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_KEY, ecodes.BTN_LEFT, 1),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vmouse.write.assert_any_call(ecodes.EV_KEY, ecodes.BTN_LEFT, 1)
    vkbd.write.assert_not_called()


async def test_btn_right_goes_to_vmouse():
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_KEY, ecodes.BTN_RIGHT, 1),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vmouse.write.assert_any_call(ecodes.EV_KEY, ecodes.BTN_RIGHT, 1)
    vkbd.write.assert_not_called()


# ── EV_SYN last-target routing ─────────────────────────────────────────────


async def test_syn_follows_keyboard_event():
    """EV_SYN goes to vkbd when last non-SYN was a keyboard event."""
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_KEY, ecodes.KEY_A, 1),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vkbd.write.assert_any_call(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
    syn_to_mouse = [c for c in vmouse.write.call_args_list if c.args[0] == ecodes.EV_SYN]
    assert not syn_to_mouse


async def test_syn_follows_mouse_event():
    """EV_SYN goes to vmouse when last non-SYN was a mouse event."""
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_REL, ecodes.REL_X, 5),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vmouse.write.assert_any_call(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
    syn_to_kbd = [c for c in vkbd.write.call_args_list if c.args[0] == ecodes.EV_SYN]
    assert not syn_to_kbd


# ── cleanup ────────────────────────────────────────────────────────────────


async def test_devices_closed_on_server_disconnect():
    """Both virtual devices are closed even when the server disconnects."""
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader()  # immediate EOF
    await _run_client(reader, vkbd, vmouse)

    vkbd.close.assert_called_once()
    vmouse.close.assert_called_once()


async def test_devices_closed_after_events():
    vkbd, vmouse = MagicMock(), MagicMock()
    reader = _make_reader(
        (ecodes.EV_KEY, ecodes.KEY_A, 1),
        (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
    )
    await _run_client(reader, vkbd, vmouse)

    vkbd.close.assert_called_once()
    vmouse.close.assert_called_once()
