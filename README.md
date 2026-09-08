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

[Open the setup notebook in Colab](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb), then select **Runtime > Run all**. For a private repository, add a Colab secret named `GITHUB_TOKEN` with read access and enable notebook access; public repositories need no token. The pinned Paddle package runs OCR on CPU; a T4 GPU can accelerate Ollama. The notebook installs dependencies, optionally starts Ollama, launches the OCR server, and embeds the application in Colab.

## Flow

1. `ocr_web.py` receives and validates the PDF upload.
2. `coordinate_ocr.py` renders pages and extracts text with bounding boxes.
3. `local_ai_parser.py` asks local Ollama for schema-valid invoice JSON.
4. `invoice_formatter.py` provides deterministic spatial parsing and validation.
5. Temporary files are removed after each request.

## Processing speed

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
