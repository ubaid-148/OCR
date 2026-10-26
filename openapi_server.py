"""OpenAPI/Swagger interface for the GitHub invoice OCR pipeline.

Start it with:
    python -m uvicorn openapi_server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
ALLOWED_LANGUAGES = {"eng", "ara", "eng+ara", "urd", "eng+urd"}
SCHEMA_VERSION = "1.2"

app = FastAPI(
    title="Invoice OCR API",
    version="1.2",
    description=(
        "GitHub OCR pipeline for Arabic and English invoice extraction. "
        "Returned values include evidence-based review flags when verification is required."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health", tags=["service"])
def health() -> dict[str, str]:
    return {"status": "ok", "pipeline": "github-invoice-ocr", "schema_version": SCHEMA_VERSION}


@app.post("/api/v1/extract", tags=["OCR"])
async def extract_invoice(
    document: UploadFile = File(..., description="One PDF invoice (maximum 100 MB)"),
    languages: str = Query("eng+ara", description="eng, ara, eng+ara, urd, or eng+urd"),
) -> dict:
    """Extract a validated canonical invoice JSON document from a PDF."""
    if languages not in ALLOWED_LANGUAGES:
        raise HTTPException(status_code=422, detail="Unsupported languages value.")

    filename = Path(document.filename or "input.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="Only PDF files are supported.")

    temporary_path: Path | None = None
    try:
        total_bytes = 0
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as temporary_file:
            temporary_path = Path(temporary_file.name)
            while chunk := await document.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="PDF must be 100 MB or smaller.")
                temporary_file.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="The uploaded PDF is empty.")
        if temporary_path.read_bytes()[:5] != b"%PDF-":
            raise HTTPException(status_code=400, detail="The uploaded file is not a valid PDF.")

        started = perf_counter()
        # Import the OCR runtime only when a document is submitted. This keeps
        # Swagger usable on Windows even when Paddle's native DLL cannot load;
        # use Colab/Linux for actual PaddleOCR processing.
        from coordinate_ocr import extract_pdf
        from invoice_response import clean_invoice_response
        from local_ai_parser import parse_invoice_hybrid
        from page_rotation import page_orientation

        coordinates = extract_pdf(temporary_path, languages)
        ocr_finished = perf_counter()
        payload = parse_invoice_hybrid(coordinates["pages"], filename, languages, mode="fast")
        payload["timings_seconds"] = {
            **coordinates.get("timings_seconds", {}),
            "ocr_total": round(ocr_finished - started, 3),
            **payload.get("stage_timings", {}),
            "invoice_parser": round(perf_counter() - ocr_finished, 3),
            "total": round(perf_counter() - started, 3),
        }
        payload["ocr_device"] = coordinates.get("device", "unknown")
        payload["page_orientations"] = [page_orientation(page) for page in coordinates["pages"]]
        payload["pipeline_version"] = "github-main-openapi"
        payload["schema_version"] = SCHEMA_VERSION
        payload["status"] = payload.get("quality", {}).get("overall_status", "extracted")
        return clean_invoice_response(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"OCR could not process this PDF: {exc}") from exc
    finally:
        await document.close()
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
