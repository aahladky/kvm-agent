# SESSION 2026-07-23 — The serving layer gets a contract (not an adoption)

## What this session was

The model server lives outside this repo (llama-swap + `modelctl`,
`~/services/llama-swap/config.yaml`) and nothing here had ever looked at it. Operator
question: *"at what point does the grounding model need to be added, since the serving
architecture currently lives outside the kvm-agent project but given its role likely
deserves its own scrutiny."* This session did the scrutiny and built the thin contract
it justified. **The serving stack was NOT adopted into this repo** — it serves 16 models
for unrelated purposes, and owning it is exactly the "component you now have to maintain
and didn't need" the roadmap warns about (§6).

## What the investigation found

**`modelctl`** (`~/workspace/modelctl/`, ~3.3k lines, git-versioned with a gitea remote,
~99KB of tests) owns profile lifecycle for two backends — GGUF/llama.cpp and
OVMS/OpenVINO. Profiles are JSON in `~/.local/share/modelctl/profiles/`; `modelctl sync`
merges generated entries into `config.yaml` by name and restarts the unit. It is a real,
tested tool, not a scratch script.

Three findings, in order of importance:

### 1. holo3.1 is absent from llama-swap's `matrix:` — and gets evicted

Every other significant model has a `var`, an `evict_costs`, and a `sets:` membership.
holo3.1 has none. This is not a modelctl bug: matrix membership is the one step it
deliberately leaves to a human (its README says so), and the manual follow-up never
happened after `modelctl pull`.

Reproduced live, **both directions**: one `say ok` to `fast-7b` evicted holo3.1
(`['holo3.1']` → `['fast-7b']`); warming holo3.1 evicted fast-7b. So any other consumer
of the box silently evicts the model this project depends on, at a measured **~13-17s**
reload — which, against a 180s client timeout, lands as latency and never as an error.

**Has it bitten? No.** Every step in the recorded battery archive exceeding its run's
median by >12s is at **step 0** (35.9s / 37.4s / 31.0s — first task, cold load). No
mid-run eviction signature. Proposed fix written up as
`docs/PLAN_2026-07-23_serving_matrix_enrollment.md` (three lines, external config, not
applied).

### 2. The serving config carries model-input knobs the project didn't know about

holo3.1 launches with `--image-min-tokens 1024` — a **server-side floor on image
tokens** — plus `--cache-type-k q8_0 --cache-type-v q4_0` and `-c 64000`. Meanwhile
`kvm_agent/config.py` documents a client-side resolution A/B (1080 vs 720: −24% prompt
tokens, −33% wall) as though model input were this project's alone to control. It isn't:
half the model-input contract lives in a config outside this repo, and the two interact.
`meta.json` already travels the system prompt with every run (second review #7, same
reasoning); now it travels these too.

### 3. holo3.1 is single-GPU, so Phase 5's co-residency is already a proven pattern

Its command has `--split-mode none`, which makes llama.cpp use one GPU and renders the
`--tensor-split 4,1` inert. So holo3.1 occupies the **B70 alone**, and the README's
blanket "GGUF models span both GPUs and evict the whole board" rule does not apply to it.
The four existing `X & f7` sets (big model on B70 + pinned utility model on B580) are
exactly the arrangement Phase 5 wants for a grounder — the prerequisite is enrolling
holo3.1, not inventing a capability.

Also noted: `--parallel 1` means no request concurrency, so a Phase-2 verify call
serializes behind the actor call rather than overlapping it (~4s added per gated step).

## Corrections to what I told the operator last turn

Three claims were wrong and are corrected in the record:

- "`groups` block absent → models are mutually exclusive" — llama-swap here uses
  **`matrix`**, not `groups`, and co-residency is already configured and working.
- "Phase 5 needs a `groups` block, a new capability" — the capability exists; holo3.1 is
  merely not enrolled in it.
- An implication that llama-swap was unsupervised — it is a systemd user unit
  (`llama-swap.service`, enabled, up 1d16h). The earlier "no entries" was a
  `journalctl -n 0` flag error of mine.

## Changes

- **`kvm_agent/llm/serving.py`** (new): `parse_serving_cmd` (pure — launch command →ithe
  params that shape what the model sees), `serving_snapshot` (reachable / configured /
  resident / params / co_resident), `describe`. Everything is **fail-soft**: a probe that
  can raise is a new way to kill a run, which is the opposite of the point. `configured`
  and `resident` are deliberately separate — a cold model costs ~15s but is not a fault.
  A server with no `/running` (plain llama-server, vLLM) reports `resident: None`
  ("unknowable"), never a fabricated `False`.
- **`agent_loop_holo.py`**: `boot(serving_check=True)` records the snapshot and **warns**
  — it does not raise. The HID gate raises because clicking into a dead device corrupts
  silently; every serving problem announces itself at the first model call. `run()`
  re-snapshots **per run, not per session** (a battery runs an hour; eviction happens
  between tasks) and puts it in `meta.json` under `serving`. The refresh is gated on
  `SERVING["checked"]`, which only `boot()` sets — so the offline suite, which sets
  `ENV` directly and never boots, keeps its hands off the network.
- **`tools/serving_probe.py`** (new): the fail-closed preflight, `verify_hid`'s analogue
  one layer up (the config says what the server *would* launch; `/running` says what it
  *is* running). Hard-fails on exactly three things that ruin a run silently: endpoint
  unreachable, model not configured, and **a resident vision model with no mmproj** —
  which answers fluently from text alone and therefore reads as "the model got bad at
  grounding", the most expensive misdiagnosis available here (AGENTS.md §2). Context
  size, image-token floor, cache quant and co-residency are **recorded, not asserted**:
  asserting an uncalibrated number manufactures false alarms.
- **Tests 116 → 131 green**, `tests/test_serving.py` (15) plus a rewritten boot test.

## A test caught me breaking hermeticity

Adding the serving check to `boot()` put a real HTTP call inside the offline suite —
`test_p0_4_boot_hid_gate` calls `boot()` three times. Caught because a guard test passed
standalone and failed in the full suite. Fixed: that test now passes `serving_check=False`
(it is about the HID gate), and the guard was rewritten from inspecting a mutable global
into a behavioural test — an exploding probe injected into `run()` proves it is never
called without `boot()`'s opt-in.

Verified rather than asserted: the whole suite passes in the same time against a dead
endpoint (`HOLO_LOCAL_URL=http://127.0.0.1:1/v1` → 131 passed, 10.9s).

## Verification

- `python -m pytest tests/` — 131 passed (was 116).
- `HOLO_LOCAL_URL=http://127.0.0.1:1/v1 python -m pytest tests/` — 131 passed, no
  slowdown: the suite is endpoint-independent.
- `python tests/test_serving.py` — dual-mode script run passes.
- `python tools/serving_probe.py` — `runs/serving_probe_20260723_075311/probe.json`:
  cold 12.7s, warm 0.1s, penalty 12.7s; ctx 64000, image_min_tokens 1024, cache k/v
  q8_0/q4_0, quant Q4_K_M, mmproj present, split_mode none. Exit 0.

## Follow-ups

- **Apply `docs/PLAN_2026-07-23_serving_matrix_enrollment.md`** (operator, external
  config, three lines). It closes the eviction hole and is simultaneously the Phase 5
  co-residency prerequisite.
- **The grounding model itself is still not due.** Roadmap §5 gates it on grounding rate,
  which remains uncomputed — but the slice D-a oracle makes it measurable offline over
  the existing archive ("is `<element>` at this coordinate?" is the same shape as a
  postcondition check, replayable with no rig and no new model). Worth doing before any
  hardware/serving decision, since it answers whether grounding is the bottleneck at all.
- D-b unchanged and still next; its battery run will now record a `serving` block per
  task, so any eviction during the measurement is visible after the fact.
