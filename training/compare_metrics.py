"""Fail closed unless a trained adapter clears the deployment quality gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare(base: dict, candidate: dict, min_critical: float, min_exact: float) -> list[str]:
    failures: list[str] = []
    if not candidate.get("documents"):
        failures.append("candidate has no evaluated documents")
    if base.get("split") != candidate.get("split") or base.get("documents") != candidate.get("documents"):
        failures.append("base and candidate did not evaluate the same split and document count")
    if (not base.get("manifest_sha256") or not base.get("document_ids")
            or base.get("manifest_sha256") != candidate.get("manifest_sha256")
            or base.get("document_ids") != candidate.get("document_ids")):
        failures.append("base and candidate metrics belong to different ground-truth manifests")
    if base.get("prediction_documents") != base.get("documents"):
        failures.append("base prediction count does not match the held-out document count")
    for key in ("missing_prediction_ids", "extra_prediction_ids", "duplicate_prediction_ids"):
        if base.get(key):
            failures.append(f"base has {key}")
    if candidate.get("prediction_documents") != candidate.get("documents"):
        failures.append("candidate prediction count does not match the held-out document count")
    for key in ("missing_prediction_ids", "extra_prediction_ids", "duplicate_prediction_ids"):
        if candidate.get(key):
            failures.append(f"candidate has {key}")
    if candidate.get("json_parse_failures", 1) != 0:
        failures.append("candidate has JSON parse failures")
    if (candidate.get("critical_accuracy_non_null") or 0) < min_critical:
        failures.append(f"critical accuracy is below {min_critical:.1%}")
    if (candidate.get("exact_document_rate") or 0) < min_exact:
        failures.append(f"exact document rate is below {min_exact:.1%}")
    if (candidate.get("item_row_exact_rate") or 0) < min_critical:
        failures.append(f"exact item-row rate is below {min_critical:.1%}")
    for metric in ("field_accuracy_non_null", "critical_accuracy_non_null", "exact_document_rate", "item_row_exact_rate"):
        if (candidate.get(metric) or 0) < (base.get(metric) or 0):
            failures.append(f"candidate regressed on {metric}")
    if candidate.get("unexpected_values_for_null_targets", 0) > base.get("unexpected_values_for_null_targets", 0):
        failures.append("candidate invented more values for null ground-truth fields")
    if candidate.get("unexpected_values_for_null_targets", 0):
        failures.append("candidate emitted values for null ground-truth fields")
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
