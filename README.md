# Local Invoice OCR

Local web application that extracts positioned text from invoice PDFs with
PaddleOCR, converts it to structured invoice JSON with Ollama, and validates
invoice arithmetic. If Ollama is unavailable or its output fails validation,
the application uses its deterministic spatial parser.

## Requirements

- Python 3.11+
- Tesseract OCR installed and available on `PATH`
- Ollama running locally (optional, used for AI parsing)
- The Ollama model configured by `OLLAMA_MODEL` (default: `qwen2.5:3b`)

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
- `OLLAMA_MODEL` (default: `qwen2.5:3b`)
- `OCRMYPDF_EXE` (explicit OCRmyPDF executable path)
- `OCR_PYTHON_EXE` (Python executable used for coordinate OCR)

## Run

```powershell
python ocr_web.py
```

Open <http://127.0.0.1:8765> and upload a PDF invoice.

## Google Colab

[Open the setup notebook in Colab](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb), select **Runtime > Change runtime type > T4 GPU**, then **Runtime > Run all**. For a private repository, add a Colab secret named `GITHUB_TOKEN` with read access and enable notebook access; public repositories need no token. The notebook installs GPU Paddle on GPU runtimes and CPU Paddle otherwise, and verifies the installation in a fresh process. A GPU installation failure stops setup instead of silently running OCR on CPU. Local AI is enabled by default for unfamiliar layouts; Balanced mode skips it when spatial parsing passes validation. Fast mode explicitly skips AI and can miss fields on unfamiliar layouts.

## Flow

1. `ocr_web.py` receives and validates the PDF upload.
2. `coordinate_ocr.py` renders pages and extracts text with bounding boxes.
3. `local_ai_parser.py` asks local Ollama for schema-valid invoice JSON.
4. `invoice_formatter.py` provides deterministic spatial parsing and validation.
5. Temporary files are removed after each request.

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

Balanced mode skips Ollama when spatial parsing passes its checks and includes
an invoice date. Fast mode skips AI entirely; unfamiliar layouts may need more
manual review. Neither mode lowers the 200 DPI rendering resolution.
`USE_LOCAL_AI=false` disables AI globally. `OLLAMA_TIMEOUT_SECONDS` defaults to
60 seconds (HTTP socket timeout, not an overall job deadline). Ollama is asked
to keep its model loaded for 30 minutes.

JSON output includes `timings_seconds` for OCR, invoice parsing, and total
processing; the persistent path also reports queue, model load, and render/OCR.
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

The hybrid parser uses this result when it detects rows without dropping rows
found by the legacy parser, then checks required fields and arithmetic. Unknown
layouts and incomplete results still go to Ollama in Balanced mode. A compact
`[x,y,width,height,text]` representation retains every OCR box for AI parsing.
This reduces prompt size, but does not guarantee that Ollama finishes within
its configured timeout. Zero tax and explicitly supplied non-15% rates are
supported by hybrid validation; an absent rate is not assumed to be 15%.

Limitations: unusual or merged table headers, wrapped item rows, mixed tax rates,
and OCR spelling errors can still require review. Passing arithmetic checks is
not proof that every field matches the source PDF. The supplied 9612 OCR payload
was replayed locally; the original PDF and live Colab inference were not tested.

Run parser regression checks with `python -m unittest test_invoice_parsing test_layout_invoice test_invoice_evidence`.

The parser retains rows with a missing price or amount and attaches nearby
wrapped description lines, so incomplete rows remain visible for review.
Missing supplier/customer names and item descriptions also trigger review.
AI-generated identifiers, dates and numeric values are checked against OCR;
unsupported values become null and receive an evidence issue. Supported AI
fields include their OCR page, text and confidence. Presence in OCR alone does
not prove the correct role or column was chosen. Names and descriptions are not
covered by this occurrence check. AI results with fewer rows or a worse
validation/completeness score do not replace the spatial result.

Colab now preloads Ollama during cell 3 and displays `ollama ps` to show GPU/CPU
placement. Chat requests use an explicit 8192-token context, configurable with
`OLLAMA_NUM_CTX`. Preloading follows the [Ollama API guidance](https://docs.ollama.com/faq#how-can-i-preload-a-model-into-ollama-to-get-faster-response-times).
Truncated AI responses are rejected. Setup loading has a 180-second socket
timeout; invoice requests retain the 60-second socket timeout. These changes
have local regression coverage, not a measured accuracy percentage across a
production invoice dataset or a live Colab/Ollama benchmark.
