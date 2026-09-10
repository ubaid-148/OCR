from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from time import perf_counter
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from local_ai_parser import parse_invoice_hybrid
from pdf_errors import InvalidPDFError
from invoice_response import clean_invoice_response


ROOT = Path(__file__).resolve().parent
OCR_EXE = os.environ.get("OCRMYPDF_EXE") or shutil.which("ocrmypdf") or str(
    ROOT / "OCRmyPDF" / ".venv" / "Scripts" / "ocrmypdf.exe"
)
PYTHON_EXE = os.environ.get("OCR_PYTHON_EXE") or sys.executable
COORDINATE_SCRIPT = ROOT / "coordinate_ocr.py"
UPLOAD_DIR = ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


def normalize_digits(value: str) -> str:
    table = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    return value.translate(table).replace(",", ".")


def invoice_candidates(text: str) -> dict[str, object]:
    normalized = normalize_digits(text)
    vat_numbers = list(dict.fromkeys(__import__("re").findall(r"(?<!\d)\d{15}(?!\d)", normalized)))
    dates = __import__("re").findall(r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])[/-](?:0?[1-9]|1[0-2])[/-](?:20\d{2}|14\d{2})(?!\d)", normalized)
    times = __import__("re").findall(r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)", normalized)
    item_codes = list(dict.fromkeys(__import__("re").findall(r"(?<!\d)\d{7}(?!\d)", normalized)))
    decimals = [float(value) for value in __import__("re").findall(r"(?<!\d)\d{1,6}[.]\d{2}(?!\d)", normalized)]
    invoice_numbers = __import__("re").findall(
        r"(?i)(?:invoice|فاتورة|رقم)\D{0,24}(\d{4,10})(?!\d)", normalized
    )
    data = {
        "supplier_vat_no": vat_numbers[0] if vat_numbers else None,
        "customer_vat_no": vat_numbers[1] if len(vat_numbers) > 1 else None,
        "invoice_no": invoice_numbers[0] if invoice_numbers else None,
        "date_candidates": dates,
        "time_candidates": times,
        "item_code_candidates": item_codes,
        "decimal_candidates": decimals,
    }
    required = ["supplier_vat_no", "customer_vat_no", "invoice_no", "date_candidates", "item_code_candidates"]
    missing = [field for field in required if not data[field]]
    checks: dict[str, object] = {"critical_fields_present": not missing, "missing_fields": missing}
    if len(decimals) >= 3:
        checks["arithmetic_candidates"] = {
            "quantity_times_unit_price": "not reliably inferable from plain OCR",
            "candidate_decimals": decimals,
        }
    checks["status"] = "needs_review" if missing else "candidate_only_review_required"
    return {"candidates": data, "validation": checks}


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
.message {{ background:#e4f2ec; padding:12px; border-radius:8px; white-space:pre-wrap; }}
.hint {{ color:#55736b; font-size:14px; }}
</style></head><body><main>
<h1>OCR PDF Lab</h1>
<p>Upload an invoice PDF. PaddleOCR reads it, local AI understands the layout, and validation checks the totals.</p>
<p class="hint"><strong>Parser:</strong> Hybrid Local AI v2 (Ollama + spatial fallback)</p>
{safe_message}
<form method="post" enctype="multipart/form-data" onsubmit="const b=this.querySelector('button'); b.textContent='Processing OCR... please wait'; b.disabled=true;">
<label>PDF file<input type="file" name="pdf" accept="application/pdf,.pdf" required></label>
<label>Languages<select name="languages"><option value="eng+ara">English + Arabic</option><option value="eng">English only</option><option value="ara">Arabic only</option><option value="eng+urd">English + Urdu</option></select></label>
<label>Processing<select name="mode"><option value="auto">Balanced (AI only when review is needed)</option><option value="fast">Fast (spatial parser, no AI)</option></select></label>
<label>Output<select name="format"><option value="invoice">Invoice JSON</option><option value="invoice_debug">Detailed invoice JSON (debug)</option><option value="json">Raw OCR JSON (technical boxes)</option></select></label>
<p class="hint">PaddleOCR uses Arabic recognition for Arabic/Urdu selections; it also handles Latin text and numbers.</p>
<button type="submit">Run PaddleOCR</button>
</form></main></body></html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_failure(self, message, status):
        self.send_json({"schema_version": "1.0", "status": "error", "data": None,
                        "error": {"code": status, "message": message}}, status)

    def send_html(self, body: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/":
            self.send_html(page())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path != '/':
            self.send_failure('Unknown endpoint.', 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"schema_version": "1.0", "status": "error", "data": None,
                            "error": {"code": 400, "message": "Invalid Content-Length"}}, 400)
            return
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self.send_failure("Please upload a PDF smaller than 100 MB.", 413)
            return

        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
        )
        pdf_part = next((part for part in message.iter_attachments() if part.get_param("name", header="content-disposition") == "pdf"), None)
        language_part = next((part for part in message.iter_attachments() if part.get_param("name", header="content-disposition") == "languages"), None)
        format_part = next((part for part in message.iter_attachments() if part.get_param("name", header="content-disposition") == "format"), None)
        mode_part = next((part for part in message.iter_attachments() if part.get_param("name", header="content-disposition") == "mode"), None)
        mode = "fast" if mode_part and mode_part.get_content().strip() == "fast" else "auto"
        if pdf_part is None:
            self.send_failure("No PDF was received.", 400)
            return

        languages = (language_part.get_content() if language_part else "eng+ara").strip()
        output_format = (format_part.get_content() if format_part else "json").strip()
        if output_format not in {"json", "pdf", "invoice_debug"}:
            output_format = "invoice"
        if languages not in {"eng", "ara", "eng+ara", "urd", "eng+urd"}:
            languages = "eng+ara"
        filename = Path(pdf_part.get_filename() or "input.pdf").name
        if Path(filename).suffix.lower() != ".pdf":
            self.send_failure("Only PDF files are supported.", 400)
            return

        job_id = uuid.uuid4().hex
        input_path = UPLOAD_DIR / f"{job_id}-input.pdf"
        output_path = UPLOAD_DIR / f"{job_id}-searchable.pdf"
        sidecar_path = UPLOAD_DIR / f"{job_id}-ocr.txt"
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
            "--oversample", "300", "--output-type", "pdf", "--sidecar", str(sidecar_path), "--force-ocr",
            str(input_path), str(output_path),
        ]
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
                        self.send_failure(f"OCR failed:\n{detail}", 500)
                        return
                    coordinate_payload = json.loads(coordinate_path.read_text(encoding="utf-8"))
                else:
                    from coordinate_ocr import extract_pdf
                    coordinate_payload = extract_pdf(input_path, languages)
                ocr_finished = perf_counter()
                raw_text = "\n".join(item.get("text", "") for item in coordinate_payload["pages"])
                payload: dict[str, object] = {
                    "source_filename": filename,
                    "language": languages,
                    "engine": coordinate_payload["engine"],
                    "pages": coordinate_payload["pages"],
                }
                if output_format in {"invoice", "invoice_debug"}:
                    payload = parse_invoice_hybrid(
                        coordinate_payload["pages"], filename, languages, mode=mode
                    )
                    # Preserve the evidence when parsing fails, without another OCR run.
                    if payload.get("quality", {}).get("needs_review"):
                        payload["raw_ocr"] = {"pages": coordinate_payload["pages"]}
                payload["timings_seconds"] = {
                    **coordinate_payload.get("timings_seconds", {}),
                    "ocr_total": round(ocr_finished - started, 3),
                    "invoice_parser": round(perf_counter() - ocr_finished, 3),
                    "total": round(perf_counter() - started, 3),
                }
                payload["ocr_device"] = coordinate_payload.get("device", "unknown")
                payload["pipeline_version"] = coordinate_payload.get("pipeline_version", "unknown")
                payload["schema_version"] = "1.0"
                payload["status"] = payload.get("quality", {}).get("overall_status", "extracted")
                payload["extraction_methods"] = [p.get("extraction_method", "ocr") for p in coordinate_payload["pages"]]
                self.send_json(clean_invoice_response(payload) if output_format == "invoice" else payload)
                return
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=1800)
            if result.returncode != 0 or not output_path.exists():
                detail = (result.stderr or result.stdout or "OCR failed").strip()
                self.send_failure(f"OCR failed:\n{detail}", 500)
                return
            if output_format in {"json", "invoice"}:
                raw_text = sidecar_path.read_text(encoding="utf-8", errors="replace")
                page_texts = raw_text.split("\f")
                if page_texts and not page_texts[-1].strip():
                    page_texts.pop()
                payload = {
                    "source_filename": filename,
                    "language": languages,
                    "engine": "tesseract via OCRmyPDF",
                    "pages": [
                        {"page": index, "text": text.strip()}
                        for index, text in enumerate(page_texts, start=1)
                    ],
                }
                if output_format == "invoice":
                    payload["document_type"] = "invoice"
                    payload["extraction"] = invoice_candidates(raw_text)
                data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Disposition", f'inline; filename="{Path(filename).stem}-ocr.json"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            data = output_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{Path(filename).stem}-searchable.pdf"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except InvalidPDFError as error:
            self.send_failure(str(error),400)
        except subprocess.TimeoutExpired:
            self.send_failure("OCR timed out after 30 minutes.", 504)
        except Exception as error:
            self.log_message("OCR failed: %s", error)
            self.send_failure(f"OCR failed: {error}", 500)
        finally:
            for path in (input_path, output_path, sidecar_path, coordinate_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("OCR PDF Lab running at http://127.0.0.1:8765")
    server.serve_forever()
