const std = @import("std");
const evdev = @import("evdev");
const device = @import("device.zig");

pub const VirtualDevice = evdev.VirtualDevice;

pub fn createVirtualKeyboard() !VirtualDevice {
    var builder = VirtualDevice.Builder.new();
    builder.setName("pykvm-keyboard");

    try builder.enableEventType(.key);
    inline for (@typeInfo(evdev.Event.Code.KEY).@"enum".fields) |field| {
        if (std.mem.startsWith(u8, field.name, "KEY_")) {
            const codeField = @field(evdev.Event.Code.KEY, field.name);
            try builder.enableEventCode(.{ .key = codeField }, null);
        }
    }

    return builder.build();
}

pub fn createVirtualMouse() !VirtualDevice {
    var builder = VirtualDevice.Builder.new();
    builder.setName("pykvm-mouse");

    try builder.enableProperty(.pointer);
    try builder.enableEventType(.key);
    try builder.enableEventType(.rel);

    try builder.enableEventCode(.{ .rel = .REL_X }, null);
    try builder.enableEventCode(.{ .rel = .REL_Y }, null);
    try builder.enableEventCode(.{ .rel = .REL_WHEEL }, null);
    try builder.enableEventCode(.{ .rel = .REL_HWHEEL }, null);
    if (@hasField(evdev.Event.Code.REL, "REL_WHEEL_HI_RES")) {
        try builder.enableEventCode(.{ .rel = @field(evdev.Event.Code.REL, "REL_WHEEL_HI_RES") }, null);
    }
    if (@hasField(evdev.Event.Code.REL, "REL_HWHEEL_HI_RES")) {
        try builder.enableEventCode(.{ .rel = @field(evdev.Event.Code.REL, "REL_HWHEEL_HI_RES") }, null);
    }

    try builder.enableEventCode(.{ .key = .BTN_LEFT }, null);
    try builder.enableEventCode(.{ .key = .BTN_RIGHT }, null);
    try builder.enableEventCode(.{ .key = .BTN_MIDDLE }, null);
    try builder.enableEventCode(.{ .key = .BTN_SIDE }, null);
    try builder.enableEventCode(.{ .key = .BTN_EXTRA }, null);

    return builder.build();
}

pub fn createVirtualTouchpad(src: evdev.Device) !VirtualDevice {
    var builder = VirtualDevice.Builder.new();
    builder.setName("pykvm-touchpad");
    try builder.copyCapabilities(src);
    return builder.build();
}

pub fn createVirtualTouchpadFromJson(allocator: std.mem.Allocator, json_data: []const u8) !VirtualDevice {
    const parsed = try std.json.parseFromSlice(device.CapsJson, allocator, json_data, .{});
    defer parsed.deinit();
    const caps = parsed.value;

    var builder = VirtualDevice.Builder.new();
    builder.setName(caps.name);

    if (caps.is_touchpad) {
        try builder.enableProperty(.pointer);
        try builder.enableEventType(.key);
        try builder.enableEventCode(.{ .key = .BTN_TOUCH }, null);
        try builder.enableEventCode(.{ .key = .BTN_TOOL_FINGER }, null);
        try builder.enableEventCode(.{ .key = .BTN_TOOL_DOUBLETAP }, null);
        try builder.enableEventCode(.{ .key = .BTN_TOOL_TRIPLETAP }, null);
        try builder.enableEventCode(.{ .key = .BTN_TOOL_QUADTAP }, null);
        try builder.enableEventCode(.{ .key = .BTN_TOOL_QUINTTAP }, null);
        try builder.enableEventCode(.{ .key = .BTN_LEFT }, null);
    }

    if (caps.abs_axes.len > 0) {
        try builder.enableEventType(.abs);
        for (caps.abs_axes) |axis| {
            const abs_info = evdev.AbsInfo{
                .value = 0,
                .minimum = axis.min,
                .maximum = axis.max,
                .fuzz = axis.fuzz,
                .flat = axis.flat,
                .resolution = axis.res,
            };
            try builder.enableEventCode(.{ .abs = axis.code }, .{ .abs_info = &abs_info });
        }
    }

    return builder.build();
}

pub fn writeEvent(dev: VirtualDevice, code: evdev.Event.Code, value: c_int) !void {
    try dev.writeEvent(code, value);
}

pub fn writeEventSync(dev: VirtualDevice, code: evdev.Event.Code, value: c_int) !void {
    try dev.writeEvent(code, value);
    try dev.writeEvent(.{ .syn = .SYN_REPORT }, 0);
}

test "create virtual devices" {
    const allocator = std.testing.allocator;

    if (createVirtualKeyboard()) |kbd| {
        kbd.destroy();
    } else |_| {
        // ignore error.AccessDenied, error.FileNotFound
    }

    if (createVirtualMouse()) |mouse| {
        mouse.destroy();
    } else |_| {
        // ignore
    }

    const dummy_json =
        \\{"name":"dummy","is_keyboard":false,"is_mouse":false,"is_touchpad":true,"abs_axes":[]}
    ;
    if (createVirtualTouchpadFromJson(allocator, dummy_json)) |tp| {
        tp.destroy();
    } else |_| {
        // ignore
    }
}
