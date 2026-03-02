# Zig evdev Feasibility Study

## Background / Objectives

pykvm 目前使用 Python + `python-evdev` 实现键鼠捕获与虚拟设备注入。本文探讨将其用 Zig +
[`futsuuu/zig-evdev`](https://github.com/futsuuu/zig-evdev) 重新实现的可行性，为
[roadmap.md](roadmap.md) 中规划的 Phase 5 Zig 移植提供技术依据。

---

## pykvm evdev Feature Inventory

对照 `src/pykvm/` 源码（`devices.py`、`server.py`、`client.py`），以下是所有用到的
`python-evdev` 功能：

| 功能 | 使用位置 | 说明 |
|---|---|---|
| `evdev.list_devices()` | `devices.py`, `server.py` | 枚举 `/dev/input/event*` |
| `InputDevice(path)` | `devices.py`, `server.py` | 打开物理设备 |
| `dev.capabilities()` | `devices.py`, `server.py` | 读取设备能力（EV_KEY/EV_REL/EV_ABS） |
| `dev.capabilities(absinfo=True)` | `devices.py`, `server.py` | 读取含 AbsInfo 的完整能力（触摸板） |
| `dev.grab()` / `dev.ungrab()` | `server.py` | 独占设备（EVIOCGRAB） |
| `dev.async_read_loop()` | `server.py` | asyncio 异步事件读取循环 |
| `dev.close()` | `server.py` | 关闭设备 |
| `UInput(caps, name, input_props)` | `devices.py` | 创建虚拟键盘/鼠标/触摸板 |
| `uinput.write(type, code, value)` | `server.py`, `client.py` | 写入虚拟设备事件 |
| `uinput.close()` | `server.py`, `client.py` | 关闭虚拟设备 |
| `ecodes.*` 常量 | 所有文件 | 事件类型/代码常量 |
| `AbsInfo` namedtuple | `devices.py` | ABS 轴参数（min/max/fuzz/flat/res） |

---

## futsuuu/zig-evdev Coverage Comparison

### 已覆盖 ✅

| zig-evdev API | 对应 python-evdev 功能 |
|---|---|
| `Device.open(path, flags)` | `InputDevice(path)` |
| `dev.raw.grab()` / `dev.raw.ungrab()` | `dev.grab()` / `dev.ungrab()` |
| `Device.readEvents()` / `dev.nextEvent()` + `hasEventPending()` | `dev.async_read_loop()` 事件读取 |
| `VirtualDevice.fromDevice(dev)` | `UInput(caps)` 从物理设备复制能力 |
| `VirtualDevice.Builder` | `UInput(caps, name, input_props)` 自定义虚拟设备 |
| `Builder.copyCapabilities(src)` | `dev.capabilities(absinfo=True)` 完整能力复制 |
| `VirtualDevice.writeEvent(code, value)` | `uinput.write(type, code, value)` |
| `VirtualDevice.destroy()` | `uinput.close()` |
| `Builder.enableProperty(prop)` | `input_props=[INPUT_PROP_POINTER]` |
| `Event.Code` / `Event.Type` 枚举 | `ecodes.*`（类型安全） |
| `Device.isKeyboard()` / `isMouse()` / `isMultiTouch()` / `isSingleTouch()` | 设备分类检测 |
| `raw.AbsInfo` (`input_absinfo`) | `AbsInfo` namedtuple |
| `Device.raw.getName()` | `dev.name` |

### 缺失或需补充 ❌ / ⚠️

| 缺失功能 | 说明 |
|---|---|
| `evdev.list_devices()` | **缺失**，需用标准库扫描 `/dev/input/event*` |
| `dev.async_read_loop()` 异步封装 | **缺失**，需基于 fd + epoll/poll 自行实现 |
| 从 JSON caps 重建虚拟触摸板（client 端） | **缺失**，需结合 `std.json` + `Builder` 自行实现 |
| Hot-plug 监控 | **完全缺失**，需用 `inotify` 或轮询实现 |
| `capabilities()` 返回完整 map | 无直接等价，但 `copyCapabilities()` 已在 comptime 内部实现 |

---

## Gap Analysis

### Gap 1: `list_devices()`

`python-evdev` 的 `list_devices()` 自动枚举 `/dev/input/event*`，zig-evdev 无此接口。
可用 `std.fs` 约 20 行代码补充：

```zig
var dir = try std.fs.openDirAbsolute("/dev/input", .{ .iterate = true });
defer dir.close();
var iter = dir.iterate();
while (try iter.next()) |entry| {
    if (std.mem.startsWith(u8, entry.name, "event")) {
        // 构造完整路径并尝试打开
        // "/dev/input/" (11) + "event" (5) + up to 5 digits + NUL = 22 bytes max
        var path_buf: [32]u8 = undefined;
        const path = try std.fmt.bufPrint(&path_buf, "/dev/input/{s}", .{entry.name});
        const dev = Device.open(path, .{}) catch continue;
        // 收集到列表
    }
}
```

### Gap 2: 异步事件读取（替代 `async_read_loop()`）

`python-evdev` 的 `async_read_loop()` 与 `asyncio` 深度集成，zig-evdev 仅提供同步
`readEvents()`。推荐两种方案：

**方案 A：原生 Linux `epoll`**

```zig
const epoll_fd = try std.posix.epoll_create1(0);
defer std.posix.close(epoll_fd);

for (devices) |dev| {
    var event = std.os.linux.epoll_event{
        .events = std.os.linux.EPOLL.IN,
        .data = .{ .fd = dev.raw.fd },
    };
    try std.posix.epoll_ctl(epoll_fd, std.os.linux.EPOLL.CTL_ADD, dev.raw.fd, &event);
}

var events: [16]std.os.linux.epoll_event = undefined;
while (true) {
    const n = try std.posix.epoll_wait(epoll_fd, &events, -1);
    for (events[0..n]) |ev| {
        // 根据 ev.data.fd 找到对应设备，调用 readEvents()
    }
}
```

**方案 B：集成 [`libxev`](https://github.com/mitchellh/libxev)**

libxev 提供跨平台事件循环，可替代 asyncio，适合需要同时处理 TCP + 设备 fd 的场景。

### Gap 3: 从 JSON caps 重建虚拟触摸板

client 端需根据 server 发来的 JSON 能力描述重建虚拟触摸板。zig-evdev 无此内建支持，
需结合 `std.json` 解析后逐项调用 `Builder`：

```zig
const parsed = try std.json.parseFromSlice(CapsJson, allocator, json_data, .{});
defer parsed.deinit();

var builder = VirtualDevice.Builder.init(allocator);
defer builder.deinit();
try builder.setName("zig-kvm Touchpad");

for (parsed.value.abs_axes) |axis| {
    const abs_info = raw.AbsInfo{
        .value = 0,
        .minimum = axis.min,
        .maximum = axis.max,
        .fuzz = axis.fuzz,
        .flat = axis.flat,
        .resolution = axis.res,
    };
    try builder.enableEventCode(axis.code, .{ .abs_info = &abs_info });
}
```

### Gap 4: Hot-plug 监控

Python 版规划用轮询实现；Zig 版可选用 Linux `inotify` 获得更低延迟：

```zig
const inotify_fd = try std.posix.inotify_init1(0);
_ = try std.posix.inotify_add_watch(inotify_fd, "/dev/input", std.os.linux.IN.CREATE);

var buf: [4096]u8 align(@alignOf(std.os.linux.inotify_event)) = undefined;
while (true) {
    const n = try std.posix.read(inotify_fd, &buf);
    var offset: usize = 0;
    while (offset < n) {
        const ev = @as(*const std.os.linux.inotify_event, @ptrCast(&buf[offset]));
        const hdr_size = @sizeOf(std.os.linux.inotify_event);
        if (offset + hdr_size + ev.len > n) break; // 防止越界
        const name = buf[offset + hdr_size ..][0..ev.len];
        if (std.mem.startsWith(u8, name, "event")) {
            // 新设备出现，尝试打开并 grab
        }
        offset += hdr_size + ev.len;
    }
}
```

轮询方案（与当前 Python 规划一致）：每秒扫描一次 `/dev/input/event*`，对比已知设备集合。

---

## Feasibility Summary

| 维度 | 评估 |
|---|---|
| 核心 evdev 功能覆盖率 | ~75%，缺失部分可用标准库补充 |
| UInput 虚拟设备能力 | ✅ 完整，`Builder.copyCapabilities()` 尤为强大 |
| 触摸板检测与支持 | ✅ `isMultiTouch` / `isSingleTouch` 比 Python 版更完整 |
| 异步 I/O | ⚠️ 需额外工作（epoll 或 libxev） |
| 设备枚举 | ⚠️ 需约 20 行标准库代码补充 |
| Hot-plug 监控 | ⚠️ 需完全自行实现（inotify 或轮询） |
| TCP 协议层 | ✅ 用 `std.net` 可完全实现，且更高效 |
| 整体可行性 | ✅ **可行**，主要挑战在异步架构选型 |

---

## TODO List

### Phase 0 — 调研与准备

- [ ] 确定异步 I/O 方案：原生 `epoll` 封装 vs `libxev` vs 其他
- [ ] 评估 `futsuuu/zig-evdev` 的 Zig 版本兼容性（当前需要 Zig 0.14+）
- [ ] 确认 `build.zig.zon` 依赖管理方式（添加 `zig-evdev` 为依赖）

### Phase 1 — 设备层（evdev 封装）

- [ ] 实现 `list_devices()` — 扫描 `/dev/input/event*`
- [ ] 封装 `Device.open` + `grab` / `ungrab` / `close`
- [ ] 实现设备分类检测（keyboard / mouse / touchpad）
- [ ] 实现 `getCapabilitiesJson()` — 将设备能力序列化为 JSON（供 client 端重建触摸板）

### Phase 2 — 虚拟设备层（uinput）

- [ ] 实现 `createVirtualKeyboard()` — 使用 `VirtualDevice.Builder`
- [ ] 实现 `createVirtualMouse()` — 含 `INPUT_PROP_POINTER` + REL 轴 + 鼠标按钮
- [ ] 实现 `createVirtualTouchpad(src: Device)` — 使用 `Builder.copyCapabilities`
- [ ] 实现 `createVirtualTouchpadFromJson(json: []const u8)` — client 端重建触摸板
- [ ] 实现 `VirtualDevice.writeEvent` 封装（含 EV_SYN 刷新）

### Phase 3 — 事件循环与多路复用

- [ ] 实现基于 `epoll` 的多设备事件读取循环（替代 `async_read_loop()`）
- [ ] 实现 held_keys 追踪（`EV_KEY` down/up 状态维护）
- [ ] 实现 slot-switch 热键检测（`switch_mods + digit`）
- [ ] 实现 stuck key 释放（slot 切换时向 outgoing target 合成 key-up 序列）

### Phase 4 — Hot-plug 监控

- [ ] 实现 `/dev/input/` 目录变化监控（`inotify` 或 1s 轮询）
- [ ] 实现热插拔设备的自动 grab
- [ ] 实现设备拔出时的 task 清理与 ungrab

### Phase 5 — TCP 协议层

- [ ] 实现 `makeAuthToken(psk)` — SHA-256(PSK) 或全零
- [ ] 实现 `packCaps(caps)` / `unpackCaps(data)` — u32 BE length + JSON body
- [ ] 实现 8-byte 事件打包 `packEvent` / `unpackEvent`（type:u16 + code:u16 + value:i32, BE）
- [ ] 实现 TCP server：accept → auth → caps handshake → event stream
- [ ] 实现 TCP client：connect → auth → caps handshake → uinput 注入 + 指数退避重连

### Phase 6 — 集成与测试

- [ ] 端到端测试：Zig server ↔ Python client（协议兼容性验证）
- [ ] 端到端测试：Python server ↔ Zig client
- [ ] 性能基准测试：Python vs Zig 的事件延迟对比
- [ ] NixOS flake 更新：添加 Zig 构建目标

---

## References

- [`futsuuu/zig-evdev`](https://github.com/futsuuu/zig-evdev) — Zig evdev/uinput 绑定
- [`mitchellh/libxev`](https://github.com/mitchellh/libxev) — Zig 跨平台事件循环（可选）
- [Linux `input.h` evdev 文档](https://www.kernel.org/doc/html/latest/input/event-codes.html)
- [Linux `uinput` 文档](https://www.kernel.org/doc/html/latest/input/uinput.html)
- [Linux `inotify` 文档](https://man7.org/linux/man-pages/man7/inotify.7.html)
