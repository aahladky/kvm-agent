# UI-TARS-1.5-7B integration — status & deploy

_2026-06-19. Milestone 1 of the bake-off plan: a backend-agnostic agent interface + the
UI-TARS adapter, wired into operate.py, verified offline. Serving + live runs happen on the
laptop/rig (not the sandbox)._

## What's done (and how it was verified)
- **`cua_agent.py`** — `make_agent(backend, ...)` factory. `backend="evocua"` constructs the
  EvoCUA agent with the **exact** args operate.py used before (behavior unchanged); `backend="uitars"`
  builds the new adapter. Imports are lazy so neither path drags in the other's deps.
- **`uitars_agent.py`** — `UITARSAgent`, same contract as `EvoCUAAgent`
  (`reset()`, `predict(instruction, obs) -> (text, actions)`, `last_answer`). It calls UI-TARS over
  the Ollama OpenAI-compatible endpoint, parses the DSL with the official `ui-tars` package, and
  emits clean **pico_env-ready** action strings.
- **`operate.py`** — added `--backend {evocua,uitars}` (default `evocua`) and `--model`
  (defaults per backend). The EvoCUA path is byte-for-byte the same.
- **`Modelfile.uitars`** — two-FROM Ollama Modelfile (LM GGUF + mmproj), `num_ctx 16384`.
- **`tests/test_uitars_adapter.py`** — 20 offline assertions, **all green** (no rig/Ollama):
  coordinate math (center→(960,544), corners exact, the flail search-box→(176,1066)), type/hotkey/
  scroll/drag, control tokens (finished→DONE+answer, wait→WAIT), and the exec-safety invariants
  (no `import` lines; only PicoPyAutoGUI-supported calls).

## Three things this adapter gets right (verified offline 2026-06-19)
1. **Coordinates.** UI-TARS-1.5-7B (Qwen2.5-VL) emits **absolute coords on the smart-resized
   image** (factor 28). The `ui-tars` parser normalizes them to [0,1] fractions; we map
   `fraction × real_capture_dim → pixel`. NB: the package's `parsing_response_to_pyautogui_code`
   defaults to `scale_factor=1000`, which is **wrong** for this path (yields sub-pixel 0.96 not 960) —
   we do the px math ourselves, so that footgun can't bite.
2. **No `import pyautogui`.** The package's codegen prepends imports + a docstring; `pico_env`
   execs in a shim namespace where `import pyautogui` would grab the real module (wrong machine) or
   crash. We emit bare action calls only.
3. **HID typing, not clipboard.** The package's `type` uses pyperclip+Ctrl-V; the Pico has no
   clipboard. We emit `pyautogui.typewrite(...)` (→ `r4.type`), with a trailing Enter only when the
   model's content ends in newline (UI-TARS submit convention).

## Deploy on the laptop (192.168.0.155)
1. **Get a VISION-capable GGUF + mmproj** (text-only GGUFs can't see the screen). Known good:
   `Mungert/UI-TARS-1.5-7B-GGUF` or `adriabama06/UI-TARS-1.5-7B-GGUF` — grab a `Q5_K_M` (or `Q8_0`)
   LM gguf **and** the `mmproj-*-f16.gguf`. Both fit the 12 GB mobile 4080.
2. Put both files next to `Modelfile.uitars`, fix the two `FROM` paths, then:
   `ollama create uitars-1.5-7b -f Modelfile.uitars`
3. **Vision smoke-test** (must describe the actual screen, not hallucinate):
   `curl localhost:11434/api/generate -d '{"model":"uitars-1.5-7b","prompt":"Describe this screen.","images":["<base64 png>"]}'`
4. On the operate.py host: `pip install ui-tars openai` (the `ui-tars` package provides the parser).
5. Run the flail task on the new backend:
   `python operate.py --backend uitars --once "Open Notepad and type: milk, eggs, and bread. Then open Calculator and compute 42 + 17"`

## On-rig calibration (do this first, one click)
The adapter assumes the server smart-resizes with **factor 28 + default min/max pixels**
(`min=78400`, `max=12845056`). If Ollama's qwen2.5-vl image processing uses different caps, clicks
will be **biased** (same class as the EvoCUA calibration). Verify with one click at a known target
(e.g. the Start button) and check the crosshair lands; if it's off, set `min_pixels`/`max_pixels`
in `UITARSAgent` to match Ollama, or pass them through. The capture line must still read the real
dims; the Pico `SCREEN_W/H` vs 1088 latent issue is unchanged (see CLAUDE.md / flail findings).

## Verified offline vs unproven (needs the rig)
- **Verified (sandbox):** adapter parse→pyautogui, coordinate math, control mapping, exec-safety,
  factory wiring, the edited `operate.py` control flow.
- **Unproven (laptop/rig):** vision quality of the Qwen2.5-VL mmproj in Ollama; live grounding on the
  real Windows target; `num_ctx 16384` VRAM headroom at history=4; the on-rig coordinate calibration.
  Run with `--confirm` first on anything you care about.

## Note on the sandbox
`py_compile` in the Linux sandbox reported a syntax error in `operate.py` — that was the **stale
mount** serving a truncated pre-edit cache (`stat` showed the original size/mtime; `Edit` updates the
real file but the mount lags, per CLAUDE.md). The edited `main()` was confirmed valid by independent
`ast.parse`, and the authoritative editor view shows the file complete.
