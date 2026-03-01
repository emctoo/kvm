"""
Wire protocol.

Capability handshake (sent by server immediately on connect)
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

import json
import struct
from typing import NamedTuple

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
    """Deserialise the JSON caps body."""
    return json.loads(data.decode())


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
