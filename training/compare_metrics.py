"""Fail closed unless a trained adapter clears the deployment quality gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare(base: dict, candidate: dict, min_critical: float, min_exact: float) -> list[str]:
    failures: list[str] = []
    if candidate.get("json_parse_failures", 1) != 0:
        failures.append("candidate has JSON parse failures")
    if (candidate.get("critical_accuracy_non_null") or 0) < min_critical:
        failures.append(f"critical accuracy is below {min_critical:.1%}")
    if (candidate.get("exact_document_rate") or 0) < min_exact:
        failures.append(f"exact document rate is below {min_exact:.1%}")
    for metric in ("field_accuracy_non_null", "critical_accuracy_non_null", "exact_document_rate"):
        if (candidate.get(metric) or 0) < (base.get(metric) or 0):
            failures.append(f"candidate regressed on {metric}")
    if candidate.get("unexpected_values_for_null_targets", 0) > base.get("unexpected_values_for_null_targets", 0):
        failures.append("candidate invented more values for null ground-truth fields")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--min-critical", type=float, default=0.98)
    parser.add_argument("--min-exact", type=float, default=0.90)
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    failures = compare(base, candidate, args.min_critical, args.min_exact)
    print(json.dumps({"approved": not failures, "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
