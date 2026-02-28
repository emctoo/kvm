# Dev Desktop VM

`vm-dev-desktop` is a NixOS VM with an XFCE desktop.  It is used to verify
that pykvm-client correctly injects keyboard and mouse events — the cursor
should move and keys should appear in whichever window has focus.

---

## Recipes overview

All five recipes are controlled by the `VM_HOST` environment variable
(default: `localhost`).  Setting `VM_HOST` once covers every subsequent
command in that shell session.

| Recipe | What it does |
|---|---|
| `vm-dev-desktop-start` | Build + launch the VM |
| `vm-dev-desktop-view` | Open a VNC viewer to the VM display |
| `vm-dev-desktop-sync` | Rsync `src/` into `/root/pykvm/src` inside the VM |
| `vm-dev-desktop-ssh` | Root shell inside the running VM |
| `vm-dev-desktop-run [server]` | Run `pykvm-client` inside the VM |

---

## Local workflow (VM runs on this machine)

```bash
# Terminal 1 — start the VM (opens a QEMU display window)
just vm-dev-desktop-start

# Terminal 2 — start pykvm-server
just server

# Terminal 3 — sync code and run the client
just vm-dev-desktop-sync
just vm-dev-desktop-run        # connects to 10.0.2.2 = this host
```

Press **Left-Ctrl + Left-Alt + Tab** to toggle the server into remote mode.
The cursor inside the XFCE window should move with your physical mouse.

The QEMU window shows the VM's XFCE desktop.  The `user` account is
auto-logged in; you work with the VM through the window or over SSH (port 2223
on localhost).

---

## Remote workflow (VM runs on another machine)

Running the VM on a separate machine (e.g. `192.168.9.33`) removes the visual
overlap — the XFCE desktop appears on a different screen and the server machine
is unaffected when the physical mouse is grabbed.

```bash
# Point all desktop-VM recipes at the remote machine.
export VM_HOST=192.168.9.33

# Terminal 1 — build locally, copy to .33, launch with VNC there
just vm-dev-desktop-start

# Terminal 2 — open a VNC viewer to see the XFCE desktop
just vm-dev-desktop-view       # runs: vncviewer 192.168.9.33

# Terminal 3 — start pykvm-server on this machine
just server

# Terminal 4 — sync code and run the client
just vm-dev-desktop-sync
just vm-dev-desktop-run 192.168.9.34   # explicit server address
```

### How `vm-dev-desktop-start` works remotely

1. `nix build .#vm-dev-desktop` — build (or reuse cached) VM closure locally.
2. `nix copy --to ssh://maple@192.168.9.33` — push the closure to the remote
   Nix store.  The remote does **not** need to rebuild anything.
3. `ssh -t maple@192.168.9.33 "QEMU_OPTS='-display vnc=0.0.0.0:0' ..."` —
   start QEMU on the remote machine with VNC output on port 5900.

> `nix copy` writes to the remote Nix store.  If the remote machine requires
> trusted users, either use `root@...` or add `maple` to
> `nix.settings.trusted-users` on that machine.

---

## Syncing code changes

After editing `src/`, push the changes into the running VM:

```bash
just vm-dev-desktop-sync
```

This rsyncs `src/` → `/root/pykvm/src/` inside the VM.  Because
`PYTHONPATH=/root/pykvm/src` is set in the VM's environment, the next
`pykvm-client` invocation picks up the new code immediately — no VM rebuild
needed.

Typical edit → test loop:

```
edit src/   →   just vm-dev-desktop-sync   →   just vm-dev-desktop-run 192.168.9.34
```

---

## Verifying injection

### Mouse

```bash
# In vm-dev-desktop-ssh (or a second SSH session):
evtest /dev/input/by-name/pykvm-mouse
```

Move the physical mouse while in remote mode — you should see `EV_REL REL_X`
and `EV_REL REL_Y` events.

The debug log (written when `--debug` is active) prints one line per mouse
frame:

```
14:05:01 mouse pos (42, -17)
14:05:01 mouse pos (89, -31)
```

### Keyboard

```bash
evtest /dev/input/by-name/pykvm-keyboard
```

---

## Network diagram (remote case)

```
maple@192.168.9.34                          maple@192.168.9.33
┌────────────────────────┐                  ┌───────────────────────────────┐
│  Physical kbd + mouse  │                  │  QEMU process (maple)         │
│         │ grabbed      │   TCP :5900      │  ┌─────────────────────────┐  │
│  pykvm-server :5900 ───┼─────────────────►│  │ VM (NixOS + XFCE)       │  │
│                        │                  │  │  pykvm-client           │  │
│  $ just server         │                  │  │  uinput kbd + mouse     │  │
│                        │                  │  └─────────────────────────┘  │
│  VNC viewer ◄──────────┼──────────────────┼── VNC :5900                   │
│  (XFCE desktop)        │                  │                               │
└────────────────────────┘                  └───────────────────────────────┘
                          SSH :2223 (to VM root) ──────────────────►
```

---

## Troubleshooting

### `nix copy` fails with permission error

Add `maple` to `trusted-users` on the remote machine:

```nix
nix.settings.trusted-users = [ "root" "maple" ];
```

Or use `root@192.168.9.33` as the `VM_HOST` user by editing the recipe.

### VNC shows a black screen

The VM is still booting or LightDM is starting.  Wait ~15 seconds and
reconnect.

### Mouse cursor does not move in XFCE

1. Confirm the client is connected (`just vm-dev-desktop-ssh`, then
   `tail -f /tmp/pykvm.debug.log`).
2. Confirm the server is in remote mode (server log shows `→ remote`).
3. Check that libinput sees the virtual mouse:
   ```bash
   libinput list-devices | grep pykvm
   ```
   See [mouse-support.md](mouse-support.md) for details.
