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

[Open the setup notebook in Colab](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb). Because this repository is private, first add a Colab secret named `GITHUB_TOKEN` with read access to the repository and enable notebook access. Then select **Runtime > Run all**. A T4 GPU runtime is recommended. The notebook installs dependencies, optionally starts Ollama, launches the OCR server, and embeds the application in Colab.

## Flow

1. `ocr_web.py` receives and validates the PDF upload.
2. `coordinate_ocr.py` renders pages and extracts text with bounding boxes.
3. `local_ai_parser.py` asks local Ollama for schema-valid invoice JSON.
4. `invoice_formatter.py` provides deterministic spatial parsing and validation.
5. Temporary files are removed after each request.
