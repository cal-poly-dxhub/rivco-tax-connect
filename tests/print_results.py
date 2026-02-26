#!/usr/bin/env python3
"""Pretty-print e2e_results.json for human reading."""
import json, sys, textwrap

path = sys.argv[1] if len(sys.argv) > 1 else "tests/e2e_results.json"
results = json.load(open(path))

passed = sum(1 for r in results if r["verdict"] == "PASS")
failed = sum(1 for r in results if r["verdict"] == "FAIL")
errors = sum(1 for r in results if r["verdict"] == "ERROR")
total_in = sum(r.get("input_tokens", 0) for r in results)
total_out = sum(r.get("output_tokens", 0) for r in results)

print(f"\n  {passed} passed / {failed} failed / {errors} errors  ({len(results)} total)")
print(f"  Tokens: {total_in:,} in / {total_out:,} out\n")
print("=" * 70)

for r in results:
    v = r["verdict"]
    tag = "PASS" if v == "PASS" else "FAIL" if v == "FAIL" else "ERR "
    print(f"\n  [{tag}] {r['idx']}. {r['name']}  ({r['duration']}s)")
    print(f"         tokens: {r.get('input_tokens',0):,} in / {r.get('output_tokens',0):,} out")

    if r.get("failure_reason"):
        print(f"         reason: {r['failure_reason']}")
    if r.get("failed_checks"):
        for fc in r["failed_checks"]:
            print(f"           - {fc}")

    reasoning = r.get("reasoning", "")
    if reasoning:
        wrapped = textwrap.fill(reasoning, width=65, initial_indent="         ", subsequent_indent="         ")
        print(wrapped)

    print(f"\n         --- transcript ---")
    for line in r.get("transcript", "").strip().splitlines():
        print(f"         {line}")
    print(f"         --- end ---")

print()
