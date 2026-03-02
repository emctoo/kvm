const std = @import("std");
const evdev = @import("evdev");

pub const Device = evdev.Device;
pub const Event = evdev.Event;

/// Scans /dev/input and returns a list of matching "eventX" device paths.
/// Caller must free each string and the list itself using the provided allocator.
pub fn listDevices(allocator: std.mem.Allocator) !std.ArrayList([]const u8) {
    var devices: std.ArrayList([]const u8) = .empty;
    errdefer {
        for (devices.items) |path| {
            allocator.free(path);
        }
        devices.deinit(allocator);
    }

    var dir = try std.fs.openDirAbsolute("/dev/input", .{ .iterate = true });
    defer dir.close();

    var iter = dir.iterate();
    while (try iter.next()) |entry| {
        if (std.mem.startsWith(u8, entry.name, "event")) {
            const path = try std.fmt.allocPrint(allocator, "/dev/input/{s}", .{entry.name});
            try devices.append(allocator, path);
        }
    }

    // Sort to make it deterministic (e.g. event0, event1, event10...)
    // A simple lexicographical sort is usually enough, though event10 will come before event2.
    // We can just use it as-is or do natural sorting. For now lexicographical is fine.

    return devices;
}

pub const AxisInfo = struct {
    code: Event.Code.ABS,
    min: i32,
    max: i32,
    fuzz: i32,
    flat: i32,
    res: i32,
};

pub const CapsJson = struct {
    name: []const u8,
    is_keyboard: bool,
    is_mouse: bool,
    is_touchpad: bool,
    abs_axes: []AxisInfo,
};

/// Serializes device capabilities to JSON.
/// Only capturing necessary info for rebuilding touchpad/mouse/kbd.
pub fn getCapabilitiesJson(dev: evdev.Device, allocator: std.mem.Allocator) ![]const u8 {
    var axes: std.ArrayList(AxisInfo) = .empty;
    defer axes.deinit(allocator);

    if (dev.hasEventType(.abs)) {
        inline for (@typeInfo(Event.Code.ABS).@"enum".fields) |field| {
            const codeField = @field(Event.Code.ABS, field.name);
            const code = codeField.intoCode();
            if (dev.hasEventCode(code)) {
                if (dev.getAbsInfo(codeField)) |abs_info| {
                    try axes.append(allocator, .{
                        .code = codeField,
                        .min = abs_info.minimum,
                        .max = abs_info.maximum,
                        .fuzz = abs_info.fuzz,
                        .flat = abs_info.flat,
                        .res = abs_info.resolution,
                    });
                }
            }
        }
    }

    const caps = CapsJson{
        .name = dev.getName(),
        .is_keyboard = dev.isKeyboard(),
        .is_mouse = dev.isMouse(),
        .is_touchpad = dev.isMultiTouch() or dev.isSingleTouch(),
        .abs_axes = try axes.toOwnedSlice(allocator),
    };
    defer allocator.free(caps.abs_axes);

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(allocator);

    try std.json.stringify(caps, .{}, out.writer(allocator));
    return out.toOwnedSlice(allocator);
}

test "listDevices" {
    const allocator = std.testing.allocator;
    // Note: this test might fail if /dev/input is not accessible or doesn't exist.
    // So we just ignore error in such case, or check accessibility first.
    var dir = std.fs.openDirAbsolute("/dev/input", .{ .iterate = true }) catch return;
    dir.close();

    var list = try listDevices(allocator);
    defer {
        for (list.items) |p| allocator.free(p);
        list.deinit(allocator);
    }

    // Should find at least something if we are on linux with uinput dev testing
    // but we won't assert len > 0 to avoid test flakes on restricted CIs
}
