#!/usr/bin/env python3
"""End-to-end integration test runner for the Riverside refund-bot stack.

Usage
-----
  # Run every case against the rivco v3 stack:
  python -m tests.integration.runner

  # Limit to one case:
  python -m tests.integration.runner --case chat-smoke

  # Point at a different stack:
  python -m tests.integration.runner \
      --api-url   https://owago2nrwc.execute-api.us-west-2.amazonaws.com/prod \
      --ws-url    wss://2tfmq7uxcj.execute-api.us-west-2.amazonaws.com/prod

Defaults come from environment variables:
  RIV_API_URL   default https://owago2nrwc.execute-api.us-west-2.amazonaws.com/prod
  RIV_WS_URL    default wss://2tfmq7uxcj.execute-api.us-west-2.amazonaws.com/prod

Exit code: 0 if every selected case passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from time import perf_counter

from tests.integration import cases as cases_mod
from tests.integration.client import StackEndpoints

DEFAULT_API = "https://owago2nrwc.execute-api.us-west-2.amazonaws.com/prod"
DEFAULT_WS = "wss://2tfmq7uxcj.execute-api.us-west-2.amazonaws.com/prod"


def main() -> int:
    p = argparse.ArgumentParser(description="Riverside integration tests")
    p.add_argument(
        "--api-url",
        default=os.environ.get("RIV_API_URL", DEFAULT_API),
        help="Upload REST API base URL (with /prod, no trailing /)",
    )
    p.add_argument(
        "--ws-url",
        default=os.environ.get("RIV_WS_URL", DEFAULT_WS),
        help="Chat WebSocket URL (no query string)",
    )
    p.add_argument(
        "--case",
        action="append",
        default=[],
        help="Run only this case (slug). Repeat to run a subset.",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List discovered case names and exit",
    )
    args = p.parse_args()

    eps = StackEndpoints(api_url=args.api_url, ws_url=args.ws_url)
    discovered = cases_mod.all_cases()

    if args.list:
        for name, _ in discovered:
            print(name)
        return 0

    selected = discovered
    if args.case:
        wanted = set(args.case)
        selected = [c for c in discovered if c[0] in wanted]
        missing = wanted - {c[0] for c in selected}
        if missing:
            print(f"Unknown case(s): {sorted(missing)}", file=sys.stderr)
            print("Discovered:", [c[0] for c in discovered], file=sys.stderr)
            return 2

    print(f"\nRunning {len(selected)} case(s) against:")
    print(f"  API:  {eps.api_url}")
    print(f"  WS:   {eps.ws_url}\n")

    passed: list[str] = []
    failed: list[tuple[str, str]] = []

    for name, fn in selected:
        start = perf_counter()
        try:
            fn(eps)
            dur = perf_counter() - start
            print(f"  ✓  {name}  ({dur:.2f}s)")
            passed.append(name)
        except AssertionError as e:
            dur = perf_counter() - start
            print(f"  ✗  {name}  ({dur:.2f}s)")
            print(f"     assert: {e}")
            failed.append((name, str(e)))
        except Exception as e:  # noqa: BLE001
            dur = perf_counter() - start
            print(f"  ✗  {name}  ({dur:.2f}s)  [ERROR]")
            tb = traceback.format_exc().splitlines()
            for line in tb[-6:]:
                print(f"     {line}")
            failed.append((name, f"{type(e).__name__}: {e}"))

    print()
    print(f"  {len(passed)} passed / {len(failed)} failed  ({len(selected)} total)")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
