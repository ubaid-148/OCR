from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# oneDNN currently fails on some Colab CPU runtimes while converting PIR
# attributes. The regular Paddle inference path is slower but portable.
os.environ.setdefault("FLAGS_use_mkldnn", "0")

import pypdfium2 as pdfium
from paddleocr import PaddleOCR


LANGUAGE_MAP = {
    "eng": "en", "ara": "ar", "eng+ara": "ar",
    "urd": "ar", "eng+urd": "ar",
}


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


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: coordinate_ocr.py INPUT.pdf OUTPUT.json LANGUAGES")
    input_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    languages = sys.argv[3]
    paddle_language = LANGUAGE_MAP.get(languages, "ar")
    recognition_model = (
        "PP-OCRv5_mobile_rec" if paddle_language == "en"
        else "arabic_PP-OCRv5_mobile_rec"
    )
    ocr = PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name=recognition_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
    )
    document = pdfium.PdfDocument(str(input_path))
    pages = []
    render_dpi = 200
    with tempfile.TemporaryDirectory(prefix="paddle-ocr-") as temp_dir:
        temp_root = Path(temp_dir)
        for number, page in enumerate(document, start=1):
            image_path = temp_root / f"page-{number}.png"
            page.render(scale=render_dpi / 72).to_pil().convert("RGB").save(image_path)
            words = []
            for prediction in ocr.predict(str(image_path)):
                words.extend(extract_words(prediction))
            pages.append({
                "page": number, "render_dpi": render_dpi,
                "width": page.get_width(), "height": page.get_height(),
                "words": words,
                "text": "\n".join(item["text"] for item in words),
            })
    output_path.write_text(json.dumps({
        "engine": f"PaddleOCR 3 ({paddle_language})",
        "language": languages, "pages": pages,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
