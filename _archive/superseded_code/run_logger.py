"""
run_logger.py — instrumentation for the agent loop.

Each run writes a timestamped folder under runs/:
    runs/<timestamp>/run.jsonl      one JSON line per iteration
    runs/<timestamp>/summary.json   totals (iters, reclicks, tokens, cost)
    runs/<timestamp>/frames/        annotated screenshot per step

Tracks regular vs cached tokens so you can see prompt caching working, and
prices them correctly (cache writes ~1.25x input, cache reads ~0.1x input).
"""

import os
import json
import time
import cv2

# $/million tokens. VERIFY at the current pricing page — these change and are
# placeholders so the cost readout works out of the box.
PRICES = {
    "claude-opus-4-8":   {"in": 5.0, "out": 25.0},   # <-- verify
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},   # <-- verify
}
CACHE_WRITE_MULT = 1.25   # 5-min ephemeral cache write surcharge
CACHE_READ_MULT  = 0.10   # cache read discount


class RunLogger:
    def __init__(self, model, goal, outdir="runs"):
        self.t0 = time.time()
        self.model = model
        self.goal = goal
        self.dir = os.path.join(outdir, time.strftime("%Y%m%d_%H%M%S"))
        os.makedirs(os.path.join(self.dir, "frames"), exist_ok=True)
        self.f = open(os.path.join(self.dir, "run.jsonl"), "w")
        self.steps = []
        self.in_tok = self.out_tok = 0
        self.cache_read = self.cache_write = 0
        print(f"[logger] writing to {self.dir}")

    def step(self, i, think, actions, coord, usage, latency, frame=None):
        reclick = False
        if coord:
            for s in self.steps[-3:]:
                c = s.get("coord")
                if (c and s.get("actions") == actions
                        and abs(c[0] - coord[0]) < 15 and abs(c[1] - coord[1]) < 15):
                    reclick = True
                    break

        it = ot = cr = cw = 0
        if usage:
            it = usage.input_tokens
            ot = usage.output_tokens
            cr = getattr(usage, "cache_read_input_tokens", 0) or 0
            cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
            self.in_tok += it
            self.out_tok += ot
            self.cache_read += cr
            self.cache_write += cw

        rec = {"i": i, "t": round(time.time() - self.t0, 2),
               "latency": round(latency, 2), "think": think,
               "actions": actions, "coord": coord, "reclick": reclick,
               "in_tok": it, "out_tok": ot, "cache_read": cr, "cache_write": cw}
        self.steps.append(rec)
        self.f.write(json.dumps(rec) + "\n")
        self.f.flush()

        if frame is not None:
            ff = frame.copy()
            if coord:
                x, y = int(coord[0]), int(coord[1])
                cv2.circle(ff, (x, y), 24, (0, 0, 255), 3)
                cv2.line(ff, (x - 36, y), (x + 36, y), (0, 0, 255), 2)
                cv2.line(ff, (x, y - 36), (x, y + 36), (0, 0, 255), 2)
            cv2.imwrite(os.path.join(self.dir, "frames", f"step_{i:02d}.png"), ff)

        if reclick:
            print("   ^ RE-CLICK flagged (same action+coord as a recent step)")

    def finish(self, finished):
        wall = round(time.time() - self.t0, 1)
        reclicks = sum(1 for s in self.steps if s["reclick"])
        cost = None
        if self.model in PRICES:
            p = PRICES[self.model]
            cost = round((self.in_tok * p["in"]
                          + self.cache_write * p["in"] * CACHE_WRITE_MULT
                          + self.cache_read * p["in"] * CACHE_READ_MULT
                          + self.out_tok * p["out"]) / 1e6, 4)

        summary = {"model": self.model, "goal": self.goal, "finished": finished,
                   "iters": len(self.steps), "reclicks": reclicks, "wall_s": wall,
                   "in_tok": self.in_tok, "out_tok": self.out_tok,
                   "cache_read": self.cache_read, "cache_write": self.cache_write,
                   "est_cost_usd": cost}
        with open(os.path.join(self.dir, "summary.json"), "w") as s:
            json.dump(summary, s, indent=2)
        self.f.close()

        total_in = self.in_tok + self.cache_read + self.cache_write
        hit_rate = (100 * self.cache_read / total_in) if total_in else 0
        print("\n" + "=" * 48)
        print(f" RUN SUMMARY  ({self.model})")
        print("=" * 48)
        print(f" outcome      : {'FINISHED' if finished else 'hit cap / aborted'}")
        print(f" iterations   : {len(self.steps)}")
        print(f" re-clicks    : {reclicks}")
        print(f" wall time    : {wall}s")
        print(f" fresh input  : {self.in_tok:,}")
        print(f" cache writes : {self.cache_write:,}")
        print(f" cache reads  : {self.cache_read:,}  ({hit_rate:.0f}% of input cached)")
        print(f" output       : {self.out_tok:,}")
        if cost is not None:
            print(f" est cost     : ${cost}   (VERIFY prices in run_logger.py)")
        print(f" saved to     : {self.dir}")
        print("=" * 48)
