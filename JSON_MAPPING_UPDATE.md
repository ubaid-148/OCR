# JSON mapping update

Compact Colab/batch results now include `source_filename`, `extraction_mode`,
`field_sources`, `unassigned_ocr`, `missing_fields`, and `timings_seconds`.
Existing invoice fields remain in place. Consumers that forbid extra JSON keys
must allow these additions. The web response envelope remains unchanged.

`field_sources` maps canonical field paths to original OCR text, page numbers,
and `[left, top, width, height]` boxes in upright OCR coordinates. It contains
only associations accepted by the existing mapping audit; absence does not
prove a value was absent from the PDF. `unassigned_ocr` retains all remaining
boxes, including labels/logos; these are not automatically missing fields.

Explicit `Label: value` OCR boxes now retain their inline value in label pairing.
Missing-field reporting is refreshed after header recovery. On the 111 saved
OCR records, two recovered invoice numbers (9620 and 9865) had stale missing
flags. Those flags are corrected; this is not two newly extracted values.
Replay errors remain zero, invoice numbers present remain 90, and records
passing the three existing financial checks remain 23. Remaining extraction
gaps are not resolved by this update.

For speed, use the existing `fast` mode when an OCR-only result is sufficient.
`auto` also runs original-page vision and recovery, which can take substantially
longer. No live Paddle/vision latency improvement has been measured here.
The new timings separate OCR model loading/retries from field extraction.
Colab saves raw OCR before field extraction so an AI failure does not discard it.
You can regenerate JSON from saved raw OCR without running Paddle again:

```sh
python invoice_result.py path/to/raw.json --filename original.pdf --mode fast \
  --output result.json --details-output details.json
```

Verification: full existing suite plus initial regressions passed (297 tests);
two additional recovery/failure tests passed in the focused 11-test suite.
Browser upload queue checks also passed. These checks do not establish
ground-truth accuracy on arbitrary PDFs or separate invoices inside one PDF.
