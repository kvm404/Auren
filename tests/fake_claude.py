"""A stand-in for `claude -p --input-format stream-json`: answers slowly, word by word, and stops on an
interrupt control request (or, with "stubborn", ignores it; with "deaf", never reads). It remembers the
messages it was sent."""

import json
import queue
import sys
import threading

stubborn = "stubborn" in sys.argv
seen = []
lines: queue.Queue = queue.Queue()      # read on a thread, so no line waits unseen in stdin's buffer


def read():
    for line in sys.stdin:
        lines.put(json.loads(line))
    lines.put(None)


def out(ev):
    print(json.dumps(ev), flush=True)


if "deaf" in sys.argv:                   # never reads: a big enough message blocks the writer
    threading.Event().wait()
threading.Thread(target=read, daemon=True).start()
while (msg := lines.get()) is not None:
    if msg.get("type") != "user":
        continue                             # a stop with nothing in progress: nothing to do
    text = msg["message"]["content"]
    seen.append(text)
    if "slow" not in text:
        out({"type": "result", "is_error": False, "result": "earlier: " + " | ".join(seen[:-1]), "num_turns": 1})
        continue
    for i in range(200):
        try:
            req = lines.get(timeout=0.05)
        except queue.Empty:
            req = {}
        if req is None:
            sys.exit()
        if req.get("type") == "control_request" and not stubborn:
            out({"type": "control_response", "response": {"subtype": "success", "request_id": req["request_id"]}})
            out({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}})
            out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
            break
        out({"type": "assistant", "message": {"content": [{"type": "text", "text": f"word {i}"}]}})
    else:
        out({"type": "result", "is_error": False, "result": "finished", "num_turns": 1})
