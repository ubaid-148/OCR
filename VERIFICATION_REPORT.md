> Historical baseline. See [COMPLETION_REPORT.md](COMPLETION_REPORT.md) for the subsequent fixes and current verification results.

# Current extraction verification — 2026-09-23

**Verdict: complete general-purpose extraction is not verified. Known missing-field
failures remain, including values present in raw OCR but absent from final JSON.**
This verification run changed no extraction logic.

## What was actually run

| Check | Result | What it establishes |
| --- | --- | --- |
| Python regression suite | 235 tests passed | Covered software behavior; inference is mocked in AI tests |
| Browser upload queue tests | Passed | Sequential uploads, failure isolation, single-file compatibility |
| PDFium rendering | All 111 PDFs / 115 pages rendered | Available source files can be opened and rendered at verification resolution |
| Current parser over cached OCR | 111 documents, zero exceptions | Cached text can be processed without crashes |
| Public JSON schema validation | 111/111 valid | Correct response structure, not correct field values |
| Filename substitution | 111/111 unchanged business results | Extraction does not depend on these source filenames |
| Three arithmetic checks together | 21/111 pass | Line calculations, subtotal and net reconcile in those results; not source accuracy |
| Limited source-image comparison | 28 of 35 selected fields matched; 7 failed | Concrete source-checked failures below; not an accuracy benchmark |
| Fresh PaddleOCR inference | Not run | Paddle is absent from the local Python environment |
| Fresh local vision inference | Not run | Ollama endpoint at 127.0.0.1:11434 refused connection outside the sandbox |

All cached results returned `needs_review`. The compact output also adds review
notes for absent optional fields, so this status alone does not prove a document
is wrong. The corpus consists of existing development samples, not an independent
held-out evaluation set. No universal or unseen-layout accuracy claim is justified.

## Missing fields in the cached replay

| Field | Documents missing it |
| --- | ---: |
| Invoice number | 23 |
| Invoice date | 28 |
| Entire item table | 23 |
| Subtotal | 46 |
| VAT amount | 21 |
| Net amount | 52 |

These are parser-required-field counts. They do not establish that each field was
printed on every source. The seven manually confirmed omissions below *are*
visible on the checked source pages.

## Confirmed source discrepancies

Only selected header and numerical/unit fields on page 1 of three source PDFs
were scored. Names, addresses and description spelling were not exhaustively
checked. Existing regression samples were used, so this is not held-out testing.

| Source | Field | Printed value | Current cached-input JSON | Evidence |
| --- | --- | --- | --- | --- |
| 9480.pdf | Invoice number | 692 | null | Printed invoice number; 9480 is handwritten. 692 is absent from this old raw OCR |
| 9480.pdf | Item 1 quantity | 2 | null | Visible in source; no standalone 2 in this cached OCR |
| 9515.pdf | Item 1 unit | pcs | null | Raw OCR contains pcs at 94.92% recognition confidence |
| 9515.pdf | Item 1 tax rate | 15% | null | Raw OCR contains 15.00% at 98.19% recognition confidence |
| 9522.pdf | Item 1 tax rate | 15% | null | Raw OCR contains 15% at 98.27% recognition confidence |
| 9522.pdf | Other charges | 0.00 | null | Label and zero are both present in raw OCR |
| 9522.pdf | Taxable amount | 40.00 | null | Source prints it; raw OCR contains the amount |

The first two failures require recognition recovery from the source image. The
remaining failures demonstrate field association/coverage gaps even when OCR
read the relevant values. High recognition confidence does not establish the
correct semantic field.

## Scope and evidence

The current `auto` default reads original page images, but this run could evaluate
only the deterministic path over cached OCR. The auto mode's results on these
PDFs remain unmeasured. Cached OCR provenance/version is not independently
established; fresh extraction could improve or worsen these outputs.

Saved artifacts:

- `benchmark_outputs/verification/summary.json`: per-file page counts, source and
  cache hashes, missing fields, arithmetic checks and contract results.
- `benchmark_outputs/verification/source-checks.json`: all 35 checked expected
  and actual field values.
- `benchmark_outputs/verification/<PDF stem>.json`: current result and diagnostics
  for each of the 111 cached inputs.
- `benchmark_outputs/verification/regression-tests.log`: regression run output.
- `benchmark_outputs/verification/code-fingerprints.json`: hashes identifying the
  extraction code evaluated.

To verify the end-to-end default, the prepared Paddle/vision runtime must run
fresh OCR and `auto` extraction on source PDFs, followed by source-label comparison.
A general-purpose claim additionally needs independently labelled unseen layouts,
long tables, continuation pages, rotations, faint scans and failure cases. Passing
an arithmetic check or JSON schema alone is insufficient.
