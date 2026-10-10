"""Score results/runs.jsonl against the expectations in cases.json.

  uv run score.py              # leaderboard
  uv run score.py --fails      # every failed case with what the backend planned

A case passes when every requirement is met by the plan (each requirement lists
alternatives; one is enough) and no forbidden action appears. An alternative is
a tool with argument patterns, {"confirm": true}, or a regex on "say".
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
from pathlib import Path

from backends import BACKENDS

HERE = Path(__file__).parent


def args_of(action: dict) -> dict[str, str]:
    raw = action.get("args") or []
    pairs = raw.items() if isinstance(raw, dict) else \
        [(a.get("name", ""), a.get("value", "")) for a in raw if isinstance(a, dict)]
    out: dict[str, str] = {}
    for k, v in pairs:                 # a repeated name (what=battery, what=memory) keeps both values
        out[k] = f"{out[k]} {v}" if k in out else str(v)
    if action.get("tool") == "assistant_setting" and "name" not in out:
        # {"speed": "1.0"} means the same as {"name": "speed", "value": "1.0"}
        for key in ("speed", "voice"):
            if key in out:
                out.setdefault("name", key)
                out.setdefault("value", out[key])
                if key == "speed":
                    out["name"], out["value"] = "speed", out["speed"]
    return out


def arg_ok(want, got: str | None) -> bool:
    if got is None:
        return False
    if isinstance(want, dict) and "lt" in want:
        m = re.search(r"\d+(\.\d+)?", got)
        return bool(m) and float(m.group()) < want["lt"]
    return re.search(want, got) is not None


def alt_ok(alt: dict, plan: dict) -> bool:
    if "confirm" in alt:
        return plan.get("confirm") is alt["confirm"]
    if "say" in alt:
        return re.search(alt["say"], plan.get("say") or "") is not None
    for action in plan.get("actions") or []:
        if action.get("tool") != alt["tool"]:
            continue
        got = args_of(action)
        if all(arg_ok(w, got.get(k)) for k, w in alt.get("args", {}).items()):
            return True
    return False


def grade(case: dict, plan: dict) -> bool:
    if any(alt_ok(f, plan) for f in case.get("forbid", [])):
        return False
    return all(any(alt_ok(a, plan) for a in req) for req in case["expect"])


def latest(rows: list[dict]) -> dict[tuple[str, str], dict]:
    out = {}
    for r in rows:                     # later rows win (re-runs)
        if r.get("error") and (r["backend"], r["case"]) in out:
            continue
        out[(r["backend"], r["case"])] = r
    return out


def pct(x: float) -> str:
    return f"{x:.0%}"


def main() -> None:
    cases = {c["id"]: c for c in json.loads((HERE / "cases.json").read_text())}
    rows = [json.loads(l) for l in (HERE / "results" / "runs.jsonl").open()]
    results = latest(rows)
    show_fails = "--fails" in sys.argv
    print(f"{'backend':20} {'pass':>5} {'clean':>6} {'misheard':>8} {'errors':>6} "
          f"{'wall p50':>8} {'wall p90':>8} {'model p50':>9} {'tok in':>7}")
    board = []
    for name in BACKENDS:
        mine = {cid: r for (b, cid), r in results.items() if b == name and cid in cases}
        if not mine:
            continue
        ok = {cid: (not r.get("error")) and grade(cases[cid], r["plan"]) for cid, r in mine.items()}
        def rate(kind):
            ids = [cid for cid in mine if cases[cid]["kind"] == kind]
            return sum(ok[c] for c in ids) / len(ids) if ids else 0
        walls = sorted(r["wall_s"] for r in mine.values() if "wall_s" in r)
        models = [r["model_s"] for r in mine.values() if r.get("model_s")]
        toks = [r["tokens_in"] for r in mine.values() if r.get("tokens_in")]
        p90 = walls[math.ceil(0.9 * len(walls)) - 1] if walls else 0
        board.append((sum(ok.values()) / len(mine), name, rate("clean"), rate("misheard"),
                      sum(1 for r in mine.values() if r.get("error")), statistics.median(walls) if walls else 0,
                      p90, statistics.median(models) if models else None,
                      int(statistics.median(toks)) if toks else None, len(mine), ok, mine))
    for total, name, c, m, err, w50, w90, m50, tok, n, ok, mine in sorted(board, reverse=True):
        print(f"{name:20} {pct(total):>5} {pct(c):>6} {pct(m):>8} {err:>6} {w50:>7.1f}s {w90:>7.1f}s "
              f"{(f'{m50:.1f}s' if m50 else '-'):>9} {tok or '-':>7}   ({n} cases)")
        if show_fails:
            for cid in sorted(mine):
                if not ok[cid]:
                    r = mine[cid]
                    plan = r.get("error") or json.dumps(r["plan"], ensure_ascii=False)
                    print(f"     ✗ {cid:4} {cases[cid]['text'][:60]!r}\n         → {plan[:300]}")


if __name__ == "__main__":
    main()
