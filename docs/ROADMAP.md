# Self-Hosted KVM Agent — Design & Roadmap Guide

*Personal reference. Synthesized from a full design session. Treat as a compass, not a contract.*

---

## 0. How to read this

- **The architecture below is a destination, not a to-do list.** Nothing here should be built until measurement forces the piece. A withheld component is a minor loss; a component you now have to maintain and didn't need is a real one.
- **Measurement gates every step.** Each roadmap phase names the number that tells you it's worth doing and the number that tells you it worked.
- **The principles in §1 are the part that doesn't change.** If you lose the thread, start there.

---

## 1. North star & non-negotiable principles

**Purpose.** A self-hosted agent that carries out long-horizon tasks on *any* desktop through hardware only — HDMI in, USB-HID out — so the target needs zero software installed. Everything else is downstream of that constraint.

**The constraint is the design.** No software access on the target is a hard stop. It's *why* perception is pixels-only and action is blind HID, and it's why the primitive (a reliable click) is the product, not the plumbing beneath a product.

**Principles that govern everything:**

1. **The camera is the only ground truth for the target.** No action is trusted to self-report. Firmware "online" flags lie; ACKs prove delivery to the wire, not effect on screen. Only the pixels adjudicate.
2. **Make failure loud.** Silent no-ops and swallowed errors are the enemy. Every dropped/rejected/undelivered action must be visible to the model, the recorder, and you.
3. **Primitive + postcondition, from day one.** Every capability — a click, a subgoal, a whole task — ships with a camera-checkable postcondition. This is the one rule the whole system runs on. You learned it reactively at the click level; apply it proactively at every tier.
4. **Intelligence and frequency run inversely.** The smartest component runs rarest; the thing that runs every step is the cheapest and narrowest.
5. **Solo scope discipline.** Bus factor is one. Be *more* ruthless about scope than a team would be — you are the entire maintenance department. Never let code generation outrun your comprehension of it.

---

## 2. Where things stand

### Solid — write-once, run-forever (protect these; they don't add daily load)

- **Firmware (ported PiKVM pico-hid, RP2350):** absolute-pointer HID (resolution-agnostic, 0–32767), lock-LED readback in every PONG, per-command confirmation. Phantom-scroll clear bug caught and fixed (worth upstreaming).
- **Transport (hid_bridge / pikvm_proto / appliance client):** CRC-framed binary UART, ACK'd HTTP surface, persistent wire-level command log.
- **Capture / env:** freshness discipline (monotonic seq, `wait_newer`, dead-vs-stale-vs-settled), single-home tile-diff metric, smart settle, `verify_hid` gate that distrusts firmware online flags, adopt-actual-negotiated-resolution.
- **Instrumentation:** `RunRecorder` — per-step frame, raw response, tokens, timing, action. Already a regression suite.

### Thin — where the work is

**Firmware reliability gaps (cheap, high-impact for unattended runs):**
- [x] No hang watchdog in `main()` — enable HW watchdog + pet it, or the whole input channel can wedge silently mid-task. *[CODE LANDED 2026-07-22 (Slice B, docs/SESSION_2026-07-22_slice_b_firmware_hardening.md): `watchdog_enable(1000, true)` + gated pet in `main.c`; an unpetted-hang reboot surfaces as a new PONG bit (`PH_PROTO_PONG_WATCHDOG_REBOOTED`), loud in `/health` and the wire log. Firmware compiles clean (`-Wall -Wextra`); rig confirmation rides the Phase-0 soak (`tools/soak.py`), not yet run.]*
- [x] UART framing resyncs only on idle gaps → enforce **lock-step host discipline** (send → await PONG → retry) so the host's own pause creates the resync gap. Buys confirmation *and* self-healing framing in one rule. *[correction 2026-07-22: half done — the host already blocks send→await-PONG per command under a lock (`pikvm_proto._roundtrip`); only the retry-on-failure half is missing.] [CODE LANDED 2026-07-22 (Slice B): the missing half — NACK retries any command, an ambiguous (no/garbled) response retries only idempotent commands (never MOUSE_WHEEL), 150ms pre-retry pause doubles as the resync trigger. Offline-tested (`tests/test_pikvm_proto_retry.py`); soak confirmation pending.]*
- [x] Mode changes (`SET_KBD`/`SET_MOUSE`) re-enumerate USB → set ABS once at boot, never switch mid-task. *[correction 2026-07-22: already the actual behavior — the host defines no SET_KBD/SET_MOUSE commands at all; mode is set once at boot from GPIO defaults (abs USB mouse) and persists across reboots. Only dead firmware-side paths remain.]*
- [ ] No firmware-side timed-motion primitive → smooth drags ride host+UART jitter. Add "drag A→B over N ms" when a task needs it.
- [ ] Verify absolute-pointer behavior on multi-monitor targets before trusting it there.

**Architecture gaps (the real project):**
- Loop is **medium-horizon wearing a long-horizon label** — flat reactive step loop, capped by max_steps.
- **Memory is unbounded in-context** — notes accumulate as un-evicted assistant turns; no hierarchy, no summarization.
- **`update_plan` is decorative** — telemetry, not control. Nothing in the loop sequences/verifies/recovers off it. *[correction 2026-07-22: worse than decorative — **unused**. It has never once been emitted: zero occurrences across all 19 recorded battery runs (`grep '"update_plan"' runs/battery_*/step_*.json`). Benign cause: the native prompt says to plan "within your first 2-3 steps for non-trivial tasks (>5 steps or multiple sources)" (`docs/native/local-desktop-2026-06-12.j2:132-142`) and every battery task finishes in 1-10 steps, so the model is obeying its prompt. A task-length problem, not a prompt problem — and testable. Consequence for Phase 2: a harness-sequenced plan cannot be built by making the model's existing plan load-bearing; there is no plan to make load-bearing yet.]*
- **No subgoal decomposition** — no independently-verified units.
- **Verification is change-detection, not correctness** — frame-diff says *something moved*, and the model alone judges *whether it was right*. No independent postcondition oracle at runtime (the battery has graders; the loop does not). *[correction 2026-07-22: the battery's "graders" are the human operator (p/f/v grades in `tools/battery.py`) — NO automated postcondition oracle exists anywhere yet; automated fail-closed vision grading is deferred per PROJECT_STATE.]*
- **Recovery is abort-only** — stuck / no-progress / max_steps all just end the run.
- **Grounding is single-shot** — no re-check, no set-of-marks; the model's `element` description is captured but unused as a verification signal.
- *[added 2026-07-22, from the first complete battery (SESSION_2026-07-22):]* **Decide-act TOCTOU staleness** — the screen can re-flow during the model's ~15–20s think, so a click correct against the decision frame lands on whatever slid under it (paint_line s09). The measured dominant live failure source. Fix: pre-fire target-tile guard (refuse-to-fire on change, re-observe — gating, not injection).
- *[added 2026-07-22:]* **The tool-result signal is semantically misleading** — the changed/unchanged binary confirmed real-but-irrelevant pixels as success at decision-critical steps. Needs magnitude + region.

---

## 3. Target architecture (the map)

### Three tiers

| Tier | Card | Cadence | State | Faces |
|---|---|---|---|---|
| **Planner** (big) | B70 | rare (per subgoal) | stateful | the human + the agent's own world |
| **Grounder / Verifier** (small) | B580 (or co-resident) | constant (per action) | stateless | the pixels only |
| **Harness + HID** | — | always-on | durable | the loop |

The planner holds the *task contract*: takes the goal, owns the plan and task-level state, decides done, writes the report, escalates. The grounder/verifier are pure perceptual functions — (frame, narrow question) → answer, no memory, no task awareness. The harness is deterministic control flow and carries your hard-won plumbing. **Only the planner holds state** — that's what bounds the context problem to one place.

### The subgoal-with-postcondition — the keystone

The single unit that simultaneously gives you: a **verification floor** (each subgoal has a camera-checkable postcondition), a **memory-chunking boundary** (recall on open, retain on close), and a **recovery unit** (a subgoal can be retried/demoted/escalated without killing the run). Build this first among the architecture pieces; the rest hang off it.

Postcondition gating keeps the anti-contamination property you already protect: you don't inject a retry *for* the model, you *refuse to advance* until the postcondition holds. Gating progression ≠ injecting action.

### The oversight dial (tight ↔ manager) — one axis, not two modes

- **Tight:** planner re-engages every step. Accuracy win (independent grounding + verification), planner still in the hot path.
- **Manager:** planner sets subgoal + postcondition, then hands off; grounder/verifier + cached macros drive the steps; planner wakes only on completion, failure, or uncertainty. Latency/cost win — but only as safe as the verifier gating it.

Rules that make this clean:
- **Mode is a property of a subgoal, not the run.** One task interleaves both.
- **Shared substrate.** Same grounder, verifier, HID primitives, escalation. The *only* difference is re-engage interval. Build the manager loop; tight is "interval = 1."
- **Mode is also a recovery action.** Trouble → *demote* manager→tight and retry with the planner watching → still failing → escalate to human. One dial, three stops, replacing abort-only.
- **Manager mode is earned.** A subgoal type is promoted to hand-off only once its verifier clears a false-confirmation bar on the battery; it demotes the moment that slips.

### Models & hardware

- **Holo3.1 is a family** (0.8B → 35B-A3B). The 35B is **MoE (A3B = ~3B active)**: memory-heavy (all experts resident), compute-light/fast. Decomposing *within the family* means the adapter you already paid for largely carries over — near-zero new conformance cost.
- **B70 (Big Battlemage, 32GB, 608 GB/s, strong XMX) = planner.** MoE profile fits perfectly (32GB holds experts; strong compute for the active path).
- **B580 (Battlemage, 12GB) = grounder/verifier.** *[correction 2026-07-23: this was written as "A770 (Alchemist, 16GB)" — a card mix-up caught by the operator. The actual second card is a B580, SAME generation as the B70 (both Battlemage), 12GB not 16GB. Every A770/Alchemist-specific claim below is corrected or flagged accordingly.]*
- **Same generation → the mixed-generation constraint is gone; one-model-per-card is still the simple default, not the only sane one.** *[correction 2026-07-23: the original "don't tensor-parallel across Alchemist + Battlemage, use data parallelism" reasoning doesn't apply — both cards are Battlemage. Tensor-parallelism across the pair is now a real option worth a line in Phase 5's measurement, not just data parallelism (separate vLLM/IPEX-LLM instances, one per GPU) — but data parallelism stays the simpler solo-maintainer default until measurement says the extra complexity earns its keep.]*
- **Arc caveats to measure/plan around** *(the vision-encoding-weakness caveat below was Alchemist-specific and likely does NOT apply to the B580 — Battlemage shares the B70's "strong XMX" characteristics — but re-measure rather than assume; not verified either way yet)*:
  - Grounding *latency* on the B580 still needs measuring before committing the per-click path (12GB budgets a smaller Holo3.1 family member than the 35B planner — check which size actually fits before assuming one). Fallback if it doesn't clear budget: both Holo models co-resident on the 32GB B70; B580 stays free for other use.
  - *[correction 2026-07-23: "the A770 already has a job (photo/video), claiming it full-time is a real cost" — WRONG. The second card's actual media workload is a transcode roughly once a month; that's not a real claim on it, and uptime/24-7 availability isn't a near-term concern for this project either. The card is effectively free for the grounder/verifier role whenever Phase 5's measurement calls for it — this was §7 item 4's open question, now answered.]*
  - Vision encoder eats context (budget the haircut). FP8 dynamic-quant-at-load spikes system RAM (~50GB+) — prefer prebuilt GGUF/quantized checkpoints.

### Two action surfaces & tool use

- **In-band:** through the target via HID, camera-verified. Governed by no-software-access.
- **Out-of-band:** the planner's own tools (memory, web) — executed host-side, never through the KVM. These live on *your* side of the air gap, so they don't violate the constraint — but must never be confused with target state.
- **Tools attach to the planner tier only.** Grounder/verifier stay pure. The harness is a router at the planner's output boundary: device action → grounder+HID; tool call → host-side executor → result back to planner context.
- **The cheapest click is the one you didn't make** because you recalled or looked it up instead. Route information-acquisition out-of-band; spend HID only on what must happen on the target.
- **Don't let out-of-band tools do the task *around* the target** — that's a different system. They *inform* in-band actuation; they don't replace it.

### Memory (e.g., Hindsight — MIT, locally hostable, retain/recall/reflect)

Its design thesis (separate evidence from inference; distinct epistemic networks) *is* your camera-as-truth discipline extended from within-run to across-run. Map it:
- **World network** ← out-of-band facts (web results, looked-up values). Truth about the world.
- **Experience network** ← your `RunRecorder` trajectories, promoted from post-hoc logs to live recall. Powers **mode selection** (was the verifier reliable on this subgoal type?) and **macro caching** (what click sequence worked last time?).
- **`reflect`** ← offline consolidation that distills runs into reusable procedure. Closes the verify → record → retrieve → inform-next-run loop.
- Hooks onto subgoal boundaries: **recall on open, retain on close.** Working memory stays the planner's bounded context.

### The epistemic firewall (burn this in)

- **Web tells you what to type; the camera tells you whether it got typed.** Never let a web/memory result stand in as evidence about the screen.
- **Treat all retrieved/observed content as untrusted data, not instructions** — a fetched page (or even rendered target text) is a prompt-injection surface, and that matters far more for an autonomous long-horizon agent than a chat assistant. Keep observation and instruction channels separate in the planner's prompt.

---

## 4. Roadmap (sequenced, measurement-gated)

Each phase: **Goal** / **Do** / **Gate to proceed.** Do them roughly in order; the ordering encodes "primitives first, intelligent tiers last, everything earned by measurement."

### Phase 0 — Harden the primitive for unattended runs
- **Goal:** the input channel never dies silently over a long run.
- **Do:** firmware watchdog; lock-step host discipline (send→PONG→retry) for confirmation + UART self-healing; set-ABS-once; multi-monitor abs check if in scope.
- **Gate:** an overnight idle-plus-periodic-action soak with zero silent wedges; every injected fault surfaces loudly.

### Phase 1 — Seal the model seam (no new model)
- **Goal:** the model becomes a swappable component.
- **Do:** formalize `holo.py` as *one implementation* of a capability interface (`propose` / `ground` / `verify`); move the native-shaped conversation protocol (history layout, tool_output channel, image trim) fully behind it, so the loop speaks a model-neutral vocabulary.
- **Gate:** you could stub a second `propose/ground/verify` implementation without touching the loop. Battery scores unchanged (pure refactor).

### Phase 2 — Subgoal unit + independent verification (the keystone)
- **Goal:** "long" starts becoming real; verification stops being self-judged.
- **Do:** restructure the loop from flat-step to **subgoal-gated**. Give the planner a real (non-decorative) plan that the harness sequences. Pull verification into its own call/prompt (still Holo is fine) — a postcondition oracle separate from the actor. Gate progression on it (refuse-to-advance, don't inject retries).
- **Gate:** confident-wrong progress that the old loop missed now gets caught at a subgoal boundary; battery completion rate up, false-"finished" rate down.

### Phase 3 — Bounded / hierarchical memory
- **Goal:** kill context bloat; make experience reusable.
- **Do:** working memory = planner's curated context (plan + relevant notes). Externalize episodic/procedural — start simple (a structured store fed from `RunRecorder`) before reaching for Hindsight. Recall on subgoal open, retain on close.
- **Gate:** long runs no longer degrade from context growth; retrieval measurably helps (fewer steps / higher completion on repeat task types).

### Phase 4 — The oversight dial + macro caching
- **Goal:** speed and graceful degradation.
- **Do:** add manager mode (hand-off) sharing the Phase-2 substrate; deterministic replay for proven subsequences; mode selection driven by verifier-reliability stats from memory; demote-on-trouble recovery replacing abort-only.
- **Gate:** a subgoal type promoted to manager mode holds its completion rate while cutting planner calls/latency; demotion catches the cases where it doesn't.

### Phase 5 — Multi-model / hardware decomposition
- **Goal:** dedicated fast grounder + higher-precision grounding; planner runs rarely.
- **Do:** split grounder/verifier onto B580 (or co-resident on B70), planner on B70, as separate inference instances (data parallelism — the simple default; see §3's correction on why tensor-parallelism across the two same-gen Battlemage cards is now a real option worth a line item here too, not required). **Only if measurement says so.**
- **Gate:** (a) small-model grounding holds up on *your analog capture* vs the 35B baseline, measured on the battery; (b) B580 vision-encode latency clears your per-step budget, and 12GB actually fits the family member you need. If either fails → co-resident on B70 (the B580 has no real competing workload to protect it from — §3's correction).

### Phase 6 — External tools (last, one at a time)
- **Goal:** capability the target can't provide.
- **Do:** graduate to a real memory DB (Hindsight) if the simple store isn't enough; add scoped web search (allowlist, read-only, rate-limited, planner-gated) with the untrusted-data + epistemic-firewall discipline.
- **Gate:** each tool added alone, measured to add real capability without adding silent failure; prompt-injection handling tested adversarially.

---

## 5. Measurement — the oracle that replaces "port-and-diff-against-reference"

The harness has no upstream to conform to, so **the battery is your reference.** The methodological shift for every bespoke layer: *author-and-diff-against-battery.* Keep strengthening it as you go.

Track (you already log most of this): *[correction 2026-07-22: "most" = steps, completion, per-step latency/tokens, and the refusal-vs-exhaustion split (via `answer_text`). The two metrics below that gate Phases 4/5 are NOT computed — grounding rate has only raw material (frames + the unused `element` descriptions), and false-confirmation is unmeasurable until a verifier exists (Phase 2).]*
- **Grounding rate** — did the click land on the intended element? (tells you if grounding is the bottleneck → gates Phase 5)
- **Verifier false-confirmation rate** per subgoal type — the promotion/demotion signal for manager mode (gates Phase 4)
- **Steps-to-completion** and **completion rate** — overall health; regression detector
- **Per-step latency distribution** (esp. grounding / vision-encode) — the Arc/decomposition signal
- **Honest-refusal vs budget-exhaustion** — never let one masquerade as the other

---

## 6. Solo-builder guardrails

- **Everything above is a destination.** Build the piece the day the numbers demand it, not before.
- **The heavy parts are write-once-run-forever** (HID primitives, settle model, cached macros, deterministic replays) — they don't tax you once they work. **The intelligent/stateful/integration tiers are what tax a lone maintainer** — add them last, one at a time.
- **The harness is bespoke by necessity** (no reference artifact exists for camera-as-truth + no-software-access). It's where the solo-vs-team gap is *widest*, because integration and long-horizon emergent bugs only surface in full runs. So here: thinnest scope, most rigorous coverage. Everywhere else you can afford ambition.
- **Port patterns, not code.** Map each hard sub-problem to its studied shape (max-iteration caps, failure-threshold escalation, explicit termination, plan-and-execute vs ReAct, state-machine control) and adopt the *shape* — the code stays yours. You've already reinvented several (stuck limit, no-progress abort, confirm-first); do it deliberately.
- **Resist heavy frameworks** (e.g., LangGraph). For a solo maintainer whose edge is holding the system in your head, owning your code *plus* someone else's abstraction inverts the benefit — especially when it breaks across the exact camera/HID boundary it was never designed for. Steal the patterns; keep the implementation thin and yours until you hit a specific wall a framework specifically solves.
- **Keep the surface small enough to hold in your head.** That constraint is not a limitation here — it's the design methodology.

---

## 7. Immediate next steps

0. *[added 2026-07-22, per §0's own rule — measurement forces this piece first:]* **TOCTOU pre-fire guard + tool-result magnitude/region** — the first complete battery measured target-side async re-flow vs the ~15s step cadence as the dominant failure source. Host-side only, offline-testable, the runtime slice of Phase 2's "verification stops being self-judged." **DONE AND RIG-CONFIRMED 2026-07-22** (`agent_loop_holo.py`, `docs/SESSION_2026-07-22_roadmap_alignment.md`; confirmed on two live GNOME batteries, 5/5 and 5/5 (1 void), 4 legitimate guard refusals across 64 steps, no task regressions — `docs/SESSION_2026-07-22_toctou_guard_rig_confirmation.md`). Fully closed.
1. **Phase 0 firmware hardening** — watchdog + lock-step host discipline. Cheap, high-leverage, protects every future long run. **CODE LANDED AND DEPLOYED 2026-07-22/23** (`docs/SESSION_2026-07-22_slice_b_firmware_hardening.md`) — watchdog, host retry, mouse-suspend retain+resend, PONG visibility bits, `tools/soak.py`; BOOTSEL-flashed, deployed to the Pi 5, `/health` + the camera-verified HID gate both passed live. **The overnight soak gate itself is POSTPONED** (operator decision — the inconvenience it guards against doesn't currently justify tying up the rig for 8+ hours); not abandoned, `tools/soak.py --hours 8` whenever the rig is free that long.
2. **Phase 1 seam** — turn `holo.py` into a `propose/ground/verify` interface; get the native conversation protocol out of the loop. Unblocks everything downstream and costs only a refactor. **DONE 2026-07-22** as `decide`/`commit` (not three methods — see `kvm_agent/models/base.py`'s docstring for why `verify` waits for Phase 2); `docs/SESSION_2026-07-22_model_seam_slice_c.md`.
3. **Map your existing guards to named patterns** — inventory stuck-limit / no-progress / confirm-first against the studied shapes and see how much "agent harness" is actually left to write. Likely less than the phrase implies. **DONE 2026-07-22** (`docs/REPORT_2026-07-22_harness_pattern_inventory.md`): confirmed — one max-iteration cap, three instances of the same failure-threshold circuit breaker, one human-confirmation gate, and the model's own termination call. The only real gap against the five studied shapes is plan-and-execute, and closing it is Phase 2, not a naming exercise.
4. **The second-card question** — is it an AI card or a media card? **ANSWERED 2026-07-23** (and the card itself corrected — see §3: it's a B580, not an A770): effectively an AI card. Its actual media workload is a transcode roughly once a month, not a real claim on it, and uptime/24-7 availability isn't a near-term concern for this project. Free for Phase 5's grounder/verifier role whenever measurement calls for it; no layout decision is forced by contention.
5. **Phase 2 — subgoal unit + independent verification.** **IN PROGRESS 2026-07-23** (`docs/PLAN_2026-07-22_phase2_subgoal_verification.md`): **D-a DONE** — the independent postcondition oracle passed its offline replay and adversarial claim-resistance gates; **D-b DONE AND RIG-CONFIRMED** — shadow wiring, four longer tasks, and metrics landed, with 0/9 live false refusals, so D-c's hard gate clears. **D-c is next** — flip both gates on: in-loop terminal gating (k-strikes → run ends FAILED, loudly) and battery auto-grading with a defined human ground-truth sample. **D-d remains measurement-gated** — `update_plan` was unused (0/76), settling its mechanism as an explicit planner call, but the extended battery was another 10/10 sweep and produced no confident-wrong progress case for the proposed subgoal unit to catch.

---

*Compass, not contract. When in doubt: harden the primitive, keep the harness thin, let the camera and the battery decide.*
