> **Upload button stuck or duplicate old form?** Open the [Native Upload v2 notebook](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_native_upload.ipynb) and run cells 1–3. Step 3 must display `INVOICE OCR — native-upload-v2`. This uses Colab’s built-in uploader and starts processing automatically after upload.

> **Colab testing:** open the [GitHub notebook in Colab](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb)
> in a fresh runtime and run from cell 1. See [COLAB_TESTING.md](COLAB_TESTING.md) for testing instructions.

# Local Invoice OCR

Colab step 3 uses the built-in file uploader. Choose PDFs and wait for upload to
finish; extraction starts automatically, without a separate Process button.
Set the mode in step 2 and language/diagnostics in step 3 before running it.
Results display inline and JSON downloads start automatically. Rerunning step 3
reloads the upload UI module and clears its previous output.

The Colab upload flow now uses one isolated worker per batch, keeping OCR models
loaded between PDFs. Eligible upright, image-free PDFs use the existing native
text checks instead of forced raster OCR; scans still use PaddleOCR. Explicit
`OCR_FORCE_RASTER=true` remains available. The first scanned PDF still pays model
startup cost, and Accuracy mode still runs the original-page vision checks.
Localized item descriptions no longer trigger an extra table reread simply
because the generic description key is absent. Invoice-number candidates cannot
silently replace an existing conflicting reading; disagreements require review,
and low-confidence candidates do not fill missing numbers.
These changes have local regression coverage; fresh GPU timing and extraction
accuracy must still be checked on the affected source PDFs.

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

Open <http://127.0.0.1:8765> and select one or more PDF invoices. The browser processes them sequentially and shows a separate result for each file.

## Google Colab

[Open the invoice notebook](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb), choose **Runtime → Change runtime type → T4 GPU**, then **Runtime → Run all**.
Upload one or more PDFs when prompted. Its final invoice JSON is displayed and downloaded automatically.

For the free OCR flow, use `colab_setup.ipynb`, not the separate training notebook.
The notebook has three steps: load project, prepare OCR, and upload/get results. Multiple uploads are processed separately; a failed PDF does not discard successful results.
Step 1 prints the Git commit being tested. Step 2 checks the Paddle device and
runs a committed invoice sample through the parser before enabling upload.
Step 2 reads dependencies from `/content/OCR/requirements.txt`, so it works
even when Colab's current directory changes after cloning.
If Colab reconnects and clears notebook variables, step 3 reuses the verified
OCR environment. If setup never completed, step 3 prepares and verifies it
before opening the PDF upload prompt.
The sample check does not run live OCR; the first PDF upload does. After upload,
the notebook prints the OCR device, pipeline version, and stage timings above the
downloaded invoice JSON. Check that the device says `gpu:0` when testing on T4.
The Colab JSON preserves invoice time/reference, supported party/address fields,
item units/tax rates/discounts, VAT summary, notes, extra labelled fields and bank
details when extracted. It also includes `date_of_supply`, `payment_method`,
`customer.customer_code`, `customer.address`, and both parties' `commercial_registration`.
Absent values remain null; vision's `cr_number` is exposed as `commercial_registration`.

`Vision diagnostics` lists each page's header and item request separately, including
wall time, server total time, model load, prompt evaluation, generation, and token counts.
These records are also saved in `extraction_details.json` and the raw summary, including
when vision is rejected or fails. Durations are seconds, converted from
[Ollama's nanosecond metrics](https://github.com/ollama/ollama/blob/main/docs/api.md).
Prompt evaluation is not an isolated measurement of image encoding; Ollama does not
provide that separate metric, so `image_processing_seconds` stays null. Missing server
metrics also stay null (for example, on timeout). Server phases are parts of request
wall time, and should not be added to it. `visual_render` measures local PDF rendering.
These diagnostics identify where time goes; they do not themselves speed up inference.

If a GPU is unavailable, the notebook now falls back to CPU instead of stopping;
the OCR result flow remains the same but takes longer.
Focused retries now reread faint customer names and item descriptions from both
enhanced and original crops. A numeric item row without a readable description
triggers a table reread; a failed crop is reported while other retries continue.
Default `EXTRACTION_MODE="auto"` runs PaddleOCR plus original-page vision reading
for unfamiliar invoice layouts. Setup prepares the local vision model automatically.
Choose `fast` explicitly to use only OCR and spatial rules without vision. The existing spatial parser
provides independent evidence and a fallback. Image readings are checked against
OCR, row order/count and arithmetic; disagreement stays flagged for review.
No per-supplier template selection is required. This does not guarantee correct
reading of every layout, scan or long table.

When `auto` is selected, Step 2 prepares Ollama and `qwen3-vl:4b` in Colab. Initial setup downloads the
model; subsequent uploads reuse it. Installation follows the
[official Linux instructions](https://docs.ollama.com/linux). GPU is recommended;
vision inference adds time beyond the OCR timings. `fast` explicitly selects the
previous OCR-only path. Setup failures stop with an error; inference failures
retain the spatial result with a review note. Output prints extraction mode,
actual parser, AI status and separate extraction timings.

The compact JSON keeps the same invoice/customer/item/totals schema. Raw OCR and
the full extraction decision are saved in `*-diagnostics.json`; enable
`DOWNLOAD_DIAGNOSTICS` to download it. Unreadable quantities remain null and are
never inferred by dividing totals.
Missing or uncertain fields remain flagged for review. There are no comparison,
benchmark, raw-evidence display, or optional model setup cells in this flow.
Colab now uses the same checked image-reading flow as the web application.

Cell 1 uses `PROJECT_REF=main`. Reopen this updated notebook;
if your existing runtime has a clone of another branch, use a fresh runtime.

## Training workflow

Use the separate [T4 training notebook](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_train.ipynb)
and [training guide](training/README.md) to prepare page images, review model drafts,
train a Qwen3-VL-4B LoRA adapter, and compare held-out predictions. The 111 sample
PDFs contain 115 pages; they are source documents, not verified labels. Training
requires checked JSON and a CUDA runtime. No adapter has been trained or measured
as part of the local workflow setup. Draft OCR/vision outputs are never automatically
accepted as training truth. Production extraction still uses arithmetic,
positioned evidence and `needs_review`.

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

## General invoice layouts and batch processing

Colab and `python -m tools.batch_local_ocr` default to `auto`: the original page
images are read by the local vision model, with spatial OCR as independent
evidence and fallback. This does not require a supplier template or known PDF
filename. The web UI already defaults to Accuracy. It handles invoice PDFs;
arbitrary non-invoice documents do not have the same extraction schema.

For the batch CLI, prepare Ollama and a vision model first (Colab setup does this
automatically), then run:

```sh
python -m tools.batch_local_ocr --pdf-dir public_invoice_pdfs --output benchmark_outputs/live-local --mode auto
```

Each `.details.json` records the parser, vision failures/rejections and review
reasons. Mode, language and vision configuration participate in cache invalidation,
so switching from fast to auto reruns extraction. `--mode fast` remains available
for OCR-only runs. Vision adds model download/setup and per-page inference time;
these defaults have integration-test coverage, not a measured live accuracy rate.

## Mapping completion update (24 September 2026)

The public web contract is now **schema 1.2**. It adds `unmapped_text` (page/text
entries) and preserves bank details, supplier address/business type/commercial
registration and amount-in-words fields that the compact result already supported.
Clients enforcing schema 1.1 must update. The compact Colab result also contains
`unmapped_text`. These entries include labels, logos and other unused OCR text as
well as potentially missed values; they are not verified invoice fields. Full
`mapping_coverage` diagnostics associate selected source boxes with field paths.

Wrapped item units and percentages, explicit taxable totals and other charges
are retained. Partial tables can retain rows when a price/amount header is absent;
unreadable amounts remain null. Merged grid cells cannot move text into a nearby
column, and footer totals are excluded from partial item tables. Validation covers
printed line tax rates, mixed rates and non-zero other charges.

A failed vision page now keeps its OCR fallback while later pages continue.
Transient vision failures are retried when a batch is restarted; successful cached
results remain reusable. Unknown inline `label: value` text is retained in
`other_fields` without inventing its canonical meaning.

Verify cached mappings (does not run recognition):

```sh
python -m tools.verify_cached_extraction ../pairing-baseline/raw --output benchmark_outputs/completion --source-checks benchmark_outputs/verification/source-checks.json
```

Restart the web server or rerun the updated Colab notebook after updating code.
See `COMPLETION_REPORT.md` for checked results and remaining runtime limitations.

Automatic image recovery now rereads unresolved page fields/rows once using enlarged
original-page regions. Truncated vision requests receive one larger token-budget
attempt (up to 8192, within the configured context); repeated truncation remains a
review failure. Source conflicts and ambiguous row joins are preserved for review.
These paths have mocked regression coverage; fresh GPU accuracy is not measured.
