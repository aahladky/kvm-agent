"""hindsight.py — thin, no-dependency client for a local Hindsight memory server.

Hindsight (vectorize-io/hindsight, MIT) is a semantic agent-memory server. We talk to it over its
REST API with urllib only (no SDK dep, matching the repo's minimalism). Two uses:

  recall_block(goal) -> a short text block of relevant FACTS about this machine/task, injected into
                        the planner prompt before it decomposes a goal (RAG-style; the orchestrator
                        queries memory, NOT the planner — keeps the planner a single completion).
  retain(content)    -> store a world fact / experience (the write-back / learning path).

DESIGN NOTE: Hindsight's retain is LLM-extractive and fact-oriented — world facts ("the default
browser on this box is Chrome") and experiences ("Firefox's Start shortcut is broken") round-trip
cleanly and directly fix the planner's wrong assumptions; multi-step procedural idioms do NOT extract
well, so those stay in the planner SYSTEM prompt. Hence this is aimed at the experiential/world-fact
layer. EVERYTHING here is FAIL-SOFT: any error (server down, timeout) returns empty/False so a memory
outage never breaks a run.

Endpoints (confirmed against the live server's /openapi.json):
  POST /v1/default/banks/{bank}/memories          {"items":[{"content","context","tags"}],"async":false}
  POST /v1/default/banks/{bank}/memories/recall    {"query","types","budget","max_tokens"} -> {"results":[{"text","type",...}]}
"""
import json
import re
import urllib.request
import urllib.error

from kvm_agent.config import CFG

# words too generic to distinguish one recipe from another (op verbs, filler) — excluded from the
# dedup key so matching keys on the DISTINCTIVE terms (app names, values) instead.
_STOP = {"the", "a", "an", "and", "or", "to", "of", "on", "in", "with", "this", "that", "was",
         "is", "by", "for", "then", "from", "using", "successfully", "completed", "task", "machine",
         "windows", "working", "sequence", "result", "screen", "press", "type", "click", "launch",
         "open", "verify", "hotkey", "scroll", "enter", "key", "app"}


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _keywords(text):
    """Distinctive lowercased, lightly-stemmed words of `text` (op verbs/filler removed)."""
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def _plan_to_text(plan):
    """Render a (successful) plan as a readable step sequence for the write-back recipe — a natural
    sentence extracts into a usable experience far better than raw op JSON."""
    parts = []
    for s in plan or []:
        if not isinstance(s, dict):
            continue
        op = s.get("op")
        if op == "launch":
            parts.append(f"launch {s.get('app')}")
        elif op == "type":
            parts.append(f"type {s.get('text')!r}")
        elif op == "tap":
            parts.append(f"press {s.get('key')}")
        elif op == "key":
            parts.append(f"hotkey {s.get('combo')}")
        elif op == "click":
            parts.append(f"click {s.get('target')!r}")
        elif op == "scroll":
            parts.append(f"scroll {s.get('direction', 'down')}")
        elif op == "verify":
            parts.append("verify the result on screen")
        # sleep / done are omitted
    return "; ".join(parts)


# ─────────────────────────── hard-fact classification (retrieval -> ENFORCEMENT) ───────────────
# A recalled fact is a HARD CONSTRAINT (not a neutral world-fact) when it states a PROHIBITION or a
# BREAKAGE. Those become (a) imperative directives at the top of the planner prompt and (b) where a
# forbidden op+target can be parsed, machine-enforceable GATES the executive blocks on. Conservative
# cues so a plain world-fact ("the default browser is Chrome") stays a soft fact.
_PROHIBIT_CUES = ("do not ", "don't ", "do n't ", "never ", "avoid ", "must not ", "should not ",
                  "cannot ", "can't ", "won't ", "do nt ")
_BREAKAGE_CUES = ("is broken", "are broken", "broken", "doesn't work", "does not work",
                  "doesn’t work", "no longer work", "not working", "fails to", "is missing",
                  "was moved", "has moved", "been moved", "is removed", "was removed",
                  "unavailable", "is dead", "is gone")
# op-verb in a fact -> the plan op a derived gate should forbid.
_OP_VERBS = {"launch": "launch", "launching": "launch", "open": "launch", "opening": "launch",
             "run": "launch", "running": "launch", "start": "launch", "starting": "launch",
             "click": "click", "clicking": "click", "type": "type", "typing": "type"}
# capitalized tokens that are NOT app/window names (so they're never chosen as a gate target).
_GATE_STOP = {"the", "this", "that", "windows", "start", "make", "set", "default", "problem",
              "shortcut", "machine", "do", "not", "never", "avoid", "it", "a", "an", "use",
              "when", "if", "by", "name", "menu", "search"}


def _gate_target(text):
    """Best-effort target token for a gate: a quoted name, else the first capitalized proper-noun
    that isn't a generic word (an app/window name like 'Firefox'). None if none is confident."""
    m = re.search(r"['\"]([A-Za-z0-9 .:_-]{2,40})['\"]", text)
    if m:
        return m.group(1).strip()
    for tok in re.findall(r"\b([A-Z][a-zA-Z0-9]{2,})\b", text):
        if tok.lower() not in _GATE_STOP:
            return tok
    return None


def _govern_op(low, tgt):
    """The plan op forbidden for `tgt`: the op-verb occurrence NEAREST the target token, preferring
    one that appears BEFORE it ('launching Firefox', 'clicking "Set as default"'). This stops a stray
    noun ('first run', 'a long run') from hijacking the op when a real governing verb sits next to
    the target. None if no op-verb is present. `low` is the lowercased fact."""
    tpos = low.find((tgt or "").lower())
    if tpos < 0:
        tpos = 0
    best = None  # (before_rank, distance, mapped_op) — lower is better
    for verb, mapped in _OP_VERBS.items():
        for mm in re.finditer(r"\b" + re.escape(verb) + r"\b", low):
            vpos = mm.start()
            cand = (0 if vpos <= tpos else 1, abs(tpos - vpos), mapped)
            if best is None or cand[:2] < best[:2]:
                best = cand
    return best[2] if best else None


def _object_after(tail):
    """The name token an action verb governs (its object): skip a leading article/preposition, then
    take a quoted phrase or a run of Capitalized words (an app/window/button name). None if what
    follows the verb isn't a name. This is what makes 'launching Firefox' -> 'Firefox' instead of
    grabbing an unrelated quoted phrase elsewhere in the sentence (e.g. a 'Problem with Shortcut'
    DIALOG name, which is the consequence, not the thing to forbid)."""
    t = re.sub(r"^\s*(the|a|an|to|it|that|this|by|your)\s+", "", tail.lstrip(), flags=re.I)
    m = re.match(r"['\"]([^'\"]{2,40})['\"]", t)                       # quoted object after the verb
    if m:
        return m.group(1).strip()
    m = re.match(r"([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3})", t)   # a Capitalized name run
    if m:
        name = re.sub(r"\s+(Start|Shortcut|Menu|Dialog|Window|Button|App|Application|Icon)\b.*$",
                      "", m.group(1)).strip()
        return name or m.group(1).strip()
    return None


def _gate_for(text):
    """Derive (op, match) for an enforcement gate from a prohibition/breakage fact, or (None, None).
    The target is the OBJECT of the action verb (the token the verb governs), NOT merely any quoted
    phrase — the quoted phrase is frequently a CONSEQUENCE (a dialog name) rather than the thing to
    forbid. Scans op-verbs left-to-right and returns the first that governs a name, so '…the Firefox
    Start-menu shortcut is broken … instead of launching Firefox' -> (launch, firefox): the 'start'
    in 'Start-menu' is skipped (its next token isn't a name) and 'launching Firefox' wins."""
    low = text.lower()
    verb_re = re.compile(r"\b(" + "|".join(map(re.escape, _OP_VERBS)) + r")\b")
    for m in verb_re.finditer(low):
        op = _OP_VERBS[m.group(1)]
        obj = _object_after(text[m.end():])
        if obj:
            return op, obj.lower()
    return None, None


def classify_facts(facts):
    """Split recalled fact strings into (directives, soft_facts, gates).
      directives — prohibition/breakage facts, surfaced imperatively at the top of the prompt.
      soft_facts — neutral world facts (kept in the soft RELEVANT MEMORY block).
      gates      — {"op","match","reason"} rules the executive enforces (a forbidden op + target
                   parsed from a directive). A directive with no parseable op+target yields no gate
                   (it still steers the planner via the imperative text)."""
    directives, soft, gates = [], [], []
    for f in facts:
        t = (f or "").strip()
        if not t:
            continue
        low = t.lower()
        if any(c in low for c in _PROHIBIT_CUES) or any(c in low for c in _BREAKAGE_CUES):
            directives.append(t)
            op, match = _gate_for(t)
            if op and match:
                gates.append({"op": op, "match": match, "reason": t})
        else:
            soft.append(t)
    return directives, soft, gates


class HindsightMemory:
    def __init__(self, base_url=None, bank=None, timeout=15):
        self.base = (base_url or CFG.hindsight_url).rstrip("/")
        self.bank = bank or CFG.hindsight_bank
        self.timeout = timeout

    def _post(self, path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)

    # ---- read path (plan-time recall) ----
    def recall(self, query, max_tokens=1024, types=None):
        """Relevant memories for `query` as a list of fact strings. Empty list on ANY error."""
        try:
            body = {"query": query, "max_tokens": max_tokens}
            if types:
                body["types"] = types
            res = self._post(f"/v1/default/banks/{self.bank}/memories/recall", body)
            return [(r.get("text") or "").strip()
                    for r in (res.get("results") or []) if (r.get("text") or "").strip()]
        except Exception:
            return []

    def recall_block(self, query, max_items=6, max_tokens=1024):
        """A formatted, de-duplicated block to inject into the planner prompt, or '' if nothing
        relevant / memory is unreachable."""
        seen, facts = set(), []
        for t in self.recall(query, max_tokens=max_tokens):
            k = t.lower()
            if k not in seen:
                seen.add(k)
                facts.append(t)
            if len(facts) >= max_items:
                break
        if not facts:
            return ""
        return ("RELEVANT MEMORY (recalled facts about this machine/task — use them to plan, but "
                "still verify on screen):\n" + "\n".join(f"- {f}" for f in facts))

    def recall_constraints(self, query, max_items=6, max_tokens=1024):
        """Recall for `query`, de-dup, and CLASSIFY into {"directives","facts","gates"} (see
        classify_facts). directives = imperative hard constraints (top-of-prompt); facts = soft
        world facts; gates = executive-enforced rules. Empty fields on any error (fail-soft)."""
        seen, uniq = set(), []
        for t in self.recall(query, max_tokens=max_tokens):
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
            if len(uniq) >= max_items:
                break
        directives, facts, gates = classify_facts(uniq)
        return {"directives": directives, "facts": facts, "gates": gates}

    # ---- write path (learning / seeding) ----
    def retain(self, content, context="kvm-agent", tags=None):
        """Store a world fact / experience. Returns True on success, False on any error (fail-soft)."""
        try:
            item = {"content": content, "context": context}
            if tags:
                item["tags"] = tags
            res = self._post(f"/v1/default/banks/{self.bank}/memories",
                             {"items": [item], "async": False})
            return bool(res.get("success"))
        except Exception:
            return False

    def retain_recipe(self, goal, plan, tags=None, dedup=True, sim_thresh=0.6):
        """Write-back: store the working step sequence for a goal as an experience, so a future run
        of a similar goal can recall HOW it was done. Returns True on success, False on error/skip.

        DEDUP-ON-WRITE: recall first; if a sufficiently-similar recipe already exists (>= sim_thresh
        of this recipe's distinctive keywords already present), SKIP the write — repeated successes
        would otherwise bloat recall with near-duplicate recipes. Fail-open: if the recall check
        errors, we still write (don't silently drop a learned recipe)."""
        steps = _plan_to_text(plan)
        if not steps:
            return False
        content = (f"On this Windows machine, the task '{goal}' was completed successfully with "
                   f"this working sequence: {steps}.")
        if dedup:
            key = _keywords(goal + " " + steps)
            if key:
                try:
                    for t in self.recall(goal, max_tokens=1024):
                        ek = _keywords(t)
                        if ek and len(key & ek) / len(key) >= sim_thresh:
                            return False   # a similar recipe is already stored — skip
                except Exception:
                    pass               # recall failed -> allow the write (don't lose the recipe)
        return self.retain(content, context="kvm-agent successful-run",
                           tags=(tags or ["recipe", "windows"]))

    def ping(self):
        """True if the server answers a trivial recall (used by preflight)."""
        try:
            self._post(f"/v1/default/banks/{self.bank}/memories/recall",
                       {"query": "ping", "max_tokens": 16})
            return True
        except Exception:
            return False
