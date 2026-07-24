# Holo3.1 Bring-Up — Findings & Go/No-Go

> Ported reference doc (2026-07-17) from the software-layer bring-up, which lives in a
> separate repo/worktree (`worktree-holo-bringup` branch, `computer-use/` dir), unmerged
> to that repo's `master`. `HOLO_TESTING_PLAN.md` and the probe scripts it references live
> there, not here — this doc is the GO/no-go conclusion HOLO_INTEGRATION_PLAN.md (this
> repo) builds on for Phase I0's "prereqs already done" claim.

Software/model-layer bring-up per `HOLO_TESTING_PLAN.md`, executed 2026-07-16/17 against
the local `holo3.1` llama-swap profile (llama.cpp SYCL, Arc Pro B70, Q4_K_M GGUF). No
hardware (Pico, HID, live capture) involved — static screenshots only, per plan scope.

## Recommendation: **GO**

All four of the plan's phase-0-through-4 acceptance criteria were met, several by a wide
margin. Nothing here found a Battlemage/serving-specific problem — where the plan's
guardrails predicted risk (image tokens, coordinate space, non-determinism), the risk was
either already mitigated or resolved with hard evidence rather than assumption.

## What was done

- **Phase 0 (env sanity):** confirmed `--image-min-tokens 1024` already applied in the
  `holo3.1` llama-swap profile; bumped `--cache-type-v` from `q4_0` → `q8_0` for the test
  phase (VRAM headroom exists); verified endpoint + coherent text completion.
- **Phase 1 (vision smoke test):** two real screenshots (a dense terminal UI, a web
  dashboard), both described with high specificity — exact stat values, exact sidebar
  labels, exact small terminal text (GPU temps/fan RPM) all read correctly. Vision path
  works cleanly on the B70.
- **Phase 2 (format capture):** Holo3.1 uses **native OpenAI function-calling**
  (`message.tool_calls`, not text action strings) with no special llama-swap tool-parser
  flag needed. `content` is always empty; `reasoning_content` carries the thinking trace
  separately. Captured 6 real examples covering click/write/scroll/drag_and_drop — see
  `FORMAT_NOTES.md` for the full table and `phase2_native_tools_raw.json` for raw JSON.
- **Phase 3 (coordinate system):** confirmed `[0, 1000]`-normalized coordinates (matching
  H Company's documented contract, i.e. NOT the older `0-999` Qwen-VL convention the plan
  flagged as the prior model's failure mode, and NOT absolute pixels). Verified against a
  synthetic 1920×1080 calibration image with 9 markers at precisely known positions,
  probed by color/label only (no printed numbers, so the model couldn't OCR its way to a
  right answer). Projection `screen = raw/1000 * image_dim` lands within **0–17.5px** at
  every corner/edge tested. **This was the plan's single highest-risk unknown and it
  resolved cleanly** — llama.cpp on this Battlemage build does not appear to silently
  reproject into some other (e.g. smart-resized) space.
- **Phase 4 (grounding-rate harness):** built `ground_probe.py`, ran K=10 reps × 8 targets
  (mix of large nav rows and small/dense targets — sidebar list items, table column
  headers) against the real `llamaswap_ui.png` screenshot.
  **Result: 80/80 (100%) local hit rate, 0 dropped_actions.**
  Raw returned coordinates *did* jitter run-to-run by a few units even at temp=0 (e.g.
  `(80,150)` vs `(100,150)` for the same target across reps) — confirming the plan's
  "temp≈0 is not deterministic on this stack" warning was correct — but the jitter stayed
  well inside real target bounds, so it never flipped a hit to a miss here.
- **Hosted cross-check (after `HAI_API_KEY` became available):** re-ran the Phase 3
  coordinate probe and the full Phase 4 harness against `api.hcompany.ai`. Coordinate
  projection matched local to within 1 unit — confirms local Battlemage/Q4_K_M serving
  isn't introducing a coordinate bug. Grounding rate: **60/80 (75%) hosted vs 80/80 (100%)
  local**, entirely because hosted returned non-scalar `[min,max]` range values for `x`/`y`
  on 2 of 8 targets (never observed in any of the 80 local calls) whose midpoint fell
  outside the true target. See `FORMAT_NOTES.md` "Hosted API cross-check" for the full
  table and raw examples — **local outperformed hosted here**, the opposite of the naive
  assumption that hosted is always the safer reference.
- **Phase 5 (adapter):** `holo.py` builds the request, calls the endpoint, parses
  `tool_calls` into a normalized action dict (`left_click` / `type` / `scroll` / `drag` /
  `finished`), and projects coordinates. Includes a `dropped_actions` counter with loud
  logging (per the plan's "make failure loud" guardrail) and an offline `__main__`
  self-test that parses all 6 captured Phase-2 examples with zero drops.

## Caveats / what this does NOT prove

- **Single screenshot, single resolution (3132×1515).** Grounding accuracy and the
  coordinate formula were verified on one real UI screenshot and one synthetic
  1920×1080 calibration image. Different resolutions, DPI, or visual density (e.g. a
  cluttered macOS desktop with many small icons) have not been tested — re-run
  `probe_phase3_coords.py` and `ground_probe.py` against real target-resolution
  screenshots before trusting the formula/rate at the eventual capture resolution.
- **Hosted API has its own quirk to design around.** If the eventual production system
  ever falls back to the hosted API (e.g. local box down), the parser must handle
  non-scalar `x`/`y` — `holo.py`'s `_scalar()` does a midpoint fallback today, which is
  demonstrably not always safe (see `FORMAT_NOTES.md`). Not investigated further: whether
  this correlates with specific target types, or changes with `reasoning_effort`.
- **8 targets is a good spread, not exhaustive.** ScreenSpot-Pro-style tiny/dense-icon
  targets (not just text labels) haven't specifically been tested — the plan calls these
  out as "where grounding models are weakest."
- **Bounding-box ground truth was hand-annotated and got it wrong on the first pass** — an
  initial tight glyph-bbox produced false misses for the sidebar list items; the real
  clickable row is much wider (~460px, not ~230px). Fixed by overlaying the model's
  returned coordinates on the image and visually verifying, not by assumption. Worth
  remembering for Phase 4 target-list design against new screenshots: bbox to the real
  clickable region, not the tight text glyph bounds.

## What changes when hardware is added later

- Capture dimensions become whatever the live HDMI/MacBook capture produces (plan notes
  the prior rig used 1920×1080 for both capture and HID mapping) — the `[0,1000]` →
  pixel projection formula in `holo.py`'s `project_point()` needs no code change, just the
  correct `image_w`/`image_h` passed in (already how `call_holo()` derives them, via
  `PIL.Image.open(...).size` on whatever screenshot is sent).
  **But re-verify Phase 3 at that actual resolution before trusting it** — see caveats above.
- HID/`r4_client` target IP: out of scope here entirely, nothing in this phase depends on
  it (per plan's explicit "no Pico, no HID" scope).
- Multi-step loop: `holo.py`'s `build_messages()` takes an optional `history` list already,
  and `call_holo()` returns one normalized action per call — the loop just needs to append
  the prior assistant tool-call + a `<tool_output>`/tool-role result message per the two
  chat-layout conventions noted in `FORMAT_NOTES.md`, then call again.
