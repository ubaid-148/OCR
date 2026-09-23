# OCR code review and fixes — 2026-09-23

The requested `public_invoice_pjoson` directory was not present. Reviewed the
application, parser, result boundaries and batch/Colab upload paths using
`public_invoice_pdfs` and 111 cached OCR payloads in `../pairing-baseline/raw`.

## Fixed

- Web UI accepts multiple PDFs and sends one request at a time, preserving each
  filename/result. Network or document failures do not discard other results.
  Single-file responses retain their existing shape. Direct requests containing
  multiple PDF parts are rejected explicitly instead of silently keeping the last.
- Colab accepts multiple PDFs, isolates failures and prefixes batch output names
  to avoid collisions after filename sanitization. Its current subprocess flow
  still reloads OCR per document; the batch CLI reuses recognition models.
- Totals extraction selects a page-local financial footer instead of blindly
  reading the last page. A final attachment/QR page no longer erases prior totals.
  Labels and numeric values are never paired across pages.
- Label and amount in one OCR box are supported, including thousands separators.
  Percentages, unrelated label suffixes and multiple amounts are not accepted as
  a single monetary value. Original evidence remains attached.
- Added Arabic total-label variants.
- Batch discovery accepts uppercase `.PDF`; non-object cache JSON is reprocessed
  rather than crashing outside the document error handler.

## Verification

228 Python tests passed in a temporary environment containing Pillow, OpenCV and
PDFium. Browser queue tests passed with Node (sequential upload, failure isolation,
and single-file compatibility). These are not live Paddle/Ollama inference tests.

Replayed all 111 cached OCR records before and after: zero parser errors. Missing
field counts changed as follows:

| Field | Before | After |
| --- | ---: | ---: |
| Subtotal | 47 | 46 |
| VAT amount | 23 | 21 |
| Net amount | 53 | 52 |
| Invoice number | 23 | 23 |
| Invoice date | 28 | 28 |
| Entire item table | 23 | 23 |

Source PDF images were inspected for the recovered multi-page totals:
`9525.pdf` page 2 prints VAT 24.00 and net total 184.00; page 3 is an attachment.
`9855.pdf` page 1 prints subtotal 4675.00 and VAT 701.25; page 2 contains a QR code.
No previously populated required field was lost in the replay. The count of
records passing all three existing arithmetic checks remains 20/111. This is a
coverage check, not a measured accuracy percentage.

## Remaining limitations

- Missing/wrong OCR characters cannot be restored reliably by parser rules alone.
  No fresh PaddleOCR or vision inference was run in this review.
- Headerless continuation tables and multiple separate invoices within one PDF
  still need document segmentation/layout work; multiple uploaded PDFs are
  processed independently, while pages within one PDF are treated as one invoice.
- Source-verified labels for all invoices are needed to measure exact-value
  accuracy. Missing fields remain null and review flags remain visible.
- The web and compact Colab result schemas differ; the web schema does not expose
  every optional field supported by the compact Colab result (e.g. bank details).

Run regression tests:

```sh
python -m unittest discover -p 'test_*.py' -q
node tests/test_upload_queue.js
```

Run fresh batch OCR in the prepared OCR/Colab environment:

```sh
python -m tools.batch_local_ocr --pdf-dir public_invoice_pdfs --output benchmark_outputs/live-local
```

Each PDF gets its own result and raw OCR file; `summary.json` lists failures and
missing fields. Failed files are retried on restart. The CLI now defaults to auto (original-page local vision plus OCR); use `--mode fast` for OCR-only extraction.

## General-layout default follow-up

Colab and the batch CLI now default to the existing image-first `auto` path.
The batch passes the original PDF into vision extraction, saves full extraction
diagnostics, and includes extraction mode, language and model configuration in
its cache signature. An explicit `fast` option preserves OCR-only usage.
These changes wire the existing general-layout reader into the default flows;
they do not establish universal PDF accuracy or add multi-invoice segmentation.

## Raw OCR to field mapping follow-up

Fixed overlapping subtotal/VAT/net label matches: a tax-inclusive total no longer
also populates subtotal, and an Arabic pre-tax total no longer populates VAT.
Joined Arabic tax labels are recognized without losing their field meaning.
The legacy label matcher now stays on the same page, excludes other recognized
labels as values, and uses word-boundary matching instead of arbitrary substrings.

Source-verified example: `9657.pdf` contains subtotal 62.61, VAT 9.39 and total
72.00. The original parser incorrectly assigned 62.61 to both subtotal and VAT
despite raw OCR containing 9.39. The updated parser assigns VAT 9.39. This is a
field-association fix, not a recognition/model improvement.

235 Python tests pass, including six mapping regressions. All 111 cached records
replay without parser errors. These focused fixes do not make every possible
label association unambiguous; unseen layouts still require source review.
