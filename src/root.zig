const std = @import("std");
pub const device = @import("device.zig");

test {
    std.testing.refAllDecls(@This());
}
