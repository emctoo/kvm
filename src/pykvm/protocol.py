"""
Wire protocol: each evdev event is packed as 8 bytes.

  type  : u16 (big-endian)
  code  : u16 (big-endian)
  value : i32 (big-endian)

EV_SYN events are included so the client can flush properly.
Timestamps are dropped; the client synthesises them on injection.
"""

import struct
from typing import NamedTuple

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
