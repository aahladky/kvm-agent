# EvoCUA-8B on the physical rig — root cause, fixes, and integration plan
_2026-06-18 deep audit_

## TL;DR
The erratic runs were **not** caused by quant (Q4/Q5/Q8/imatrix), history depth, target
size, or the HID/capture pipeline. The dominant root cause is a **brittle response
parser interacting with verbatim history replay**, inside the official `EvoCUAAgent` we
adopted via `pico_env`. The whole multi-session quant/history saga was mostly reading
**noise this bug produced on single deterministic samples**.

Three fixes are in place and verified:
1. Normalize tool_call formatting at receipt (root-cause fix).
2. Harden the parser to be format-agnostic (defense in depth).
3. Verify-before-terminate guard (kills false-positive successes).

---

## The bug
The model **mimics the formatting of the tool_calls in its replayed history**, and the
official `_parse_response_s2` only parsed a tool_call when `<tool_call>` and the JSON were
on **separate lines**. The common GGUF/Ollama form `<tool_call>{json}</tool_call>` on one
line was **silently dropped** → no action that step.

### Decisive A/B (live model, Q5-clean, temp 0.01) — only the history *format* changed
| Replayed history response | Model then emits | Parser | `+` grounding |
|---|---|---|---|
| `<tool_call>\n{json}` (newline) | newline 12/12 | parsed 12/12 | correct (672,769) 12/12 |
| `<tool_call>{json}` (same line) | same-line 12/12 | **dropped 12/12** | **no action 12/12** |

### Why it cascades in a real run
1. Model occasionally emits a same-line tool_call (stochastic).
2. Stored verbatim → replayed as history.
3. Model mimics same-line format → parser drops it → **no action**.
4. The dropped response is appended to history → reinforces the bad format.
→ stalls, "re-click loops", false-positive `terminate`, run-to-run irreproducibility —
exactly the symptoms previously misattributed to quant/history.

### What was ruled OUT (with evidence)
- **HID / capture / coordinates correct.** Measured real button pixels: operator column
  x≈677; the model's correct `+` clicks logged at [672,771] land dead on.
- **Target size is not the cause.** On the tiny calculator with clean history, `+` grounds
  correctly ~every time; the bimodal landings were a *symptom* of dropped/garbled history,
  not resolution.
- **Coordinate math (/999), smart_resize, prompts, tool schema** — byte-faithful to upstream.

---

## Fixes (all in `evocua/mm_agents/evocua/evocua_agent.py` unless noted)

### 1. Root-cause fix — normalize at receipt (`_predict_s2`)
Normalizes tool_call delimiters onto their own lines **before both parsing and storing**,
so the parse succeeds AND replayed history stays canonical (mimicry now reinforces the
*good* format).
```python
if response:
    response = re.sub(r"<tool_call>\s*", "<tool_call>\n", response)
    response = re.sub(r"\s*</tool_call>", "\n</tool_call>", response)
self.responses.append(response)
```
**Verified:** same-line-history condition **0/12 → 10/10** parsed, `+` correct.

### 2. Parser hardening — format-agnostic extraction (`_parse_response_s2`)
Replaced the line-based scan with a DOTALL regex over `<tool_call>...</tool_call>` plus a
bare-`{…}` fallback. **Verified** correct on same-line, newline, all-one-line,
pretty-printed, bare-json, `type`, and `terminate`.

### 3. Verify-before-terminate (`verify.py` + wired into `run_probe.py`)
On `terminate(success)`, OCR the result region (reuses `score_batch`'s calibrated
`RESULT_BBOX`) and only count success if the display matches `expected`. Records
`term_status` ∈ {`success`, `false_positive`, `done_unverified`}. Degrades gracefully
where tesseract isn't installed. **Verified:** true run reads `61`→success; a false-positive
run reads `55`→flagged.

> Note: the original hand-rolled `evocua.py` already used a regex that tolerated both
> formats; the regression came from switching to the official line-based parser. Fixes 1–2
> bring the official agent up to that robustness.

---

## Recommendations — properly integrating with the model

### A. Re-baseline now that the bug is gone (highest priority)
- **Measure success *rates*, not single runs.** `temp 0.01` is NOT deterministic on Ollama;
  every prior Q5-vs-Q8 / history-depth conclusion is within this bug's noise. Run K≥10 reps
  per config with `run_probe.py` (auto AC-reset already in `pico_env.reset`) and read
  `term_status` from the manifest.
- **Expectation:** most of the Q5↔Q8 gap should collapse. Decide quant on the *re-baselined*
  rates, not the old saga. The 12 GB mobile 4080 argument still favors Q5 if rates tie
  (it's the only quant that fits num_ctx 16384 for spec history depth).

### B. Treat the agent's I/O contract as a first-class integration surface
- The failure was a **format contract mismatch** between GGUF output and a strict parser.
  Add a tiny **per-step assertion**: if a non-terminal step parses to zero actions, log it
  loudly (don't silently no-op). A single counter "dropped_actions" per run would have
  surfaced this in minutes.
- Keep **history canonical**: never store raw model text that you also feed back as
  exemplars without normalizing it first (the mimicry channel). This applies to any future
  field (Action: lines, thoughts), not just tool_calls.

### C. Robustness hooks worth adding next
- **Verify-before-terminate → recover, not just flag.** Today it labels false positives. Next:
  on `false_positive`, inject one corrective turn (e.g. a user message "the result is X, not
  the target; correct it") and allow N extra steps instead of ending. Generic and OCR-cheap.
- **Stuck-detector on dropped/over-repeated actions** (the existing re-click guard is good;
  add a "k consecutive empty-action steps → abort" guard so a future format regression can't
  burn the whole step budget).
- **Settle/observe discipline:** confirm the capture frame used for a decision is post-settle
  (stale-frame reads look like grounding errors). `SETTLE`/`reset_settle` already exist;
  consider an explicit "frame changed since last action" check before the next predict.

### D. Methodology (so the next investigation is cheap)
- The decisive tool here was an **offline, hardware-free probe** that hits Ollama directly
  and pins one variable at a time (`_fmt2.py` / `_fmt3.py` / `_fix_verify.py`). Keep this
  layer: most "agent" bugs are in preprocessing / message-construction / parsing, not the
  hardware, and they reproduce far faster without the rig in the loop.
- Only escalate to the full rig once the offline probe is clean.

### E. Cleanup
- The from-scratch `evocua.py` + `agent_loop_evocua.py` are now superseded by
  `run_probe.py` + `pico_env.py` (official agent). Keep one path to avoid drift; the official
  path is now patched and is the better base.

---

## Reusable probes (repo root)
- `_fmt2.py` / `_fmt3.py` — history-format A/B (the decisive test).
- `_fix_verify.py` — proves the normalization fix (0→10/10).
- `_size_probe.py` — small vs enlarged target (ruled size out).
- All reach the laptop Ollama at `192.168.0.155:11434`; models present: `evocua-8b` (Q8_0),
  `evocua-8b-q5-clean` (Q5_K_M).

_Sandbox caveat: the Windows↔Linux mount lags on writes, so freshly-edited files may need
`__pycache__` cleared (and occasionally a moment to sync) before re-running in a new process.
The committed files on `C:\Dev\vllm` are correct._
