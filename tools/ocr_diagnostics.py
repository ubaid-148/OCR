"""Optional Colab OCR diagnostics for a caller-selected PDF.

Run with the isolated OCR Python used by the web app, not the notebook kernel.
These commands do not modify production OCR parameters or public JSON output.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from time import perf_counter


def _pdf(path: Path) -> Path:
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF not found: {path}")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError(f"Not a PDF file: {path}")
    return path


def _save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _selected_page(document: object, number: int) -> object:
    if not 1 <= number <= len(document):
        raise ValueError(f"Page {number} is outside this PDF ({len(document)} pages).")
    return document[number - 1]


def run_raw(pdf: Path, output_dir: Path) -> None:
    # This subprocess is used only for evidence: raster OCR with no targeted retry.
    os.environ["OCR_FORCE_RASTER"] = "true"
    os.environ["OCR_TARGETED_RETRY"] = "false"
    from coordinate_ocr import extract_pdf
    from local_ai_parser import parse_invoice_hybrid

    started = perf_counter()
    raw = extract_pdf(pdf, "eng+ara", progress=print)
    elapsed = perf_counter() - started
    raw["benchmark"] = {
        "input": pdf.name,
        "forced_raster": True,
        "device": raw.get("device"),
        "elapsed_seconds": round(elapsed, 3),
    }
    _save(output_dir / "raw_paddleocr.json", raw)

    rows = [(page.get("page"), word) for page in raw.get("pages", [])
            for word in page.get("words", [])]
    numeric = [{"page": page, "text": word.get("text"),
                "confidence": word.get("confidence"),
                "bbox": {key: word.get(key) for key in ("left", "top", "width", "height")}}
               for page, word in rows if re.search(r"\d", str(word.get("text", "")))]
    print(f"OCR time: {elapsed:.3f} sec; pages: {len(raw.get('pages', []))}; "
          f"detected boxes: {len(rows)}", flush=True)
    print("Numeric OCR evidence (text, confidence, page, bbox):", flush=True)
    print(json.dumps(numeric, ensure_ascii=False, indent=2), flush=True)

    started = perf_counter()
    parsed = parse_invoice_hybrid(raw["pages"], pdf.name, "eng+ara", mode="fast")
    _save(output_dir / "parser_full.json", parsed)
    data = parsed.get("data") or {}
    quality = parsed.get("quality") or {}
    view = {
        "parser": quality.get("parser"),
        "quality": quality,
        "validation": data.get("validation"),
        "items": data.get("items", []),
        "totals": data.get("totals", {}),
        "parser_seconds": round(perf_counter() - started, 3),
    }
    _save(output_dir / "parser_fast.json", view)
    print("Parser output derived from the same OCR pages:", flush=True)
    print(json.dumps(view, ensure_ascii=False, indent=2), flush=True)
    print("Compare source and raw boxes before attributing errors to OCR or parsing.", flush=True)


def run_preprocessing(pdf: Path, output_dir: Path, page_number: int) -> None:
    from PIL import ImageEnhance, ImageFilter, ImageOps
    import pypdfium2 as pdfium
    from coordinate_ocr import _get_model, extract_words

    images_dir = output_dir / "preprocessing"
    images_dir.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument(str(pdf)) as document:
        page = _selected_page(document, page_number)
        try:
            variants = {}
            for name, dpi in (("original", 200), ("high_resolution", 300)):
                bitmap = page.render(scale=dpi / 72)
                try:
                    variants[name] = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
        finally:
            page.close()
    base = variants["high_resolution"]
    variants["grayscale"] = ImageOps.grayscale(base).convert("RGB")
    variants["contrast_sharpened"] = ImageEnhance.Contrast(base).enhance(1.6).filter(ImageFilter.SHARPEN)
    try:
        import cv2
        import numpy as np
        gray = np.array(ImageOps.grayscale(base))
        threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        points = cv2.findNonZero(threshold)
        angle = 0.0
        if points is not None and len(points) > 20:
            angle = cv2.minAreaRect(points)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
        variants["deskew_if_required"] = base.rotate(angle, expand=True, fillcolor="white")
    except ImportError:
        variants["deskew_if_required"] = base
        angle = None

    model = _get_model("ar")
    comparison = []
    for name, image in variants.items():
        image_path = images_dir / f"page-{page_number}-{name}.png"
        image.save(image_path)
        started = perf_counter()
        words = []
        for prediction in model.predict(str(image_path)):
            words.extend(extract_words(prediction))
        numeric = [word for word in words if re.search(r"\d", str(word.get("text", "")))]
        comparison.append({
            "preprocessing": name,
            "image_size": image.size,
            "words": len(words),
            "high_confidence_words": sum(float(word.get("confidence", 0)) >= 80 for word in words),
            "numeric_words": len(numeric),
            "elapsed_seconds": round(perf_counter() - started, 3),
            "deskew_angle": angle if name == "deskew_if_required" else None,
            "numeric_text": [word["text"] for word in numeric],
        })
    _save(output_dir / f"page-{page_number}-preprocessing-comparison.json", comparison)
    print(json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)


def run_annotation(pdf: Path, output_dir: Path, page_number: int) -> None:
    from PIL import ImageDraw, ImageFont
    import pypdfium2 as pdfium

    raw_path = output_dir / "raw_paddleocr.json"
    if not raw_path.is_file():
        raise ValueError("Run the raw benchmark first; raw_paddleocr.json is missing.")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    page_data = next((row for row in raw.get("pages", []) if row.get("page") == page_number), None)
    if page_data is None:
        raise ValueError(f"Page {page_number} is outside OCR results for this PDF.")
    render_dpi = float(page_data.get("render_dpi", 200))
    with pdfium.PdfDocument(str(pdf)) as document:
        page = _selected_page(document, page_number)
        try:
            bitmap = page.render(scale=render_dpi / 72)
            try:
                image = bitmap.to_pil().convert("RGB")
            finally:
                bitmap.close()
        finally:
            page.close()
    draw = ImageDraw.Draw(image)
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = ImageFont.truetype(str(font_path), 13) if font_path.is_file() else ImageFont.load_default()
    for word in page_data.get("words", []):
        left, top, width, height = (float(word.get(key) or 0)
                                    for key in ("left", "top", "width", "height"))
        confidence = float(word.get("confidence") or 0)
        draw.rectangle((left, top, left + width, top + height), outline="red", width=2)
        label = f"{word.get('text', '')} [{confidence:.0f}]"
        try:
            draw.text((left, max(0, top - 14)), label, fill="red", font=font)
        except UnicodeError:
            draw.text((left, max(0, top - 14)), f"[{confidence:.0f}]", fill="red", font=font)
    path = output_dir / f"page-{page_number}-ocr-boxes.png"
    image.save(path)
    print("Annotated OCR evidence:", path, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("raw", "preprocess", "annotate"))
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
    pdf = _pdf(args.pdf)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "raw":
        run_raw(pdf, output_dir)
    elif args.mode == "preprocess":
        run_preprocessing(pdf, output_dir, args.page)
    else:
        run_annotation(pdf, output_dir, args.page)


if __name__ == "__main__":
    main()
