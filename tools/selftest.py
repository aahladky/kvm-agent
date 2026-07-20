"""
selftest.py — exercise each HID primitive against the live capture and report a capability table.

Preempts the SILENT-NO-OP class. A primitive can be wired host-side yet do NOTHING on the
target: the live firmware (code.py) maps the scroll command 'S' to a no-op and the boot.py v4
HID report has no wheel byte, so r4.scroll() moves nothing — which is exactly why a
below-the-fold control is unreachable and UI-TARS grounds at the screen edge. This tool proves,
against the actual HDMI capture, which primitives really act. Run it after any firmware reflash
or HID/wiring change so a dead primitive is known UP FRONT, not discovered as a mysterious
mid-task misground.

Checks (each isolated + best-effort, so one dead primitive doesn't abort the rest):
  launch  — Win+R app launch confirms a window opened
  type    — Notepad + typed token, OCR-verified on screen (keyboard path)
  scroll  — make content longer than the viewport, scroll, frame-diff (the no-op suspect)
  click   — ground + click the File menu, expect the screen to change (mouse + grounding)

    python tools/selftest.py
Saves frames + results.json to runs/selftest_<ts>/ and prints a SUMMARY flagging anything dead.

NOTE: I can't run this from here (camera/Pico/Ollama are on your LAN); re-verify on the rig.
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kvm_agent.config import CFG
from kvm_agent.orchestration.executive import Executive, Verifier


def main():
    tag = time.strftime("selftest_%Y%m%d_%H%M%S")
    out = os.path.join(CFG.runs_dir, tag)
    os.makedirs(out, exist_ok=True)
    ex = Executive.open(executor_model="uitars-q4", verifier=Verifier())
    results = {}
    n = [0]

    def snap(name):
        png = ex.observe()
        n[0] += 1
        with open(os.path.join(out, f"{n[0]:02d}_{name}.png"), "wb") as f:
            f.write(png)
        return png

    def report(prim, status, detail=""):
        results[prim] = {"status": status, "detail": detail}
        print(f"  {prim:8s}: {status:14s} {detail}")

    print(f"[selftest] out={out}")
    try:
        ex.reset_clean(max_close=10)
        snap("clean")

        # 1) launch ------------------------------------------------------------
        try:
            ok = ex.launch("notepad")
            snap("after_launch")
            report("launch", "ok" if ok else "FAIL")
        except Exception as e:
            report("launch", "ERROR", repr(e))

        # 2) type + enter + caps (mixed-case token with a digit) ---------------
        token = "KvmSelfTest_47"
        try:
            ex.env.r4.type(token)
            time.sleep(0.8)
            snap("after_type")
            seen = ex.verifier.has_text(ex.observe(), token)
            status = "ok" if seen else ("FAIL" if seen is False else "unknown(no-ocr)")
            report("type", status, f"looked for {token!r}")
        except Exception as e:
            report("type", "ERROR", repr(e))

        # 3) scroll — THE silent-no-op suspect --------------------------------
        # Use DISTINCT numbered lines (blank lines look identical when scrolled, so the old
        # "\n"*60 could never show a diff even if scroll worked) + a semantic check: does the
        # TOP marker come back into view after scrolling up? `before` is captured AFTER typing,
        # so Notepad's type-time auto-scroll can't be mistaken for the wheel.
        try:
            body = "TOPMARK_SCROLL\n" + "\n".join(f"scroll row {i:02d}" for i in range(1, 61))
            ex.env.r4.type(body)                        # typing auto-scrolls to the BOTTOM
            time.sleep(1.2)
            ex.env.r4.move(900, 500)                    # cursor over the text area
            time.sleep(0.2)
            before = ex.observe()
            top_before = bool(ex.verifier.has_text(before, "TOPMARK_SCROLL"))
            for _ in range(6):                          # wheel-scroll up toward the top
                ex.env.r4.scroll(10)
                time.sleep(0.15)
            time.sleep(0.6)
            after = ex.observe()
            d = Executive._frame_diff(before, after)
            top_after = bool(ex.verifier.has_text(after, "TOPMARK_SCROLL"))
            moved = (d > 3.0) or (top_after and not top_before)
            snap("after_scroll")
            report("scroll", "ok" if moved else "NO-OP",
                   f"frame-diff={d:.2f}, top-marker {top_before}->{top_after}  "
                   f"(needs the v5 wheel firmware: boot.py wheel field + code.py 'S' handler; "
                   f"NO-OP here means that firmware isn't flashed)")
        except Exception as e:
            report("scroll", "ERROR", repr(e))

        # 4) click (left-button) LIVENESS -------------------------------------
        # Click the Start button — a large, FIXED target — and confirm the Start menu opens
        # (a big, unmistakable change). The old File-menu version conflated grounding accuracy
        # with click liveness AND couldn't detect the small open menu (frame-diff 0.47, OCR
        # missed the tiny text) even though the click WORKED — a false FAIL. Grounding accuracy
        # is covered separately by the default-browser harness / probe_grounding.py.
        try:
            start_xy = (18, 1058)   # Win10 Start button, far bottom-left at 1920x1080
            before = ex.observe()
            ex.env.r4.move(*start_xy)
            ex.env.r4.click()
            time.sleep(1.0)
            after = ex.observe()
            d2 = Executive._frame_diff(before, after)
            opened = d2 > 5.0 or bool(ex.confirm("Is the Windows Start menu open?"))
            snap("after_click")
            ex.env.r4.key("esc")
            time.sleep(0.3)
            report("click", "ok" if opened else "FAIL",
                   f"clicked Start {start_xy}, frame-diff={d2:.2f} (Start menu = big change; "
                   f"if FAIL with a tiny diff, the Start coord may be off for this taskbar)")
        except Exception as e:
            report("click", "ERROR", repr(e))

        ex.reset_clean(max_close=10)
    finally:
        json.dump({"tag": tag, "results": results},
                  open(os.path.join(out, "results.json"), "w"), indent=2)
        ex.close()

    dead = [k for k, v in results.items() if v["status"] not in ("ok", "unknown(no-ocr)")]
    print(f"\n  results + frames: {out}")
    print("  SUMMARY:", "all primitives act on the target" if not dead
          else f"ATTENTION — not acting: {dead}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
