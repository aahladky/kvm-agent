# Holo3.1 — Observed Action Format & Coordinate Projection

> Ported reference doc (2026-07-17) from the software-layer bring-up, which lives in a
> separate repo/worktree (`worktree-holo-bringup` branch, `computer-use/` dir) — not
> authored in this repo. The raw-evidence JSON files it references
> (`phase2_native_tools_raw*.json`, `phase3_coords_raw.json`) live there, not here;
> `kvm_agent/models/_fixtures/holo_phase2_native_tools_raw.json` in this repo is a copy of
> just the Phase-2 examples, kept for `kvm_agent/models/holo.py`'s offline self-test.

Captured empirically against the local `holo3.1` llama-swap profile (llama.cpp SYCL, Arc
Pro B70, `Holo-3.1-35B-A3B.Q4_K_M.gguf` + `mmproj-f16.gguf`), 2026-07-16. Cross-referenced
against `hub.hcompany.ai/agent-loop` and `hub.hcompany.ai/element-localization` docs, but
**every claim below was independently verified against real local output** — see the
`phase2_native_tools_raw*.json` and `phase3_coords_raw.json` dumps for raw evidence.

## Output format: native function-calling (confirmed)

Holo3.1 supports two modes per the docs (structured-output JSON, or native OpenAI
`tool_calls`). Sending a normal OpenAI `tools` array with a `holo3.1` local request works
cleanly — **we use native function-calling, not text action strings.**

Every response observed:
- `message.content` — always empty string `""`.
- `message.reasoning_content` — the thinking trace (llama.cpp's field name; H Company's
  own docs call this `message.reasoning`, so treat the field name as serving-specific, not
  part of the model's actual output).
- `message.tool_calls` — a single-element array (we always saw exactly one call per step):
  `[{"type": "function", "function": {"name": ..., "arguments": "<json string>"}, "id": ...}]`.
  `arguments` is a JSON-encoded string, not a nested object — must `json.loads()` it.

No `--tool-call-parser` flag was needed in the llama-swap `holo3.1` profile; `--jinja` with
the model's built-in chat template was sufficient for llama.cpp to parse native tool calls
correctly.

### Captured examples (tool schema we offered: `click`, `write`, `scroll`, `drag_and_drop`,
`answer` — see `probe_phase2_native_tools.py`)

| Instruction | Tool called | Arguments |
|---|---|---|
| "Click the 'Models' item in the left sidebar." | `click` | `{"element":"Models item in left sidebar","x":81,"y":243}` |
| "Click the 'Playground' item in the left sidebar." | `click` | `{"element":"Playground menu item in left sidebar","x":68,"y":194}` |
| "Type 'hello world' into the browser address bar." (not pre-focused) | `click` | `{"element":"browser address bar showing localhost:9221/ui/","x":250,"y":30}` |
| "...address bar is now focused and selected. Type 'hello world' into it." | `write` | `{"content":"hello world","press_enter":false}` |
| "Scroll down on the requests table." | `scroll` | `{"direction":"down"}` |
| "Drag from top-left to bottom-right corner of the Prompt Processing chart." | `drag_and_drop` | `{"x1":173,"y1":244,"x2":477,"y2":394}` |

Notable: when asked to type into an unfocused field, the model correctly chose `click`
first rather than blindly calling `write` — it reasons about GUI state, not just the literal
instruction. Parser must NOT assume one tool call per user instruction; multi-step tasks
will need the loop to re-prompt after each tool result.

`element` is a free-text description the model includes as a grounding rationale
alongside `x`/`y` — useful for debugging/logging but not required by the format.

## Coordinate system: `[0, 1000]` normalized (confirmed, NOT `0-999`, NOT absolute pixels)

Per `hub.hcompany.ai/element-localization`: coordinates are integers in `[0, 1000]`,
normalized to the exact image bytes sent in the request.

**Verified empirically** with a synthetic 1920×1080 calibration image
(`test_screens/calibration_1920x1080.png`, built by `make_calibration_image.py`) with 9
colored markers at precisely known pixel positions (corners, edges, center). The probe
asked Holo to click each marker **by color/label only — no coordinates were printed
anywhere in the image**, to rule out the model OCR-ing a printed number instead of actually
grounding on the marker.

Projection formula (confirmed correct):
```
screen_x = round(raw_x / 1000 * image_width)
screen_y = round(raw_y / 1000 * image_height)
```

Results (`probe_phase3_coords.py`, `python3 probe_phase3_coords.py`):

| label | true (px) | raw returned | abs-pixel hyp. error | **norm/1000 hyp. error** | norm/999 hyp. error |
|---|---|---|---|---|---|
| top_left | (60, 60) | (34, 59) | 26.0 | **6.5** | 6.5 |
| top_right | (1860, 60) | (970, 55) | 890.0 | **2.5** | 4.3 |
| bottom_left | (60, 1020) | (32, 937) | 87.6 | **8.2** | 7.2 |
| bottom_right | (1860, 1020) | (969, 939) | 894.7 | **5.9** | 5.4 |
| center | (960, 540) | (500, 500) | 461.7 | **0.0** | 1.1 |
| top_mid | (960, 40) | (500, 40) | 460.0 | **3.2** | 3.4 |
| left_mid | (30, 540) | (20, 500) | 41.2 | **8.4** | 8.5 |
| right_mid | (1890, 540) | (983, 500) | 907.9 | **2.6** | 0.9 |
| bottom_mid | (960, 1050) | (500, 956) | 469.5 | **17.5** | 16.5 |

The `/1000` hypothesis wins decisively and holds at every corner/edge (the plan's flagged
worst case — "right-edge icon mapped to screen center" in the prior model — does **not**
reproduce here: `right_mid` at raw x=983 projects to screen x=1885, true is 1890, 5px off).
`/999` is statistically indistinguishable from `/1000` at this resolution (max ~1px
difference) and not worth the extra complexity — **use `/1000`,** matching the documented
contract.

**Important:** llama.cpp on this Battlemage/SYCL build does **not** appear to internally
resize the image and report coordinates against some other (e.g. `smart_resize`) space —
the returned numbers project correctly against the exact dimensions of the image bytes we
sent. This was the plan's single highest-risk unknown and it resolved cleanly. (Caveat:
only verified at 1920×1080; if the eventual capture resolution differs, re-run
`probe_phase3_coords.py` against an image at that resolution before trusting the formula.)

## Hosted API cross-check (2026-07-17, `HAI_API_KEY` obtained)

Ran the identical Phase 3 coordinate probe and Phase 4 grounding-rate harness against
`https://api.hcompany.ai/v1` (`holo3-1-35b-a3b`).

**Coordinate projection:** matches local almost exactly — raw returned values differ by at
most 1 unit (in `[0,1000]` space) from local on the calibration-image probe. Confirms local
Battlemage/Q4_K_M serving is not introducing a coordinate bug relative to the reference.

**Format quirk found on hosted, never observed locally (0/80 local calls):** for 2 of 8
targets (`Activity nav item`, `Models nav item` — both top-level sidebar nav rows), hosted
returned `x`/`y` as a **`[min, max]` range list** instead of a scalar integer, despite the
`click` tool's JSON schema declaring `"type": "integer"` — e.g.
`{"element": "Activity nav item in left sidebar", "x": [58, 157], "y": [157, 500]}`. This
was **fully deterministic across all 10 reps** for each of those two targets (identical
range every time, unlike local's small temp=0 jitter — hosted infra likely does some
request-level caching). The `y` range in particular (`[157, 500]`) spans well beyond the
actual target row, so the naive "take the midpoint" fallback (implemented in both
`ground_probe.py` and `holo.py`'s `_scalar()`) lands outside the true target — see Phase 4
results below. **A robust parser must handle non-scalar `x`/`y` from the hosted API**; a
parser that assumes `int` per the schema will crash on this input.

Also confirmed as hosted-only serving-layer differences (not model behavior): `content` is
`null` on hosted vs `""` locally; the reasoning field is literally named `reasoning` on
hosted vs llama.cpp's `reasoning_content`. Free-tier hosted API also rate-limits
(`HTTP 429`) after roughly 10 requests in quick succession — `probe_common.chat()` now
retries with exponential backoff, and `ground_probe.py` paces hosted requests 2s apart.

**Phase 4 grounding rate, local vs hosted (K=10 each, same 8 targets, same screenshot):**

| target | local | hosted |
|---|---|---|
| Activity nav item (sidebar) | 10/10 (100%) | **0/10 (0%)** — non-scalar x/y, see above |
| Models nav item (sidebar) | 10/10 (100%) | **0/10 (0%)** — non-scalar x/y, see above |
| big-gemma model in sidebar list | 10/10 (100%) | 10/10 (100%) |
| qwen-agent model in sidebar list | 10/10 (100%) | 10/10 (100%) |
| 'Prompt Processing' chart title | 10/10 (100%) | 10/10 (100%) |
| 'REQUESTS' stat label | 10/10 (100%) | 10/10 (100%) |
| 'Model' table column header | 10/10 (100%) | 10/10 (100%) |
| 'Status' table column header | 10/10 (100%) | 10/10 (100%) |
| **OVERALL** | **80/80 (100.0%)** | **60/80 (75.0%)** |

Counterintuitive but well-evidenced: **local (Battlemage/Q4_K_M) outperformed the hosted
reference on this test set**, entirely because of the hosted-only non-scalar-coordinate
quirk on 2 targets — not because of any local grounding-accuracy shortfall. This is exactly
the kind of thing the plan's "diff against hosted before assuming a local bug" guardrail is
for, just in the opposite direction: don't assume hosted is always the more-correct
reference either. If this recurs against real target UIs, prefer using the largest single
value in a returned range (or re-prompting) over a blind midpoint — a midpoint is only
safe when the range is actually tight around the true target, which it was not here for
2/8 targets.

## Config used for these results

- llama-swap `holo3.1` profile: `--image-min-tokens 1024` (already applied),
  `--cache-type-k q8_0 --cache-type-v q8_0` (bumped V from q4_0 → q8_0 for this test phase
  per the plan's Phase 0 guidance — VRAM headroom exists at Q4_K_M on 32GB).
- Endpoint: `http://127.0.0.1:9292/v1` via llama-swap, model id `holo3.1`.
- `probe_common.py` has both `local` and `hosted` (`api.hcompany.ai`) targets wired up,
  both exercised in this bring-up. `HAI_API_KEY` is read from env or a git-ignored `.env`.

## Open items for Phase 4+

- Only one real screenshot (`llamaswap_ui.png`, 3132×1515) and one synthetic calibration
  image tested. Different resolutions/visual density not yet covered.
- The hosted non-scalar-coordinate quirk deserves more targeted investigation (does it
  correlate with ambiguous/icon-plus-text targets specifically? does `reasoning_effort`
  change it?) if hosted is to be relied on as a production fallback — not investigated
  further here, out of scope for this bring-up.
