"""A stand-in for the judge's `claude -p`: says ALLOW if the message's text (the first “…” in the action)
appears in what the user said, or in Auren's question just before a plain "yes"; ASK otherwise
(answered in the judge's JSON shape) ("slow"
in the text: never answers; "broken": an error result)."""

import json
import re
import sys
import time

msg = json.loads(json.loads(sys.stdin.readline())["message"]["content"])
lines = msg["conversation"]
text = re.search(r"“([^”]*)”", msg["action"]).group(1).lower()
if "slow" in text:
    time.sleep(30)
if "broken" in text:
    print(json.dumps({"type": "result", "is_error": True, "result": "usage limit reached"}), flush=True)
    sys.exit(0)
said = " ".join(line["text"] for line in lines if line["from"] == "user").lower()
yes_to = (lines[-2]["text"].lower() if len(lines) > 1 and lines[-1] == {"from": "user", "text": "yes"}
          and lines[-2]["from"] == "auren" else "")
allow = text in said or bool(yes_to) and text in yes_to
answer = {"kind": "chat", "to_said": "them", "to_shown": "them", "same_text": allow, "told_to_send": allow,
          "verdict": "ALLOW" if allow else "ASK"}
print(json.dumps({"type": "result", "is_error": False, "result": json.dumps(answer)}), flush=True)
