# Local Invoice OCR

Colab-first application for multi-layout invoice PDFs. In Accuracy mode, a local
vision model reads **the original image of every PDF page**, while PaddleOCR
provides independent text/position evidence. The application returns full
structured invoice fields, checks table rows and totals, and flags disagreement.
Fast mode uses only the deterministic spatial parser. No generic model or
unverified PDF collection guarantees Cloud-level accuracy.

## Requirements

- Python 3.11+
- Tesseract OCR installed and available on `PATH`
- Ollama running locally (optional, used for AI parsing)
- A vision-capable Ollama model (Colab accuracy default: `qwen3-vl:4b`)

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Place required Tesseract language files in `tessdata/`, or configure your
Tesseract installation. This repository includes English, Arabic, Urdu, and
orientation language data.

Optional environment variables:

- `OLLAMA_URL` (default: `http://127.0.0.1:11434/api/chat`)
- `OLLAMA_MODEL` (default for image-first requests: `qwen3-vl:4b`)
- `OCRMYPDF_EXE` (explicit OCRmyPDF executable path)
- `OCR_PYTHON_EXE` (Python executable used for coordinate OCR)

## Run

```powershell
python ocr_web.py
```

Open <http://127.0.0.1:8765> and upload a PDF invoice.

## Google Colab

[Open the invoice notebook](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb), choose **Runtime → Change runtime type → T4 GPU**, then **Runtime → Run all**.
Upload one PDF when prompted. Its final invoice JSON is displayed and downloaded automatically.

The notebook has three steps: load project, prepare OCR, and upload/get result.
Step 1 prints the Git commit being tested. Step 2 checks the Paddle device and
runs a committed invoice sample through the parser before enabling upload.
Step 2 reads dependencies from `/content/OCR/requirements.txt`, so it works
even when Colab's current directory changes after cloning.
The sample check does not run live OCR; the first PDF upload does. After upload,
the notebook prints the OCR device, pipeline version, and stage timings above the
downloaded invoice JSON. Check that the device says `gpu:0` when testing on T4.
Focused retries now reread faint customer names and item descriptions from both
enhanced and original crops. A numeric item row without a readable description
triggers a table reread; a failed crop is reported while other retries continue.
It runs PaddleOCR with focused retries, followed by the spatial invoice parser and validation (`invoice_result.py`). The result contains invoice identity, supplier/customer, real table rows, totals, and short deduplicated review notes. Raw evidence and repeated arithmetic diagnostics are not printed. Unreadable quantities remain null; they are never inferred by dividing totals.
Missing or uncertain fields remain flagged for review. There are no comparison,
benchmark, raw-evidence display, or optional model setup cells in this flow.
The web application's optional full-page vision flow is separate.

Cell 1 uses `PROJECT_REF=main`. Reopen this updated notebook;
if your existing runtime has a clone of another branch, use a fresh runtime.

## No training workflow

This repository does not train or fine-tune a model. The 111 PDFs in
`public_invoice_pdfs/` were explicitly authorized for public distribution and
are source documents for Colab diagnostics, not verified labels. OCR and stock
vision outputs remain drafts: arithmetic, positioned evidence, and
`needs_review` are required before using extracted financial data.

## Flow

The image-first output keeps seller/customer names and addresses separate; invoice
and supply dates, payment, handwritten notes, item code/quantity/price/tax/total,
VAT summary, printed totals and other labelled fields are exposed when visible.
Absent or uncertain fields remain null. Handwriting and signatures are not
independently verified; Arabic spelling may still need human review.

Faint tables retain partial rows when quantity is unreadable. Explicit alphabetic
item-code columns are supported. Total excluding VAT and total including VAT are
mapped separately; nearby dates and VAT identifiers cannot become invoice numbers.
Selected faint English table crops use 400 DPI with contrast and stroke thickening.
This does not guarantee recovery: uncertain fields remain null and require review.

Uploads default to Accuracy (full-page vision plus OCR, validation and review). Fast explicitly skips AI;
Accuracy makes two focused vision requests per page (non-table fields, then item rows) to reduce the one-shot JSON truncation seen on `9498.pdf`. A long page can still exceed the 4096-token output limit and will be flagged for review. Colab allows a 180-second socket timeout **per request**. The page shows
the current OCR stage and elapsed time and displays formatted JSON without navigation.
Concurrent uploads receive HTTP 429 instead of accumulating in the native OCR queue.
Colab loads Arabic and English OCR models during server startup, so model download
time is visible in setup rather than hidden in the first upload. This moves cold
startup cost; it does not remove it. GPU acceleration still requires CUDA Paddle.

The default **Invoice JSON** output contains `status`, complete supported invoice `data`, short
`review_notes`, `ocr_device`, and stage-level `timings_seconds`. It omits raw OCR,
coordinates, confidence evidence, and internal validation details. Missing values
remain null and uncertain results retain `needs_review`. Item `amount` is pre-tax;
`gross_amount` includes VAT.
Select **Detailed invoice JSON (debug)** (`format=invoice_debug`) for the original
diagnostic response, including derivation evidence. Raw OCR remains a separate option.

The September sample fixes add skew-aware table rows, bounded column matching
(including serial numbers versus item codes), broader footer labels and payment
receipt isolation. Invoice fields never use text inside the detected receipt
region. Detection is heuristic: missing fields still need the unobstructed source.

Uncertain identifiers, malformed numeric cells and mixed-script text can receive
up to six targeted crops per page at 300 DPI using the English Paddle recognition
model. Crop results are mapped back to the original 200 DPI coordinates.
Ruled tables with merged headers additionally receive cell-local header and value
crops when rows are missing or a detected row loses/collides with a printed column.
Missing totals and source arithmetic discrepancies no longer trigger a costly
whole-table retry; they use focused footer crops and remain flagged for review.
Header fields can be recovered independently of table detection. Responses carry
`pipeline_version: 2026-09-evidence-gated-v12` to identify this flow.
Typed candidates below 85% confidence are not promoted; raw alternatives remain in the
OCR output. Set `OCR_TARGETED_RETRY=false` to disable retries. Both recognition
models are cached after first use. Retry failures preserve the base OCR and flag
review, rather than dropping the invoice.

Items additionally expose `vat_amount`, `discount`, `gross_amount`,
`amount_source` and `field_evidence`. A missing pre-tax line amount is derived
only when quantity/price and the printed VAT/gross reconcile, and is marked
`derived_quantity_price`. Printed values are retained. A line/document VAT
rounding discrepancy triggers review. Field confidence uses selected evidence
instead of unrelated footer noise. A receipt can cover source fields, so such
invoices stay in review even when visible financial columns reconcile.

Tests and QA do not establish 100% accuracy. Names/descriptions recovered by
targeted OCR still require spelling review. The three real sample PDFs and their
raw QA outputs stay local under ignored `qa_samples/`; synthetic regressions are
committed. Colab cell 1 downloads this repository's `main`, so rerun the notebook
from cell 1 after an update; an existing running server retains its old imports.

1. `ocr_web.py` receives the PDF upload.
2. `native_pdf.py` checks each page for usable positioned text. Unrotated,
   uncropped pages without images and with sufficient readable text can bypass OCR.
   Image-bearing, rotated, sparse or invalid-text pages use PaddleOCR at 200 DPI.
3. Paddle models load only when a page needs OCR, and remain cached across uploads.
4. The spatial parser builds a fallback from positioned OCR boxes. Accuracy mode
   separately renders **each original page** into memory and sends it to
   Ollama's vision model in bounded header and item-table passes; pages are
   merged without dropping later-page items.
5. Vision output is checked against line arithmetic, totals, OCR identifiers and
   item-code order. OCR misses do not silently erase an image reading: they flag
   review. A swapped/shorter table, or a result weaker than a validated spatial
   result, retains the spatial fallback. Detailed JSON includes rejected vision
   output for inspection. Passing checks is not proof of source correctness.
6. JSON includes `schema_version`, `status`, and per-page `extraction_methods`.
   Existing `data`, `quality`, timings and review OCR remain available. Upload and
   processing failures return JSON with an error code and message.
7. Temporary files are removed after each request.

Set `OCR_FORCE_RASTER=true` to bypass native extraction for comparison or PDFs
with suspect text layers. Native boxes use the same 200 DPI coordinate system,
with `source=native_text` and `confidence=null` (no OCR confidence is invented).
The native-text heuristic cannot establish semantic correctness. Pages containing
logos also conservatively use OCR. No source-PDF accuracy or Colab latency claim
has been measured for this change. Continuation pages without table headers still
need AI/manual review; automatic header propagation and AI chunking are pending.

Raster pages are classified for residual right-angle orientation after PDFium has
already consumed the PDF page's intrinsic `/Rotate`. The conservative default
`OCR_ORIENTATION_MIN_CONFIDENCE=0.90` leaves a lower-confidence page unrotated and
marks its sourced fields for review; set the variable to a value from `0` to `1`
only after measuring the relevant invoice set. Per-page angle, confidence, status,
and `/Rotate` diagnostics are returned in `page_orientations`. Classifier latency is
reported separately as `timings_seconds.orientation_detection` in both Fast and
Accuracy mode responses.

Run static/mocked regressions with `python -m unittest discover -p 'test_*.py' -q`.
These do not run the Colab OCR or vision model and do not establish invoice accuracy.

## Processing speed

Colab installs OCR into `/content/ocr-runtime`, isolated from the notebook's
preinstalled packages. Both runtime verification and the web server use that
environment. ModelScope uses CPU PyTorch there, while Paddle still uses the GPU;
this avoids the observed `libtorch_cuda.so: undefined symbol: ncclCommShrink`
failure from mixing PyTorch and Paddle CUDA dependencies. CPU PyTorch installation
follows https://pytorch.org/get-started/previous-versions/ .

The server automatically selects CUDA when available. Set `OCR_DEVICE=cpu` or
`OCR_DEVICE=gpu:0` to override; requesting an unavailable GPU raises an error.
Output includes `ocr_device` so you can confirm GPU use. CPU inference uses at
most four threads, limited by the available CPU count. Model choice and 200 DPI
resolution are unchanged. Fast/spatial parsing can require manual field review.

After updating these files on GitHub, restart the Colab runtime and run the
updated notebook. Cell 2 should print `OCR device: gpu:0` on a T4 runtime.
Compare `timings_seconds` on the first and second upload of the same PDF;
GPU acceleration primarily targets OCR time, while disabling AI removes the
Ollama parsing wait. GPU performance must be measured in the actual Colab runtime.

The web server keeps Paddle models in memory between uploads (one model per
recognition language). The first upload still loads models. OCR jobs are
serialized because the native predictor and PDFium resources are shared.
Run the server with the Python environment containing PaddleOCR. If you set
`OCR_PYTHON_EXE`, the legacy subprocess path is used and models reload per upload.

Accuracy mode always sends original page images to Ollama, including when spatial
parsing appears complete, because layout checks alone cannot verify full names or
addresses. Fast mode skips AI entirely; unfamiliar layouts may need more manual
review. Both modes keep the 200 DPI base OCR resolution.
`USE_LOCAL_AI=false` disables AI globally. `OLLAMA_TIMEOUT_SECONDS` defaults to
180 seconds for image-first requests (HTTP socket timeout per request, not an overall job deadline). Ollama is asked
to keep its model loaded for 30 minutes.

JSON output includes `timings_seconds` for base render/OCR, targeted retries,
invoice parsing, and total processing; the persistent path also reports queue and
model loading.
Compare the first and second uploads of the same PDF in Colab to measure the
warm-model improvement. No fixed latency is guaranteed; page count, layout,
hardware, and runtime load matter. Update the repository copy before rerunning
the Colab notebook: cell 1 downloads the GitHub main branch.

When formatted output needs review, `raw_ocr.pages` preserves the positioned OCR
text for comparison without reprocessing the PDF. Missing items and invoice dates
are listed explicitly. Supplier names are restricted to nearby header text;
footer signature lines and plural words in return policies are excluded from
name/label matching. These guards do not establish accuracy on an unseen PDF.

## Different invoice layouts

`layout_invoice.py` detects description, quantity, unit-price and pre-tax amount
columns from English/Arabic header aliases and their positions on each page.
Columns may be reordered or scaled; item codes are optional and may contain
letters. Quantities may be fractional. Full alphanumeric invoice IDs and slash-
or hyphen-separated dates are retained. Header aliases describe field meanings;
the output JSON keys remain consistent across suppliers.

The hybrid spatial parser retains this result as a fallback. Accuracy mode reads
original-page images and uses OCR boxes as independent evidence, rather than
using boxes as its only AI input. Zero tax and explicitly supplied non-15%
rates are supported; an absent rate is not assumed to be 15%.

Limitations: unusual or merged table headers, wrapped item rows, mixed tax rates,
and OCR spelling errors can still require review. Passing arithmetic checks is
not proof that every field matches the source PDF. The supplied 9612 OCR payload
was replayed locally; the original PDF and live Colab inference were not tested.

Run parser regression checks with `python -m unittest test_invoice_parsing test_layout_invoice test_invoice_evidence`.

The parser retains rows with a missing price or amount and attaches nearby
wrapped description lines, so incomplete rows remain visible for review.
Missing supplier/customer names and item descriptions also trigger review.
Image-generated identifiers, dates and numeric values are compared with OCR.
An OCR miss creates a review issue but does not erase a value read from the
original image. Item-code row-order reversals are rejected. Presence in OCR
alone does not prove the correct role or column. Names and descriptions are
not verified by arithmetic, so they still need human review.

Colab now preloads Ollama during cell 3 and displays `ollama ps` to show GPU/CPU
placement. Image chat requests use an explicit 16384-token context, configurable with
`OLLAMA_NUM_CTX`. Preloading follows the [Ollama API guidance](https://docs.ollama.com/faq#how-can-i-preload-a-model-into-ollama-to-get-faster-response-times).
Truncated AI responses are rejected. Setup loading has a 180-second socket
timeout; invoice image requests use a 180-second socket timeout per request. These changes
have local regression coverage, not a measured accuracy percentage across a
production invoice dataset or a live Colab/Ollama benchmark.

Ollama preloading is optional: a failure prints the server response and allows
OCR startup to continue with AI still enabled. Cell 3 reuses a responding Ollama
service rather than starting a duplicate and overwriting its log. HTTP failures
during invoice parsing include Ollama's response in `quality.local_ai_error`.
This exposes model/driver/memory failures; it does not itself repair them.
Run HTTP error regressions with `python -m unittest test_ollama_http`.
