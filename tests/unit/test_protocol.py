"""Unit tests for the wire protocol."""

import hashlib
import struct

import pytest

from pykvm import protocol

# Convenient aliases matching evdev ecodes values (avoid importing evdev here).
_EV_SYN = 0
_EV_KEY = 1
_EV_REL = 2
_SYN_REPORT = 0
_KEY_A = 30
_REL_X = 0


def test_event_size_is_8():
    assert protocol.EVENT_SIZE == 8


@pytest.mark.parametrize(
    "type_, code, value",
    [
        (_EV_KEY, _KEY_A, 1),  # key down
        (_EV_KEY, _KEY_A, 0),  # key up
        (_EV_REL, _REL_X, -5),  # negative relative movement
        (_EV_SYN, _SYN_REPORT, 0),  # sync report
        (_EV_KEY, 0xFFFF, -(2**31)),  # u16 max code, i32 min value
        (0xFFFF, 0xFFFF, 2**31 - 1),  # all maxima
        (_EV_REL, _REL_X, 1000),  # larger positive
    ],
)
def test_roundtrip(type_, code, value):
    event = protocol.RawEvent(type_, code, value)
    data = protocol.pack(event)
    assert len(data) == protocol.EVENT_SIZE
    assert protocol.unpack(data) == event


def test_byte_order_big_endian():
    """Pack uses network (big-endian) byte order."""
    event = protocol.RawEvent(_EV_KEY, _KEY_A, 1)
    data = protocol.pack(event)
    expected = struct.pack("!HHi", _EV_KEY, _KEY_A, 1)
    assert data == expected


def test_negative_value_preserved():
    event = protocol.RawEvent(_EV_REL, _REL_X, -100)
    assert protocol.unpack(protocol.pack(event)).value == -100


def test_i32_min():
    event = protocol.RawEvent(0, 0, -(2**31))
    assert protocol.unpack(protocol.pack(event)).value == -(2**31)


def test_i32_max():
    event = protocol.RawEvent(0, 0, 2**31 - 1)
    assert protocol.unpack(protocol.pack(event)).value == 2**31 - 1


def test_rawevent_is_named_tuple():
    ev = protocol.RawEvent(1, 2, 3)
    assert ev.type == 1
    assert ev.code == 2
    assert ev.value == 3


def test_unpack_wrong_length_raises():
    with pytest.raises(struct.error):
        protocol.unpack(b"\x00" * 7)


# ── authentication ────────────────────────────────────────────────────────────


def test_auth_token_size():
    assert protocol.AUTH_TOKEN_SIZE == 32


def test_make_auth_token_no_psk_is_zeros():
    token = protocol.make_auth_token(None)
    assert token == b"\x00" * 32


def test_make_auth_token_is_sha256():
    token = protocol.make_auth_token("secret")
    assert token == hashlib.sha256(b"secret").digest()


def test_make_auth_token_length():
    assert len(protocol.make_auth_token("hello")) == protocol.AUTH_TOKEN_SIZE


def test_pack_auth_response_accepted():
    assert protocol.pack_auth_response(True) == b"\x01"


def test_pack_auth_response_rejected():
    assert protocol.pack_auth_response(False) == b"\x00"


def test_unpack_auth_response_ok():
    assert protocol.unpack_auth_response(b"\x01") is True


def test_unpack_auth_response_reject():
    assert protocol.unpack_auth_response(b"\x00") is False


def test_different_psks_produce_different_tokens():
    assert protocol.make_auth_token("abc") != protocol.make_auth_token("xyz")
