# Extraction safety results — 2026-09-17

| Check | Before | After |
| --- | --- | --- |
| Identical synthetic safety cases | 2/13 pass | 13/13 pass |
| Cases fixed / newly failing | — | 11 fixed / 0 regressed |
| Isolated publication regression suite | — | 127 tests pass, no skips |

The safety benchmark compares `main.py`, `validator.py`, and `llm_extractor.py`
from published commit `02fd201c23b85dea3abb7545f9155d02734cf85d` with the updated
modules, using the same remaining dependencies. A separate comparison against
the pre-edit working snapshot also scored 2/13 before and 13/13 after.
These cases were selected to reproduce known failure modes; they do not
estimate production frequency, invoice accuracy, or model recognition quality.

Covered failures: reordered/extra/duplicate/unanchored AI item rows, lost review
reasons, identifier leading zeroes and integer precision, non-finite arithmetic,
invalid AI structure/numbers, and truncated model responses. Correctly aligned
unique item rows can still fill missing values with explicit manual-review notes.

Local workspace validation: 148 tests passed before the final finite-number
parser tightening; the 13 affected safety cases passed again afterward. The
isolated publication suite was rerun after that tightening: 127 tests passed.
The workspace includes pre-existing changes not included in this publication.

A separate rules-only replay used cached OCR evidence from 22 real PDFs:
0 parser errors before/after and 0 documents with changed extracted fields.
That replay used the working snapshot and its current shared dependencies, not
an end-to-end comparison of historical repository revisions. It reran neither
PaddleOCR nor an AI model and had no verified answer set. Its raw JSON and report
remain local under `benchmark_outputs/`.

**Live Colab GPU execution has not been performed. No improvement in real-PDF
accuracy or inference speed has been established.**

## Reproduce

The Colab notebook now only prepares OCR and returns the uploaded invoice result. Developer safety checks remain available through the command below; they are not part of the notebook flow.

Locally, from the repository root with dependencies available:

```sh
python tools/reliability_benchmark.py --baseline-ref 02fd201 --output benchmark_outputs/safety-before-after.json
python -m unittest discover -p 'test_*.py' -q
```

The runtime used locally was macOS CPU, Python 3.9 with the existing Paddle
runtime dependencies. Tests use mocks/synthetic data; they do not execute a
Colab GPU or a live Ollama model.
