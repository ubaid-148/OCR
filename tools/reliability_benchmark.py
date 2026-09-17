"""Run identical synthetic safety cases against old and current extraction code.

This measures regression protection, not PDF recognition accuracy or GPU speed.
The three changed modules use the same remaining dependencies in both runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("main.py", "validator.py", "llm_extractor.py")
WORKER = r'''
import importlib.util, json, pathlib, sys, unittest
baseline, root = sys.argv[1:]
sys.path.insert(0, root)
sys.path.insert(0, baseline)
import main, validator, llm_extractor
spec = importlib.util.spec_from_file_location("safety_cases", pathlib.Path(root) / "test_extraction_safety.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class Result(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.outcomes = {}
    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes.setdefault(test.id(), "passed")
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes[test.id()] = "failed"
    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes[test.id()] = "error"
    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self.outcomes[test.id()] = "failed"
result = Result()
unittest.defaultTestLoader.loadTestsFromModule(module).run(result)
print(json.dumps({"tests": result.testsRun, "passed": sum(v == "passed" for v in result.outcomes.values()),
                  "successful": result.wasSuccessful(), "outcomes": result.outcomes,
                  "failures": [(str(t), detail) for t, detail in result.failures + result.errors]}))
'''


def run(directory: Path) -> dict:
    process = subprocess.run([sys.executable, "-c", WORKER, str(directory), str(ROOT)],
                             text=True, capture_output=True, check=True, cwd=ROOT)
    result = json.loads(process.stdout)
    result["module_sha256"] = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                               for name in MODULES}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    baseline = parser.add_mutually_exclusive_group(required=True)
    baseline.add_argument("--baseline-dir", type=Path, help="Snapshot of the three modules before editing")
    baseline.add_argument("--baseline-ref", help="Existing Git commit containing the three modules")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ocr-safety-baseline-") as directory:
        old_dir = args.baseline_dir.resolve() if args.baseline_dir else Path(directory)
        revision = None
        if args.baseline_ref:
            revision = subprocess.check_output(["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"],
                                               cwd=ROOT, text=True).strip()
            for name in MODULES:
                (old_dir / name).write_bytes(subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=ROOT))
        before, after = run(old_dir), run(ROOT)
    outcomes = before["outcomes"], after["outcomes"]
    report = {"scope": "synthetic extraction safety; not invoice accuracy or Colab latency",
              "baseline_revision": revision, "before": before, "after": after,
              "fixed": [k for k, v in outcomes[1].items() if v == "passed" and outcomes[0].get(k) != "passed"],
              "regressed": [k for k, v in outcomes[1].items() if v != "passed" and outcomes[0].get(k) == "passed"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Before: {before['passed']}/{before['tests']} passed; after: {after['passed']}/{after['tests']} passed")
    print(f"Fixed: {len(report['fixed'])}; regressed: {len(report['regressed'])}; report: {args.output}")
    print(report["scope"])
    return 0 if after["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
