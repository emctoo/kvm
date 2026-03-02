# Zig 迁移可行性分析（zig-evdev）

## 概述

本文档评估将 `pykvm`（Python + `python-evdev` 实现）迁移到 Zig（使用
[`futsuuu/zig-evdev`](https://github.com/futsuuu/zig-evdev) 库）的可行性，梳理
功能覆盖情况、关键差距与解决方案，并给出分阶段的 TODO 清单。

---

## pykvm 使用的 evdev 功能清单

### `devices.py`（设备发现与虚拟设备创建）

- `evdev.list_devices()` — 枚举 `/dev/input/event*`
- `InputDevice(path)` — 打开输入设备
- `dev.capabilities()` — 读取设备能力（`EV_KEY` / `EV_REL` / `EV_ABS` 等）
- `dev.capabilities(absinfo=True)` — 读取含 `AbsInfo`（min/max/resolution）的完整能力
- `UInput(caps, name=, version=, input_props=)` — 创建虚拟设备（键盘、鼠标、触摸板）
- `uinput.write(type, code, value)` — 向虚拟设备写入事件
- `uinput.close()` — 关闭虚拟设备
- `ecodes.*` — 事件类型/代码常量（`EV_KEY`, `KEY_A`, `BTN_MOUSE`, `ABS_X`, `INPUT_PROP_POINTER` 等）
- `AbsInfo(value, min, max, fuzz, flat, resolution)` — ABS 轴参数

### `server.py`（服务端：设备 grab 与事件读取）

- `dev.grab()` / `dev.ungrab()` — 独占/释放设备（`EVIOCGRAB`）
- `dev.close()` — 关闭设备
- `dev.capabilities()` — 检测设备类型（键盘 / 鼠标 / 触摸板）
- `async for ev in dev.async_read_loop()` — asyncio 异步事件读取循环
- `ev.type`, `ev.code`, `ev.value` — 事件字段
- `evdev.list_devices()` — hot-plug 监控时重新枚举设备
- `vtouchpad.write(ev.type, ev.code, ev.value)` — 本地 passthrough

### `client.py`（客户端：接收并注入事件）

- `ecodes.*` — 事件代码常量
- `vkbd.write(type, code, value)` / `vmouse.write(...)` / `vtouchpad.write(...)` — 注入事件

---

## zig-evdev 功能覆盖对照

| pykvm 功能 | zig-evdev 接口 | 状态 |
|---|---|---|
| `evdev.list_devices()` | ❌ 未提供 | ❌ 缺失 |
| `InputDevice(path)` | `Device.open(path, flags)` | ✅ |
| `dev.capabilities()` | `hasEventType()` + `hasEventCode()` + `hasProperty()` | ⚠️ 需手动迭代 |
| `dev.capabilities(absinfo=True)` | `getAbsInfo(axis)` | ⚠️ 需手动迭代 |
| `dev.grab()` / `dev.ungrab()` | `Device.raw.grab()` / `ungrab()` | ✅ |
| `async for ev in dev.async_read_loop()` | `Device.nextEvent()` + `hasEventPending()` | ⚠️ 同步，需 async 封装 |
| `Device.readEvents()`（批量读） | `Device.readEvents()` | ✅ |
| 触摸板检测 | `Device.isMultiTouch()` / `isSingleTouch()` | ✅（比 Python 更完整） |
| 键盘 / 鼠标检测 | `Device.isKeyboard()` / `isMouse()` | ✅ |
| `dev.name` | `Device.raw.getName()` | ✅ |
| `UInput(caps, ...)` 虚拟设备 | `VirtualDevice.Builder` | ⚠️ 部分（见差距说明） |
| `UInput.write(type, code, value)` | `VirtualDevice.writeEvent(code, value)` | ✅ |
| `UInput.close()` | `VirtualDevice.destroy()` | ✅ |
| `input_props=[INPUT_PROP_POINTER]` | `Builder.enableProperty(prop)` | ✅ |
| 从物理设备复制所有 capabilities | `Builder.copyCapabilities(src)` | ✅（comptime 全量复制） |
| 从 JSON caps 重建虚拟触摸板 | ❌ 未提供 | ❌ 需自行实现 |
| `ecodes.*` 常量 | `Event.Code`, `Event.Type` tagged union 枚举 | ✅（类型安全） |
| `AbsInfo` 结构 | `raw.AbsInfo` (= `c.input_absinfo`) | ✅ |
| `VirtualDevice.getSysPath()` / `getDevNode()` | `VirtualDevice.getSysPath()` / `getDevNode()` | ✅ |

---

## 关键差距与解决方案

### 差距 1：`list_devices()` — 设备枚举（缺失）

`zig-evdev` 不提供设备枚举函数，需用 `std.fs` 手动扫描 `/dev/input/event*`：

```zig
fn listDevices(allocator: std.mem.Allocator) ![][]const u8 {
    var paths = std.ArrayList([]const u8).init(allocator);
    var dir = try std.fs.openDirAbsolute("/dev/input", .{ .iterate = true });
    defer dir.close();
    var it = dir.iterate();
    while (try it.next()) |entry| {
        if (std.mem.startsWith(u8, entry.name, "event")) {
            const path = try std.fmt.allocPrint(
                allocator, "/dev/input/{s}", .{entry.name},
            );
            try paths.append(path);
        }
    }
    return paths.toOwnedSlice();
}
```

约 20 行标准库代码即可完整实现。

### 差距 2：`async_read_loop()` — 异步事件循环（部分）

`zig-evdev` 的 `Device.nextEvent()` 和 `Device.readEvents()` 是同步调用，无法直接
替代 Python asyncio 的多设备并发读取。需要对多个设备 fd 进行 I/O 多路复用：

- **推荐方案**：使用 [`libxev`](https://github.com/mitchellh/libxev)，Zig 原生
  跨平台异步 I/O 库，API 设计与 `libuv` 类似，支持 epoll / kqueue / io_uring。
- **备选方案**：手写 Linux `epoll` 封装，将多个设备 fd 注册到同一个 epoll
  实例，主循环统一 dispatch。

两种方案均可实现与 Python asyncio 等效的并发语义。

### 差距 3：`capabilities()` 完整枚举（部分）

`zig-evdev` 没有返回完整 capabilities map 的单一函数，但已提供：

- `hasEventType(ev_type)` — 检查是否支持某事件类型
- `hasEventCode(code)` — 检查是否支持某事件代码
- `hasProperty(prop)` — 检查设备属性

`VirtualDevice.Builder.copyCapabilities()` 已通过 `comptime` 内联循环实现了从物
理设备到虚拟设备的全量 capabilities 复制，这是 pykvm 触摸板克隆的核心需求。对于
设备类型检测，`isKeyboard()` / `isMouse()` / `isMultiTouch()` / `isSingleTouch()`
直接可用，无需手动枚举。

### 差距 4：从 JSON 重建虚拟触摸板（缺失）

pykvm client 侧通过 TCP 接收服务端序列化的触摸板 capabilities JSON，再重建虚拟设
备。`zig-evdev` 不提供此功能，需自行实现：

1. 用 `std.json` 解析 capabilities JSON
2. 用 `Builder.enableEventCode` 逐项注册 `EV_ABS` / `EV_KEY` 等事件代码
3. 用 `Builder.setAbsInfo` 设置每个 ABS 轴的 `AbsInfo`（min/max/resolution）
4. 用 `Builder.enableProperty` 设置设备属性

整个流程完全可用标准库实现，无需额外依赖。

### 差距 5：Hot-plug 监控（缺失）

Python 版通过 1 秒轮询 `evdev.list_devices()` 检测新设备。Zig 可选择：

- **inotify**（推荐）：监控 `/dev/input/` 目录的 `IN_CREATE` 事件，零延迟响应
  新设备出现，实现更精准
- **轮询**：与 Python 版一致，定时（1 秒）重新扫描 `/dev/input/event*`，实现简单

---

## 优势：类型安全的事件代码

Python 版使用整数常量（如 `ecodes.EV_KEY = 1`），运行时才能发现类型错误。
`zig-evdev` 使用 tagged union `Event.Code`，将事件类型与事件代码绑定在一起：

```zig
// 编译期即可检查：EV_KEY 事件不可能携带 ABS_X 代码
const ev = Event.Code{ .key = .KEY_A };
device.writeEvent(ev, 1); // 按下 A 键
```

错误的类型组合（如 `EV_KEY` + `ABS_X`）在编译期即报错，消除了 Python 版运行时
整数混用的风险。

---

## 总体可行性结论

| 维度 | 评估 |
|---|---|
| 核心 evdev 功能覆盖率 | ~75%，缺失部分可用标准库补充 |
| UInput 虚拟设备能力 | ✅ 完整，`Builder.copyCapabilities()` 尤为强大 |
| 触摸板支持 | ✅ `isMultiTouch` / `isSingleTouch` 比 Python 版更完整 |
| 异步 I/O | ⚠️ 需额外工作（epoll 或 libxev） |
| 设备枚举 | ⚠️ 需约 20 行标准库代码补充 |
| Hot-plug 监控 | ⚠️ 需自行实现（inotify 或轮询） |
| TCP 协议层 | ✅ `std.net` 完全可实现，且更高效 |
| 整体结论 | ✅ **可行**，主要挑战在异步架构选型 |

推荐实现策略：

1. **优先使用 `zig-evdev` 已有接口**：`Builder.copyCapabilities()` 直接用于触摸
   板克隆，`isKeyboard()` / `isMouse()` 用于设备分类，无需重复实现。
2. **异步层选用 `libxev`**：避免手写 epoll 封装，获得跨平台兼容性，且与
   `std.net` 集成更自然。
3. **设备枚举与 hot-plug 用标准库自行实现**：代码量小（各约 20–50 行），无需引
   入额外依赖。
4. **保持与 Python 版相同的 8 字节线缆格式**：协议层零改动，Zig server 可与
   Python client 互通。
5. **先实现同步版本，再引入异步**：同步版本可用于单设备快速验证，异步层作为第
   二阶段引入，降低初期复杂度。

---

## TODO List（迁移任务清单）

### Phase 0：项目脚手架

- [ ] 创建 Zig 项目（`build.zig` + `build.zig.zon`）
- [ ] 添加 `zig-evdev` 依赖（`build.zig.zon` 中声明）
- [ ] 配置 NixOS flake（添加 Zig toolchain + libevdev dev 头文件）
- [ ] 验证 `zig build` 能编译通过

### Phase 1：基础设施层（对应 `devices.py`）

- [ ] 实现 `listDevices()` — 扫描 `/dev/input/event*`
- [ ] 封装设备检测工具函数：`isKeyboard(dev)` / `isMouse(dev)` / `isTouchpad(dev)`（可直接复用 `zig-evdev` 的 `isKeyboard` / `isMouse` / `isMultiTouch` / `isSingleTouch`）
- [ ] 实现 `createVirtualKeyboard()` — 用 `Builder` + 全量 `EV_KEY` codes
- [ ] 实现 `createVirtualMouse()` — 带 `INPUT_PROP_POINTER`，支持 `REL_WHEEL_HI_RES` / `REL_HWHEEL_HI_RES`
- [ ] 实现 `createVirtualTouchpad(src: Device)` — 用 `Builder.copyCapabilities(src)`
- [ ] 实现 `createVirtualTouchpadFromCaps(caps_json)` — 解析 JSON 后用 `Builder` 逐项构建

### Phase 2：配置层（对应 `config.py`）

- [ ] 定义 `ServerConfig` / `ClientConfig` 结构体
- [ ] 实现 CLI 参数解析（`--host`, `--port`, `--psk`, `--ignore-device`）
- [ ] 定义默认 switch mods（Ctrl+Win = keycodes 29+125）

### Phase 3：协议层（对应 `protocol.py`）

- [ ] 实现 PSK 认证：SHA-256(psk) → 32 字节 token
- [ ] 实现 caps handshake：u32 长度头 + JSON body 序列化 / 反序列化
- [ ] 实现 8 字节事件 pack/unpack（`u16 type` + `u16 code` + `i32 value`，big-endian）

### Phase 4：异步 I/O 层

- [ ] 选型：评估 `libxev` vs 手写 `epoll` 封装
- [ ] 实现多设备 fd 的 I/O 多路复用（替代 Python 的 `async_read_loop()`）
- [ ] 实现 TCP 异步读写（替代 `asyncio.StreamReader` / `Writer`）
- [ ] 实现 TCP keepalive 设置（`SO_KEEPALIVE` + `TCP_KEEPIDLE` / `INTVL` / `CNT`）

### Phase 5：服务端（对应 `server.py`）

- [ ] 实现启动时设备发现与 grab（键盘 / 鼠标 / 触摸板）
- [ ] 实现设备 own_paths 过滤（不 grab 自己创建的虚拟设备）
- [ ] 实现 ignore_devices 过滤（按名称或路径模式）
- [ ] 实现 hot-plug 监控（`inotify` 或 1 秒轮询 `/dev/input/`）
- [ ] 实现多设备并发事件读取（epoll 上的多 fd 监听）
- [ ] 实现 slot 切换逻辑（switch_mods + 数字键 1–9）
- [ ] 实现 held_keys 跟踪与切换时的 synthetic key release
- [ ] 实现本地 passthrough 路由（事件写入虚拟设备）
- [ ] 实现远端路由（事件序列化发送给当前 active client）
- [ ] 实现 TCP 服务端（接受多客户端连接，slot 分配）
- [ ] 实现客户端认证（PSK check）+ capabilities handshake
- [ ] 实现客户端断线时自动回退到 local 模式
- [ ] 实现设备热拔出时的任务清理与 ungrab

### Phase 6：客户端（对应 `client.py`）

- [ ] 实现 TCP 客户端连接（指数退避重连：1s → 2s → … → 60s）
- [ ] 实现 capabilities handshake 接收（5 秒超时）
- [ ] 实现 8 字节事件流读取循环
- [ ] 实现事件路由：键盘事件 → vkbd，鼠标事件 → vmouse，触摸板事件 → vtouchpad
- [ ] 实现 touchpad BTN codes 识别（`BTN_TOUCH`, `BTN_TOOL_FINGER` 等）
- [ ] 实现虚拟键盘 / 鼠标跨连接保持（不在重连时重建）
- [ ] 实现虚拟触摸板按连接重建（capabilities 可能变化）

### Phase 7：测试与集成

- [ ] 为协议 pack/unpack 添加单元测试
- [ ] 为设备发现逻辑添加单元测试（mock `/dev/input/`）
- [ ] 更新 NixOS flake 添加 Zig 版本的 package / app
- [ ] 更新 `justfile` 添加 Zig 构建命令

---

## 参考资料

- [futsuuu/zig-evdev](https://github.com/futsuuu/zig-evdev) — zig-evdev 库
- [python-evdev 文档](https://python-evdev.readthedocs.io/)
- [libevdev 文档](https://www.freedesktop.org/software/libevdev/doc/latest/)
- [libxev](https://github.com/mitchellh/libxev) — Zig 异步 I/O 库候选
- Linux kernel: `include/uapi/linux/input.h`, `include/uapi/linux/input-event-codes.h`
