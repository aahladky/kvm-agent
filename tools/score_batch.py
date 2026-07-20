"""
score_batch.py — env.evaluate() analog for the eval_harness batches. Runs in the
Linux sandbox (where tesseract is easy):  pip install pytesseract --break-system-packages
and  apt-get install -y tesseract-ocr.

Reads a batch produced by eval_harness.py:
  - OCRs the END-STATE display from each run's end_full.png  ->  true pass/fail
    (display == expected). This is the visual end-state verification, automated.
  - Folds in the coord-only grounding metric (operator-column hit rate) that the
    harness already logged with no OCR.
Emits analysis.json + a printed summary aggregated by (model, history, task).

RESULT_BBOX is the result-number crop in 1920x1080 capture pixels. CALIBRATE it once
from a real end_full.png (the script saves crop_result.png per run so you can eyeball
the crop) — the calculator window is fixed when pre-opened, so one calibration holds.

Usage:  python3 score_batch.py [batch_dir]      (defaults to newest runs/batch_*)
"""
import os
import re
import sys
import glob
import json
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kvm_agent.config import CFG

try:
    import pytesseract
except ImportError:
    pytesseract = None

# Result-number crop (x1, y1, x2, y2) in capture pixels. CALIBRATE from end_full.png.
RESULT_BBOX = (510, 560, 705, 608)   # calibrated against a real 1920x1080 end_full.png (reads "61" clean)


def newest_batch():
    cands = sorted(glob.glob(os.path.join(CFG.runs_dir, "batch_*")))
    return cands[-1] if cands else None


def ocr_result(full_png_path, save_crop=None):
    """Crop the result region, threshold, OCR digits. Returns the read string."""
    img = cv2.imread(full_png_path)
    if img is None:
        return None
    x1, y1, x2, y2 = RESULT_BBOX
    crop = img[y1:y2, x1:x2]
    # upscale + grayscale + Otsu threshold; result is light-on-dark -> invert to dark-on-light
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if save_crop:
        cv2.imwrite(save_crop, th)
    if pytesseract is None:
        return None
    cfg = "--psm 7 -c tessedit_char_whitelist=0123456789.-"
    txt = pytesseract.image_to_string(th, config=cfg)
    return re.sub(r"[^0-9.\-]", "", txt.strip())


def main():
    batch = sys.argv[1] if len(sys.argv) > 1 else newest_batch()
    if not batch or not os.path.isdir(batch):
        sys.exit(f"no batch dir ({batch})")
    manifest = json.load(open(os.path.join(batch, "manifest.json")))
    print(f"batch: {batch}   runs: {len(manifest)}   "
          f"tesseract: {'yes' if pytesseract else 'NO (display read skipped)'}\n")

    rows = []
    for e in manifest:
        rundir = os.path.join(batch, e["dir"])
        full = os.path.join(rundir, "end_full.png")
        read = ocr_result(full, save_crop=os.path.join(rundir, "crop_result.png")) \
            if os.path.exists(full) else None
        success = (read is not None and read == str(e["expected"]))
        # + grounding: did the '+' (and any operator) land in the operator column?
        plus = [o for o in e["operator_clicks"] if o["op"] == "+"]
        plus_ok = all(o["in_op_col"] for o in plus) if plus else None
        any_op = e["operator_clicks"]
        op_ok = (sum(1 for o in any_op if o["in_op_col"]), len(any_op))
        rows.append({**{k: e[k] for k in ("model", "history", "task", "expected",
                                          "rep", "finished", "iters")},
                     "read": read, "success": success,
                     "plus_in_op_col": plus_ok, "op_in_col": op_ok,
                     "false_terminate": bool(e["finished"] and not success)})

    # aggregate by (model, history, task)
    agg = {}
    for r in rows:
        k = (r["model"], r["history"], r["task"])
        a = agg.setdefault(k, {"n": 0, "success": 0, "false_term": 0, "iters": 0,
                               "plus_ok": 0, "plus_tot": 0})
        a["n"] += 1
        a["success"] += int(r["success"])
        a["false_term"] += int(r["false_terminate"])
        a["iters"] += r["iters"]
        if r["plus_in_op_col"] is not None:
            a["plus_tot"] += 1
            a["plus_ok"] += int(r["plus_in_op_col"])

    print(f"{'model':<22}{'h':>2} {'task':<7}{'n':>3} {'succ%':>6} "
          f"{'+OPcol%':>8} {'falseTerm':>10} {'iters':>6}")
    print("-" * 70)
    for k, a in sorted(agg.items()):
        succ = 100 * a["success"] / a["n"]
        plus = (100 * a["plus_ok"] / a["plus_tot"]) if a["plus_tot"] else float("nan")
        ft = 100 * a["false_term"] / a["n"]
        print(f"{k[0]:<22}{k[1]:>2} {k[2]:<7}{a['n']:>3} {succ:>6.0f} "
              f"{plus:>8.0f} {ft:>9.0f}% {a['iters']/a['n']:>6.1f}")

    out = {"batch": batch, "rows": rows,
           "aggregate": [{"model": k[0], "history": k[1], "task": k[2], **a}
                         for k, a in agg.items()]}
    with open(os.path.join(batch, "analysis.json"), "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {os.path.join(batch, 'analysis.json')}")
    if pytesseract is None:
        print("NOTE: tesseract missing -> success% is meaningless. Install:\n"
              "  apt-get install -y tesseract-ocr && pip install pytesseract --break-system-packages")


if __name__ == "__main__":
    main()
