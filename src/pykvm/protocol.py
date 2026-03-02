"""
Wire protocol.

Authentication (client → server, then server → client)
-------------------------------------------------------
  auth_token : 32 bytes — SHA-256(PSK).  All-zero bytes when no PSK is configured.
  auth_result:  1 byte  — 0x01 = accepted, 0x00 = rejected (server closes connection).

Capability handshake (sent by server after successful auth)
------------------------------------------------------------
  caps_len : u32 big-endian — byte length of the JSON that follows
  caps_body: UTF-8 JSON     — serialised touchpad capabilities, or absent if caps_len == 0

The JSON object maps ev_type (as a decimal string) to:
  - EV_ABS (3): list of [code, [value, min, max, fuzz, flat, resolution]]
  - other types: list of integer codes

Event stream (follows the handshake)
-------------------------------------
Each event is 8 bytes:
  type  : u16 (big-endian)
  code  : u16 (big-endian)
  value : i32 (big-endian)

EV_SYN events are included so the client can flush properly.
Timestamps are dropped; the client synthesises them on injection.
"""

import hashlib
import json
import struct
from typing import NamedTuple

# ── authentication ───────────────────────────────────────────────────────────
AUTH_TOKEN_SIZE = 32  # SHA-256 digest length in bytes


def make_auth_token(psk: str | None) -> bytes:
    """Return the 32-byte auth token: SHA-256(PSK), or all-zeros if *psk* is None."""
    if psk is None:
        return b"\x00" * AUTH_TOKEN_SIZE
    return hashlib.sha256(psk.encode()).digest()


def pack_auth_response(accepted: bool) -> bytes:
    return b"\x01" if accepted else b"\x00"


def unpack_auth_response(data: bytes) -> bool:
    return data == b"\x01"


# ── capability handshake ─────────────────────────────────────────────────────
_CAPS_HDR_FMT = "!I"  # u32 big-endian
CAPS_HDR_SIZE = struct.calcsize(_CAPS_HDR_FMT)  # 4 bytes


def pack_caps(caps: dict | None) -> bytes:
    """Serialise touchpad capabilities to the wire handshake format."""
    if caps is None:
        return struct.pack(_CAPS_HDR_FMT, 0)
    body = json.dumps(caps, separators=(",", ":")).encode()
    return struct.pack(_CAPS_HDR_FMT, len(body)) + body


def unpack_caps_header(data: bytes) -> int:
    """Return the JSON body length from the 4-byte caps header."""
    (length,) = struct.unpack(_CAPS_HDR_FMT, data)
    return length


def unpack_caps_body(data: bytes) -> dict:
    """Deserialise and minimally validate the JSON caps body.

    Raises ValueError if the bytes are not valid UTF-8, not valid JSON,
    or the top-level value is not a JSON object (dict).
    """
    try:
        obj = json.loads(data.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"caps: malformed payload: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"caps: expected JSON object, got {type(obj).__name__}")
    return obj


# ── event stream ─────────────────────────────────────────────────────────────
_FMT = "!HHi"  # network byte order: u16 type, u16 code, i32 value
EVENT_SIZE = struct.calcsize(_FMT)  # 8 bytes


class RawEvent(NamedTuple):
    type: int
    code: int
    value: int


def pack(event: RawEvent) -> bytes:
    return struct.pack(_FMT, event.type, event.code, event.value)


def unpack(data: bytes) -> RawEvent:
    t, c, v = struct.unpack(_FMT, data)
    return RawEvent(t, c, v)
