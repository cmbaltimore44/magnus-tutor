"""Send one message to a session and print the streamed reply (dev helper).
Usage: turn.py new <course> [mode] | turn.py <session_id> "<text>" [action]"""
import json
import sys

import httpx

URL = "http://127.0.0.1:8765"
if sys.argv[1] == "new":
    r = httpx.post(f"{URL}/api/sessions", json={"course": sys.argv[2], "mode": sys.argv[3] if len(sys.argv) > 3 else "office_hours"})
    print(r.json()["session"]["id"])
    sys.exit()
sid, text = sys.argv[1], sys.argv[2]
action = sys.argv[3] if len(sys.argv) > 3 else None
with httpx.stream("POST", f"{URL}/api/sessions/{sid}/messages", json={"text": text, "action": action}, timeout=600) as r:
    ev = None
    for line in r.iter_lines():
        if line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            d = json.loads(line[5:])
            if ev == "token":
                print(d["text"], end="", flush=True)
            elif ev == "meta":
                print(f"[meta level={d.get('hint_level')} reason={d.get('reason')} solver={d.get('solver_status')}]")
            elif ev == "reset":
                print(f"\n[RESET: {d['reason']}]")
            elif ev == "done":
                print(f"\n[done {d['stats']}]")
            elif ev == "error":
                print(f"[ERROR {d['message']}]")
