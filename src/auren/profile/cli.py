"""auren-profile: show what auren knows about this laptop.

  auren-profile                    # the compact text the brain reads
  auren-profile --json             # everything, as JSON
  auren-profile --refresh          # rebuild the cached static part first
  auren-profile lookup keys obsidian
  auren-profile lookup commands screenshot
  auren-profile --timing           # how long each collector takes
"""

from __future__ import annotations

import argparse
import json

from . import Profile


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--timing", action="store_true")
    p.add_argument("rest", nargs="*", help="lookup <topic> [words...]")
    a = p.parse_args()
    prof = Profile()
    if a.refresh:
        prof.static(refresh=True)
    if a.rest[:1] == ["lookup"]:
        if len(a.rest) < 2 or a.rest[1] not in Profile.TOPICS:
            raise SystemExit(f"topics: {', '.join(Profile.TOPICS)}")
        print(prof.lookup(a.rest[1], " ".join(a.rest[2:])))
    elif a.json:
        print(json.dumps(prof.full(), indent=1, ensure_ascii=False))
    elif a.timing:
        s, l = prof.static(), prof.live()
        print("static (cached):", s.get("_timing_ms"))
        print("live:", l.get("_timing_ms"))
    else:
        text = prof.context()
        print(text)
        print(f"\n[{len(text)} chars ≈ {len(text) // 4} tokens]")


if __name__ == "__main__":
    main()
