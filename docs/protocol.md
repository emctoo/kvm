# Wire Protocol

## Overview

pykvm uses a minimal binary protocol over a plain TCP stream.  Each evdev input
event is encoded as a fixed-size 8-byte frame.  There is no handshake, no
framing header, and no length-prefix — the fixed frame size is sufficient for
reliable parsing.

---

## Frame format

```
 0       1       2       3       4       5       6       7
 ┌───────────────┬───────────────┬───────────────────────┐
 │  type  (u16)  │  code  (u16)  │      value  (i32)     │
 └───────────────┴───────────────┴───────────────────────┘
   big-endian      big-endian         big-endian (signed)
```

| Field | Size | Type | Description |
|---|---|---|---|
| `type` | 2 bytes | `uint16`, big-endian | evdev event type (`EV_KEY`, `EV_REL`, `EV_SYN`, …) |
| `code` | 2 bytes | `uint16`, big-endian | event code (`KEY_A`, `REL_X`, `SYN_REPORT`, …) |
| `value` | 4 bytes | `int32`, big-endian | event value (key state, relative delta, …) |

Total: **8 bytes per event**.

---

## Rationale

### Why drop timestamps?

Linux evdev timestamps (`timeval`) are 16 bytes of host-specific data.  They are
meaningless across machines because clock sources differ.  The client
re-timestamps events using its own `CLOCK_REALTIME` at injection time, which is
the same behaviour `uinput` applies when no explicit timestamp is provided.

### Why big-endian?

Network byte order (big-endian) is the standard for binary TCP protocols.
Python's `struct` format `"!HHi"` handles the conversion transparently.

### Why fixed-size frames?

A fixed 8-byte frame allows the receiver to call `recv(8)` in a tight loop
without needing a parser or state machine.  The lack of a framing envelope
removes 4–8 bytes of overhead per event.

---

## Implementation

```python
# protocol.py
_FMT = "!HHi"       # network byte order: u16 type, u16 code, i32 value
EVENT_SIZE = 8      # struct.calcsize(_FMT)

class RawEvent(NamedTuple):
    type: int
    code: int
    value: int

def pack(event: RawEvent) -> bytes:
    return struct.pack(_FMT, event.type, event.code, event.value)

def unpack(data: bytes) -> RawEvent:
    t, c, v = struct.unpack(_FMT, data)
    return RawEvent(t, c, v)
```

---

## Event types carried

| `type` | Decimal | Forwarded | Notes |
|---|---|---|---|
| `EV_SYN` | 0 | Yes | `SYN_REPORT` flushes an event batch to the kernel |
| `EV_KEY` | 1 | Yes | Key press (`value=1`), release (`0`), repeat (`2`) |
| `EV_REL` | 2 | Yes | Relative mouse movement, scroll |
| `EV_ABS` | 3 | No (POC) | Absolute pointer — not forwarded in current scope |
| `EV_MSC` | 4 | No | Misc (scancode) — filtered out |

---

## Example byte sequences

### KEY_A pressed

```
EV_KEY (1) / KEY_A (30) / value=1 (pressed)

00 01  00 1e  00 00 00 01
```

### KEY_A released

```
EV_KEY (1) / KEY_A (30) / value=0 (released)

00 01  00 1e  00 00 00 00
```

### SYN_REPORT (flush)

```
EV_SYN (0) / SYN_REPORT (0) / value=0

00 00  00 00  00 00 00 00
```

### Mouse moved right by 5 pixels

```
EV_REL (2) / REL_X (0) / value=5

00 02  00 00  00 00 00 05
```

### Mouse scroll up by 1 tick

```
EV_REL (2) / REL_WHEEL (8) / value=1

00 02  00 08  00 00 00 01
```

---

## Stream structure

A typical sequence for pressing and releasing a key looks like:

```
[EV_KEY / KEY_A / 1]   ← key down
[EV_SYN / SYN_REPORT / 0]
[EV_KEY / KEY_A / 0]   ← key up
[EV_SYN / SYN_REPORT / 0]
```

A mouse movement with a click:

```
[EV_REL / REL_X / 10]
[EV_REL / REL_Y / -3]
[EV_KEY / BTN_LEFT / 1]
[EV_SYN / SYN_REPORT / 0]
[EV_KEY / BTN_LEFT / 0]
[EV_SYN / SYN_REPORT / 0]
```

---

## Future extensions

If TLS or authentication is added, the protocol will be wrapped in a TLS
session.  The frame format itself is not expected to change — additional metadata
(e.g. absolute coordinates for edge-switching) would be a new event type.
