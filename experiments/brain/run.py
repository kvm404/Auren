"""Run brain backends over the test cases.

  uv run run.py                          # every backend, every case
  uv run run.py --only claude-haiku,groq-qwen --cases 01,01m
  uv run run.py --parallel 3             # how many backends run at the same time

Results are appended to results/runs.jsonl; a (backend, case) pair that already
has a result is skipped, so an interrupted run can simply be restarted.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backends import BACKENDS

HERE = Path(__file__).parent
RESULTS = HERE / "results"
RUNS = RESULTS / "runs.jsonl"
LOCK = threading.Lock()


def done() -> set[tuple[str, str]]:
    if not RUNS.exists():
        return set()
    return {(r["backend"], r["case"]) for r in map(json.loads, RUNS.open()) if not r.get("error")}


def run_backend(name: str, cases: list[dict], skip: set) -> None:
    fn, _ = BACKENDS[name]
    for case in cases:
        if (name, case["id"]) in skip:
            continue
        record = {"backend": name, "case": case["id"], "ts": time.time()}
        try:
            record.update(fn(case["text"]))
        except Exception as e:
            record["error"] = str(e)[:400]
        with LOCK:
            with RUNS.open("a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            took = f"{record['wall_s']:.1f}s" if "wall_s" in record else "ERROR " + record["error"][:120]
            print(f"{name:20} {case['id']:5} {took}", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", help="comma-separated backend names")
    p.add_argument("--cases", help="comma-separated case ids")
    p.add_argument("--parallel", type=int, default=3)
    a = p.parse_args()
    RESULTS.mkdir(exist_ok=True)
    cases = json.loads((HERE / "cases.json").read_text())
    if a.cases:
        wanted = a.cases.split(",")
        cases = [c for c in cases if c["id"] in wanted]
    names = a.only.split(",") if a.only else list(BACKENDS)
    skip = done()
    with ThreadPoolExecutor(a.parallel) as pool:
        for f in [pool.submit(run_backend, n, cases, skip) for n in names]:
            f.result()


if __name__ == "__main__":
    main()
