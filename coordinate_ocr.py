from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from time import perf_counter
from pathlib import Path

# oneDNN currently fails on some Colab CPU runtimes while converting PIR
# attributes. The regular Paddle inference path is slower but portable.
os.environ.setdefault("FLAGS_use_mkldnn", "0")

import pypdfium2 as pdfium
import paddle
from paddleocr import PaddleOCR


LANGUAGE_MAP = {
    "eng": "en", "ara": "ar", "eng+ara": "ar",
    "urd": "ar", "eng+urd": "ar",
}

# PDFium and Paddle predictors are shared native resources: serialize jobs.
_OCR_LOCK = threading.Lock()
_MODELS = {}


def get_ocr_device() -> str:
    device = os.environ.get("OCR_DEVICE", "auto").strip().lower()
    gpu_available = paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0
    if device == "auto":
        return "gpu:0" if gpu_available else "cpu"
    if device.startswith("gpu") and not gpu_available:
        raise RuntimeError("OCR_DEVICE requests GPU, but Paddle cannot access CUDA. Install paddlepaddle-gpu and select a GPU runtime.")
    return device


def result_payload(result: object) -> dict[str, object]:
    """Return PaddleOCR 3.x result data as a normal dictionary."""
    data = getattr(result, "json", result)
    if callable(data):
        data = data()
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict) and isinstance(data.get("res"), dict):
        data = data["res"]
    if not isinstance(data, dict):
        raise RuntimeError("PaddleOCR returned an unsupported result format")
    return data


def extract_words(result: object) -> list[dict[str, object]]:
    data = result_payload(result)
    texts = data.get("rec_texts") or []
    scores = data.get("rec_scores") or []
    polygons = data.get("rec_polys") or data.get("dt_polys") or []
    words: list[dict[str, object]] = []
    for index, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue
        polygon = polygons[index] if index < len(polygons) else []
        points = [[float(point[0]), float(point[1])] for point in polygon]
        xs = [point[0] for point in points] or [0.0]
        ys = [point[1] for point in points] or [0.0]
        left, top = min(xs), min(ys)
        right, bottom = max(xs), max(ys)
        score = float(scores[index]) if index < len(scores) else 0.0
        words.append({
            "text": text,
            "confidence": round(score * 100, 2),
            "left": round(left, 2), "top": round(top, 2),
            "width": round(right - left, 2), "height": round(bottom - top, 2),
            "polygon": points,
        })
    return words


def _get_model(paddle_language: str):
    if paddle_language in _MODELS:
        return _MODELS[paddle_language]
    recognition_model = (
        "PP-OCRv5_mobile_rec" if paddle_language == "en"
        else "arabic_PP-OCRv5_mobile_rec"
    )
    ocr = PaddleOCR(
        device=get_ocr_device(),
        cpu_threads=max(1, min(4, os.cpu_count() or 1)),
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name=recognition_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
    )
    _MODELS[paddle_language] = ocr
    return ocr


def extract_pdf(input_path: Path, languages: str) -> dict[str, object]:
    started = perf_counter()
    with _OCR_LOCK:
        acquired = perf_counter()
        paddle_language = LANGUAGE_MAP.get(languages, "ar")
        ocr = _get_model(paddle_language)
        loaded = perf_counter()
        payload = _extract_pdf(input_path, languages, paddle_language, ocr)
        payload["device"] = get_ocr_device()
        payload["timings_seconds"] = {
            "queue": round(acquired - started, 3),
            "model_load": round(loaded - acquired, 3),
            "render_and_ocr": round(perf_counter() - loaded, 3),
        }
        return payload


def _extract_pdf(input_path, languages, paddle_language, ocr):
    pages = []
    render_dpi = 200
    with pdfium.PdfDocument(str(input_path)) as document, tempfile.TemporaryDirectory(prefix="paddle-ocr-") as temp_dir:
        temp_root = Path(temp_dir)
        for number, page in enumerate(document, start=1):
            image_path = temp_root / f"page-{number}.png"
            bitmap = page.render(scale=render_dpi / 72)
            try:
                bitmap.to_pil().convert("RGB").save(image_path)
            finally:
                bitmap.close()
            words = []
            for prediction in ocr.predict(str(image_path)):
                words.extend(extract_words(prediction))
            pages.append({
                "page": number, "render_dpi": render_dpi,
                "width": page.get_width(), "height": page.get_height(),
                "words": words,
                "text": "\n".join(item["text"] for item in words),
            })
            page.close()
    return {
        "engine": f"PaddleOCR 3 ({paddle_language})",
        "language": languages, "pages": pages,
    }


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: coordinate_ocr.py INPUT.pdf OUTPUT.json LANGUAGES")
    payload = extract_pdf(Path(sys.argv[1]).resolve(), sys.argv[3])
    Path(sys.argv[2]).resolve().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
