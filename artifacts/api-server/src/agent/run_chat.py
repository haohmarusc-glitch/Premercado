"""
Entry point: python3 -m agent.run_chat
Reads CHAT_MESSAGE and CHAT_HISTORY_JSON from environment.
Prints STEP: lines and a final RESULT:<json> line to stdout.
"""
import json
import os
import sys

from . import agent as a


if __name__ == "__main__":
    message = os.environ.get("CHAT_MESSAGE", "").strip()
    if not message:
        print("ERROR: CHAT_MESSAGE is empty", file=sys.stderr, flush=True)
        sys.exit(1)

    try:
        history = json.loads(os.environ.get("CHAT_HISTORY_JSON", "[]"))
    except Exception:
        history = []

    try:
        a.run_chat_stream(message, history)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
