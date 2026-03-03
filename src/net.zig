const std = @import("std");
const protocol = @import("protocol.zig");
const device = @import("device.zig");
const uinput = @import("uinput.zig");
const evdev = @import("evdev");

pub const AuthError = error{
    InvalidToken,
    Timeout,
    ConnectionClosed,
};

fn readExact(stream: std.net.Stream, buf: []u8) !void {
    var read: usize = 0;
    while (read < buf.len) {
        const n = try stream.read(buf[read..]);
        if (n == 0) return error.ConnectionClosed;
        read += n;
    }
}

pub const ServerClientSession = struct {
    stream: std.net.Stream,

    pub fn init(stream: std.net.Stream) ServerClientSession {
        return .{ .stream = stream };
    }

    /// Read exactly 32 bytes auth token and verify if psk matches
    pub fn authenticate(self: *ServerClientSession, expected_token: [protocol.AUTH_TOKEN_SIZE]u8) !bool {
        var token: [protocol.AUTH_TOKEN_SIZE]u8 = undefined;
        try readExact(self.stream, &token);

        const accepted = std.mem.eql(u8, &token, &expected_token);
        var reply: [1]u8 = .{if (accepted) 1 else 0};
        try self.stream.writeAll(&reply);
        return accepted;
    }

    pub fn sendCaps(self: *ServerClientSession, caps: []const u8) !void {
        try self.stream.writeAll(caps);
    }

    pub fn sendEvent(self: *ServerClientSession, ev: evdev.Event) !void {
        const packed_buf = protocol.packEvent(ev);
        try self.stream.writeAll(&packed_buf);
    }
};

pub const ClientSession = struct {
    stream: std.net.Stream,

    pub fn init(stream: std.net.Stream) ClientSession {
        return .{ .stream = stream };
    }

    pub fn authenticate(self: *ClientSession, token: [protocol.AUTH_TOKEN_SIZE]u8) !bool {
        try self.stream.writeAll(&token);
        var reply: [1]u8 = undefined;
        try readExact(self.stream, &reply);
        return reply[0] == 1;
    }

    pub fn readCaps(self: *ClientSession, allocator: std.mem.Allocator) ![]u8 {
        var len_buf: [4]u8 = undefined;
        try readExact(self.stream, &len_buf);
        const len = protocol.readCapsLength(&len_buf);

        const buf = try allocator.alloc(u8, len);
        errdefer allocator.free(buf);

        try readExact(self.stream, buf);
        return buf;
    }

    pub fn readEvent(self: *ClientSession) !evdev.Event {
        var buf: [8]u8 = undefined;
        try readExact(self.stream, &buf);
        return protocol.unpackEvent(&buf);
    }
};
