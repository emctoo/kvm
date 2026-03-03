const std = @import("std");
const xev = @import("xev");
const evdev = @import("evdev");

// Phase 3 mentions using libxev multi-device event loop instead of async_read_loop.
pub const EventCallback = *const fn (dev: *evdev.Device, ev: evdev.Event) void;

pub const DeviceReader = struct {
    file: xev.File,
    dev: evdev.Device,
    completion: xev.Completion,
    callback: EventCallback,
    allocator: std.mem.Allocator,

    pub fn init(dev: evdev.Device, allocator: std.mem.Allocator, cb: EventCallback) !DeviceReader {
        // Ensure device is non-blocking so nextEvent() returns null on EAGAIN
        const fd = dev.getFd();
        var flags = try std.posix.fcntl(fd, std.posix.F.GETFL, 0);
        const OFlags = std.posix.O;
        const nonblock_flags: u32 = @bitCast(OFlags{ .NONBLOCK = true });
        flags |= @as(usize, nonblock_flags);
        _ = try std.posix.fcntl(fd, std.posix.F.SETFL, flags);

        return .{
            .file = xev.File.initFd(fd),
            .dev = dev,
            .completion = undefined,
            .callback = cb,
            .allocator = allocator,
        };
    }

    pub fn start(self: *DeviceReader, loop: *xev.Loop) void {
        self.file.poll(loop, &self.completion, .read, DeviceReader, self, onReadReady);
    }

    fn onReadReady(
        ud: ?*DeviceReader,
        l: *xev.Loop,
        c: *xev.Completion,
        _: xev.File,
        r: xev.PollError!xev.PollEvent,
    ) xev.CallbackAction {
        _ = c;
        const self = ud.?;
        _ = r catch |err| {
            std.log.warn("Device poll error: {}", .{err});
            return .disarm;
        };

        // Read all available events
        var events: std.ArrayList(evdev.Event) = .empty;
        defer events.deinit(self.allocator);

        _ = self.dev.readEvents(self.allocator, &events) catch |err| {
            std.log.err("evdev read error: {}", .{err});
            return .disarm;
        };

        for (events.items) |e| {
            self.callback(&self.dev, e);
        }

        // Re-arm manually by starting again
        self.start(l);
        return .disarm;
    }
};

pub const EventLoop = struct {
    loop: xev.Loop,
    allocator: std.mem.Allocator,

    pub fn init(allocator: std.mem.Allocator) !EventLoop {
        return .{
            .loop = try xev.Loop.init(.{}),
            .allocator = allocator,
        };
    }

    pub fn deinit(self: *EventLoop) void {
        self.loop.deinit();
    }

    pub fn getLoop(self: *EventLoop) *xev.Loop {
        return &self.loop;
    }

    pub fn run(self: *EventLoop) !void {
        try self.loop.run(.until_done);
    }
};

test "event loop wrapper" {
    const allocator = std.testing.allocator;
    var el = try EventLoop.init(allocator);
    defer el.deinit();
}
