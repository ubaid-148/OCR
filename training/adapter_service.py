"""Private Colab-only HTTP service for an approved full-schema invoice adapter.

Runs under Colab's CUDA PyTorch interpreter, separate from the PaddleOCR venv.
It binds only to loopback; the OCR web process sends original-page JPEGs to it.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter

from training.compare_metrics import compare
from training.invoice_dataset import PROMPT, PROMPT_VERSION
from training.qwen_processor import load_processor


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def adapter_digest(directory: Path) -> str:
    directory = directory.resolve()
    files = sorted(path for path in directory.iterdir()
                   if path.name == "adapter_config.json" or path.name.startswith("adapter_model."))
    if not any(path.name.startswith("adapter_model.") for path in files):
        raise ValueError("Adapter weight file is missing")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def load_approval(path: Path) -> dict:
    approval = json.loads(path.read_text(encoding="utf-8"))
    def asset_path(value: str) -> Path:
        candidate = Path(value)
        return (candidate if candidate.is_absolute() else path.parent / candidate).resolve()
    if approval.get("approval_version") != "invoice-adapter-approval-v1" or approval.get("prompt_version") != PROMPT_VERSION:
        raise ValueError("Adapter approval is missing or belongs to an old training schema")
    if approval.get("approved") is not True:
        raise ValueError("Adapter has not passed the final test gate")
    adapter = asset_path(approval.get("adapter_dir", ""))
    if not (adapter / "adapter_config.json").is_file():
        raise ValueError("Approved adapter weights/configuration are missing")
    config = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    if config.get("base_model_name_or_path") != approval.get("model_id"):
        raise ValueError("Adapter was trained on a different base model")
    if adapter_digest(adapter) != approval.get("adapter_sha256"):
        raise ValueError("Adapter weights changed after held-out approval")
    base_path = asset_path(approval["base_test_metrics"])
    candidate_path = asset_path(approval["adapter_test_metrics"])
    if (file_digest(base_path) != approval.get("base_test_sha256")
            or file_digest(candidate_path) != approval.get("adapter_test_sha256")):
        raise ValueError("Held-out metrics changed after adapter approval")
    base = json.loads(base_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if base.get("split") != "test" or candidate.get("split") != "test" or candidate.get("documents", 0) < 10:
        raise ValueError("Final approval requires at least 10 untouched test documents")
    failures = compare(base, candidate, max(0.98, float(approval.get("min_critical", 0.98))),
                       max(0.90, float(approval.get("min_exact", 0.90))))
    if failures:
        raise ValueError("Adapter failed the test gate: " + "; ".join(failures))
    approval["adapter_dir"] = str(adapter)
    return approval


class Adapter:
    def __init__(self, approval: dict):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForImageTextToText

        if not torch.cuda.is_available():
            raise RuntimeError("Approved adapter inference requires a CUDA GPU")
        self.model_id = approval["model_id"]
        self.max_new_tokens = int(approval["max_new_tokens"])
        self.processor = load_processor(self.model_id, int(approval["max_pixels"]))
        base = AutoModelForImageTextToText.from_pretrained(
            self.model_id, torch_dtype=torch.float16, device_map="auto", attn_implementation="sdpa")
        self.model = PeftModel.from_pretrained(base, approval["adapter_dir"])
        self.model.eval()
        self.lock = threading.Lock()

    def extract(self, images: list[str]) -> dict:
        import torch
        if not isinstance(images, list) or not 1 <= len(images) <= 30:
            raise ValueError("Provide between 1 and 30 original page images")
        with tempfile.TemporaryDirectory(prefix="invoice-adapter-") as directory:
            paths = []
            for index, encoded in enumerate(images):
                if not isinstance(encoded, str):
                    raise ValueError("Page image is not a base64 string")
                raw = base64.b64decode(encoded, validate=True)
                if not raw.startswith(b"\xff\xd8\xff"):
                    raise ValueError("Expected original-page JPEG data")
                path = Path(directory) / f"page-{index + 1:03d}.jpg"
                path.write_bytes(raw)
                paths.append(path)
            messages = [{"role": "user", "content": [
                *({"type": "image", "image": str(path)} for path in paths),
                {"type": "text", "text": PROMPT},
            ]}]
            started = perf_counter()
            with self.lock, torch.inference_mode():
                inputs = self.processor.apply_chat_template(
                    messages, tokenize=True, add_generation_prompt=True,
                    return_dict=True, return_tensors="pt").to(self.model.device)
                generated = self.model.generate(**inputs, max_new_tokens=self.max_new_tokens, do_sample=False)
            output = generated[:, inputs["input_ids"].shape[-1]:]
            if output.shape[-1] >= self.max_new_tokens:
                raise ValueError("Trained vision output reached its token limit; result rejected")
            raw = self.processor.batch_decode(output, skip_special_tokens=True,
                                              clean_up_tokenization_spaces=False)[0]
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Trained vision output is not a JSON object")
            return {"prediction": parsed, "model_id": self.model_id,
                    "inference_seconds": round(perf_counter() - started, 3)}


def serve(approval_path: Path, port: int = 8766) -> None:
    adapter = Adapter(load_approval(approval_path))

    class Handler(BaseHTTPRequestHandler):
        def reply(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply({"ready": True, "model_id": adapter.model_id} if self.path == "/health"
                       else {"error": "not found"}, 200 if self.path == "/health" else 404)

        def do_POST(self):
            if self.path != "/extract":
                self.reply({"error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100 * 1024 * 1024:
                    raise ValueError("Request size is invalid or too large")
                request = json.loads(self.rfile.read(length))
                self.reply(adapter.extract(request.get("images")))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                self.reply({"error": str(error)}, 400)
            except Exception as error:
                self.reply({"error": f"{type(error).__name__}: {error}"}, 500)

        def log_message(self, format, *args):
            pass  # Never log invoice images or extracted text.

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Approved invoice adapter ready on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    serve(args.approval, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
