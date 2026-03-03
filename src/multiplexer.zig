const std = @import("std");
const xev = @import("xev");
const evdev = @import("evdev");
const Event = evdev.Event;
const device = @import("device.zig");

pub const HeldKeys = struct {
    keys: std.AutoHashMap(Event.Code, void),
    allocator: std.mem.Allocator,

    pub fn init(allocator: std.mem.Allocator) HeldKeys {
        return .{
            .keys = std.AutoHashMap(Event.Code, void).init(allocator),
            .allocator = allocator,
        };
    }

    pub fn deinit(self: *HeldKeys) void {
        self.keys.deinit();
    }

    pub fn update(self: *HeldKeys, ev: Event) !void {
        if (ev.code.getType() == .key) {
            if (ev.value == 1 or ev.value == 2) {
                // down or repeat
                try self.keys.put(ev.code, {});
            } else if (ev.value == 0) {
                // up
                _ = self.keys.remove(ev.code);
            }
        }
    }

    pub fn isHeld(self: *HeldKeys, code: Event.Code) bool {
        return self.keys.contains(code);
    }
};

/// Tracks slot switching hotkeys, e.g. Left_Ctrl + Left_Meta + 1 or 2
pub const HotkeyTracker = struct {
    const MODS = [_]Event.Code{
        .{ .key = .KEY_LEFTCTRL },
        .{ .key = .KEY_LEFTMETA }, // or LEFTALT
    };

    pub fn checkSwitch(held: *HeldKeys, ev: Event) ?u8 {
        if (ev.code.getType() != .key or ev.value != 1) return null; // only on down

        for (MODS) |mod| {
            if (!held.isHeld(mod)) return null;
        }

        switch (ev.code.intoInt()) {
            @intFromEnum(Event.Code.KEY.KEY_1) => return 1,
            @intFromEnum(Event.Code.KEY.KEY_2) => return 2,
            @intFromEnum(Event.Code.KEY.KEY_3) => return 3,
            @intFromEnum(Event.Code.KEY.KEY_4) => return 4,
            else => return null,
        }
    }
};

test "held keys tracking" {
    const allocator = std.testing.allocator;
    var held = HeldKeys.init(allocator);
    defer held.deinit();

    const ev_a_down = Event{ .code = .{ .key = .KEY_A }, .value = 1, .time = std.mem.zeroes(std.posix.timeval) };
    const ev_a_up = Event{ .code = .{ .key = .KEY_A }, .value = 0, .time = std.mem.zeroes(std.posix.timeval) };

    try held.update(ev_a_down);
    try std.testing.expect(held.isHeld(.{ .key = .KEY_A }));

    try held.update(ev_a_up);
    try std.testing.expect(!held.isHeld(.{ .key = .KEY_A }));
}

test "slot switch hotkey" {
    const allocator = std.testing.allocator;
    var held = HeldKeys.init(allocator);
    defer held.deinit();

    _ = try held.update(Event{ .code = .{ .key = .KEY_LEFTCTRL }, .value = 1, .time = std.mem.zeroes(std.posix.timeval) });
    _ = try held.update(Event{ .code = .{ .key = .KEY_LEFTMETA }, .value = 1, .time = std.mem.zeroes(std.posix.timeval) });

    const key_1_down = Event{ .code = .{ .key = .KEY_1 }, .value = 1, .time = std.mem.zeroes(std.posix.timeval) };
    const slot = HotkeyTracker.checkSwitch(&held, key_1_down);
    try std.testing.expectEqual(@as(?u8, 1), slot);
}
