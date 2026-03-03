const std = @import("std");
const evdev = @import("evdev");
const Event = evdev.Event;

pub const AUTH_TOKEN_SIZE = 32;

/// Creates an auth token using SHA-256(psk) if psk is provided, else all zeros.
pub fn makeAuthToken(psk: ?[]const u8) [AUTH_TOKEN_SIZE]u8 {
    var token: [AUTH_TOKEN_SIZE]u8 = .{0} ** AUTH_TOKEN_SIZE;
    if (psk) |key| {
        if (key.len > 0) {
            std.crypto.hash.sha2.Sha256.hash(key, &token, .{});
        }
    }
    return token;
}

/// Pack an event into an 8-byte buffer:
/// type: u16 BE
/// code: u16 BE
/// value: i32 BE
pub fn packEvent(ev: Event) [8]u8 {
    var buf: [8]u8 = undefined;
    std.mem.writeInt(u16, buf[0..2], ev.code.getType().intoInt(), .big);
    std.mem.writeInt(u16, buf[2..4], ev.code.intoInt(), .big);
    std.mem.writeInt(i32, buf[4..8], ev.value, .big);
    return buf;
}

pub fn unpackEvent(buf: *const [8]u8) Event {
    const ev_type = Event.Type.new(std.mem.readInt(u16, buf[0..2], .big));
    const ev_code = std.mem.readInt(u16, buf[2..4], .big);
    const ev_value = std.mem.readInt(i32, buf[4..8], .big);

    return Event{
        // Event time typically isn't sent over network to maintain accuracy we use current time,
        // but since we only need to write it to uinput, the virtual device handles timing.
        .time = std.mem.zeroes(std.posix.timeval),
        .code = Event.Code.new(ev_type, ev_code),
        .value = ev_value,
    };
}

/// Packs capabilities JSON with a u32 BE length prefix.
pub fn packCaps(allocator: std.mem.Allocator, json_data: []const u8) ![]const u8 {
    var buf = try allocator.alloc(u8, 4 + json_data.len);
    std.mem.writeInt(u32, buf[0..4][0..4], @intCast(json_data.len), .big);
    @memcpy(buf[4..], json_data);
    return buf;
}

/// Unpacks capabilities JSON from a buffer that contains exactly one caps payload
/// Assuming the buffer already stripped the 4-byte length prefix to read exactly `len` bytes.
/// Or just a helper to read the length.
pub fn readCapsLength(header: *const [4]u8) u32 {
    return std.mem.readInt(u32, header[0..4], .big);
}

test "auth token" {
    const empty = makeAuthToken(null);
    try std.testing.expectEqual(@as(u8, 0), empty[0]);
    try std.testing.expectEqual(@as(u8, 0), empty[31]);

    const str_token = makeAuthToken("my_secret_key");
    const str_token2 = makeAuthToken("my_secret_key");
    try std.testing.expectEqualSlices(u8, &str_token, &str_token2);
}

test "pack/unpack event" {
    const orig = Event{
        .time = std.mem.zeroInit(std.posix.timeval, .{}),
        .code = .{ .key = .KEY_A },
        .value = 1,
    };
    const packed_ext = packEvent(orig);
    const unpacked = unpackEvent(&packed_ext);
    try std.testing.expectEqual(orig.code.getType().intoInt(), unpacked.code.getType().intoInt());
    try std.testing.expectEqual(orig.code.intoInt(), unpacked.code.intoInt());
    try std.testing.expectEqual(orig.value, unpacked.value);
}

test "pack caps" {
    const allocator = std.testing.allocator;
    const json = "{\"test\":true}";
    const packed_buf = try packCaps(allocator, json);
    defer allocator.free(packed_buf);

    try std.testing.expectEqual(@as(usize, 4 + 13), packed_buf.len);
    const len = readCapsLength(packed_buf[0..4]);
    try std.testing.expectEqual(@as(u32, 13), len);
    try std.testing.expectEqualStrings(json, packed_buf[4..]);
}
