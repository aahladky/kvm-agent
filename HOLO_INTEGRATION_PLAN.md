> **SUPERSEDED 2026-07-20.** Phases I0–I5 are done and the topology this plan describes
> (WiFi Pico + r4_client, DSHOW-on-Windows capture) was retired in the 2026-07-20 sweep.
> Current plan: docs/PLAN_2026-07-20_physical_target_move.md. Kept for history.

# Holo3.1 Integration Plan — Closing the Loop

**Scope:** take the validated Holo3.1 grounding model (software layer: GO) and connect it to
the physical rig — capture in, HID out — to run real multi-step tasks against a live target.
This is assembly + live testing. The model is proven; what's unproven is everything that only
appears when the loop closes: multi-step re-prompting under ~18s/step latency, error recovery
mid-task, and the three-way coordinate-space agreement across Holo / capture / Pico.

**Prereqs already done:** Holo serves on the B70 (`127.0.0.1:9292/v1`, model `holo3.1`),
grounding validated (100% on representative UI, `[0,1000]`→pixel formula confirmed), `holo.py`
adapter exists with the multi-step `history` hook, Pico v4 firmware (Report ID 2 + WiFi
resilience) verified on macOS.

---

## Guiding discipline (same as prior phases)

- **Isolate each junction; verify it alone before connecting.** The loop has four seams
  (capture→loop, loop→model, loop→Pico, Pico→target). Test each in isolation; never debug two
  unknowns at once. This is the rule that turned the Holo software phase into an hour.
- **One coordinate space, agreed three ways.** Holo's input image dims = capture frame dims =
  Pico's `SCREEN_W/SCREEN_H`. A mismatch here is the highest-risk bug in this phase (clicks
  land consistently wrong, hardest symptom to diagnose). Nail it first, verify with a single
  corner click before any full run.
- **Make failure loud.** Keep the `dropped_actions` counter; add a per-step frame-changed
  check and a stuck-detector (k consecutive no-op steps → abort) so a live regression
  announces itself instead of burning the step budget.
- **Escalate only when the lower seam is clean.** Don't run a full task until move+click+type
  are individually confirmed on the live target.
- **Diff against reference when something's wrong.** Get the `HAI_API_KEY` wired NOW so the
  first live grounding miss can be instantly checked local-Q4 vs hosted-BF16 (quant vs model).

---

## Topology — LOCKED (2026-07)

Decisions are made; this is the confirmed hardware allocation. Each GPU does exactly one job.

| Device | PCI ID | Role | Driver |
|---|---|---|---|
| iGPU UHD 770 | `00:02.0` (`8086:a780`) | **VM display** → HDMI → capture card | `vfio-pci` (bound, host released it) |
| Arc Pro B70 (G31) | `03:00.0` (`8086:e223`) | **Holo inference** (host, llama.cpp SYCL) | host (`xe`) |
| Arc B580 (G21) | `08:00.0` (`8086:e20b`) | **host display** + reserved for future reasoning model | host (`xe`) |

- **Target = Windows 11 VM** on the desktop (not the MacBook — MacBook remains the eventual
  bare-metal/undetectable fallback).
- **iGPU passed to the VM** (NOT the B580). Rationale: keeps BOTH Battlemage cards on the host
  for inference — B70 for Holo now, B580 available for the two-model split later. The cost is
  that Intel iGPU passthrough is fiddlier than discrete-GPU passthrough (see I1 gotchas).
- **Host display runs on the B580** (confirmed working). iGPU was previously disabled/unused;
  now `vfio-pci`-bound for the guest. So the host is unaffected by the handoff.
- **Capture resolution = 1920×1080** — THE one true coordinate space. The unused 1080p monitor
  used for passthrough bring-up is the same res, and grounding was validated near 1080p. This
  single number must be shared by: VM display output, capture card, and Pico `SCREEN_W/SCREEN_H`.
- **Pico → USB passthrough to the VM.**

Pre-flight to confirm before building: IOMMU/VT-d enabled in BIOS + `intel_iommu=on` on the
kernel cmdline (the `vfio-pci` bind implies it, but verify the iGPU sits in its own IOMMU
group); the iGPU's **audio function** (`00:1f.3` HDMI audio, or its dedicated function) is
passed with it or stubbed, not split.

---

## Phases

### Phase I0 — Recover / regenerate the harness pieces
The old EvoCUA harness is on an unpacking drive image; most is superseded, but a few pieces
carry over. Regenerate rather than block on the image where cheaper.

- **Pico firmware v4** — regenerate `boot.py` (Report ID 2: `0x85,0x02`, `report_ids=(2,)`) +
  `code.py` (byte-mask fix, `0.0.0.0` bind, WiFi-resilience: radio toggle + hard-reset-after-N,
  self-test in try/except). The working version currently exists only on the CIRCUITPY drives
  and the image — commit it to the repo as source of truth (this project has drifted twice;
  don't let firmware live only on the drives).
- **`r4_client.py`** — TCP client, protocol `M/C/R/D/U/K/T/X/S/H`, point `TARGET_IP` at the
  Pico. Trivial regenerate; verify the key-name protocol (names like `Kenter`, `Xctrl+a`, not
  numeric codes — the prior version had a numeric-vs-name bug that silently killed keyboard).
- **`agent_loop_holo.py`** — the loop, built around `holo.py` (not the old EvoCUA loop).
- **Acceptance:** firmware committed to repo; `r4_client` + loop skeleton exist and import.

### Phase I1 — VM + iGPU passthrough + capture (the display seam)

The iGPU is the ONE component in this phase with real unknowns (Intel integrated-GPU
passthrough is fiddlier than discrete). So bring it up against a **known-good monitor first**,
then swap in the capture card. Never debug "did passthrough output a picture?" and "did the
capture card read it?" at the same time — an unused 1080p monitor is on hand specifically for
this isolation.

**I1a — VM boots, no passthrough yet.** Create the Windows 11 VM in libvirt/virt-manager with
a normal virtual display (virtio/QXL/SPICE). Confirm Windows installs and boots cleanly. This
isolates "VM healthy" from "passthrough works." Do not add the iGPU until Windows runs.

**I1b — add iGPU passthrough, verify on the physical monitor.**
- Attach the iGPU (`00:02.0`) + its audio function to the VM via VFIO.
- **Intel-iGPU-specific gotchas — expect at least one of these:**
  - `x-igd-opregion=on` (or the libvirt equivalent) is usually REQUIRED for the iGPU to drive
    display output; without the OpRegion, Windows enumerates the GPU but shows **Code 43** or
    outputs no signal.
  - A dumped VBIOS/ROM for the iGPU may be needed (`romfile=`), depending on QEMU/kernel version.
  - QEMU has a legacy **IGD passthrough mode** (`igd-passthru`) distinct from normal VFIO for
    Intel integrated graphics — try it if vanilla passthrough gives no output.
- **Plug the 1080p monitor into the motherboard's iGPU HDMI port** (`00:02.0`'s output — NOT
  the B580's ports; the VM owns the iGPU, so its display comes out the motherboard HDMI).
- Boot. **Pass criterion:** Windows shows a desktop on the monitor. Set the guest display to
  **1920×1080**.
- **Guest-side isolation check** (do this even with the monitor): in Windows **Device Manager
  → Display adapters**, the UHD 770 must show healthy — **no Code 43 / yellow bang**. Code 43
  = OpRegion/VBIOS not passed → fix that, it's known-solvable, not a dead end.
- If the monitor is black AND Device Manager shows Code 43 → passthrough display problem
  (OpRegion/ROM). If Device Manager is clean but monitor is black → output-routing/port issue.
  Either way you're debugging PASSTHROUGH, isolated, before the capture card is involved.

**I1c — swap monitor → capture card.** Once Windows provably drives the iGPU HDMI at 1080p,
replace the monitor with the Acer capture card. Because I1b proved the picture exists, a black
frame here is unambiguously the CAPTURE chain (proven-easy half), not the iGPU.
- `cv2.VideoCapture` with the **DSHOW** backend (MSMF mangled chroma in prior work); grab a
  frame; confirm it's a clean 1920×1080 image matching the VM desktop. Threaded/always-warm
  handle as before. Close any other app holding the card (single-client device).

**Acceptance:** VM drives a clean 1920×1080 desktop out the iGPU HDMI (verified on monitor +
Device Manager clean), and the capture card reads a matching clean frame from the host.

### Phase I2 — Pico HID on the live target (the "input is the hard part, already won" seam)
- Flash v4 firmware; set the Pico's `SCREEN_W/SCREEN_H` = the agreed capture resolution.
- If VM target: pass the Pico USB through to the VM (libvirt USB passthrough). If MacBook:
  plug USB directly.
- **Verify HID alone, over WiFi, via `r4_client`** (not the full loop):
  - `M <center>` → cursor teleports to center of target.
  - Four corners → cursor hits each corner. **This is the three-way coordinate check:** send
    pixel coords, confirm they land where expected on the captured frame.
  - `C` (click), `R` (right-click → context menu), `T hello` (type), `X ctrl+a` (combo).
- **Acceptance:** move (all 4 corners + center pixel-accurate), click, right-click, type, and
  combo all confirmed on the live target via WiFi. No full loop yet.

### Phase I3 — Coordinate-space closure (the critical junction)
- With capture + HID both live, verify the **full coordinate round-trip**: take a capture
  frame, pick a known on-screen element, get its pixel coords from the frame, send `M x,y`,
  confirm the cursor lands on that element *as seen in the capture*.
- Re-run the Phase-3 grounding coordinate probe **at the actual capture resolution** (the
  FINDINGS doc flags this explicitly — the `/1000` formula was verified at 1920×1080 and
  3132×1515 but must be confirmed at whatever the capture actually produces).
- **Acceptance:** capture-pixel → HID → cursor-on-element round-trips correctly; Holo's
  `[0,1000]`→pixel projection confirmed at the live capture resolution.

### Phase I4 — Single-action closed loop (first real integration)
- Wire `agent_loop_holo.py`: capture frame → `holo.py` (ground) → `r4_client` (act) →
  re-capture. Start with `CONFIRM_FIRST` high (gate the first several actions with a preview +
  keypress, like the prior loop) so you eyeball each click before it fires.
- Task: a **single-step** goal ("click the X icon"). Confirm the model grounds on the live
  captured frame and the Pico lands the click on the real target.
- **Acceptance:** one model-decided click, on a live target, lands correctly and is confirmed
  via the capture feed. This is the first true end-to-end action.

### Phase I5 — Multi-step loop + the re-prompt contract
- Enable multi-step: after each action, append the assistant tool-call + a tool-result message
  to `history` (per the chat-layout convention in `FORMAT_NOTES.md`), re-capture, re-prompt.
- This is where Holo's **native function-calling + observe-after-each-action** behavior gets
  exercised — the Phase-2 finding (model calls `click` before `write` on an unfocused field)
  means the loop MUST re-prompt after each tool result, not batch.
- Task: a **2–3 step** goal (e.g. "open the app, click the search box, type a query").
- Watch: does the model recover if a step doesn't land? Does per-step latency (~18s, mostly
  image processing) compound acceptably over the sequence?
- **Acceptance:** a 2–3 step task completes end-to-end; the re-prompt/history contract works;
  latency over the sequence is tolerable (or the image-token latency dial is tuned — see below).

### Phase I6 — Robustness + latency tuning (only if I5 surfaces problems)
- **Latency dial:** if ~18s/step is too slow over multi-step tasks, the lever is IMAGE TOKENS
  (85% of step time is prompt/vision processing, not generation). Try a smaller/downscaled
  capture or a lower `--image-min-tokens`, but **re-run the grounding-rate check** — image
  tokens trade directly against grounding accuracy. Measure the tradeoff; don't guess.
- **Stuck-detector:** k consecutive no-op/dropped-action steps → abort (don't burn the budget).
- **Verify-before-terminate:** if the model calls `finished`/`answer`, optionally confirm the
  end state matches the goal before counting success (the EvoCUA false-positive-terminate
  lesson). Cheap OCR check on a result region.
- **Frame-freshness:** confirm the frame used for a decision is post-settle (stale-frame reads
  look like grounding errors). A "frame changed since last action" check before each predict.

### Phase I7 — Real task battery + write-up
- Run a small battery of realistic multi-step tasks (the kind you actually want the agent to
  do). Measure success rate, steps-to-completion, per-step latency, dropped_actions.
- **Hosted diff** on any task that misses (now that `HAI_API_KEY` is wired): local-Q4 vs
  hosted-BF16 to attribute misses to quant vs model.
- Write `FINDINGS_integration.md`: what works, where it breaks, the latency profile, and
  whether solo Holo reasoning is sufficient or the two-model split is warranted.

---

## Deliverables

- `boot.py` / `code.py` (Pico v4) — committed to repo (not just on drives).
- `r4_client.py` — name-based key protocol, target IP set.
- `agent_loop_holo.py` — capture→ground→act→re-capture loop with confirm-gate, dropped_actions
  counter, stuck-detector.
- `FINDINGS_integration.md` — results + whether two-model split is needed.
- Updated coordinate-space note: the one agreed resolution across Holo/capture/Pico.

## Explicitly deferred (don't build until data demands)

- Two-model split (reasoning model on B580) — only if I5/I7 shows Holo's solo planning is the
  weak link. Live multi-step testing is what reveals this.
- Fine-tuning on logged trajectories — separate future project.
- Bare-metal undetectable deployment — the VM is fine for integration; the MacBook/physical
  target is the eventual "real" undetectable rig if that becomes the point.
- Re-adding scroll to the Pico descriptor (dropped in firmware for robustness; `S` is a no-op
  placeholder — add back when a task needs it).

## Things only Aaron can decide / provide

- ~~Target choice~~ / ~~B580 allocation~~ / ~~capture resolution~~ — **DECIDED** (see locked
  topology: VM target, iGPU→VM, B580 host-reserved, 1920×1080).
- **`HAI_API_KEY`** — free, portal.hcompany.ai. Wire before I4 so live misses are diagnosable.
- Real target tasks for the I7 battery.
- BIOS/kernel IOMMU confirmation and any iGPU OpRegion/ROM steps that need physical/BIOS
  access (I1b).

## Suggested order

Topology is locked. I0 (confirm on-disk v4 firmware, commit as source of truth — files are
now unpacked, so verify-don't-regenerate) → **I1a (VM boots) → I1b (iGPU passthrough, verify
on MONITOR + Device Manager) → I1c (swap to capture card)** → I2 (Pico HID alone, over WiFi) →
**I3 (coordinate closure — the critical junction)** → I4 (single action) → I5 (multi-step +
re-prompt) → I6 (tune only if needed) → I7 (task battery). Each seam verified alone before the
next connects. The iGPU passthrough (I1b) is the single riskiest step in the phase — that's why
it's verified against a known-good monitor before anything is chained behind it.

Note on I0: the project folder is now fully unpacked on disk, so the working v4 firmware,
`holo.py`, and prior harness pieces exist — I0 is "diff the on-disk copies against the verified
versions and commit them as source of truth," NOT regenerate from spec. Confirm `boot.py` has
Report ID 2 (`0x85,0x02`, `report_ids=(2,)`) and `code.py` has the WiFi-resilience patch before
trusting them (the repo has drifted from the working drives twice).
