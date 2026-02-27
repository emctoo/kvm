# VM Testing

pykvm includes NixOS VM configurations so you can test the full server ↔ client
flow on a single machine using two QEMU virtual machines.

---

## Architecture

```
┌─────────────── Host machine ───────────────────┐
│                                                │
│  ┌──────────────────┐   ┌──────────────────┐  │
│  │   VM: kvm-server │   │   VM: kvm-client │  │
│  │                  │   │                  │  │
│  │  pykvm-server    │   │  pykvm-client    │  │
│  │  listens :5900   │   │  connects to     │  │
│  │                  │   │  10.0.2.2:15900  │  │
│  │  QEMU user net   │   │  QEMU user net   │  │
│  └────────┬─────────┘   └────────┬─────────┘  │
│           │ hostfwd              │             │
│      host:15900 ◄────────────────┘             │
│      (loopback)                                │
└────────────────────────────────────────────────┘
```

### Networking explanation

Both VMs use QEMU's user-mode networking.  In user-mode networking, the QEMU
process acts as a NAT gateway, and the guest can reach the host at `10.0.2.2`.

The server VM's QEMU process is started with:

```
-netdev user,id=user.0,hostfwd=tcp::15900-:5900
```

This means: bind port 15900 on the host's loopback interface and forward
incoming TCP connections to the guest's port 5900.

The client VM connects to `10.0.2.2:15900`, which is:
- `10.0.2.2` — the host, as seen from inside any QEMU user-net guest
- `:15900` — the forwarded port that the host relays to the server VM

This setup does not require any bridging, TAP devices, or root privileges beyond
what `nix build` needs.

---

## Building the VMs

```bash
# Build the server VM run script
nix build .#vm-server
# Result is at ./result/bin/run-kvm-server-vm

# Build the client VM run script
nix build .#vm-client
# Result is at ./result/bin/run-kvm-client-vm
```

Each build produces a shell script that launches QEMU with the correct
arguments.

---

## Running the VMs

Open **two terminals**.

### Terminal 1 — server VM

```bash
nix build .#vm-server && ./result/bin/run-kvm-server-vm
```

The VM will boot, auto-login as root, and attempt to start `pykvm-server` via
systemd.  Check the service status:

```bash
systemctl status pykvm-server
journalctl -u pykvm-server -f
```

### Terminal 2 — client VM

```bash
nix build .#vm-client && ./result/bin/run-kvm-client-vm
```

Same process — systemd starts `pykvm-client` automatically.  Check it with:

```bash
systemctl status pykvm-client
journalctl -u pykvm-client -f
```

---

## QEMU display

By default NixOS VMs open a QEMU graphical window.  To run headless (useful for
CI or if you don't need the display):

```bash
QEMU_OPTS="-nographic" ./result/bin/run-kvm-server-vm
```

Or set `virtualisation.graphics = false` in the VM module in `flake.nix`.

---

## Virtual keyboard in the server VM

The server VM is started with `-device virtio-keyboard-pci`, which creates a
virtual keyboard inside the guest.  This gives `pykvm-server` a real evdev
device to discover and grab.

Verify inside the server VM:

```bash
ls /dev/input/
# event0  event1  …

python3 -c "import evdev; print(evdev.list_devices())"
# ['/dev/input/event1', '/dev/input/event0']

python3 -c "
import evdev
for p in evdev.list_devices():
    d = evdev.InputDevice(p)
    print(d.path, d.name)
"
```

---

## uinput in both VMs

Both VMs load the `uinput` kernel module and apply udev rules so that
`/dev/uinput` and `/dev/input/*` are readable/writable by group `input` (and
by root, which is the auto-login user).

Verify:

```bash
ls -la /dev/uinput
# crw-rw---- 1 root input 10, 223 ...

lsmod | grep uinput
# uinput   20480  0
```

---

## Flake configuration

The VM configs are defined in `flake.nix`:

```nix
nixosConfigurations = {
  vm-server = vmServerSystem;
  vm-client = vmClientSystem;
};

packages.x86_64-linux = {
  vm-server = vmServerSystem.config.system.build.vm;
  vm-client = vmClientSystem.config.system.build.vm;
};
```

Key NixOS options used:

| Option | Value | Purpose |
|---|---|---|
| `virtualisation.memorySize` | 1024 | 1 GB RAM per VM |
| `virtualisation.qemu.networkingOptions` | see flake | port forward / user net |
| `virtualisation.qemu.options` | `-device virtio-keyboard-pci` | virtual KB in server |
| `boot.kernelModules` | `["uinput"]` | load uinput on boot |
| `services.getty.autologinUser` | `"root"` | no login prompt |
| `system.stateVersion` | `"25.05"` | suppress stateVersion warning |

---

## Disk persistence

NixOS VMs created with `system.build.vm` use a temporary disk image stored in
`/tmp/nixos-vm-*` (or the current directory, depending on the NixOS version).
State is **not** preserved between runs unless you set:

```nix
virtualisation.diskImage = "./kvm-server.qcow2";
```

For testing pykvm, ephemeral disks are fine.

---

## Troubleshooting

### `pykvm-server` / `pykvm-client` fails with `NotImplementedError`

This is expected — the entry-points are stubs.  Implementation is the next
development phase.

### Port 15900 already in use on host

Another process is using port 15900.  Either stop it, or change the forwarded
port in `flake.nix`:

```nix
"-netdev user,id=user.0,hostfwd=tcp::15901-:5900"
# and update the client's --port argument accordingly
```

### Client cannot reach server

1. Confirm the server VM is running and listening:
   ```bash
   # inside server VM
   ss -tlnp | grep 5900
   ```
2. Confirm the host is forwarding:
   ```bash
   # on the host
   ss -tlnp | grep 15900
   ```
3. Verify the client is connecting to `10.0.2.2:15900`.

### Permission denied on `/dev/uinput`

The udev rule might not have been applied.  Inside the VM:

```bash
udevadm control --reload-rules
udevadm trigger
ls -la /dev/uinput
```
