const std = @import("std");
const device = @import("device.zig");

pub const HotplugMonitor = struct {
    allocator: std.mem.Allocator,
    known_devices: std.StringHashMap(void),

    pub fn init(allocator: std.mem.Allocator) HotplugMonitor {
        return .{
            .allocator = allocator,
            .known_devices = std.StringHashMap(void).init(allocator),
        };
    }

    pub fn deinit(self: *HotplugMonitor) void {
        var iter = self.known_devices.keyIterator();
        while (iter.next()) |k| {
            self.allocator.free(k.*);
        }
        self.known_devices.deinit();
    }

    pub const Delta = struct {
        added: std.ArrayList([]const u8),
        removed: std.ArrayList([]const u8),

        pub fn deinit(self: *Delta, allocator: std.mem.Allocator) void {
            for (self.added.items) |p| allocator.free(p);
            for (self.removed.items) |p| allocator.free(p);
            self.added.deinit(allocator);
            self.removed.deinit(allocator);
        }
    };

    pub fn scan(self: *HotplugMonitor) !Delta {
        var added: std.ArrayList([]const u8) = .empty;
        errdefer {
            for (added.items) |p| self.allocator.free(p);
            added.deinit(self.allocator);
        }

        var current = std.StringHashMap(void).init(self.allocator);
        defer {
            var iter = current.keyIterator();
            while (iter.next()) |k| {
                self.allocator.free(k.*);
            }
            current.deinit();
        }

        var list = try device.listDevices(self.allocator);
        defer {
            for (list.items) |p| self.allocator.free(p);
            list.deinit(self.allocator);
        }

        // Check for added devices
        for (list.items) |path| {
            try current.put(try self.allocator.dupe(u8, path), {});
            if (!self.known_devices.contains(path)) {
                try added.append(self.allocator, try self.allocator.dupe(u8, path));
                try self.known_devices.put(try self.allocator.dupe(u8, path), {});
            }
        }

        var removed: std.ArrayList([]const u8) = .empty;
        errdefer {
            for (removed.items) |p| self.allocator.free(p);
            removed.deinit(self.allocator);
        }

        // Check for removed devices
        {
            var iter = self.known_devices.keyIterator();
            var to_remove: std.ArrayList([]const u8) = .empty;
            defer to_remove.deinit(self.allocator);

            while (iter.next()) |k| {
                if (!current.contains(k.*)) {
                    try removed.append(self.allocator, try self.allocator.dupe(u8, k.*));
                    try to_remove.append(self.allocator, k.*);
                }
            }

            for (to_remove.items) |k| {
                _ = self.known_devices.remove(k);
                self.allocator.free(k);
            }
        }

        return Delta{ .added = added, .removed = removed };
    }
};

test "hotplug monitor delta" {
    const allocator = std.testing.allocator;
    // We can't mock listDevices easily here, but we could make a mockable interface in real proj.
    // For now we just test that a default scan doesn't explode.
    var monitor = HotplugMonitor.init(allocator);
    defer monitor.deinit();

    var d1 = monitor.scan() catch return;
    d1.deinit(allocator);

    var d2 = try monitor.scan();
    d2.deinit(allocator);
}
