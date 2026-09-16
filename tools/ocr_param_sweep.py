"""Colab-only Stage 1 PaddleOCR DPI/side-limit diagnostic (no default changes).

Run with the OCR virtualenv after colab_setup.ipynb has installed its models::

    /content/ocr-runtime/bin/python -m tools.ocr_param_sweep \
        --pdf public_invoice_pdfs/9498.pdf --output-dir benchmark_outputs

This measures OCR output, not ground-truth accuracy. It saves each run before
starting the next, and never invokes the web parser or modifies production OCR
settings. Stage 2 is intentionally absent until Stage 1 has been reviewed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import inspect
import json
from pathlib import Path
import re
from statistics import mean, median
import tempfile
from time import perf_counter
import unicodedata


RENDER_DPI = (200, 300, 400)
SIDE_LIMITS = (960, 1600, 2400, 3200)
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789.")


def major_version(version: str) -> int:
    match = re.match(r"^(\d+)(?:\.|$)", version)
    if not match:
        raise ValueError(f"Unrecognized PaddleOCR version: {version!r}")
    return int(match.group(1))


def resolve_side_parameter(version: str, constructor: object, predict: object | None,
                           legacy_options: set[str] | None = None) -> tuple[str, str]:
    """Inspect actual signatures; a **kwargs slot alone is not proof of support."""
    constructor_names = inspect.signature(constructor).parameters
    predict_names = inspect.signature(predict).parameters if predict else {}
    if major_version(version) >= 3:
        name = "text_det_limit_side_len"
        if name in predict_names:
            return name, "predict"
        if name in constructor_names:
            return name, "constructor"
    else:
        name = "det_limit_side_len"
        # PaddleOCR 2.x exposes only **kwargs on its constructor. Verify the
        # option against the package's own parse_args registry before passing.
        if name in constructor_names or name in (legacy_options or set()):
            return name, "constructor"
    raise RuntimeError(f"The installed PaddleOCR {version} does not expose {name}; sweep stopped")


def is_decimal_token(text: object) -> bool:
    value = unicodedata.normalize("NFKC", str(text)).translate(DIGITS).strip()
    value = re.sub(r"(?<=\d),(?=\d{3}(?:[, .]|$))", "", value)
    value = value.replace(",", ".")
    if not re.fullmatch(r"[+-]?\d+\.\d+", value):
        return False
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _box_height(polygon: object) -> float | None:
    try:
        ys = [float(point[1]) for point in polygon]
    except (TypeError, ValueError, IndexError):
        return None
    return max(ys) - min(ys) if len(ys) >= 2 else None


def _result_payload(result: object) -> dict[str, object]:
    data = getattr(result, "json", result)
    if callable(data):
        data = data()
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict) and isinstance(data.get("res"), dict):
        data = data["res"]
    if not isinstance(data, dict):
        raise RuntimeError("Unsupported PaddleOCR 3.x result format")
    return data


def prediction_metrics(raw: object, major: int) -> dict[str, object]:
    if major >= 3:
        data = _result_payload(raw[0]) if isinstance(raw, (list, tuple)) and raw else {}
        texts = list(data.get("rec_texts") if data.get("rec_texts") is not None else [])
        scores = [float(x) for x in (data.get("rec_scores") if data.get("rec_scores") is not None else [])]
        boxes = data.get("dt_polys")
        if boxes is None:
            boxes = data.get("rec_polys")
        if boxes is None:
            boxes = []
        params = data.get("text_det_params") or {}
    else:
        rows = (raw or [None])[0] or []
        texts = [entry[1][0] for entry in rows]
        scores = [float(entry[1][1]) for entry in rows]
        boxes = [entry[0] for entry in rows]
        params = None
    heights = [height for box in boxes if (height := _box_height(box)) is not None]
    return {
        "detected_boxes": len(boxes),
        "recognized_boxes": len(texts),
        "mean_confidence": round(mean(scores) * 100, 2) if scores else None,
        "minimum_confidence": round(min(scores) * 100, 2) if scores else None,
        "decimal_tokens": sum(is_decimal_token(text) for text in texts),
        # Output polygons are in source-image coordinates; this is NOT the
        # detector's internal resized glyph height.
        "median_output_box_height_px": round(median(heights), 2) if heights else None,
        "reported_text_det_params": params,
    }


def _write_result(path: Path, result: dict[str, object]) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _table(result: dict[str, object]) -> str:
    rows = [run for run in result["runs"] if "error" not in run]
    rows.sort(key=lambda r: (-r["decimal_tokens"], -(r["mean_confidence"] or 0),
                             -r["recognized_boxes"], r["predict_seconds"]))
    lines = ["Rank  DPI  Limit  Det  Rec  Decimal  Mean%  Min%  Box-h(px)  Predict(s)  Limit-type"]
    for rank, row in enumerate(rows, 1):
        det = row.get("reported_text_det_params") or {}
        lines.append(f"{rank:>4} {row['render_dpi']:>4} {row['side_limit']:>6} "
                     f"{row['detected_boxes']:>4} {row['recognized_boxes']:>4} "
                     f"{row['decimal_tokens']:>7} {str(row['mean_confidence']):>6} "
                     f"{str(row['minimum_confidence']):>5} "
                     f"{str(row['median_output_box_height_px']):>10} "
                     f"{row['predict_seconds']:>11.3f}  {det.get('limit_type', '?')}")
    for row in result["runs"]:
        if "error" in row:
            lines.append(f"FAILED dpi={row['render_dpi']} limit={row['side_limit']}: {row['error']}")
    lines.append("Ranking is a diagnostic heuristic, not a field-accuracy score.")
    lines.append("Output box heights are mapped to source pixels; they cannot prove internal detector resolution.")
    return "\n".join(lines)


def _comparisons(result: dict[str, object]) -> str:
    lines = ["Change from side-limit 960 at the SAME render DPI:"]
    observed = False
    reported_types = {params["limit_type"] for row in result["runs"]
                      if isinstance((params := row.get("reported_text_det_params")), dict)
                      and params.get("limit_type")}
    runs = {(row["render_dpi"], row["side_limit"]): row for row in result["runs"]
            if "error" not in row}
    for dpi in RENDER_DPI:
        base = runs.get((dpi, 960))
        if base is None:
            lines.append(f"  {dpi} DPI: no successful 960 baseline")
            continue
        for limit in SIDE_LIMITS[1:]:
            row = runs.get((dpi, limit))
            if row is None:
                continue
            box_delta = row["detected_boxes"] - base["detected_boxes"]
            numeric_delta = row["decimal_tokens"] - base["decimal_tokens"]
            mean_delta = ((row["mean_confidence"] or 0) - (base["mean_confidence"] or 0))
            height_delta = ((row["median_output_box_height_px"] or 0) -
                            (base["median_output_box_height_px"] or 0))
            observed |= box_delta != 0 or numeric_delta != 0 or abs(mean_delta) >= 0.01
            lines.append(f"  {dpi} DPI, {limit}: boxes {box_delta:+}, decimal tokens {numeric_delta:+}, "
                         f"mean confidence {mean_delta:+.2f}pp, output box height {height_delta:+.2f}px, "
                         f"predict time {row['predict_seconds'] - base['predict_seconds']:+.3f}s")
    lines.append("Observed OCR-output change on this page: " + ("YES" if observed else "NO"))
    if reported_types and reported_types != {"max"}:
        lines.append(f"Downscale premise needs revision: detector reported limit_type={sorted(reported_types)}, not exclusively 'max'.")
    lines.append("This does not identify the winning accuracy setting; compare source fields before changing defaults.")
    return "\n".join(lines)


def run_stage1(pdf_path: Path, page_number: int, output_dir: Path) -> Path:
    # Heavy imports occur only when this CLI is explicitly run in Colab.
    import paddleocr
    from paddleocr import PaddleOCR
    import pypdfium2 as pdfium

    version = paddleocr.__version__
    major = major_version(version)
    constructor_signature = inspect.signature(PaddleOCR)
    prediction_method = getattr(PaddleOCR, "predict", None) if major >= 3 else None
    prediction_signature = inspect.signature(prediction_method) if prediction_method else None
    legacy_options = None
    if major < 3:
        from paddleocr.paddleocr import parse_args
        legacy_options = set(vars(parse_args(mMain=False)))
    side_name, placement = resolve_side_parameter(version, PaddleOCR, prediction_method,
                                                   legacy_options)

    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF not found: {pdf_path}")
    with pdfium.PdfDocument(str(pdf_path)) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError(f"Page {page_number} is outside this PDF")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = output_dir / f"{pdf_path.stem}_page{page_number}_stage1_{stamp}.json"
    if output.exists():
        raise FileExistsError(output)

    if major >= 3:
        from coordinate_ocr import ocr_model_kwargs
        from paddleocr._common_args import parse_common_args
        base_kwargs = ocr_model_kwargs("ar")
        # Explicit OCR parameters must appear in the installed constructor.
        for key in ("text_detection_model_name", "text_recognition_model_name",
                    "use_doc_orientation_classify", "use_doc_unwarping",
                    "use_textline_orientation"):
            if key not in constructor_signature.parameters:
                raise RuntimeError(f"PaddleOCR {version} constructor lacks required {key}")
        common_kwargs = {key: value for key, value in base_kwargs.items()
                         if key not in constructor_signature.parameters}
        # PaddleOCR 3.x routes device/cpu_threads through **kwargs. Its own
        # common-args parser must accept them; **kwargs alone is not proof.
        parse_common_args(common_kwargs, default_enable_hpi=None)
    else:
        base_kwargs = {"lang": "arabic", "use_angle_cls": False, "show_log": False}
        unknown = set(base_kwargs) - (legacy_options or set())
        if unknown:
            raise RuntimeError(f"Legacy PaddleOCR option registry lacks: {sorted(unknown)}")

    result: dict[str, object] = {
        "stage": 1, "pdf": str(pdf_path.resolve()), "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "page": page_number, "paddleocr_version": version,
        "constructor_signature": str(constructor_signature),
        "predict_signature": str(prediction_signature) if prediction_signature else None,
        "side_parameter": side_name, "side_parameter_placement": placement,
        "base_constructor_kwargs": base_kwargs,
        "sweep_axes": {"render_dpi": RENDER_DPI, "side_limit": SIDE_LIMITS},
        "runs": [], "status": "running",
    }
    _write_result(output, result)
    print(f"PaddleOCR {version}; constructor={constructor_signature}", flush=True)
    print(f"Side limit: {side_name} via {placement}; base kwargs={json.dumps(base_kwargs)}", flush=True)
    shared_model = None
    if placement == "predict":
        load_started = perf_counter()
        shared_model = PaddleOCR(**base_kwargs)
        result["model_load_seconds"] = round(perf_counter() - load_started, 3)
        _write_result(output, result)

    with tempfile.TemporaryDirectory(prefix="ocr-param-sweep-") as temporary:
        with pdfium.PdfDocument(str(pdf_path)) as document:
            page = document[page_number - 1]
            try:
                for dpi in RENDER_DPI:
                    render_started = perf_counter()
                    bitmap = page.render(scale=dpi / 72)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                    image_path = Path(temporary) / f"page-{page_number}-{dpi}.png"
                    image.save(image_path)
                    width, height = image.size
                    render_seconds = perf_counter() - render_started
                    for limit in SIDE_LIMITS:
                        call_kwargs = {side_name: limit} if placement == "predict" else {}
                        constructor_kwargs = dict(base_kwargs)
                        if placement == "constructor":
                            constructor_kwargs[side_name] = limit
                        record = {"render_dpi": dpi, "side_limit": limit,
                                  "input_width_px": width, "input_height_px": height,
                                  "render_seconds": round(render_seconds, 3),
                                  "constructor_kwargs": constructor_kwargs,
                                  "predict_kwargs": call_kwargs}
                        print("RUN " + json.dumps(record, ensure_ascii=False), flush=True)
                        try:
                            if placement == "constructor":
                                load_started = perf_counter()
                                model = PaddleOCR(**constructor_kwargs)
                                record["model_load_seconds"] = round(perf_counter() - load_started, 3)
                            else:
                                model = shared_model
                            started = perf_counter()
                            raw = model.predict(str(image_path), **call_kwargs) if major >= 3 else model.ocr(str(image_path), cls=False)
                            record["predict_seconds"] = round(perf_counter() - started, 3)
                            record.update(prediction_metrics(raw, major))
                        except Exception as error:
                            record["error"] = f"{type(error).__name__}: {error}"
                            result["runs"].append(record)
                            result["status"] = "partial_error"
                            _write_result(output, result)
                            raise RuntimeError(f"Stage 1 stopped at DPI={dpi}, limit={limit}; partial report: {output}") from error
                        result["runs"].append(record)
                        _write_result(output, result)
                        print(f"DONE boxes={record['detected_boxes']} decimals={record['decimal_tokens']} "
                              f"mean={record['mean_confidence']}% time={record['predict_seconds']}s "
                              f"detector={record['reported_text_det_params']}", flush=True)
            finally:
                page.close()
    result["status"] = "complete"
    _write_result(output, result)
    print(_table(result), flush=True)
    print(_comparisons(result), flush=True)
    print("Stage 1 report saved to", output, flush=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark_outputs"))
    args = parser.parse_args()
    run_stage1(args.pdf, args.page, args.output_dir)


if __name__ == "__main__":
    main()
