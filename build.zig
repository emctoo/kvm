const std = @import("std");

pub fn build(b: *std.Build) !void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const evdev_dep = b.dependency("evdev", .{ .target = target, .optimize = optimize });
    const xev_dep = b.dependency("libxev", .{ .target = target, .optimize = optimize });

    const exe = b.addExecutable(.{
        .name = "kvm",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/main.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    exe.root_module.addImport("evdev", evdev_dep.module("evdev"));
    exe.root_module.addImport("xev", xev_dep.module("xev"));
    b.installArtifact(exe);

    const kvm_server = b.addExecutable(.{
        .name = "pykvm-server",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/server.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    kvm_server.root_module.addImport("evdev", evdev_dep.module("evdev"));
    kvm_server.root_module.addImport("xev", xev_dep.module("xev"));
    b.installArtifact(kvm_server);

    const kvm_client = b.addExecutable(.{
        .name = "pykvm-client",
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/client.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    kvm_client.root_module.addImport("evdev", evdev_dep.module("evdev"));
    kvm_client.root_module.addImport("xev", xev_dep.module("xev"));
    b.installArtifact(kvm_client);

    const run_cmd = b.addRunArtifact(exe);
    run_cmd.step.dependOn(b.getInstallStep());

    if (b.args) |args| {
        run_cmd.addArgs(args);
    }

    const run_step = b.step("run", "Run the app");
    run_step.dependOn(&run_cmd.step);

    const exe_tests = b.addTest(.{
        .root_module = b.createModule(.{
            .root_source_file = b.path("src/root.zig"),
            .target = target,
            .optimize = optimize,
        }),
    });
    exe_tests.root_module.addImport("evdev", evdev_dep.module("evdev"));
    exe_tests.root_module.addImport("xev", xev_dep.module("xev"));

    const run_exe_tests = b.addRunArtifact(exe_tests);

    const test_step = b.step("test", "Run unit tests");
    test_step.dependOn(&run_exe_tests.step);
}
