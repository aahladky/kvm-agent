VLM hardware computer-use agent — active files
================================================

Run from this folder. Requires: pip install anthropic opencv-python numpy
and ANTHROPIC_API_KEY set in the environment.

  agent_loop_logged.py  -- the loop you run. Capture -> Claude computer use
                           -> R4 HID -> re-capture. Has logging + prompt
                           caching. Switch Opus/Sonnet in the CONFIG block.
  run_logger.py         -- per-run JSONL, annotated frames, token/cost
                           summary. Imported by the loop. VERIFY the PRICES
                           dict against current pricing.
  compare.py            -- `python compare.py` prints all runs side by side.
  r4_client.py          -- TCP client for the R4 HID listener. Set R4_IP.

Not included (lives in your Arduino IDE): r4_hid_listener.ino — the sketch
flashed to the UNO R4. Keep your calibrated SCALE_X/SCALE_Y there.

Bring-up notes baked in from the build:
  - capture: DSHOW backend, threaded (always-warm, no tearing)
  - target: physical mouse OFF during runs (second cursor fights the R4)
  - target: flat pointer accel + calibrated R4 scale
  - cost levers, biggest first: prompt caching > screenshot trim > 720p downscale
