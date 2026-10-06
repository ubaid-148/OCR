# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local invoice OCR pipeline for mostly Saudi (ZATCA-style), Arabic and English invoices. It reads text from PDFs with PaddleOCR, maps that text to structured invoice JSON, and validates the arithmetic. An optional "Accuracy" mode also asks a local Ollama Qwen3-VL vision model to read the page image. Invoice PDFs are never sent to a cloud OCR or LLM provider. Keep it that way.

## Commands

```powershell
python -m pip install -r requirements.txt       # Python 3.11+; Tesseract on PATH only for the searchable-PDF path
python ocr_web.py                                # web app on http://127.0.0.1:8765 (OCR_PRELOAD=true warms models)
python coordinate_ocr.py invoice.pdf out.json eng+ara    # PDF -> pages/words/bboxes JSON
python main.py out.json --output invoice.json --pdf invoice.pdf

# Tests: stdlib unittest, flat test_*.py files at the repo root.
# On Windows set PYTHONUTF8=1 first: fixtures are UTF-8 and the default cp1252 decode fails.
python -m unittest discover -p "test_*.py"
python -m unittest test_layout_invoice                       # one module
python -m unittest test_layout_invoice.LayoutInvoiceTests.test_address_and_vat_rate_are_not_money   # one test
node tests/test_upload_queue.js                              # browser upload-queue test for app.js

python -m tools.build_colab_bundle      # writes dist/ notebook + invoice-ocr-code.zip
python -m tools.batch_local_ocr --pdf-dir public_invoice_pdfs --mode fast
```

Most tests run on the fixtures in `tests/fixtures/` (saved OCR output) and don't need Paddle or Ollama inference. `tools/` holds diagnostics: OCR parameter sweeps, per-field error attribution against reference files, and cached-extraction replay/verification.

## Pipeline architecture

1. **OCR**: `coordinate_ocr.extract_pdf` renders pages with pypdfium2 and uses embedded PDF text when it exists (`native_pdf.py`). Otherwise it runs PaddleOCR, fixing page orientation first (`page_rotation.py`). When the parsed result has gaps, `targeted_ocr.py` rereads cropped regions at higher resolution. The output is `{"engine", "pages": [{"words": [{text, bbox, confidence}], ...}]}`, the raw OCR payload that every later stage uses.
2. **Spatial draft**: `local_ai_parser.parse_invoice_hybrid` is the main parser. `document_regions.invoice_words` removes attached payment-receipt regions. Two parsers then compete. `invoice_formatter.parse_invoice` is the legacy parser and only reads page 1. `layout_invoice.parse_layout` reads all pages and finds the item table. `_validate` checks the math and `_result_score` picks the better candidate; `_merge_losing_draft` then fills empty header fields and item descriptions from the losing draft. Post-passes in `parse_invoice_hybrid` fill the rest, each fill-only and flagged when weak: `invoice_details` (CR, bank, labelled extras), `label_value_pairing` (invoice number), `party_fields` (seller/customer label grids, stacked or side by side), `totals_reconcile` (missing totals accepted only if a printed amount satisfies subtotal − discount + VAT = net), `amount_words` (amount in words, verified against the net). `mapping_coverage` records which OCR box each field came from; words mapped to no field end up in `unmapped_text`.
3. **Accuracy mode (`mode="auto"`)**: `invoice_result.extract_result` calls `visual_invoice.parse_invoice_visual`. That function renders the page images and sends them through `ollama_http` to Qwen3-VL. When fields are missing, `visual_recovery` rereads focused crops. The vision reading is then checked against the spatial draft. `fast` mode skips vision completely.
4. **Output boundary**: `invoice_response.clean_invoice_response` (web) and `invoice_result.extract_result` (Colab) turn the output into the public JSON contract with `SCHEMA_VERSION`. `canonical_schema.py` and `validator.py` do the same job for the `main.py` CLI path.

Entry points:
- `ocr_web.py` is a stdlib `http.server`. It takes one PDF per POST, and `app.js` queues multiple uploads on the client. It always runs `fast` mode, and its output includes `pipeline_version`. Set `OCR_PYTHON_EXE` to run OCR in a separate interpreter, for example the ignored `.paddle-venv`.
- **Colab** (`colab_setup.ipynb` and `colab_native_upload.ipynb`) is where GPU and Accuracy mode actually run. `colab_runtime.py` and `colab_vision.py` install Paddle, Ollama and the model. `colab_worker.InvoiceWorker` keeps a single subprocess alive across a batch so the Paddle models stay loaded. The GitHub notebook downloads published `main`, so Colab only sees a change after it is pushed. `tools.build_colab_bundle` is the way to test local changes. See `COLAB_TESTING.md`.
- `training/` is a separate Qwen3-VL LoRA fine-tuning workflow (`python -m training.data prepare|export`, `python -m training.run draft|evaluate|train`) run from `colab_train.ipynb`. Its own `requirements.txt` and `README.md` describe it.

## Key env vars

`OCR_DEVICE` (`cpu` / `gpu` / `gpu:0`), `OCR_FORCE_RASTER`, `OCR_TARGETED_RETRY`, `OCR_PRELOAD`, `OCR_PYTHON_EXE`, `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_NUM_CTX`, `OLLAMA_NUM_PREDICT` (item token budget), `OLLAMA_HEADER_NUM_PREDICT` (header token budget, default 8192), `OLLAMA_THINK`, `VISION_RECOVERY`, `VISION_REQUIRE_GPU`, `VISION_RENDER_DPI`, `VISION_TABLE_DPI`.

## Conventions that matter

- **Evidence over guessing.** Never fabricate, infer or "fix" a value. That includes assuming a default VAT rate or currency, filling fields by row index, or correcting printed arithmetic. If a value can't be traced to an OCR box or a vision reading, leave it `null`, set `quality.needs_review`, and explain why in `review_reasons`. When two readings disagree, flag the conflict rather than silently choosing one. Many tests enforce this, for example `test_extraction_safety`, `test_live_sample_safety` and `test_missing_fields`.
- Validation uses `Decimal` with a 0.02 tolerance. Some templates print per-unit taxable and VAT columns (`calculation_mode="per_unit_printed_columns"`). Handle those explicitly rather than rearranging numbers until the math works.
- Legacy parser geometry is single-page. Never mix coordinates from different pages.
- Invoice-specific bugs get a regression test named after the PDF, such as `test_9480_regression.py`, using the matching fixture in `tests/fixtures/`. The sample PDFs are in `public_invoice_pdfs/`.
- The repo has many status/report markdown files (`*_RESULTS.md`, `*_REPORT.md`, `QA_FIXES.md`, and so on). They are historical notes, not specs. `PROJECT_GUIDE_ROMAN_URDU.md` is a project guide written in Roman Urdu.
