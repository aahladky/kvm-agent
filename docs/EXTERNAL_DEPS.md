# External dependencies (things that can't live in this repo's filesystem tree)

Written 2026-07-19 alongside `docs/PROJECT_LAYOUT.md`. Two things this project depends on
physically cannot move into this repo — documented here instead of being tribal knowledge.

## Pi 5 HID appliance (`192.168.0.29`)

Runs `hid_bridge.py` as a systemd service (`hid-bridge.service`) bridging host HTTP
requests to the Pico over wired UART. Deployed manually (scp + `systemctl restart
hid-bridge.service`) — there is currently no automated deploy script; if `appliance/pi5/
hid_bridge.py` changes in this repo, it has to be re-copied to the Pi 5 by hand and the
service restarted, or the running service silently keeps serving the old code.

**Log location:** writes its own copy of wire-level HID command logs (what was sent, the
Pico's decoded ACK, target kbd/mouse-online state) to `/home/aaron/hid_bridge_commands.jsonl`
**on the Pi 5 itself** — the `--log` default in `hid_bridge.py`. This is the same class of
data as the host-side `CFG.logs_dir/appliance_client_commands.jsonl`, just captured at the
appliance end instead of the host end.

**Pulling it down:** `tools/pull_pi5_logs.py` — scp's the Pi 5's log to
`CFG.logs_dir/pi5_hid_bridge_commands.jsonl`. Manual/on-demand, not scheduled — run it
whenever you need to cross-reference wire-level appliance state with the host-side log.

```bash
python3 tools/pull_pi5_logs.py
```

## WindowsAgentArena clone (`/home/aaron/workspace/WindowsAgentArena`)

A separate git clone of Microsoft's upstream
[`WindowsAgentArena`](https://github.com/microsoft/WindowsAgentArena) — `waa/runner.py`'s
actual Python interpreter is `/home/aaron/workspace/WindowsAgentArena/.venv/bin/python3`,
and `waa/runner.py` imports `desktop_env` modules straight out of that clone's
`src/win-arena-container/client` directory.

**Why it stays separate:** foreign git history, far too large/foreign to merge into this
repo. This is a real, deliberate boundary, not an oversight.

**Local patch:** that clone carries one uncommitted local modification —
`src/win-arena-container/vm/setup/server/main.py` — captured as
`docs/waa_local_patch.diff` (committed in this repo, since the external clone's own
working tree is where an accidental `git checkout .` would silently lose it). The patch
suppresses a `sys.stdout is None` crash under `pythonw` (no console) and adds
`CREATE_NO_WINDOW` to every `/execute` and `/setup/launch` subprocess spawn, since the
default console-subsystem child was popping/reusing a visible Windows Terminal window on
the guest desktop in literally every captured frame of every WAA task run.

**Reapplying after a fresh clone or an accidental revert:**
```bash
cd /home/aaron/workspace/WindowsAgentArena
git apply /path/to/this/repo/docs/waa_local_patch.diff
```
