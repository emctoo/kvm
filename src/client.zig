const std = @import("std");
const xev = @import("xev");
const evdev = @import("evdev");
const root = @import("root.zig");

const uinput = root.uinput;
const protocol = root.protocol;
const net = root.net;
const loop = root.loop;

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var args = try std.process.argsWithAllocator(allocator);
    defer args.deinit();

    _ = args.skip(); // program name

    // Config values
    var host: []const u8 = "127.0.0.1";
    var port: u16 = 5900;
    var psk: ?[]const u8 = null;

    while (args.next()) |arg| {
        if (std.mem.eql(u8, arg, "--host")) {
            host = args.next() orelse host;
        } else if (std.mem.eql(u8, arg, "--port")) {
            const port_str = args.next() orelse "5900";
            port = try std.fmt.parseInt(u16, port_str, 10);
        } else if (std.mem.eql(u8, arg, "--psk")) {
            psk = args.next();
        }
    }

    std.log.info("pykvm-client connecting to {s}:{d}", .{ host, port });

    // Connect block (synchronous structure setup)
    const stream = std.net.tcpConnectToHost(allocator, host, port) catch |err| {
        std.log.err("Failed to connect to server: {}", .{err});
        return;
    };
    defer stream.close();

    var client = net.ClientSession.init(stream);
    const token = protocol.makeAuthToken(psk);

    const accepted = try client.authenticate(token);
    if (!accepted) {
        std.log.err("Authentication rejected by server.", .{});
        return;
    }
    std.log.info("Authenticated successfully.", .{});

    // Receive capabilities
    const caps_json = client.readCaps(allocator) catch |err| {
        std.log.err("Failed to read initial capabilities: {}", .{err});
        return;
    };
    defer allocator.free(caps_json);
    std.log.info("Received touchpad capabilities: {s}", .{caps_json});

    // Instantiate output devices using our builder methods
    const vkbd_opt = uinput.createVirtualKeyboard() catch |err| blk: {
        std.log.warn("Failed to create uinput keyboard: {}", .{err});
        break :blk null;
    };
    defer if (vkbd_opt) |k| k.destroy();

    const vmouse_opt = uinput.createVirtualMouse() catch |err| blk: {
        std.log.warn("Failed to create uinput mouse: {}", .{err});
        break :blk null;
    };
    defer if (vmouse_opt) |m| m.destroy();

    const vtp_opt = uinput.createVirtualTouchpadFromJson(allocator, caps_json) catch |err| blk: {
        std.log.warn("Failed to create uinput touchpad fallback: {}", .{err});
        break :blk null;
    };
    defer if (vtp_opt) |t| t.destroy();

    // Infinite receive loop structure
    // Infinite receive loop structure
    std.log.info("Client active and waiting for events.", .{});
    while (true) {
        const ev = client.readEvent() catch |err| {
            std.log.err("Connection lost: {}", .{err});
            break;
        };

        const is_key = ev.code.getType() == .key;
        const is_rel = ev.code.getType() == .rel;
        const is_abs = ev.code.getType() == .abs;

        const is_mouse_btn = is_key and
            ev.code.intoInt() >= @intFromEnum(evdev.Event.Code.KEY.BTN_MOUSE) and
            ev.code.intoInt() < @intFromEnum(evdev.Event.Code.KEY.BTN_JOYSTICK);

        const is_tp_btn = is_key and ev.code.intoInt() == @intFromEnum(evdev.Event.Code.KEY.BTN_TOUCH);

        // Routing logic:
        if (is_abs or is_tp_btn) {
            if (vtp_opt) |tp| uinput.writeEventSync(tp, ev.code, ev.value) catch {};
        } else if (is_rel or is_mouse_btn) {
            if (vmouse_opt) |m| uinput.writeEventSync(m, ev.code, ev.value) catch {};
        } else if (is_key) {
            if (vkbd_opt) |k| uinput.writeEventSync(k, ev.code, ev.value) catch {};
        }
    }
}
