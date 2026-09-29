# Local PaddleOCR Invoice Extraction

This project extracts text and structured invoice fields from PDF files using
local PaddleOCR. In Accuracy mode, an on-device Ollama/Qwen vision model checks
the original invoice image; no PDFs are sent to Gemini, Azure, or another cloud
OCR provider.

## Requirements

- Python 3.11+
- PaddleOCR and PaddlePaddle (installed from `requirements.txt`)
- Tesseract OCR available on `PATH` for the OCRmyPDF utility path
- Google Colab Accuracy mode also installs Ollama and `qwen3-vl:4b` locally

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run the local web app

```powershell
python ocr_web.py
```

Open http://127.0.0.1:8765, choose PDF invoices, and select the recognition
language. The web app uses PaddleOCR locally and returns structured JSON with
OCR evidence, validation results, and review flags for uncertain fields.

## Command line

`coordinate_ocr.py` produces page text and coordinates from a PDF. `main.py`
converts saved PaddleOCR output into the canonical invoice JSON format.

```powershell
python coordinate_ocr.py invoice.pdf output.json eng+ara
python main.py output.json --output invoice.json --pdf invoice.pdf
```

For scanned PDFs, the first run downloads and loads PaddleOCR models. CPU works;
set `OCR_DEVICE=gpu` only when a compatible Paddle GPU installation is available.
Review output marked `needs_review` against the source invoice.
