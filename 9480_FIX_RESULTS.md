# Invoice 9480 extraction repair — 2026-09-18

The supplied output put logos, addresses, telephone numbers and email into 14
item rows and left the invoice date and totals empty. The canonical CLI used a
different, generic parser from the PDF result flow. It now uses the spatial
parser for recognized invoice layouts, including separate seller/customer VAT
fields and bounded item tables.

English retries also used `PP-OCRv5_mobile_rec` instead of the installed
PaddleOCR package's English model, `en_PP-OCRv5_mobile_rec`. The English model
was downloaded and tested locally on the original PDF. Missing numeric cells
receive bounded, enhanced crops, with one additional numeric-only pass after
table recovery. VAT retries include values printed above their labels.
Invoice-number retries reject 15-digit tax identifiers, accept terminal label
punctuation, and prefer the smaller printed serial over large handwriting.
Competing identifier readings remain visible in review notes.

## Actual PDF run

Source: `public_invoice_pdfs/9480.pdf`

SHA-256: `55a3c0dca569b522b33236f5be7fa8175fb2e317c766d03e17986aca1a3c0475`

The PDF was freshly rasterized and processed with local CPU PaddleOCR, including
live focused OCR retries. No invoice values were hard-coded into extraction.

| Field | Result |
| --- | --- |
| Printed invoice number | 692 |
| Handwritten reference | 9480 — retained as an ambiguity warning |
| Printed date | 11/03/2026 |
| Supplier VAT | 300056327900003 |
| Customer VAT | 300402905100003 |
| Item count | 1 |
| Quantity / unit price / amount | 2 / 90 / 180 |
| Subtotal / VAT / total | 180 / 27 / 207 |

These numeric fields were compared with the rendered source. This is a
single-invoice verification, not a corpus accuracy percentage. The filename is
not used as the invoice number.

## Remaining limitations

The result still requires review. Customer name is unreadable to the current
pipeline; the Arabic item description is low confidence. The printed zero
discount and currency are not recovered. Per-line VAT and gross fields remain
null where the source does not provide them. The handwritten date also differs
from the printed date; the reported date is the printed one.

## Validation and outputs

- 164 regression tests pass in the local OCR runtime, with no skips.
- Real before/after OCR evidence is retained in `tests/fixtures/9480_ocr.json`
  and `tests/fixtures/9480_recovered_ocr.json` for regression replay.
- All 111 cached corpus inputs produce schema-valid, finite canonical JSON
  without exceptions. This replay does not verify their field accuracy.
- Local outputs: `benchmark_outputs/9480-fixed.json`,
  `benchmark_outputs/9480-canonical-fixed.json`, and
  `benchmark_outputs/9480-raw-fixed.json`.
- Changes are local. GitHub publication remains unavailable because the
  authenticated account was denied write access to the repository (403).

Run regressions with `python -m unittest discover -p 'test_*.py' -q`.
