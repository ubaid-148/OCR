from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from time import perf_counter
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from visual_invoice import parse_invoice_visual
from pdf_errors import InvalidPDFError
from invoice_response import (
    SCHEMA_VERSION, clean_invoice_response, error_invoice_response, response_json_bytes,
)
from page_rotation import page_orientation


ROOT = Path(__file__).resolve().parent
OCR_EXE = os.environ.get("OCRMYPDF_EXE") or shutil.which("ocrmypdf") or str(
    ROOT / "OCRmyPDF" / ".venv" / "Scripts" / "ocrmypdf.exe"
)
PYTHON_EXE = os.environ.get("OCR_PYTHON_EXE") or sys.executable
COORDINATE_SCRIPT = ROOT / "coordinate_ocr.py"
UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
PROGRESS = {}
UPLOAD_SLOT = threading.BoundedSemaphore(1)


def accuracy_gpu_configured(mode: str) -> bool:
    if mode != "auto" or os.environ.get("VISION_REQUIRE_GPU", "false").lower() not in {"true", "1", "yes"}:
        return True
    return os.environ.get("OCR_DEVICE", "").lower().startswith("gpu")


def multipart_form_fields(message) -> dict[str, object]:
    """Read every multipart form field, including the first text field.

    ``iter_attachments`` skips a candidate body part, which is inappropriate
    for form-data and can silently drop the language selector.
    """
    return {
        name: part
        for part in message.iter_parts()
        if (name := part.get_param("name", header="content-disposition"))
    }


def page(message: str = "") -> bytes:
    safe_message = f'<p class="message">{html.escape(message)}</p>' if message else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OCR PDF Lab</title>
<style>
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#eef4f1; color:#17332d; font:16px Georgia,serif; }}
main {{ width:min(680px,calc(100% - 32px)); background:#fffdf8; padding:34px; border:1px solid #c8d8d0; border-radius:18px; box-shadow:0 18px 60px #17332d18; }}
h1 {{ margin:0 0 8px; font-size:38px; letter-spacing:-1px; }}
p {{ line-height:1.5; }}
label {{ display:block; margin-top:20px; font-weight:bold; }}
input,select,button {{ box-sizing:border-box; width:100%; margin-top:8px; padding:13px; border:1px solid #9dbbb0; border-radius:9px; font:inherit; }}
button {{ margin-top:24px; background:#17624f; color:white; border:0; cursor:pointer; font-weight:bold; }}
button:hover {{ background:#0f4b3d; }}
.advanced {{ margin-top:18px; padding:12px; border:1px solid #d5e1dc; border-radius:9px; }}
.advanced summary {{ cursor:pointer; font-weight:bold; }}
.message {{ background:#e4f2ec; padding:12px; border-radius:8px; white-space:pre-wrap; }}
.hint {{ color:#55736b; font-size:14px; }}
</style></head><body><main>
<h1>OCR PDF Lab</h1>
<p>Upload an invoice PDF. Accuracy mode reads the original page images with a vision model and checks the result against OCR and invoice arithmetic.</p>
<p class="hint"><strong>Parser:</strong> Image-first local vision AI with spatial OCR fallback.</p>
{safe_message}
<form method="post" enctype="multipart/form-data">
<label>PDF files<input type="file" name="pdf" accept="application/pdf,.pdf" multiple required></label>
<details class="advanced"><summary>Advanced options</summary>
<label>Languages<select name="languages"><option value="eng+ara">English + Arabic</option><option value="eng">English only</option><option value="ara">Arabic only</option><option value="eng+urd">English + Urdu</option></select></label>
<label>Processing<select name="mode"><option value="auto">Accuracy (original PDF image + OCR cross-check)</option><option value="fast">Fast (spatial OCR + validation only)</option></select></label>
<p class="hint">PaddleOCR uses Arabic recognition for Arabic/Urdu selections; it also handles Latin text and numbers.</p>
</details>
<button type="submit">Extract Invoice</button>
</form><p id="progress" role="status" aria-live="polite"></p><pre id="result" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
<script src="/app.js"></script></main></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload, status=200):
        data = response_json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_failure(self, message, status, partial_payload=None):
        self.send_json(error_invoice_response(status, message, partial_payload), status)

    def send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == '/app.js':
            data = (ROOT / 'app.js').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith('/progress?'):
            token = parse_qs(self.path.partition('?')[2]).get('id', [''])[0]
            self.send_json({'stage': PROGRESS.get(token, 'Uploading / waiting for server')})
            return
        if self.path == "/":
            self.send_html(page())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if not UPLOAD_SLOT.acquire(blocking=False):
            self.send_failure('Another PDF is processing. Wait for it to finish before uploading again.', 429)
            return
        try:
            try:
                self.process_upload()
            except Exception as error:
                self.log_message("Unhandled upload failure: %s", error)
                self.send_failure("The PDF could not be processed safely.", 500)
        finally:
            UPLOAD_SLOT.release()

    def process_upload(self) -> None:
        if self.path != '/':
            self.send_failure('Unknown endpoint.', 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_failure("Invalid Content-Length", 400)
            return
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_failure("Please upload a PDF smaller than 100 MB.", 413)
            return

        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
        )
        pdf_parts = [part for part in message.iter_parts()
                     if part.get_param('name', header='content-disposition') == 'pdf']
        if len(pdf_parts) > 1:
            self.send_failure('Send one PDF per request; the web form queues multiple PDFs automatically.', 400)
            return
        fields = multipart_form_fields(message)
        pdf_part = fields.get("pdf")
        language_part = fields.get("languages")
        mode_part = fields.get("mode")
        mode = "auto" if mode_part and mode_part.get_content().strip() == "auto" else "fast"
        if not accuracy_gpu_configured(mode):
            self.send_failure("Accuracy mode needs a GPU. In Colab select Runtime > Change runtime type > T4 GPU, reconnect, and rerun all cells; Fast mode remains available on CPU.", 400)
            return
        if pdf_part is None:
            self.send_failure("No PDF was received.", 400)
            return

        languages = (language_part.get_content() if language_part else "eng+ara").strip()
        # The browser/API has one public result contract. Debug/raw formats stay
        # internal so a missing form option can never change the response shape.
        output_format = "invoice"
        if languages not in {"eng", "ara", "eng+ara", "urd", "eng+urd"}:
            languages = "eng+ara"
        filename = Path(pdf_part.get_filename() or "input.pdf").name
        if Path(filename).suffix.lower() != ".pdf":
            self.send_failure("Only PDF files are supported.", 400)
            return

        job_id = uuid.uuid4().hex
        progress_id = self.headers.get('X-Progress-ID', '')[:64]
        def progress(message):
            self.log_message('%s: %s', job_id, message)
            if progress_id:
                PROGRESS[progress_id] = message
        input_path = UPLOAD_DIR / f"{job_id}-input.pdf"
        output_path = UPLOAD_DIR / f"{job_id}-searchable.pdf"
        coordinate_path = UPLOAD_DIR / f"{job_id}-coordinates.json"
        pdf_bytes=pdf_part.get_payload(decode=True) or b''
        if not pdf_bytes.startswith(b'%PDF-'):
            self.send_failure('The uploaded file is not a PDF.',400)
            return
        input_path.write_bytes(pdf_bytes)
        env = os.environ.copy()
        env["TESSDATA_PREFIX"] = str(ROOT / "tessdata")
        env["Path"] = env.get("Path", "") + os.pathsep + r"C:\Program Files\Tesseract-OCR"
        command = [
            str(OCR_EXE), "-l", languages, "--rasterizer", "pypdfium",
            "--pdf-renderer", "fpdf2", "--rotate-pages", "--deskew",
            "--oversample", "300", "--output-type", "pdf", "--force-ocr",
            str(input_path), str(output_path),
        ]
        partial_payload = None
        try:
            if output_format in {"invoice", "invoice_debug", "json"}:
                started = perf_counter()
                if os.environ.get("OCR_PYTHON_EXE"):
                    coordinate_result = subprocess.run(
                        [str(PYTHON_EXE), str(COORDINATE_SCRIPT), str(input_path), str(coordinate_path), languages],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=1800,
                    )
                    if coordinate_result.returncode != 0 or not coordinate_path.exists():
                        detail = (coordinate_result.stderr or coordinate_result.stdout or "Coordinate OCR failed").strip()
                        self.log_message("%s: coordinate OCR failed: %s", job_id, detail[:2000])
                        self.send_failure("OCR could not process the uploaded PDF.", 500, partial_payload)
                        return
                    coordinate_payload = json.loads(coordinate_path.read_text(encoding="utf-8"))
                else:
                    from coordinate_ocr import extract_pdf
                    coordinate_payload = extract_pdf(input_path, languages, progress=progress)
                ocr_finished = perf_counter()
                payload: dict[str, object] = {
                    "source_filename": filename,
                    "language": languages,
                    "engine": coordinate_payload["engine"],
                    "pages": coordinate_payload["pages"],
                }
                partial_payload = payload
                if output_format in {"invoice", "invoice_debug"}:
                    progress('Validating invoice' if mode == 'fast' else 'Reading original PDF page images and validating invoice')
                    payload = parse_invoice_visual(
                        input_path, coordinate_payload["pages"], filename, languages,
                        mode=mode, progress=progress
                    )
                    partial_payload = payload
                    # Preserve the evidence when parsing fails, without another OCR run.
                    if payload.get("quality", {}).get("needs_review"):
                        payload["raw_ocr"] = {"pages": coordinate_payload["pages"]}
                payload["timings_seconds"] = {
                    **coordinate_payload.get("timings_seconds", {}),
                    "ocr_total": round(ocr_finished - started, 3),
                    **payload.get("stage_timings", {}),
                    "invoice_parser": round(perf_counter() - ocr_finished, 3),
                    "total": round(perf_counter() - started, 3),
                }
                payload["ocr_device"] = coordinate_payload.get("device", "unknown")
                payload["page_orientations"] = [page_orientation(page)
                                                for page in coordinate_payload["pages"]]
                for orientation in payload["page_orientations"]:
                    self.log_message("%s: page %s orientation residual=%s candidate=%s confidence=%s status=%s",
                                     job_id, orientation["page"],
                                     orientation["rotation_degrees"],
                                     orientation.get("rotation_candidate_degrees"),
                                     orientation["rotation_confidence"],
                                     orientation["rotation_status"])
                payload["pipeline_version"] = "2026-09-evidence-gated-v12"
                payload["schema_version"] = SCHEMA_VERSION
                payload["status"] = payload.get("quality", {}).get("overall_status", "extracted")
                payload["extraction_methods"] = [p.get("extraction_method", "ocr") for p in coordinate_payload["pages"]]
                self.send_json(clean_invoice_response(payload))
                return
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0 or not output_path.exists():
                detail = (result.stderr or result.stdout or "OCR failed").strip()
                self.log_message("%s: searchable PDF conversion failed: %s", job_id, detail[:2000])
                self.send_failure("OCR could not process the uploaded PDF.", 500, partial_payload)
                return
            data = output_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{Path(filename).stem}-searchable.pdf"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except InvalidPDFError as error:
            self.send_failure(str(error), 400, partial_payload)
        except subprocess.TimeoutExpired:
            self.send_failure("OCR timed out after 30 minutes.", 504, partial_payload)
        except Exception as error:
            self.log_message("OCR failed: %s", error)
            self.send_failure("OCR or invoice extraction failed.", 500, partial_payload)
        finally:
            PROGRESS.pop(progress_id, None)
            for path in (input_path, output_path, coordinate_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    if os.environ.get('OCR_PRELOAD', 'false').lower() == 'true':
        from coordinate_ocr import _get_model, get_ocr_device
        print('Preparing OCR on', get_ocr_device(), flush=True)
        for language in ('ar', 'en'):
            _get_model(language)
        print('OCR models ready', flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("OCR PDF Lab running at http://127.0.0.1:8765")
    server.serve_forever()
