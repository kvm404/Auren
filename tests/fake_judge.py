"""A stand-in for the judge's `claude -p`: says ALLOW if the words of the action's question (its first
sentence) all appear in what the user said, or in Auren's question just before a plain "yes"; ASK
otherwise ("slow" in the action: never answers; "broken": an error result)."""

import json
import re
import sys
import time

msg = json.loads(json.loads(sys.stdin.readline())["message"]["content"])
lines = msg["conversation"]
action = msg["action"].lower().split("?")[0]
if "slow" in action:
    time.sleep(30)
if "broken" in action:
    print(json.dumps({"type": "result", "is_error": True, "result": "usage limit reached"}), flush=True)
    sys.exit(0)
words = re.findall(r"[a-z]+", action)
said = " ".join(line["text"] for line in lines if line["from"] == "user").lower()
yes_to = (lines[-2]["text"].lower() if len(lines) > 1 and lines[-1] == {"from": "user", "text": "yes"}
          and lines[-2]["from"] == "auren" else "")
allow = all(w in said for w in words) or bool(yes_to) and all(w in yes_to for w in words)
print(json.dumps({"type": "result", "is_error": False, "result": "ALLOW" if allow else "ASK"}), flush=True)
