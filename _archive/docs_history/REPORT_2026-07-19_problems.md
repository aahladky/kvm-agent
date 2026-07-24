# Problem Report — Holo 3.1 computer-use rig → WAA adoption (2026-07-18/19)

Covers the arc from the first 8-task battery through the adoption of WindowsAgentArena
(WAA) as the evaluation harness. Three classes of problems: **infrastructure** (fixed),
**harness** (fixed), and **model** (residual, genuine). Also a section of **false leads**
— diagnoses made and later disproven by direct test, kept for honesty.

Bottom line: the environment is now *verified* working end-to-end (not assumed). The
remaining failures are the model's own grounding/recovery limitations. All evaluation is
now deterministic (WAA's file/state getters); nothing is VLM- or self-graded anymore.

---

## 1. Infrastructure problems (all fixed)

| # | Problem | Evidence | Fix |
|---|---------|----------|-----|
| I1 | **Model shuffle during runs**: verifier graded via `gemma4-dense` (a 31B model) on the same B70 GPU as holo3.1 — llama-swap evicted/reloaded ~21GB **16 times in 45 min** (one per grading call + one per task start). ~32s first steps, ~52s grading. | `journalctl -u llama-swap`: `matrix: evict=[holo3.1] target=[gemma4-dense]` | Verifier repointed at resident `holo3.1` itself (`verifier_local_model`). Confirmed 0 evictions next run. (commit `df1e7d9`) |
| I2 | **Dead mouse collection after resets**: the composite HID device can come up keyboard-alive/mouse-dead. The NumLock LED check only proved the *keyboard*; runs burned clicks into a dead mouse and read as model failure. | Repeated Start-menu icon clicks with zero effect; `pikvm_proto.probe()` exposes per-collection flags that the bridge wasn't surfacing | `hid_bridge` now reports `kbd=`/`mouse=` in `/hid/probe`; `revert_clean` requires both online + NumLock round-trip, retries one cold reboot, else raises. (commit `d332f62`) |
| I3 | **Contaminated "clean" snapshot**: Windows 11 Notepad session-restore reopened old unsaved tabs ("STAGE3/5/6" appliance-test content), spawning multiple Notepad windows that split typed text mid-task. | Battery `notepad_type` frame: text split across two Notepad windows | Closed all leftover state; Notepad set to "Start new session and discard unsaved changes"; snapshot recreated |
| I4 | **Shell not input-ready early post-boot**: Windows draws Start/search ~30–60s before it *accepts* input on them. Tasks starting ~90–150s post-boot had shell clicks silently swallowed. | Runs where Start opened but menu-item clicks/typing did nothing; identical sequences worked minutes later | `wait_shell_ready()` — functional probe (open Start, type a char in-guest, require the search box to receive it). UIA button *presence* was tried first and is NOT sufficient (passes while input is still eaten) |
| I5 | **OCR grading silently dead**: `pytesseract` (the module) was missing even after `tesseract-ocr` (the binary) was installed; `calc_basic` graded "unverified". | `grading_backends: {tesseract: false}` | `pip install pytesseract` |
| I6 | **OCR destroyed by 720p evidence frames**: after the 720p downscale (a deliberate model-input token saving), graders received the same 720p frames — tesseract returned garbage. | `pytesseract.image_to_string` on a 720p calc frame: `"Bice cout BE searen"` | Split paths: model input 720p, **evidence frames full-res** (`_frame_png_full` for grading/verify/reference) |
| I7 | **Stale reference frame after pipeline change**: the reset verify compared a 1080p reference against 720p captures; the different resize filter chain shifted high-frequency wallpaper texture by 5–16/tile → every reset failed verification. | Tile heatmap: diff everywhere, concentrated in the water-foam area | Recapture reference whenever the capture pipeline changes (now full-res) |
| I8 | **Weather-widget false positive**: reset verify's tile-max metric flagged clock/weather taskbar churn (diff 6–12) as a broken reset. | Top-diff tiles all in the bottom-left taskbar strip on a visibly clean desktop | `_frame_diff_score(..., drop_bottom_row=True)` for reset verify |
| I9 | **WAA server install friction**: `import Xlib` crash (Linux-only dep), `pythonw` crash (`sys.stdout` is None in their logger), firewall blocked :5000, autostart batch silently never written (broken shell quoting). | server.log tracebacks; "Access is denied" on firewall rule (sandbox is a standard user) | Patched both crashes upstream-side (guarded imports, None-safe logger); user ran one elevated firewall command; batch verified on disk |
| I10 | **virt-viewer dies on every revert** (by design of SPICE) — capture card then shows the *host* desktop, and tasks ran against it before the hard-fail existed (diff 191.8). | Host GNOME desktop in the capture feed mid-battery | `revert_clean` relaunches virt-viewer, retries 3×, then **raises** — no task runs on an unverified display (commit `5da01fd`) |

## 2. Harness problems (all fixed)

- **H1 — No-progress aborts fired falsely.** Our own `frozen screen`/`same click` aborts killed tasks that were recoverable or already done (a WAA notepad task was aborted at step 4; it completed the same flow 20 minutes later). WAA's harness gives the full step budget; our aborts are now **disabled for benchmark runs** (`no_progress_abort=False`, commit `69b603d`).
- **H2 — Warn-and-continue on unverified display** (see I10) — replaced by hard-fail.
- **H3 — 8-task battery could never answer the real question.** Hand-rolled tasks + VLM self-grading + metric churn produced un-calibrated, untrustworthy numbers. Replaced by **WindowsAgentArena**: 154 tasks, deterministic per-task setup scripts, deterministic file/state evaluators (e.g. `vm_file_exists` + `compare_text_file` vs gold files). Their in-VM server runs in `win11-agent` (autostart baked into the snapshot); actions still go through our HID appliance — the KVM constraint is what's measured. First task scored 1.0 on deterministic checks (commit `d332f62`).

## 3. Model problems (residual — genuine, environment-exonerated)

With the environment verified, these remain. They are Holo 3.1's current limitations, not plumbing:

- **M1 — Grounding misses on small text menus.** Aimed ~120px low on Notepad's `File` menu (clicked the document body at y=249 vs the menu at y=128) — the save flow never started. Note: on large tile icons (Start-menu pinned apps) grounding is 5–11px accurate at both 720p and 1080p (measured A/B).
- **M2 — Icon misidentification on a cluttered taskbar.** Clicks Outlook/Copilot while narrating "Settings gear" / "Paint icon" (the current snapshot pins Copilot/Outlook/Terminal/Widgets). Each misclick opens a wrong window and compounds.
- **M3 — Double-click uncertainty.** Clicks the search box twice in a row; the second click closes the panel the first opened, then it types into nothing. The "screen changed" tool-result signal does not deter this.
- **M4 — Step-budget burn.** 20+ of 40 steps spent on app-launch recovery loops, leaving too few for the actual workflow (Settings subpages, save dialogs).
- **M5 — No refusal behavior.** On impossible tasks it flails to the step cap rather than explicitly giving up (expected — its action space has no FAIL action; refusal must be judged from answer text).

## 4. False leads chased (disproven — kept for honesty)

- **"720p downscale hurts grounding"** — disproven by controlled A/B on the same Start-menu frame: 5–11px error at both 1080p and 720p.
- **"Move/click race"** (click landing at the pre-move cursor position) — disproven: 8/8 launches back-to-back vs delayed; in-guest `pyautogui.position()` shows moves land to within 1px.
- **"Capture card stale / desynced from guest"** — disproven: capture card and in-guest screenshot agree pixel-for-pixel.
- **My own 9/9 "dead clicks"** — I was clicking *stale layout coordinates* (a dead zone between tiles) manually, not an HID failure. Ground truth must always be measured from a current frame.

## 5. Current state

- Serving: holo3.1 via llama-swap on the B70, no eviction churn; verifier = resident holo3.1.
- Latency: ~4–5s per agent step (was 13–34s) after goldfish memory (1 screenshot), 720p model input, smart settle (commits up to `11a5b97`).
- Eval: WAA runner (`waa/runner.py`), 154 tasks, deterministic grading, per-task clean-desktop revert + shell-readiness gate + dual-collection HID check.
- A 17-task shakedown (notepad/calc/paint/clock/settings) is the calibration run; then the full 154.

## 6. What would move the needle next (in rough priority)

1. A clean, fully-instrumented shakedown number with all of the above in place — the first honest capability baseline.
2. App-launch reliability for the model (M2/M3): consider priming the task instruction or tool-result text (WAA instructions are fixed, but the *loop's* tool-result message is ours).
3. Step budget: 40 is tight given M4; WAA/OSWorld norms are 50–100.
4. Refusal action design (M5) if impossible tasks matter to the score.
