# EvoCUA rig — demo runs

A small, honest set of demos to show the code working. The first two need **no hardware and
no Ollama** (they run anywhere, including CI). The rig demos need the capture card + Pico +
the laptop Ollama endpoint.

---

## Setup discrepancy fixed first (read this)

While preparing these demos I found a real bug: the three entry points
(`run_probe.py`, `operate.py`, `evocua_mcp_server.py`) each did

```python
REPO = .../evocua
sys.path.insert(0, REPO)          # <-- bug
from evocua_agent import EvoCUAAgent   # comment claimed "patched copy at repo root"
```

`insert(0, evocua/)` puts `evocua/` **ahead** of the repo root on `sys.path`, so
`import evocua_agent` resolved to the **stale `evocua/evocua_agent.py`**, *not* the patched
copy at the repo root. Head-to-head on `_parse_response_s2`:

| tool_call format the GGUF model emits | root `evocua_agent.py` (patched) | `evocua/evocua_agent.py` (stale, was live) |
|---|:--:|:--:|
| same-line `<tool_call>{json}</tool_call>` | OK | **dropped** |
| newline canonical | OK | OK |
| all-on-one-line | OK | **dropped** |
| trailing prose | OK | **dropped** |
| `terminate` | DONE | **dropped** |
| **total** | **5/5** | **1/5** |

Consequences on the stale copy: same-line tool_calls were still silently dropped (the exact
bug FINDINGS thought was fixed — the recent "clean" run just happened to emit newline-format
that step), and it has **no answer-channel** (`last_answer` / `ANSWER` absent), so the MCP
server's "2-way street" could only ever fire in `--mock`, never on the real backend.

**Fix applied:** changed `insert(0, REPO)` → `append(REPO)` in all three entry points (and
made the root dir explicit in the MCP server). `evocua/` stays on the path for the
`mm_agents.*` submodules the patched agent imports; the repo root now wins the
`evocua_agent` name. Verified: all three resolve to the patched root agent (5/5 formats +
answer-channel), and `mm_agents.evocua.utils` still imports.

> The stale `evocua/evocua_agent.py` is now harmlessly shadowed. It can be deleted later;
> the upstream agent the package actually uses lives at `evocua/mm_agents/evocua/`.

---

## Demo 1 — MCP job model, end to end (no hardware)

`demo_mcp_mock.py` drives the **real** `@mcp.tool` functions from `evocua_mcp_server.py`
against the canned `MockAgent`/`MockEnv`, exercising the full state machine:

```
python demo_mcp_mock.py
```

Shows: `start_computer_task` → `running`; the **busy-guard** rejecting a second start;
`awaiting_reply` when the model asks a question (the 2-way street); `continue_task`
supplying the answer; `succeeded` with the model's self-reported answer; a screenshot; and
the rig freeing up for the next task. Ends with `[PASS]`.

This is the offline-probe layer from FINDINGS: it validates the host ↔ server ↔
state-machine plumbing without tying up the capture card.

## Demo 2 — parser fix + answer-channel on the live path (no hardware)

`demo_parser_fix.py` imports `EvoCUAAgent` exactly the way the (now-patched) entry points
do, prints which file it resolved to, then:

```
python demo_parser_fix.py
```

Shows: **6/6** tool_call formats parse to the same click (same-line, all-on-one-line,
pretty-printed, bare-json, trailing-prose, newline) — the dropped-step bug is gone;
`terminate` → DONE/FAIL with its reported answer captured; and a standalone `answer` →
`ANSWER` sentinel with `agent.last_answer` populated (the MCP awaiting_reply seed). Ends
with `[PASS]`. It asserts it did **not** resolve to the stale `evocua/evocua_agent.py`, so
it doubles as a regression guard for the import-shadow bug above.

Both demos need only `mcp`, `pydantic`, `openai`, `backoff`, `Pillow` importable (no network;
the OpenAI client is constructed but never called).

---

## Rig demos (need the hardware + Ollama)

Pre-flight, every time:
- Capture line must print **`capture 1920x1080`** (must equal the Pico `SCREEN_W/SCREEN_H`).
  Nothing else affects click accuracy.
- Laptop Ollama reachable at `http://192.168.0.155:11434` with models `evocua-8b` (Q8) and
  `evocua-8b-q5-clean` (Q5_K_M) present.
- Only **one** process owns the capture card + Pico at a time (don't run a rig demo and
  `run_probe.py` together).

**Operate (free-run interactive):**
```
python operate.py --once "Using the open Calculator, compute 7 x 8 + 5"   # one goal, then exit
python operate.py --confirm "open Calculator"                            # approve each action
python operate.py                                                        # REPL
```
Pre-open Calculator for the known-good task. With the import fix, `--confirm`/REPL can now
also surface a model `answer` mid-run (operate prints it and lets you reply).

**Benchmark / re-baseline harness:**
```
python run_probe.py
python3 score_batch.py runs/<the-new-batch-dir>     # OCR scoring (needs tesseract)
```
`run_probe.py` carries the verify-before-terminate OCR gate and the corrective-instruction
recovery turn; the manifest records `term_status ∈ {success, false_positive, done_unverified}`.

**MCP server against the real rig:**
```
python evocua_mcp_server.py --host 0.0.0.0 --port 8077 --model evocua-8b-q5-clean
```
Then point Open WebUI (v0.6.31+, native MCP, Streamable-HTTP) or LibreChat at
`http://<host>:8077/mcp` (see `README_evocua_mcp.md`).

---

## Suggested next step (per FINDINGS)

Now that the live path actually loads the patched agent, **re-baseline** the
quant/history question properly: K≥10 reps per config with `run_probe.py`, read
`term_status` rates from the manifest (get `tesseract` installed so success is *verified*,
not assumed). Every prior single-sample Q5-vs-Q8 / history-depth conclusion predates this
fix and should be treated as noise.
