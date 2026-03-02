const std = @import("std");
const evdev = @import("evdev");
const xev = @import("xev");

pub fn main() !void {
    std.debug.print("Hello, kvm!\n", .{});
}

test "libxev test" {
    var loop = try xev.Loop.init(.{});
    defer loop.deinit();
    try std.testing.expect(true);
}

test "evdev test" {
    _ = evdev.Event;
}
