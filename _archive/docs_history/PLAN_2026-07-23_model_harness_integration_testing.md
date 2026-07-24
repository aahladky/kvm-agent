# PLAN 2026-07-23 — Model/harness integration testing (APPROVED)

_Approved by the operator after reviewing the testing design, with maintainability as
the primary constraint. This plan replaces broad desktop-task execution as the way to
answer whether the real model is integrated correctly with the harness._

## Baseline

Local `main` is clean and four reset-isolation commits ahead of `gitea/main`; those
commits form one coherent stack and remain intentionally unpushed. The complete offline
suite passes: 171 tests in 14.69 seconds
(`runs/main_baseline_20260723_155706/pytest.txt`).

The deterministic side of the integration boundary already has meaningful coverage:

- `tests/test_agent_loop.py` drives the production loop with fake camera, HID, recorder,
  model responses, and verifier behavior;
- `tests/test_model_seam.py` proves the model-neutral session contract and preserves a
  golden multi-step transcript;
- `tests/test_holo_messages.py` covers production message construction, parsing, tool
  normalization, image-history trimming, and coordinate projection.

The missing evidence is narrower: the real serving endpoint has no small, controlled
test that passes known frames through the production `HoloSession` request/parser seam,
and the complete physical capture→model→HID→capture loop has no deterministic
calibration target.

## Maintenance budget

1. Reuse `HoloSession`, `agent_loop_holo.run()`, `RunRecorder`, capture, and HID exactly
   as shipped. Do not create another agent loop or generalized evaluation framework.
2. Keep exactly four live-model contract cases and one physical calibration flow.
   Add a case only when an escaped integration defect proves the existing set cannot
   expose an important failure.
3. No database, CI matrix, retry/majority-vote layer, interactive grading, or model
   grader. Each check has a deterministic predicate and exits nonzero on failure.
4. Every invocation writes one self-contained directory under `runs/`, including the
   exact frame, prompt/request inputs, raw response, parsed action, transformation
   evidence, and result.
5. The deterministic offline suite remains the ordinary merge gate. Live checks are
   required only when the boundary they exercise changes.

## Slice A — live-model contract smoke (first implementation branch)

Add one noninteractive tool, `tools/model_contract_smoke.py`. It generates four fixed
desktop-like frames directly into its run directory and sends each through a fresh
production `HoloSession` backed by the real local model endpoint:

| Case | Visible state | Accepted contract |
|---|---|---|
| `click_target` | one large labeled action button | a click projected anywhere inside the broad target rectangle |
| `type_nonce` | a clearly focused field and displayed nonce | a type action containing the exact nonce |
| `complete` | an unambiguous success state | `finished` |
| `incomplete` | an explicit incomplete state with a next action visible | at least one valid action and no `finished` |

The assertions intentionally avoid exact prose, reasoning, or point coordinates. A
single request is made per case, with no automatic retry. The tool supports selecting
one case for diagnosis, records the serving snapshot and inference configuration, uses
a bounded per-call timeout, and distinguishes infrastructure failure from a valid but
contract-violating model response.

Offline tests cover deterministic frame generation, acceptance predicates, artifact
completeness, and exit/result classification using injected responses. They never
contact the server. The live smoke itself is an explicit command, not part of ordinary
`pytest`.

### Slice-A acceptance

- Existing 171 offline tests and the new focused tests pass.
- One live invocation completes all four cases without hardware or operator input.
- The run directory alone is sufficient to inspect the exact frame/prompt, raw output,
  parsed action, normalized-to-pixel projection, predicate, and failure ownership.
- A failing semantic case is investigated under AGENTS.md §2; it is not blindly rerun
  until green.

## Slice B — controlled physical calibration (separate later branch)

Add one repository-owned static calibration page and a thin driver that serves it,
prepares it visibly through the existing HID channel, and invokes the production
`boot()` / `run()` / `shutdown()` path. The page asks the actor to:

1. click a prominently labeled control at a seed-selected location;
2. focus a text field;
3. type a short displayed nonce;
4. submit it; and
5. call `finished` only after the page shows success.

The page transitions only after correct input. Its final state contains a fixed,
camera-readable success marker; the driver checks that marker deterministically from
the captured frame. No model grades another model, and no target-side result channel is
used as truth.

The driver duplicates no planning or execution logic. It only owns page setup, the
recorded seed/nonce, a small action/time bound, and the final visual predicate. One run
must exercise the real path:

```text
capture → production prompt/session → real model → production parser
        → coordinate projection → physical HID → capture → finished
```

### Slice-B acceptance

- The deterministic offline suite passes before any rig use.
- The calibration completes within its declared step/time bound without operator
  grading or retries.
- Final success comes from the page-owned visual state, while every intermediate model
  and harness transformation remains inspectable in `runs/`.
- Failure classification identifies serving, capture, request/parse, coordinate, HID,
  focus, page-oracle, or termination protocol as the first broken boundary.

## When these checks run

- Ordinary code: affected offline tests; complete offline suite before merge.
- Prompt, image preparation, model adapter, parser, history, or termination changes:
  offline suite plus Slice A.
- Capture, coordinate projection, HID, focus, or closed-loop changes: offline suite,
  Slice A, then Slice B.
- Real application behavior is a separate acceptance question and does not determine
  whether the model/harness integration itself is wired correctly.

## Explicitly deferred

- Broader application scenarios, sampling systems, statistical dashboards, resumable
  campaigns, richer result schemas, and additional calibration gestures.
- Any abstraction shared by the two slices beyond production code they already use.
- Changes to D-d or the planner architecture. This plan establishes trustworthy,
  bounded integration evidence before more control-flow complexity is added.
