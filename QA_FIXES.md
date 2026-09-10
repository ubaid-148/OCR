# Invoice extraction fixes — September 2026

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

## Validation

- 35 regression tests passed, including native PDF extraction and synthetic
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
