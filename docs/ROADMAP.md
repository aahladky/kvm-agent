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

**Primitive reliability gaps (bounded, evidence-triggered):**

- [x] Firmware watchdog, safe host retry, UART resync pause, mouse-ABS
  retain/resend on USB suspend, and PONG visibility are implemented and deployed.
  The camera-verified HID gate passed after deployment. The strict Phase-0
  long-idle/fault-injection soak remains postponed, so unattended-duration confidence
  is lower than functional confidence.
- [x] HID mode is fixed at boot; the host has no mid-task mode-switch surface.
- [ ] No firmware-side timed-motion primitive: smooth drags ride host/UART jitter.
  Add one only when a chosen task demonstrates the need.
- [ ] Absolute-pointer behavior on multi-monitor targets is unverified. Do not claim
  multi-monitor support until a real target requires and exercises it.
- [ ] Target power recovery is manual. A warm reboot can strand the laptop NIC, while
  a full shutdown/boot restores it. Build power control only if this repeatedly blocks
  useful runs.

**Architecture gaps (the real project):**

- The loop is **medium-horizon, not long-horizon**: one flat reactive sequence capped
  by `max_steps`, with global process state and no concurrency contract.
- Image history is bounded to three frames, but text/history has no hierarchy,
  summarization, recall, or durable task memory.
- `update_plan` is telemetry, not control, and was emitted zero times over D-b's 76
  steps (also zero across the prior 19 recorded battery runs). There is no plan for
  the harness to sequence.
- D-a/D-b/D-c provide a stateless oracle and terminal-claim gating. That is real
  independent verification, but only at `finished`: there is still no subgoal unit
  with its own postcondition. Direct `run()` also defaults verification off; a caller
  must explicitly inject the verifier and select gate mode.
- Recovery can re-observe after a stale-click or rejected terminal claim, but
  stuck/no-progress/max-steps outcomes still abort. There is no plan-level retry,
  demotion, or resume.
- Grounding remains single-shot. The pre-fire guard proves the target region did not
  change since the decision; it does not prove the selected coordinate or intended
  element was correct.
- Tool results now report magnitude, spread, and strongest region rather than a bare
  changed/unchanged bit. This is useful state-change evidence, not semantic success.
- The controlled integration harnesses are deliberately fixed at four model contracts
  and one physical flow. Together their tool/page/test code is 1,519 lines, so their
  scope must stay frozen; they cannot become another scenario framework.

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
- **Current:** implementation deployed and functionally camera-verified; overnight
  soak postponed, so the strict duration gate is not closed.

### Phase 1 — Seal the model seam (no new model)
- **Goal:** the model becomes a swappable component.
- **Do:** formalize `holo.py` as *one implementation* of a capability interface (`propose` / `ground` / `verify`); move the native-shaped conversation protocol (history layout, tool_output channel, image trim) fully behind it, so the loop speaks a model-neutral vocabulary.
- **Gate:** a fake session can drive the loop without touching it, and fixed frames can
  traverse the real production session/request/parser seam with auditable output.
- **Current:** complete. `ModelSession` is the seam, fake-session/golden-transcript
  tests pass, and the four-case real-model contract smoke passed live.

### Phase 2 — Subgoal unit + independent verification (the keystone)
- **Goal:** "long" starts becoming real; verification stops being self-judged.
- **Do:** restructure the loop from flat-step to **subgoal-gated**. Give the planner a real (non-decorative) plan that the harness sequences. Pull verification into its own call/prompt (still Holo is fine) — a postcondition oracle separate from the actor. Gate progression on it (refuse-to-advance, don't inject retries).
- **Gate:** controlled positive and negative terminal-state cases prove that
  confident-wrong completion is refused without blocking a true completion.
- **Current:** terminal portion D-a/D-b/D-c implemented and accepted on decomposed
  evidence. The verifier's live positive path measured 0/9 false refusals; offline
  replay rejected 80/80 adversarial false claims. The actual subgoal unit (D-d) is
  not implemented and remains gated on observing its target failure mode. Therefore
  Phase 2 as a whole is not complete.

### Phase 3 — Bounded / hierarchical memory
- **Goal:** kill context bloat; make experience reusable.
- **Do:** working memory = planner's curated context (plan + relevant notes). Externalize episodic/procedural — start simple (a structured store fed from `RunRecorder`) before reaching for Hindsight. Recall on subgoal open, retain on close.
- **Gate:** long runs no longer degrade from context growth; retrieval measurably helps (fewer steps / higher completion on repeat task types).
- **Current:** not started; no measurement requires it yet.

### Phase 4 — The oversight dial + macro caching
- **Goal:** speed and graceful degradation.
- **Do:** add manager mode (hand-off) sharing the Phase-2 substrate; deterministic replay for proven subsequences; mode selection driven by verifier-reliability stats from memory; demote-on-trouble recovery replacing abort-only.
- **Gate:** a subgoal type promoted to manager mode holds its completion rate while cutting planner calls/latency; demotion catches the cases where it doesn't.
- **Current:** not started; depends on a trustworthy subgoal unit and per-type
  verifier data that do not exist yet.

### Phase 5 — Multi-model / hardware decomposition
- **Goal:** dedicated fast grounder + higher-precision grounding; planner runs rarely.
- **Do:** split grounder/verifier onto B580 (or co-resident on B70), planner on B70, as separate inference instances (data parallelism — the simple default; see §3's correction on why tensor-parallelism across the two same-gen Battlemage cards is now a real option worth a line item here too, not required). **Only if measurement says so.**
- **Gate:** (a) small-model grounding holds up on the controlled capture fixtures and
  explicitly chosen acceptance tasks versus the 35B baseline; (b) B580 vision-encode
  latency clears the per-step budget, and 12GB fits the required model. If either fails
  → co-resident on B70.
- **Current:** serving co-residency prerequisite is proven, but the grounding-rate and
  B580 model/latency gates are unmeasured. Do not add the model yet.

### Phase 6 — External tools (last, one at a time)
- **Goal:** capability the target can't provide.
- **Do:** graduate to a real memory DB (Hindsight) if the simple store isn't enough; add scoped web search (allowlist, read-only, rate-limited, planner-gated) with the untrusted-data + epistemic-firewall discipline.
- **Gate:** each tool added alone, measured to add real capability without adding silent failure; prompt-injection handling tested adversarially.
- **Current:** not started.

---

## 5. Measurement — controlled seams before capability claims

The harness has no upstream reference implementation, so each owned boundary needs a
small controlled oracle. Deterministic fake-session tests prove control flow; fixed
frames through the real model prove the request/parser contract; one repository-owned
calibration surface proves capture→model→HID→capture. Real application work is a
separate capability question, selected only when a concrete claim needs it.

The maintainable implementation is fixed at four live-model frame contracts and one
physical calibration flow. Both are implemented and live-validated; they remain
explicit boundary checks, not an ordinary every-change gate. Design and evidence:
`docs/PLAN_2026-07-23_model_harness_integration_testing.md` and
`docs/SESSION_2026-07-23_physical_calibration_smoke.md`.

Track: steps, completion, per-step latency/tokens, refusal-vs-exhaustion, terminal
verdicts, and guard refusals are computed. Terminal false-refusal measured 0/9 on the
live D-b positives; live false-confirmation remains unmeasured because that run had no
true-fail terminal claim. Grounding rate still has only raw material (frames plus the
unused `element` descriptions), and per-subgoal verifier reliability cannot exist
until subgoals do.
- **Grounding rate** — did the click land on the intended element? (tells you if grounding is the bottleneck → gates Phase 5)
- **Verifier false-confirmation rate** per subgoal type — the promotion/demotion signal for manager mode (gates Phase 4)
- **Steps-to-completion** and **completion rate** — overall health; regression detector
- **Per-step latency distribution** (esp. grounding / vision-encode) — the Arc/decomposition signal
- **Honest-refusal vs budget-exhaustion** — never let one masquerade as the other

---

## 6. Solo-builder guardrails

- **Everything above is a destination.** Build the piece the day the numbers demand it, not before.
- **The heavy parts are write-once-run-forever** (HID primitives, settle model, cached macros, deterministic replays) — they don't tax you once they work. **The intelligent/stateful/integration tiers are what tax a lone maintainer** — add them last, one at a time.
- **The harness is bespoke by necessity** (no upstream reference artifact exists for
  camera-as-truth + no-software-access). Keep it thin and test each owned seam against a
  controlled artifact before using longer real-world runs to make capability claims.
- **Port patterns, not code.** Map each hard sub-problem to its studied shape (max-iteration caps, failure-threshold escalation, explicit termination, plan-and-execute vs ReAct, state-machine control) and adopt the *shape* — the code stays yours. You've already reinvented several (stuck limit, no-progress abort, confirm-first); do it deliberately.
- **Resist heavy frameworks** (e.g., LangGraph). For a solo maintainer whose edge is holding the system in your head, owning your code *plus* someone else's abstraction inverts the benefit — especially when it breaks across the exact camera/HID boundary it was never designed for. Steal the patterns; keep the implementation thin and yours until you hit a specific wall a framework specifically solves.
- **Keep the surface small enough to hold in your head.** That constraint is not a limitation here — it's the design methodology.

---

## 7. Immediate next steps

1. **Stop building infrastructure and use the system for bounded real work.** The
   offline seam, real-model contract, and physical action path are sufficiently
   characterized to expose the next genuine bottleneck. Pick tasks because they are
   useful or because they test one explicit capability claim—not because a standing
   battery demands another hour.
2. **Run only the smallest affected gate.** Ordinary changes run relevant offline
   tests and the full deterministic suite. Model adapter/prompt/parser changes add
   Slice A. Capture/coordinate/HID/closed-loop changes add Slice A then Slice B. Neither
   live slice is an every-change ritual.
3. **Turn escaped defects into minimal fixtures.** Preserve the exact frame, prompt,
   raw output, transformation walk, and offline replay required by AGENTS.md §2. Add a
   controlled case only when the existing four-plus-one set could not have exposed the
   defect.
4. **Keep D-d deferred.** Build an explicit subgoal planner only after trustworthy
   operation produces a repeated failure that terminal gating cannot handle—especially
   confident-wrong intermediate progress. The current evidence contains under-confident
   correct progress, not the failure D-d was designed to fix.
5. **Take maintenance slices only on clear triggers.**
   - Run the overnight HID soak when the rig can be spared or if long-idle mouse death
     recurs.
   - Add power control only if manual full shutdown/boot repeatedly blocks work.
   - Add bridge keep-alive only if the deployed suspend fix still fails.
   - Add timed drag or multi-monitor support only for a selected task that requires it.
6. **Close evidence-layout debt as one isolated cleanup.** Put complete request/tool
   output evidence inside each run directory, reconcile the Pi bridge log with
   `runs/`, and prevent routine test/build commands from regenerating hidden project
   caches. This is a working-agreement compliance fix, not an actor redesign.

---

*Compass, not contract. When in doubt: harden the primitive, keep the harness thin,
and use the smallest deterministic oracle that exercises the boundary.*
