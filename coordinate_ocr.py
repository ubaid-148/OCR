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
from paddleocr import DocImgOrientationClassification, PaddleOCR
from native_pdf import extract_native_words
from targeted_ocr import retry_regions, merge_retries
from document_regions import receipt_region
from pdf_errors import InvalidPDFError
from page_rotation import (
    choose_orientation, page_orientation,
    render_upright_page, rotate_image,
)


LANGUAGE_MAP = {
    "eng": "en", "ara": "ar", "eng+ara": "ar",
    "urd": "ar", "eng+urd": "ar",
}

# PDFium and Paddle predictors are shared native resources: serialize jobs.
_OCR_LOCK = threading.Lock()
_MODELS = {}
_ORIENTATION_MODEL = None


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


def orientation_result_payload(result: object) -> dict[str, object]:
    """Normalize the standalone Paddle orientation-classifier response."""
    return result_payload(result)


def orientation_decision(result: object, minimum: float | None = None) -> dict[str, object]:
    """Return a safe residual correction; uncertain guesses are never applied."""
    data = orientation_result_payload(result)
    labels = data.get("label_names") or []
    scores = data.get("scores") or []
    try:
        return choose_orientation(labels[0], scores[0], minimum)
    except (IndexError, TypeError, ValueError) as error:
        return dict(rotation_degrees=0, rotation_confidence=None,
                    rotation_source="paddle_doc_orientation", rotation_status="failed",
                    rotation_error=f"Invalid orientation result: {error}")


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


def ocr_model_kwargs(paddle_language: str) -> dict[str, object]:
    """Current production PaddleOCR constructor settings (no sweep overrides)."""
    recognition_model = (
        "en_PP-OCRv5_mobile_rec" if paddle_language == "en"
        else "arabic_PP-OCRv5_mobile_rec"
    )
    return dict(
        device=get_ocr_device(),
        cpu_threads=max(1, min(4, os.cpu_count() or 1)),
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name=recognition_model,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        enable_mkldnn=False,
    )


def _get_model(paddle_language: str):
    if paddle_language in _MODELS:
        return _MODELS[paddle_language]
    ocr = PaddleOCR(**ocr_model_kwargs(paddle_language))
    _MODELS[paddle_language] = ocr
    return ocr


def _get_orientation_model():
    global _ORIENTATION_MODEL
    if _ORIENTATION_MODEL is None:
        _ORIENTATION_MODEL = DocImgOrientationClassification(
            model_name="PP-LCNet_x1_0_doc_ori", device=get_ocr_device())
    return _ORIENTATION_MODEL


def extract_pdf(input_path: Path, languages: str, progress=None) -> dict[str, object]:
    progress = progress or (lambda message: None)
    progress('Waiting for OCR worker')
    started = perf_counter()
    with _OCR_LOCK:
        acquired = perf_counter()
        paddle_language = LANGUAGE_MAP.get(languages, "ar")
        load_seconds = 0.0
        orientation_seconds = 0.0
        def model(language=None):
            nonlocal load_seconds
            start = perf_counter()
            if (language or paddle_language) not in _MODELS:
                progress('Preparing recognition model (' + (language or paddle_language) + ')')
            result = _get_model(language or paddle_language)
            load_seconds += perf_counter() - start
            return result
        def orient(image_path):
            nonlocal load_seconds, orientation_seconds
            load_started = perf_counter()
            if globals().get("_ORIENTATION_MODEL") is None:
                progress("Preparing page-orientation model")
            try:
                detector = _get_orientation_model()
            except Exception as error:
                load_seconds += perf_counter() - load_started
                return dict(rotation_degrees=0, rotation_confidence=None,
                            rotation_source="paddle_doc_orientation", rotation_status="failed",
                            rotation_error=str(error)[:500])
            load_seconds += perf_counter() - load_started
            inference_started = perf_counter()
            try:
                result = next(iter(detector.predict(str(image_path))))
                decision = orientation_decision(result)
            except Exception as error:
                decision = dict(rotation_degrees=0, rotation_confidence=None,
                                rotation_source="paddle_doc_orientation", rotation_status="failed",
                                rotation_error=str(error)[:500])
            orientation_seconds += perf_counter() - inference_started
            return decision
        payload = _extract_pdf(input_path, languages, paddle_language, model, progress, orient)
        payload["device"] = get_ocr_device() if any(p["extraction_method"] == "ocr" for p in payload["pages"]) else "not_used"
        ocr_seconds=perf_counter() - acquired - load_seconds
        targeted_seconds=sum(float(page.get('targeted_ocr_seconds',0) or 0) for page in payload['pages'])
        payload["timings_seconds"] = {
            "queue": round(acquired - started, 3),
            "model_load": round(load_seconds, 3),
            "orientation_detection": round(orientation_seconds, 3),
            "base_render_and_ocr": round(max(0,ocr_seconds-targeted_seconds-orientation_seconds),3),
            "targeted_ocr": round(targeted_seconds,3),
            "render_and_ocr": round(ocr_seconds, 3),
        }
        return payload


def _extract_pdf(input_path, languages, paddle_language, model, progress=lambda message: None,
                 orient=None):
    pages = []
    render_dpi = 200
    try:
        document=pdfium.PdfDocument(str(input_path))
    except Exception as error:
        raise InvalidPDFError('PDF is corrupt, encrypted, or unsupported.') from error
    if len(document)==0:
        document.close()
        raise InvalidPDFError('PDF contains no pages.')
    with document, tempfile.TemporaryDirectory(prefix="paddle-ocr-") as temp_dir:
        temp_root = Path(temp_dir)
        for number, page in enumerate(document, start=1):
            progress(f'Reading page {number} of {len(document)}')
            pdf_rotation = int(page.get_rotation() or 0)
            try:
                native = extract_native_words(page) if os.environ.get("OCR_FORCE_RASTER", "false").lower() not in {"true", "1"} else []
            except Exception:
                native = []  # Unusable text layer: rasterize this page.
            if native:
                pages.append(dict(page=number, render_dpi=render_dpi,
                                  width=page.get_width(), height=page.get_height(),
                                  canonical_width=page.get_width()*render_dpi/72,
                                  canonical_height=page.get_height()*render_dpi/72,
                                  pdf_rotation_degrees=pdf_rotation, rotation_degrees=0,
                                  rotation_confidence=None, rotation_source="native_text_matrix",
                                  rotation_status="upright",
                                  extraction_method="native_text", words=native,
                                  text="\n".join(w["text"] for w in native)))
                page.close()
                continue
            image_path = temp_root / f"page-{number}.png"
            raw_image = render_upright_page(page, render_dpi, 0)
            raw_image.save(image_path)
            decision = orient(image_path) if orient else dict(
                rotation_degrees=0, rotation_confidence=None,
                rotation_source="orientation_unavailable", rotation_status="failed")
            correction = decision["rotation_degrees"]
            upright_image = rotate_image(raw_image, correction)
            upright_image.save(image_path)
            progress(f"Page {number} orientation: {correction}° ({decision['rotation_status']})")
            words = []
            predictor = model()
            progress(f'Recognizing page {number} of {len(document)}')
            for prediction in predictor.predict(str(image_path)):
                words.extend(extract_words(prediction))
            page_payload = {
                "page": number, "render_dpi": render_dpi,
                "extraction_method": "ocr",
                "width": page.get_width(), "height": page.get_height(),
                "canonical_width": upright_image.width,
                "canonical_height": upright_image.height,
                "pdf_rotation_degrees": pdf_rotation,
                **decision,
                "words": words,
                "text": "\n".join(item["text"] for item in words),
            }
            page_payload['receipt_region']=receipt_region(words,upright_image.width,upright_image.height)
            page_payload['base_words']=[dict(word) for word in words]
            if os.environ.get('OCR_TARGETED_RETRY','true').lower() not in {'false','0','no'}:
                retry_started=perf_counter()
                try:
                    progress(f'Checking uncertain fields on page {number}')
                    retries=retry_regions(page,page_payload,lambda lang='en':model(lang),extract_words,temp_root)
                    merge_retries(page_payload,retries)
                    all_retries=list(retries)
                    def merge_additional(extra):
                        if not extra:
                            return
                        prior=list(page_payload['targeted_ocr']['accepted'])
                        merge_retries(page_payload,extra)
                        page_payload['targeted_ocr']['accepted']=prior+page_payload['targeted_ocr']['accepted']
                        all_retries.extend(extra)
                        page_payload['targeted_ocr']['alternatives']=list(all_retries)
                        page_payload['targeted_ocr']['attempted_regions']=len(all_retries)
                    # Footer evidence can reveal that an apparently complete set
                    # of rows is short. Re-plan only the table after totals merge.
                    table_kinds={'table_area','table_area_en','table_cells','table_cells_en'}
                    if (any(r['kind']=='footer_totals' for r in retries) and
                            not any(r['kind'] in table_kinds for r in retries)):
                        table_retries=retry_regions(
                            page,page_payload,lambda lang='en':model(lang),
                            extract_words,temp_root,planned_kinds={'table_cells'})
                        merge_additional(table_retries)
                    # Recovered prices can reveal rows that did not exist when
                    # the first retry plan was made. Re-plan numeric cells once.
                    if any(r['kind'] in table_kinds for r in all_retries):
                        numeric_retries=retry_regions(page,page_payload,lambda lang='en':model(lang),
                            extract_words,temp_root,missing_numeric_only=True)
                        merge_additional(numeric_retries)
                except Exception as error:
                    page_payload['targeted_ocr_error']=str(error)[:500]
                page_payload['targeted_ocr_seconds']=round(perf_counter()-retry_started,3)
            pages.append(page_payload)
            page.close()
    return {
        "pipeline_version": "2026-09-ruled-grid-v13",
        "engine": f"PDFium native text / PaddleOCR 3 ({paddle_language})",
        "language": languages, "pages": pages,
        "page_orientations": [page_orientation(page) for page in pages],
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
