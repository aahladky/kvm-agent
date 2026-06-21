# Model bake-off plan — UI-TARS-1.5-7B & OpenCUA-7B vs EvoCUA-8B
_2026-06-19 — integration + comparison spec. Decision pending the bake-off; no further EvoCUA work for now._

## Why this is low-risk structurally
The harness is **already model-agnostic at the execution layer**. `pico_env.controller.execute_python_command()` execs **pyautogui** code strings, and `evocua_agent.predict(instruction, obs)` already returns pyautogui. Both candidate models also resolve to pyautogui:
- **OpenCUA-7B emits pyautogui natively** (`pyautogui.click(x=1443, y=343)`) — zero DSL translation.
- **UI-TARS ships a pip package** (`ui-tars`) with `parse_action_to_structure_output()` + `parsing_response_to_pyautogui_code()` that converts its DSL to pyautogui for you.

So `pico_env`, `r4_client`, `code.py` (Pico), capture, `operate.py`, `run_probe.py`, logging/manifest, and the (recommended) recovery guard are **100% reused**. The only new code per model is one `Agent` class: prompt + parse + coordinate-convert. This is the same shape as the EvoCUA adapter we already have.

## The three models, side by side (open weights we can actually run)
| | EvoCUA-8B (current) | UI-TARS-1.5-7B | OpenCUA-7B |
|---|---|---|---|
| Backbone | Qwen3-VL-8B | Qwen2.5-VL-7B | Qwen2.5-VL-7B |
| License | apache-2.0 | apache-2.0 | **MIT** (commercial OK) |
| Action output | `<tool_call>` JSON (S2) | DSL `Action: click(start_box='(x,y)')` → their parser → pyautogui | **pyautogui directly** |
| Coordinate convention | relative /999, smart_resize **factor 32** | **absolute on resized img, factor 1000**; `ui-tars` pkg converts | **absolute on smart-resized img, factor 28**; documented convert fn |
| History | last N frames+responses (we run 4) | N frames+actions (official OSWorld script) | up to 3 images, reflective CoT |
| Error-recovery training | not advertised | yes (reflection/error-correction traces) | yes (reflective long CoT, "identifies errors + corrective reasoning") |
| Serving on 12 GB 4080 | Ollama GGUF (Q5, ~8.5 GB @ ctx16384) ✅ proven | **Ollama GGUF** (13 community quants, Qwen2.5-VL mmproj) — drop-in like EvoCUA ✅ | **vLLM only** (custom arch: 1D-RoPE + Kimi-VL template), needs 4-bit to fit ⚠️ |
| Open-7B OSWorld | 46.1 claimed* | 27.4–27.5 (100 steps) | 26.6–27.9 (100 steps, mean of 3) |
| ScreenSpot-Pro (grounding) | not on record | 49.6 | **50.0** |
| AgentNetBench coord-actions | — | — | 79.0 |

\*EvoCUA's 46.1 is from a different source/config and **exceeds OpenCUA-72B's 45.0 SOTA**, which is implausible for an 8B unless the eval setup differs. **Cross-source OSWorld numbers are not comparable** — treat them as noise. The only valid comparison is our own harness on our own tasks. That is the entire justification for this bake-off.

## Honest framing of what we're testing
By published numbers, neither 7B is an *end-to-end* upgrade over EvoCUA (OSWorld ~27 vs EvoCUA's claimed 46). The case for switching is **not raw capability**, it's: (1) far better documentation + packaged coordinate handling (we reverse-engineered EvoCUA; these ship reference parsers and coordinate guides), (2) grounding specialization (ScreenSpot-Pro ~50; OpenCUA coord-actions 79), (3) explicit error-recovery training — directly relevant to the flail's no-recovery half. The bake-off measures whether those translate into **fewer flails on our rig/targets**, which benchmark numbers can't tell us.

Caveat to hold: UI-TARS has a **documented X-axis grounding wobble** (bytedance/UI-TARS issue #215) — the same axis that bit EvoCUA. Don't assume the misground vanishes.

## Shared interface (the only abstraction to add)
Define a tiny ABC so all three are drop-in for `operate.py`/`run_probe.py`:
```
class CUAAgent(ABC):
    def reset(self): ...
    def predict(self, instruction: str, obs: dict) -> tuple[str, list[str]]:
        # returns (raw_model_text, [pyautogui_code_strings])  -- exactly what pico_env.step execs
```
`EvoCUAAgent` already satisfies this. Add `UITARSAgent` and `OpenCUAAgent`. Selection by a `--model` flag in `operate.py`/`run_probe.py`. Keep the raw text in history canonical (the normalize-before-store lesson) for each model's own format.

## Per-model integration

### UI-TARS-1.5-7B — LOW effort (~1–2 days), reuses the Ollama path
1. **Serve:** pull a Qwen2.5-VL GGUF of UI-TARS-1.5-7B (13 community quants exist) + its mmproj; build an Ollama Modelfile with the two-FROM pattern we already use for EvoCUA. Target Q5/Q8 @ ctx 8–16k on the 4080. Sanity: vision smoke-test.
2. **Adapter:** `pip install ui-tars`. Use the `COMPUTER_USE` prompt from `codes/ui_tars/prompt.py`. Pipe model output through `parse_action_to_structure_output(resp, factor=1000, origin_resized_{w,h}=<frame dims>, model_type="qwen25vl")` then `parsing_response_to_pyautogui_code(...)` → feed straight into `pico_env`.
3. **Coordinates:** absolute-on-resized; their package does the scale-back. Pass the **real capture dims** (mind the 1088 issue — see harness fixes).
4. **History:** mirror their OSWorld `run_uitars.py` (N prior frames+actions).
5. **Risks:** Qwen2.5-VL mmproj fidelity in Ollama; the X-axis wobble (#215) is under test, not a blocker.

### OpenCUA-7B — MEDIUM/HIGH effort (~2–4 days), new serving stack
1. **Serve:** **no Ollama path** — OpenCUA replaces M-RoPE with 1D-RoPE and uses the Kimi-VL tokenizer/template (`custom_code`), which llama.cpp doesn't implement. Stand up **vLLM ≥ 0.12.0** on the laptop with `--trust-remote-code`. Fit 7B in 12 GB via **4-bit** (AWQ/GPTQ; an `exl2` exists but that's exllamav2/TabbyAPI, not vLLM — may need to build an AWQ or run bitsandbytes 4-bit). This is the main cost.
2. **Adapter:** trivial — system prompt is one line ("…perform a series of pyautogui actions…"); output is already pyautogui. Just extract the code block and run the documented `qwen25_smart_resize_to_absolute(model_x, model_y, orig_w, orig_h)` (factor 28, min 3136, max 12 845 056) to map resized→real pixels before exec.
3. **History:** up to 3 images, L2 reflective-CoT format (their `run_multienv_opencua.py`, `--coordinate_type qwen25`).
4. **Risks:** 4-bit + vision encoder VRAM fit on 12 GB (tight); standing up/maintaining vLLM next to Ollama; sourcing a vLLM-loadable 4-bit quant. Serving is ~all of the effort; the adapter is the easy part.

## Serving / VRAM plan on the 12 GB mobile 4080
- **UI-TARS:** Ollama GGUF, same lane as EvoCUA — Q5 ~6–8 GB + mmproj + KV; ctx 8–16k fits. Lowest friction.
- **OpenCUA:** vLLM + 4-bit. 7B 4-bit ≈ 5–6 GB weights + vision encoder + KV; should fit 12 GB but verify with a one-frame forward pass (watch inference-time vision activation, as we saw with EvoCUA). Fallback: lower `--gpu-memory-utilization`, shorter ctx, or test on the desktop GPU if the 4080 is too tight.
- Only one model owns the capture card + Pico at a time (serial), so Ollama and vLLM coexisting is fine.

## Recovery guard = shared harness infra (a measured variable, not EvoCUA work)
The flail was 2/3 a missing stall/verify guard. Rather than bolt it on quietly, **make "guard on/off" an axis of the bake-off**:
- **Pass A (no guard):** measures each model's *intrinsic* recovery — directly tests UI-TARS's and OpenCUA's error-correction-training claims. If they self-recover from a misground where EvoCUA looped, that's the cleanest evidence for switching.
- **Pass B (guard on):** stall-detect (consume `dup_of_prev_step`), repeat-action breaker, verify-before-terminate. Model-agnostic; benefits whichever model wins. Lives in `pico_env`/`operate.py`, touches no EvoCUA code.

## Comparison protocol (one variable at a time — house rule)
- **Tasks:** the exact flail task ("Open Notepad, type 'milk, eggs, and bread'; then open Calculator and compute 42+17"), plus the calculator-arithmetic set from the prior saga, plus 2–3 multi-app Windows tasks with small targets (grounding stress) and recoverable wrong-states (recovery stress).
- **Reps:** K ≥ 10 per (model, task, guard) — **rates, not single deterministic samples** (the core lesson from the last investigation). temp per each model's spec (UI-TARS ~0; OpenCUA 0).
- **Metrics (all from the manifest/logger):** verified task success (OCR/end-state, not self-report), steps-to-done, per-click grounding hit-rate, no-op rate (frame-unchanged after action), self-recovery events, re-click loops, false-positive terminates, per-step latency, VRAM/ctx headroom.
- **EvoCUA baseline:** reuse existing EvoCUA runs as the reference (no new EvoCUA work). If a clean apples-to-apples number is wanted later, run EvoCUA through the same harness once — but that's optional and out of current scope.
- **Decision rule:** pick on *flail rate + verified success on our tasks*, not published OSWorld. If a candidate matches/beats EvoCUA's success with fewer no-op/loop pathologies, switch; if EvoCUA-with-guard would have been fine, note that the win was the guard, not the model.

## Sequence / milestones
1. Add the `CUAAgent` ABC; confirm `EvoCUAAgent` still runs through it unchanged (smoke test, no behavior change).
2. UI-TARS first (cheapest, reuses Ollama): serve + adapter + vision smoke-test + the flail task, Pass A then Pass B.
3. OpenCUA second: stand up vLLM 4-bit (the real work) + adapter + same tasks, Pass A then Pass B.
4. Run the K-rep matrix; tabulate metrics; write the verdict.

## Open questions for Aaron
- **OpenCUA serving:** OK to add vLLM alongside Ollama on the laptop, and to spend the 4-bit-quant/VRAM-fit time? (This is the bulk of the effort; UI-TARS needs none of it.) If you'd rather stay Ollama-only, OpenCUA likely drops out and it's a UI-TARS-vs-EvoCUA bake-off.
- **Guard:** run Pass A (no guard, intrinsic recovery) before adding the guard? Recommended — it's the cleanest test of the recovery-training claim.
- **Scope of EvoCUA baseline:** reuse old runs (default) or do one same-harness EvoCUA run for a clean comparison?

## Sources
- UI-TARS-1.5-7B model card / benchmarks / deploy: https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B
- UI-TARS repo (action parser, prompts, coordinate guide, UI-TARS-2 note): https://github.com/bytedance/UI-TARS
- UI-TARS X-axis grounding issue: https://github.com/bytedance/UI-TARS/issues/215
- OpenCUA-7B model card (action space, coordinate convert, vLLM, license): https://huggingface.co/xlangai/OpenCUA-7B
- OpenCUA paper: https://arxiv.org/abs/2508.09123 · code: https://github.com/xlang-ai/OpenCUA
- OSWorld leaderboard context (Qwen3-VL-235B 66.7% top open; Claude ~72%): https://llm-stats.com/benchmarks/osworld
