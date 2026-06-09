"""
Entry point called as: python3 -m agent.run_agent
Writes STEP: lines to stdout for progress tracking, and REPORT: <content> at the end.

AGENT_MODE env var controls the run type:
  daily     (default) — full pre-market analysis
  premarket           — fast intraday flash scan
"""
import os
import sys

from . import agent as a


def progress(step: str) -> None:
    print(f"STEP:{step}", flush=True)


if __name__ == "__main__":
    mode = os.environ.get("AGENT_MODE", "daily")
    try:
        if mode == "premarket":
            report = a.run_premarket(progress_callback=progress)
        else:
            report = a.run(progress_callback=progress)
        print("REPORT:" + report, flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
