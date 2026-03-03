const std = @import("std");
const xev = @import("xev");
const evdev = @import("evdev");
const root = @import("root.zig");

const device = root.device;
const uinput = root.uinput;
const protocol = root.protocol;
const net = root.net;
const multiplexer = root.multiplexer;
const hotplug = root.hotplug;
const loop = root.loop;

var global_vkbd: ?uinput.VirtualDevice = null;
var global_vmouse: ?uinput.VirtualDevice = null;
var global_held_keys: multiplexer.HeldKeys = undefined;
var global_active_slot: u8 = 1;

var global_tcp_session: ?net.ServerClientSession = null;
var global_psk: [protocol.AUTH_TOKEN_SIZE]u8 = undefined;

fn tcpAcceptThread(listener: std.posix.socket_t) void {
    while (true) {
        const cli_fd = std.posix.accept(listener, null, null, 0) catch |err| {
            std.log.err("TCP Accept failed: {}", .{err});
            std.Thread.sleep(1 * std.time.ns_per_s);
            continue;
        };

        const stream = std.net.Stream{ .handle = cli_fd };
        var session = net.ServerClientSession.init(stream);

        const accepted = session.authenticate(global_psk) catch false;
        if (!accepted) {
            std.log.warn("Client authentication failed.", .{});
            stream.close();
            continue;
        }

        std.log.info("Client connected and authenticated!", .{});

        // Send capabilities (dummy for now)
        const dummy_caps = "{\"name\":\"pykvm-touchpad\",\"is_keyboard\":false,\"is_mouse\":false,\"is_touchpad\":true,\"abs_axes\":[]}";
        const allocator = std.heap.page_allocator;
        const caps_pack = protocol.packCaps(allocator, dummy_caps) catch {
            stream.close();
            continue;
        };
        defer allocator.free(caps_pack);

        session.sendCaps(caps_pack) catch {
            stream.close();
            continue;
        };

        // Disconnect old, connect new
        if (global_tcp_session) |*old_s| {
            old_s.stream.close();
        }
        global_tcp_session = session;
    }
}

fn releaseHeldKeys(slot: u8) void {
    var iter = global_held_keys.keys.keyIterator();
    while (iter.next()) |key_ptr| {
        const code = key_ptr.*;
        const ev = evdev.Event{
            .code = code,
            .value = 0,
            .time = std.mem.zeroes(std.posix.timeval),
        };

        if (slot == 1) {
            const is_mouse_btn = code.getType() == .key and
                code.intoInt() >= @intFromEnum(evdev.Event.Code.KEY.BTN_MOUSE) and
                code.intoInt() < @intFromEnum(evdev.Event.Code.KEY.BTN_JOYSTICK);

            if (is_mouse_btn) {
                if (global_vmouse) |m| uinput.writeEventSync(m, code, 0) catch {};
            } else {
                if (global_vkbd) |k| uinput.writeEventSync(k, code, 0) catch {};
            }
        } else if (slot == 2) {
            if (global_tcp_session) |*sess| {
                sess.sendEvent(ev) catch {};
            }
        }
    }
    // Note: We don't automatically clear the map here so that when physical keys
    // are released, they are correctly removed or ignored, unless we want to reset
    // all states. The keys physically held will remain held in the map but won't be sent down.
    global_held_keys.keys.clearRetainingCapacity();
}

fn onDeviceEvent(dev: *evdev.Device, ev: evdev.Event) void {
    _ = dev;
    if (multiplexer.HotkeyTracker.checkSwitch(&global_held_keys, ev)) |new_slot| {
        if (new_slot != global_active_slot) {
            std.log.info("Switched from slot {} to slot {}", .{ global_active_slot, new_slot });
            releaseHeldKeys(global_active_slot);
            global_active_slot = new_slot;
        }
        return;
    }

    global_held_keys.update(ev) catch {};

    if (global_active_slot == 1) {
        // Route to local virtual devices
        const is_mouse_btn = ev.code.getType() == .key and
            ev.code.intoInt() >= @intFromEnum(evdev.Event.Code.KEY.BTN_MOUSE) and
            ev.code.intoInt() < @intFromEnum(evdev.Event.Code.KEY.BTN_JOYSTICK);

        if (ev.code.getType() == .rel or is_mouse_btn) {
            if (global_vmouse) |m| {
                uinput.writeEventSync(m, ev.code, ev.value) catch {};
            }
        } else if (ev.code.getType() == .key) {
            if (global_vkbd) |k| {
                uinput.writeEventSync(k, ev.code, ev.value) catch {};
            }
        }
    } else if (global_active_slot == 2) {
        if (global_tcp_session) |*sess| {
            sess.sendEvent(ev) catch |err| {
                std.log.warn("Failed to send event to client: {}", .{err});
                sess.stream.close();
                global_tcp_session = null;
            };
        }
    }
}

pub fn main() !void {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var args = try std.process.argsWithAllocator(allocator);
    defer args.deinit();

    _ = args.skip(); // program name

    // Config values
    var host: []const u8 = "0.0.0.0";
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

    global_psk = protocol.makeAuthToken(psk);

    std.log.info("pykvm-server starting on {s}:{d}", .{ host, port });

    var event_loop = try loop.EventLoop.init(allocator);
    defer event_loop.deinit();

    global_held_keys = multiplexer.HeldKeys.init(allocator);
    defer global_held_keys.deinit();

    // 2. Setup Hotplug Monitor
    var monitor = hotplug.HotplugMonitor.init(allocator);
    defer monitor.deinit();

    const vkbd_opt: ?uinput.VirtualDevice = uinput.createVirtualKeyboard() catch |err| blk: {
        std.log.warn("Failed to create virtual keyboard: {}", .{err});
        break :blk null;
    };
    defer if (vkbd_opt) |k| k.destroy();
    global_vkbd = vkbd_opt;

    const vmouse_opt: ?uinput.VirtualDevice = uinput.createVirtualMouse() catch |err| blk: {
        std.log.warn("Failed to create virtual mouse: {}", .{err});
        break :blk null;
    };
    defer if (vmouse_opt) |m| m.destroy();
    global_vmouse = vmouse_opt;

    // Here we'd hook up async readers for event devices, a TCP Server socket using xev,
    // and hotplug timer interval scanning.
    // Given the complexity of full libxev asynchronous mapping in a single file,
    // this acts as the entrypoint where handlers will register their completion callbacks.

    // For demonstration, scan once:
    var initial_delta = monitor.scan() catch |err| {
        std.log.warn("Failed to scan devices: {}", .{err});
        return;
    };
    defer initial_delta.deinit(allocator);

    var readers: std.ArrayList(*loop.DeviceReader) = .empty;
    defer {
        for (readers.items) |r| {
            r.dev.closeAndFree();
            allocator.destroy(r);
        }
        readers.deinit(allocator);
    }

    for (initial_delta.added.items) |dev_path| {
        std.log.info("Found input device: {s}", .{dev_path});
        // We open it and attach to event loop
        const dev = device.Device.open(dev_path, std.posix.O{ .ACCMODE = .RDWR }) catch |err| {
            std.log.warn("Failed to open device {s}: {}", .{ dev_path, err });
            continue;
        };
        const reader = allocator.create(loop.DeviceReader) catch continue;
        reader.* = loop.DeviceReader.init(dev, allocator, onDeviceEvent) catch {
            dev.closeAndFree();
            allocator.destroy(reader);
            continue;
        };

        try readers.append(allocator, reader);
        reader.start(event_loop.getLoop());
        std.log.info("Tracking events from {s}", .{dev.getName()});
    }

    // Try starting the TCP socket natively to bind to the port (sync for boot check)
    var tcp_server = try std.net.Address.parseIp4(host, port);
    const listener = std.posix.socket(std.posix.AF.INET, std.posix.SOCK.STREAM, 0) catch |err| {
        std.log.warn("Could not create tcp server socket: {}", .{err});
        return;
    };
    defer std.posix.close(listener);

    std.posix.setsockopt(listener, std.posix.SOL.SOCKET, std.posix.SO.REUSEADDR, &std.mem.toBytes(@as(c_int, 1))) catch {};

    std.posix.bind(listener, &tcp_server.any, tcp_server.getOsSockLen()) catch |err| {
        std.log.err("Failed to bind TCP port {d}: {}", .{ port, err });
        return;
    };
    try std.posix.listen(listener, 128);

    std.log.info("Listening on {s}:{d} (Device dispatcher active)", .{ host, port });

    // Spawn accept thread
    const accept_th = try std.Thread.spawn(.{}, tcpAcceptThread, .{listener});
    _ = accept_th;

    try event_loop.run();
}
