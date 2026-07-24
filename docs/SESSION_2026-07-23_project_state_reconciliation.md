# SESSION 2026-07-23 — Project-state reconciliation and operating recommendation

## Outcome

The codebase, local commit stack, test evidence, live integration artifacts, open
plans, and mutable state documents were re-audited after Slice B and the serving-parser
fix. The result is a consolidated, evidence-backed overview:
`docs/REPORT_2026-07-23_project_state_overview.md`.

The implementation baseline is healthy enough for bounded real use, but the report
does not call it a general autonomous agent. It distinguishes controlled integration
proof from capability, D-c's decomposed acceptance from an all-in-one physical gate
run, functional HID validation from the postponed long soak, and a medium-horizon flat
loop from the roadmap's long-horizon destination.

## Mutable documentation corrections

`PROJECT_STATE.md` now records:

- the current 184-test offline baseline;
- both controlled integration slices as complete;
- the actual 1280×720 capture/model-input behavior;
- D-c's terminal-only, opt-in direct-run policy;
- the absence of a general capability benchmark;
- evidence-locality and hidden-workspace debt; and
- the controlled harnesses' 1,519-line maintenance footprint.

`docs/ROADMAP.md` now separates deployed primitives from their postponed duration
gate, marks Phase 1 complete, marks only the terminal portion of Phase 2 complete, and
states that Phases 3–6 are not started. Its immediate-next-step list is replaced with
the current recommendation: use the system, run only affected gates, convert escaped
defects into minimal fixtures, keep D-d deferred, and take hardware work only on named
triggers.

## Findings preserved rather than “fixed” by wording

1. Direct `agent_loop_holo.run()` defaults to `verify_mode="off"`; the battery selects
   gate mode, while the physical calibration intentionally used captured pixels and
   left verification off.
2. D-a/D-b/D-c have credible decomposed evidence, but no one physical run composes a
   wrong terminal claim, live refusal, recovery, actor, and HID.
3. Ordinary run directories do not contain the full request/tool-output walk by
   themselves; production Holo requests share `runs/logs/holo_requests.jsonl`, and the
   Pi bridge log lives on the appliance.
4. Ignored caches and build dot-directories exist despite AGENTS.md §1. They were
   inventoried, not destructively removed.
5. Slice A and Slice B remain bounded but add 1,519 lines of tool/page/test code. The
   maintenance recommendation is to freeze four contracts plus one physical flow, not
   add an abstraction layer.

The Git/code-size/workspace inventory is preserved at
`runs/project_state_overview_20260723_203110/audit.txt`.

## Validation

- Complete deterministic suite:
  `runs/project_state_overview_20260723_203110/full_pytest.txt`.
- Documentation-layout gate:
  `runs/project_state_overview_20260723_203110/docs_layout.txt`.
- Prior implementation baseline, still directly applicable:
  `runs/serving_parser_suite_20260723_202048/full_pytest.txt` (184/184).

No rig run was performed or required for this documentation-only reconciliation. No
remote push was performed.
