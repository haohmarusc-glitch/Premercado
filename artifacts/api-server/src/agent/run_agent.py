"""
Entry point called as: python3 -m agent.run_agent
Writes STEP: lines to stdout for progress tracking, and REPORT: <content> at the end.
"""
import sys

from . import agent as a


def progress(step: str) -> None:
    print(f"STEP:{step}", flush=True)


if __name__ == "__main__":
    try:
        report = a.run(progress_callback=progress)
        print("REPORT:" + report, flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
