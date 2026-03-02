const std = @import("std");

pub fn build(b: *std.Build) !void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const evdev_dep = b.dependency("evdev", .{ .target = target, .optimize = optimize });
    const xev_dep   = b.dependency("libxev", .{ .target = target, .optimize = optimize });

    const exe = b.addExecutable(.{
        .name = "kvm",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/main.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    exe.root_module.addImport("evdev", evdev_dep.module("evdev"));
    exe.root_module.addImport("xev",   xev_dep.module("xev"));
    b.installArtifact(exe);

    const run_cmd = b.addRunArtifact(exe);
    run_cmd.step.dependOn(b.getInstallStep());

    if (b.args) |args| {
        run_cmd.addArgs(args);
    }

    const run_step = b.step("run", "Run the app");
    run_step.dependOn(&run_cmd.step);
}
