# Holo3.1 Hardware Integration — Findings (Phases I0–I5 + vendor-alignment pass)

Status as of 2026-07-17. Covers the full arc from HOLO_INTEGRATION_PLAN.md's Phase I0
through I5, plus a post-I5 vendor-alignment pass and a latency investigation triggered by
a live warning. All phases below are DONE and verified live against the real rig (not
simulated/offline) unless noted otherwise. Superseded by nothing yet; this is the current
state.

## Recommendation: GO, with one topology change from the original plan

The full loop works, live, screen-verified (not self-reported): real HDMI capture → real
Holo3.1 model call → real coordinate projection → real Pico HID → real Windows VM action,
correctly, across single-step and multi-step tasks including correct completion signaling.
The one thing that did NOT work is iGPU passthrough for the VM's display — abandoned in
favor of a different, working approach (below). Everything downstream of the display seam
is unaffected by this change.

---

## Topology — as built (differs from HOLO_INTEGRATION_PLAN.md's "locked" topology)

The original plan locked iGPU → VM, B580 → host display, on the theory that iGPU
passthrough is well-documented enough to de-risk. It was not, on this hardware. Actual
working topology:

| Component | What it is | Notes |
|---|---|---|
| Host | Linux desktop, Arc Pro B70 (Holo inference) + Arc B580 (host display) | iGPU (UHD 770, `00:02.0`) is bound to `vfio-pci` but **not used** — passthrough attempted and abandoned, see below |
| VM | `win11-agent`, libvirt/QEMU, q35, UEFI (**Secure Boot disabled**), 4 vCPU / 8GB RAM / 100GB disk | Secure Boot had to be turned off — see Code 52 finding |
| VM display | QXL/SPICE, fullscreened via `virt-viewer` onto a 3rd physical monitor connected to the **B580** (not passed through — host-driven) | 1920×1080, matches capture resolution exactly |
| Capture | USB3 HDMI capture card (Macrosilicon), read via **V4L2** on the Linux host | `/dev/video0`, `cv2.CAP_V4L2` |
| Input | Raspberry Pi Pico 2 W, **whole USB device** passed through to the VM via libvirt `hostdev`, controlled over WiFi/TCP | Current IP `192.168.0.224` (DHCP — see open item) |
| Holo3.1 | Served locally, same host, via llama-swap + llama.cpp (SYCL, Arc Pro B70), Q4_K_M | `127.0.0.1:9292/v1`, model id `holo3.1` |

**Net effect vs the original plan:** the iGPU is not doing anything (still `vfio-pci`-bound,
inert). The B580 drives a real monitor connected to the capture card, and the VM's own
display gets mirrored onto it via SPICE fullscreen — a software bridge instead of a
hardware one. Functionally equivalent for this project's needs; the two-model-split
upside of freeing the B580 is deferred (B580 is doing double duty: host display +
future-reasoning-model candidate, same as before, just also carrying this monitor).

---

## Phase I0 — firmware + holo.py in repo

Done. Firmware (`boot.py` v4/v5 — Report ID 2, `code.py` — WiFi resilience + Caps-Lock
self-correct) and `holo.py` (ported from the separate `computer-use` bring-up worktree)
committed to this repo. Two real bugs found and fixed along the way, unrelated to Holo
itself:

- **`.gitignore`'s `models/` pattern was unanchored** and silently matched
  `kvm_agent/models/` as well as the intended top-level 35GB blob directory. Fixed to
  `/models/`. Consequence: `kvm_agent/models/{evocua,uitars,factory,__init__}.py` had
  **zero git history** — recovered in a separate commit.
- A live Anthropic API key was found hardcoded as a plaintext default in
  `kvm_agent/config.py` (uncommitted at the time). Flagged to Aaron; left in place per his
  explicit call.

## Phase I1 — VM + display + capture

**iGPU passthrough: attempted, root-caused, abandoned.** Code 43 ("Windows cannot verify
the digital signature...", i.e. driver-init failure) on the UHD 770 persisted through:
- `x-igd-opregion` confirmed on by default (`vfio-pci,help`) — no override needed.
- `qemu:override` (libvirt's modern per-device property mechanism) confirmed **completely
  non-functional** in this libvirt build — proved by explicitly setting a property to the
  opposite of its default and observing no change via QOM. Not a syntax issue; the
  mechanism itself doesn't apply anything.
- The older `-set device.<id>.<prop>` escape hatch doesn't work either, because modern
  libvirt emits JSON-style `-device '{...}'` args, which don't create the legacy QemuOpts
  group `-set` needs. **Working substitute found:** `-global driver.property=value` via a
  raw `<qemu:arg>` — applies to a driver class, side-steps the id-lookup problem entirely.
  (`x-igd-gms=2` applied this way; no effect on Code 43.)
- Legacy `igd-passthru` QEMU machine mode (the plan's other suggested fallback) confirmed
  **removed from this QEMU build entirely** — not in the machine-type help for either q35
  or i440fx.
- Extracted a candidate GOP driver module from the ASUS Z790 GAMING WIFI7 BIOS capsule
  (`uefi_firmware` + `uefiextract`/`UEFITool`; a 16KB PE containing `$VBT`-checking code)
  and wired it in as `romfile=`. This surfaced **two more real libvirt bugs**: neither DAC
  ownership relabeling nor the per-VM mount namespace covers `<hostdev><rom file>` paths
  (both cover `<disk>`/`<nvram>` but not this) — worked around by briefly attaching the
  same file as a throwaway floppy `<disk>` to force both, then removing it. Even with the
  ROM genuinely loading, Code 43 persisted — the raw PE lacks a proper PCI Option ROM
  header (0x55AA + PCI Data Structure), which wasn't built. **Decision: stop here, pivot
  to the SPICE-fullscreen approach** rather than hand-construct a ROM header for an
  unconfirmed candidate module.

**SPICE-fullscreen-to-monitor: works, with two more real bugs found and fixed:**
- SPICE guest agent (clipboard, auto-resize) installed but the underlying virtio-serial
  channel showed `disconnected` (confirmed via QMP `query-chardev`,
  `"frontend-open": false`) even though the Windows service showed "Running". Same two
  libvirt gaps as above (DAC relabel + mount namespace not covering `<hostdev><rom file>`
  — this time for a *different* channel/config combination) — same floppy-attach
  workaround fixed it.
- Once past that: **VirtIO Serial Driver showed Code 52** (unsigned/test-signed driver
  blocked by Secure Boot) in Device Manager. Fixed by switching the VM's OVMF firmware +
  NVRAM to the non-secure-boot variant (clean boot, no BitLocker complications since
  BitLocker was never enabled). Clipboard and auto-resize both confirmed working after.
- GNOME/Wayland specifics: `xrandr`/`wmctrl`/`xdotool` don't control real monitor
  state under Wayland (Xwayland compat layer only) — real monitor config went through
  GNOME's `org.gnome.Mutter.DisplayConfig` D-Bus interface (read-only, for verification);
  targeting `virt-viewer`'s fullscreen at a *specific* physical monitor (not whichever one
  the window happens to open on) required its documented `monitor-mapping` config
  (`~/.config/virt-viewer/settings`, keyed by the domain's libvirt UUID) — determined the
  correct monitor index via GDK's own monitor enumeration rather than guessing.

**Acceptance met:** capture card reads a clean, correctly-sized 1920×1080 frame matching
the VM desktop exactly (verified via direct V4L2 capture, not just `virsh screenshot` —
worth noting `virsh screenshot`'s QXL framebuffer grab **never shows the OS cursor
sprite**, in either capture path; verification throughout this project had to be by
state-change, not cursor-position, screenshots).

## Phase I2 — Pico HID over WiFi

Done. All primitives verified against the live rig via the real hardware path (WiFi → Pico
→ USB HID → passed-through USB → Windows): move (precise, repeatable across the whole
screen), click, right-click (real context menu), type (exact text, correct case, zero
dropped characters), combo (Ctrl+A selection). One environment fix needed:
`kvm_agent/hardware/env.py`'s `Camera` class hardcoded Windows-only `cv2.CAP_MSMF` —
made platform-aware (`CAP_V4L2` on Linux), since this rig is now Linux-hosted. Also found
the Pico's IP had drifted from the `CFG.pico_ip` default (`192.168.0.183` → actual
`192.168.0.224`) — see open items.

## Phases I3 + I4 — coordinate closure + first live action

Done, in one motion. A live Holo3.1 call against a real captured frame ("Click the
Recycle Bin icon") correctly identified the target and projected `[38.4, 43.2]` — matching
the manually-verified working coordinate from I2 testing. Sent through the Pico, the
Recycle Bin opened. This closes I3's coordinate round-trip *and* I4's "one model-decided
click on a live target" bar simultaneously, since it wasn't a synthetic/hand-picked
coordinate — Holo made its own grounding decision from a live frame.

## Phase I5 — multi-step loop + a real finding

Implemented real history threading in `agent_loop_holo.py`'s `run()` (previously
single-shot/history-less by design, honestly documented as such). First live 6-step run
(search → type → launch Notepad) worked *mechanically* perfectly — real API calls each
step, correct history accumulation, no crashes, no dropped actions — and **functionally**:
a new Notepad window genuinely opened and received typed text, confirmed via the capture
feed.

**The finding:** the model never called `answer`/`finished` to signal completion — it
kept re-attempting variations of the same action after the task was arguably already done,
so `run()` correctly reported "didn't recognize success" rather than falsely claiming
victory. Root cause, confirmed by reading the original bring-up's own `HOLO_TESTING_PLAN.md`:
completion signaling was **explicitly out of scope for the entire bring-up phase**
("No full agent loop / multi-step task execution yet... The loop comes after grounding
rates are known") — so this was genuinely untested territory, not a regression.

---

## Vendor-alignment pass (post-I5)

Aaron asked what H Company's own docs say about signaling completion. Fetched
`hub.hcompany.ai/agent-loop` and `/element-localization` directly (verbatim, not
paraphrased) and diffed against `kvm_agent/models/holo.py` / `agent_loop_holo.py`.
**Coordinate formula: exact match, nothing to fix.** Everything else had drifted:

| Gap | Fix |
|---|---|
| `tool_choice` never set (defaulted to `"auto"`) — docs' literal documented cause of "tool calls come back as plain text" | `tool_choice="required"` |
| `temperature=0.0`, no thinking configured — this is the doc's config for the **separate**, stateless `element-localization` endpoint; the actual agent-loop example uses `temperature=0.8` + thinking on ("essential in agent mode... leave it on") | `temperature=0.8`, `enable_thinking=True` via `extra_body={"chat_template_kwargs": {...}}`, defaults on `call_holo`/`call_holo_full` |
| No `<observation>...</observation>` wrapper around screenshot turns (both documented chat-layout tables use it) | `observation_message()` helper, used by `build_messages()` |
| Tool-result content hardcoded to `"ok"` regardless of outcome — docs flag exactly this as the cause of "loops, forgets earlier facts" | Real per-step frame-diff signal (`_frame_changed()`): `"Action executed. Screen changed."` / `"...did not visibly change."` |
| No screenshot history at all (docs: keep last 3, evict older to `"[screenshot evicted]"` text) | `trim_to_last_n_images()`, `MAX_HISTORY_IMAGES = 3` |
| Tool descriptions (`click`/`write`/`answer`) diverged from vendor wording | Matched verbatim; `scroll`/`drag_and_drop` kept as noted, unverified extensions (not in any official example) |

Also confirmed: no public reference agent-loop implementation exists beyond the doc page
itself — the only related GitHub repo (`hcompai/holo-desktop-cli`) just drives a
closed-source runtime binary, no prompt/schema logic published there.

**Re-test after the fix, from a fully cleared desktop:** "Open Calculator, compute 7×8,
confirm the result." 7 steps, **zero dropped/error actions**, correctly launched via
search, computed the answer, and **called `answer` immediately** with an accurate,
specific confirmation ("...the computation 7 × 8 = 56 has been completed. The result 56
is now displayed..."). `run()` correctly returned `True`. **Screen-verified**, not
self-reported: captured the live frame afterward — Calculator genuinely shows `7 × 8 =`
and `56`.

---

## Latency investigation (triggered by a live warning, not yet acted on)

Aaron noticed `llama-swap` logging `"capture N too large (N bytes), skipping: item
exceeds maximum cache size"` and asked whether we're sending uncompressed/full-size
captures. Findings:

- Confirmed via `strings` on the `llama-swap` binary that this warning is **llama-swap's
  own internal request/response debug-capture feature** hitting a hardcoded size cap —
  unrelated to the actual inference path. Does not affect correctness (the Calculator test
  ran clean through the same pipeline).
- We *do* send full 1920×1080 resolution, every step, PNG-encoded (lossless, not raw) —
  measured 2.14MB PNG / 2.86MB base64 for a real capture from this VM.
- Rigorous token-level test (controlling for the temperature=0.8 reasoning-length
  confound by reading `usage.prompt_tokens`/`completion_tokens` directly, not just wall
  time): **PNG vs JPEG at the same resolution produced byte-identical `prompt_tokens`**
  (2842 = 2842) — image format has zero effect on vision-token count or local processing
  speed; only network transfer size, which is irrelevant on loopback. **Resolution does
  matter**: 960×540 (1/4 the pixels) used ~35% fewer prompt tokens (1834 vs 2842).
- **Bigger latency lever than either of those**: `completion_tokens` (the reasoning trace,
  generated token-by-token, sequential) varied 90–154 across otherwise-identical calls,
  and one earlier untimed call took 30.5s for reasons unrelated to image size at all.
  Reasoning-length variance at `temperature=0.8` looks like the dominant source of
  per-step latency variance right now, more so than image size or format.
- **Not acted on** — left as a documented note (in `holo.py`'s `call_holo_full`
  docstring) for Phase I6 if latency tuning is ever needed. If it is: downscaling
  resolution is a real, evidence-backed lever (with a grounding-accuracy tradeoff);
  switching to JPEG is not worth doing.

---

## Hygiene + instrumentation + battery pass (session, later on 2026-07-17)

Per PROJECT_GUIDANCE_holo.md's suggested sequence: hygiene, then instrumentation, then a
custom task battery (§3.1-3.2's "start small" option, not a WindowsAgentArena/OSWorld
import). All landed on `refactor/packaging` as separate reviewable commits.

- **Hygiene done**: Anthropic key moved out of `kvm_agent/config.py` into a gitignored
  `.env.local` (env-var override still wins); Aaron confirmed the exposed key was already
  deactivated vendor-side. `CFG.pico_ip` default updated to `.224`. Working tree (17
  modified + ~26 untracked files accumulated across the whole arc) committed into 9
  logical commits. DHCP reservation for the Pico is still Aaron's to do.
- **Instrumentation**: `kvm_agent.instrumentation.RunRecorder`, wired into
  `agent_loop_holo.run()` (default on), writes every step's pre-action frame, raw
  message, parsed action, token usage, and wall time to
  `CFG.runs_dir/<tag>_<timestamp>/`, plus a `summary.json`. Along the way found and fixed
  `CFG.runs_dir` defaulting to a stale Windows path (`C:\Dev\vllm\runs`) left over from
  the pre-Holo topology — silently pointed nowhere useful on this Linux-hosted rig.
- **Task battery**: `kvm_agent.battery` — 8 tasks (2 core regression-floor + one/two per
  the five coverage categories from PROJECT_GUIDANCE_holo.md §3.2: scroll/drag_and_drop,
  long-horizon/history-eviction, wait-type, deliberately-impossible, small/dense target).
  Every task graded independently of the model's own completion signal (existing
  `executive.Verifier`, OCR or vision Q&A against the final frame) — self-report alone
  was Phase I5's original failure mode. `runner.py` + `tests/test_battery.py` (offline,
  all pass). **Not yet run against the live rig** — built and offline-tested only.
- **Rig brought up for the first time this session** (see below) — confirmed working:
  Holo3.1 (`llama-swap :9292`), capture card (1920x1080, matches `CFG.screen_size`
  exactly), VM display (SPICE fullscreen via `virt-viewer`, landed on the correct
  monitor via the saved `~/.config/virt-viewer/settings` UUID mapping), and the Pico.
- **New bug found + fixed: VM's USB hostdev was pinned to a stale bus:device address.**
  `virsh start win11-agent` failed: `Did not find USB device 239a:8162 bus:1 device:9` —
  the Pico currently enumerates as `bus 1 device 4` (device numbers aren't stable across
  replugs/reboots; same class of drift as the IP issue above). Fix: stripped the
  `<address bus='.../>` line from the `<hostdev><source>` block in the domain XML
  (`virsh dumpxml` → edit → `virsh define`), leaving only `<vendor id>`/`<product id>`, so
  libvirt matches by VID:PID instead of a numeric address that drifts. Not yet upstreamed
  anywhere / not a libvirt bug (this one's a config choice, unlike the two real libvirt
  bugs above) — just noting the fix so it isn't rediscovered cold.
- **New open item: the Pico periodically stops answering on WiFi and needs a physical
  replug to recover** — flagged by Aaron this session. Confirmed NOT caused by the USB
  hostdev address fix or VM start (rig.py's very first health check of the session
  already showed it unreachable, before the VM was touched). After Aaron replugged it
  physically, `ping` + a real TCP connect (`agent_loop_holo.boot()`) both succeeded
  immediately. Distinct from the known IP-drift item below — this is about the device
  going unresponsive on the network at all, not about which IP it's on. Root cause
  unknown (WiFi radio sleep/hang? CircuitPython WiFi stack issue?) — worth investigating
  if it recurs during a battery run, since an unresponsive Pico mid-run silently kills
  a task rather than erroring loudly.
- **Second new bug: a *live* USB hot-replug orphans the VM's passthrough binding.**
  After the fix above, Aaron physically replugged the Pico while the VM was already
  running. `virsh dumpxml` still showed the OLD bus:device (libvirt only re-resolves
  vendor:product -> bus:device at attach time, not continuously) — the VM was holding a
  now-stale USB path. Symptom was confusing: QEMU-level `info usb` showed the device
  attached and the keyboard HID interface worked, but the mouse HID interface did not
  (zero cursor movement, zero click effect, verified with a clean test after confirming
  Aaron was no longer touching the VM directly — see below). Hot detach/reattach
  (`virsh detach-device`/`attach-device --live` with a vendor/product-only fragment) did
  NOT fully fix it (mouse stayed dead); a full `virsh shutdown` + `virsh start` (cold
  boot, clean USB re-enumeration) did. **Rule of thumb going forward: after ANY physical
  Pico replug, fully reboot the VM — don't rely on hot detach/reattach.**
- **Gotcha: virt-viewer's own SPICE input forwarding is a SEPARATE path from the Pico.**
  QEMU's `info usb` showed two distinct input devices on the guest: `hostdev0` (the real
  Pico, USB HID) and a virtual `QEMU USB Tablet` (`input0`, the default absolute-pointer
  device SPICE clients use). If a human interacts with the virt-viewer window directly
  (mouse/keyboard) while the Pico is also being driven programmatically, both land on the
  guest and are indistinguishable from a single capture frame — this caused real
  confusion this session (a Calculator window's state was misattributed to a Pico click
  that actually did nothing). When debugging the Pico specifically, confirm no one is
  touching the virt-viewer window directly first.
- **First login to the VM's `sandbox` account happened this session** — it's a distinct
  local Windows account from `Aaron Hladky` (his personal account, PIN-protected, not
  for agent use). `sandbox` required a full first-time Windows OOBE flow (privacy
  settings, etc., "This might take a few minutes") since it had never been logged into
  before — now done, clean desktop reached. Use `sandbox` for rig/agent work going
  forward, never guess at or attempt `Aaron Hladky`'s PIN.
- **Grading backends were both down for the first live battery run**: `tesseract` is not
  installed on this Linux host (no passwordless sudo available to fix it from an agent
  session) and the Ollama laptop (`192.168.0.155:11434`, hosts the vision-verify model)
  was unreachable all session. Both `Verifier.has_text`/`read_number` (OCR, tesseract) and
  `Verifier._vision` (Ollama) degrade to `None` ("can't verify") when their backend is
  down — by the battery's fail-open design this means `correct` currently reflects the
  model's self-report alone for any task whose grader came back `None`, which is exactly
  the gap the battery was built to avoid. Not fatal: `RunRecorder` saves every step's raw
  frame regardless, so grading can be redone retroactively once tesseract/Ollama are
  fixed, without re-running anything live.
- **Bug found + fixed: `run_battery()` never released the camera/Pico.** First live
  battery task (`calc_basic`) completed correctly (7 steps, correct math, correct
  `answer` call, matches the Phase I5 re-test) but the process then hit `SIGABRT`
  ("exception not rethrown") at interpreter exit. Isolated repros of Camera+Pico
  together didn't reproduce it, so root cause is presumed to be an unclean native
  teardown race after ~130s of real cv2/network activity with the resources still open —
  not a correctness issue (the summary was already written to disk before the crash).
  Fixed regardless: `run_battery()` now calls `agent_loop_holo.shutdown()` in a `finally`
  block when it owns the live backend.

## First live battery run — results (2026-07-17, `sandbox` account, 1080p)

All 8 tasks run unattended (`confirm_first=0`) against the fresh `sandbox` desktop.
**4/8 correct** (`calc_basic`, `notepad_type`, `wait_settings_system`, `impossible_app`) —
but treat this number as provisional: grading was degenerate (tesseract missing, Ollama
unreachable — see above), so every `correct` here reduces to self-report / not-self-report,
not an independent screen check. Full logs: `runs/battery_20260717_181807/` (calc_basic)
+ `runs/battery_20260717_182303/` (the other 7) and their per-task `RunRecorder` dirs.

| Task | Result | Steps | Note |
|---|---|---|---|
| `calc_basic` | PASS | 7 | Clean; matches the Phase I5 re-test exactly. |
| `notepad_type` | PASS | 5 | Clean, no wasted steps. |
| `scroll_to_about` | FAIL (budget) | 14/14 | Oscillated between System and re-scrolling; never found/clicked About. |
| `drag_file_to_desktop` | FAIL (budget) | 16/16 | Never attempted a drag at all — repeatedly re-clicked sidebar entries (Documents/Downloads/Pictures/...) with no progress. Also the task precondition (a file in Documents) wasn't met on the fresh `sandbox` profile, so this run couldn't have succeeded regardless. |
| `long_horizon_recall` | FAIL (budget) | 20/20 | **Notable near-miss**: correctly recalled `4471` and computed `5471` many steps/screenshots later (typed `5,471` into Notepad, step 17) — the actual eviction-recall capability being tested appears to have worked — but never called `answer` within budget; spent steps 15-19 on cursor-positioning indecision after typing. Worth a re-run with a larger step budget before concluding anything about recall itself. |
| `wait_settings_system` | PASS (contaminated) | 1 | **Not a real pass** — Settings was already open from the prior task's leftover state (no reset between tasks), so the model correctly but trivially reported "already open." The battery has no `reset_clean` step yet (noted as a known gap in `kvm_agent/battery/tasks.py`'s docstring); this is the concrete case of it mattering. |
| `impossible_app` | PASS (weak) | 8/8 | Correct by the letter of the grader (never falsely called `answer`) but not for the intended reason: it got sidetracked opening a browser search result for "Photoshop" and ran out of steps mid-cleanup, rather than explicitly concluding the app isn't installed. `expect_answer=False`'s current logic (`correct = not finished`) can't distinguish honest refusal from running out of budget while flailing — a real scoring gap worth tightening later. |
| `small_target_tray` | FAIL (budget) | 6/6 | Six clicks at slightly different coordinates within a ~15px cluster around the tray icon, never registering success. Consistent with the small-target grounding-accuracy concern this task was designed to probe, but can't fully separate "misground" from "grounded correctly but flyout-detection failed" without independent grading. |

**Takeaways for next time:**
1. Fix grading (tesseract and/or Ollama) and re-grade from the saved frames before
   trusting these numbers, especially `wait_settings_system` and `impossible_app`.
2. Add a reset-between-tasks step (or randomize/isolate state some other way) —
   `wait_settings_system`'s contamination is a direct, reproduced demonstration of why
   the battery's own docstring flagged this as a gap.
3. `long_horizon_recall` and the scroll/drag tasks may just need a larger step budget
   (14-20 wasn't enough for any of them) before concluding anything about capability.
4. Tighten `expect_answer=False` scoring to require an explicit give-up signal, not just
   "didn't finish."
5. Seed a file in `sandbox`'s Documents folder before ever re-running `drag_file_to_desktop`.

## Open items / not yet done

- **Phase I6 (latency/robustness tuning)** — optional per the plan, only if per-step
  latency needs to come down. Candidate lever identified above (resolution), not applied.
- **Phase I7 (task battery)** — first live run happened this session (`calc_basic` +
  the remaining 7 tasks); see the battery run's own summary for results. Grading was
  degraded (see below) so re-grading from saved frames once tesseract/Ollama are back is
  worth doing before trusting the `correct` numbers.
- **Grading backends down**: `tesseract` not installed on this host (no passwordless
  sudo from an agent session — install manually: `apt-get install tesseract-ocr`), and
  the Ollama laptop (`192.168.0.155:11434`) unreachable all session. Fix either (or
  both) and re-grade the saved battery frames before trusting any `correct` numbers from
  tonight's run.
- **After any physical Pico USB replug, fully reboot the VM** (`virsh shutdown` +
  `virsh start`) rather than a hot detach/reattach — the latter left the mouse HID
  interface dead this session even though QEMU showed the device attached. See above.
- **iGPU passthrough** — abandoned for now, not fixed. Would need either a properly
  PCI-Option-ROM-headered GOP driver (nontrivial binary construction on an unconfirmed
  candidate module) or a different approach entirely. The current B580+SPICE-fullscreen
  setup is a full functional substitute; only the two-model-split upside (freeing the B580
  for a future reasoning model) remains blocked on this.
- **Pico IP is DHCP-assigned and has already drifted once** (`192.168.0.183` →
  `192.168.0.224`) — `CFG.pico_ip`'s default was updated to match, but no DHCP
  reservation exists yet (Aaron's to do). Separate from the "needs a physical replug"
  item above.
- **Pico periodically stops answering on WiFi, needs a physical replug** — see above;
  root cause not yet investigated.
- **`scroll`/`drag_and_drop` tools remain unverified** against any vendor reference or
  live test in this integration phase — the task battery's `scroll_to_about` and
  `drag_file_to_desktop` tasks target exactly this, once the battery actually runs.
- **The hardcoded Anthropic API key** — RESOLVED this session (see above): moved to
  `.env.local`, and the exposed key is confirmed deactivated.

## Files changed this arc

`boot.py`, `code.py` (firmware, committed), `kvm_agent/models/holo.py` (new, then
substantially revised for vendor alignment), `agent_loop_holo.py` (new, then revised for
history threading + vendor alignment), `kvm_agent/hardware/env.py` (Camera platform fix),
`kvm_agent/models/{evocua,uitars,factory,__init__}.py` (recovered, never-tracked),
`.gitignore` (anchoring fix), `docs/FINDINGS_holo_bringup.md` + `docs/FORMAT_NOTES_holo.md`
(ported reference docs), this file.
