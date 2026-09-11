# Invoice extraction fixes — September 2026

## Faint invoice mapping follow-up

9480 exposed missing quantity anchors, alphabetic item codes, customer VAT labels
above customer names, and separate Total (Excl) VAT / Total With VAT footers.
The parser now retains a partial row, separates those totals, excludes VAT IDs and
dates from nearby invoice-number candidates, and rejects tiny QR noise as footer
amounts. Faint English ruled cells receive bounded 400 DPI contrast/thickening
retries. Unreconciled corrected prices on quantity-missing rows remain null.

The saved base OCR plus new source-PDF crops yields one partial item with printed
amount 180, subtotal 180, VAT 27 and invoice total 207. Quantity, unit price, item
code, customer name and invoice number remain unresolved; description spelling
also requires review. Printed and handwritten dates differ, so the returned
printed date must not be taken as resolution of that conflict. This is a mapping
and conservative recovery fix, not complete transcription of the faint source.
All 46 regression tests pass. Previous 9605–9607 selected checks pass in both
modes; 9609's invoice number, item code and total also pass evidence replay.
The final full-PDF CPU run of 9480 also retained one partial item and returned
subtotal 180, VAT 27 and total 207. Unresolved fields remain flagged for review.

## Upload latency follow-up

9480.pdf took 57.0 seconds for OCR on the local CPU (2.9 seconds model setup,
54.2 seconds rendering/recognition/retries). Its Fast extraction remains incomplete
and needs review; this latency change does not claim to fix that layout. No live
Colab timing or GPU speedup was measured. Fast is now the upload default, models
are prepared during Colab server startup, and the UI reports progress instead of
only disabling the button. Balanced has a 20-second Colab AI wait and bounded
generation. Concurrent uploads return 429. All 42 tests pass, including admission
and failure cleanup checks; notebook code cells pass syntax validation.

The parser now separates receipt text from invoice fields, detects item columns
with skew-aware geometry, preserves product codes and leading zeros, and matches
footer totals using additional label forms. Selected-field evidence identifies
the actual page/box used, including each item row.

Uncertain regions receive bounded 300 DPI English re-OCR. Alternatives and source
coordinates are retained; candidates below 85% confidence are not promoted.
The English model downloads once when first needed. The main OCR route still
uses the selected Arabic/English recognition model and 200 DPI rendering.

Line VAT and gross amounts are separate. A missing line net may be derived only
when quantity, price, printed VAT and gross reconcile; `amount_source` records
that derivation. Source rounding differences remain visible and require review.
Balanced mode avoids AI calls when the outstanding work is source/spelling review
and the financial checks pass. It still uses AI for unresolved parsing problems.

## Arabic table follow-up (9609)

The supplied Colab result missed identifiers and all items because merged Arabic
headers prevented table detection and also blocked retries. Header recovery now
works independently, with up to eight additional ruled-cell Arabic crops alongside
the six targeted English crops. Same-row customer values take precedence over
nearby labels; Arabic footer labels and numeric-only invoice numbers are supported.
The response identifies this revision as `2026-09-arabic-grid`.

The original 9609 PDF was rerun through full-page OCR and targeted crops on CPU.
Fast and Balanced modes recovered invoice 236863, date 25/05/2026, both VAT
identifiers, customer, item 040814, quantity 1, price/subtotal 17.39, VAT 2.61
and total 20.00 SAR. Bilingual name/description spelling still requires review.
The previous 57 selected checks also passed in both modes when replaying their
saved OCR evidence through this parser. These follow-up checks are not new
full-page OCR runs of 9605–9607.

## Validation

- 39 regression tests passed, including native PDF extraction and synthetic
  receipt, serial/code, tax-inclusive, numeric corruption and retry cases.
- All three supplied single-page scans were run through full-page OCR and the
  updated parser. The final tighter invoice-number crop was additionally rerun
  against the original PDF; base OCR was reused for that crop-only adjustment.
- Selected acceptance checks: 9605 improved from 16/21 to 21/21, 9606 from 2/11
  to 11/11, and 9607 from 17/25 to 25/25, in both Fast and Balanced modes.
- Nine combined box-order/coordinate-scale checks preserved semantic results.
  Evidence coordinates and stored receipt regions were transformed consistently.
- Six HTTP boundary checks passed. Corrupt/wrong-extension uploads returned 400
  JSON responses and left no temporary upload files.
- Colab code cells parsed successfully. Live Colab/GPU performance is not measured.

These are selected acceptance checks, **not an overall accuracy percentage**.
They include row counts and description substrings, not exhaustive bilingual
transcription. Receipt-covered headers remain null, and supplier/description
spelling can still be imperfect. All three samples appropriately retain review
flags. The originals and detailed QA artifacts remain local in `qa_samples/`.

To test the published version, restart Colab and run the notebook from cell 1.
Cell 1 downloads `main`; merely opening the existing embedded app does not reload
its Python modules. First-use OCR model downloads can add startup time.
