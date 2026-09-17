"""Shared page-orientation contract for OCR, retry crops, and vision images.

Coordinate order is fixed for the whole pipeline:

1. Rasterize the PDF page at the requested DPI. PDFium consumes the page's
   intrinsic ``/Rotate`` value; callers request no additional PDFium rotation.
2. Apply ``rotation_degrees`` as the *residual counter-clockwise correction*.
3. The resulting pixels define the canonical upright coordinate space.
4. Scale canonical coordinates from their source DPI to the requested DPI.
5. Crop only after rasterization, residual rotation, and coordinate scaling.

Never add ``pdf_rotation_degrees`` to ``rotation_degrees``. They describe two
different stages and combining them would rotate a page twice.
"""
from __future__ import annotations

import os
import math
from contextlib import closing
from typing import Iterable


VALID_ROTATIONS = (0, 90, 180, 270)
# Paddle's score is a model confidence, not a correctness guarantee.  A 0.90
# default deliberately prefers an unrotated, review-flagged financial page over
# applying a weak 90/180/270-degree guess. Deployments can tune it via the env
# variable below after measuring their own invoice mix.
DEFAULT_ORIENTATION_MIN_CONFIDENCE = 0.90


def orientation_min_confidence() -> float:
    raw = os.environ.get("OCR_ORIENTATION_MIN_CONFIDENCE", str(DEFAULT_ORIENTATION_MIN_CONFIDENCE))
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError("OCR_ORIENTATION_MIN_CONFIDENCE must be a number between 0 and 1") from error
    if not 0 <= value <= 1:
        raise ValueError("OCR_ORIENTATION_MIN_CONFIDENCE must be between 0 and 1")
    return value


def normalize_rotation(value: object) -> int:
    try:
        angle = int(str(value).strip().rstrip("°")) % 360
    except (TypeError, ValueError) as error:
        raise ValueError(f"Unsupported page rotation: {value!r}") from error
    if angle not in VALID_ROTATIONS:
        raise ValueError(f"Page rotation must be one of {VALID_ROTATIONS}, got {angle}")
    return angle


def choose_orientation(predicted: object, confidence: object,
                       minimum: float | None = None) -> dict[str, object]:
    """Apply only a confident classifier result; retain a rejected candidate."""
    angle = normalize_rotation(predicted)
    score = float(confidence)
    threshold = orientation_min_confidence() if minimum is None else float(minimum)
    if not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError("Orientation confidence must be between 0 and 1")
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Orientation confidence threshold must be between 0 and 1")
    if score < threshold:
        return dict(rotation_degrees=0, rotation_confidence=round(score, 6),
                    rotation_source="paddle_doc_orientation", rotation_status="uncertain",
                    rotation_candidate_degrees=angle)
    return dict(rotation_degrees=angle, rotation_confidence=round(score, 6),
                rotation_source="paddle_doc_orientation",
                rotation_status="applied" if angle else "upright")


def rotate_image(image, rotation_degrees: object):
    """Return pixels in canonical upright space using a lossless right-angle turn."""
    angle = normalize_rotation(rotation_degrees)
    if angle == 0:
        return image
    from PIL import Image

    operation = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }[angle]
    return image.transpose(operation)


def render_upright_page(pdf_page, dpi: int | float, rotation_degrees: object = 0):
    """Rasterize first, then apply the stored residual correction exactly once."""
    with closing(pdf_page.render(scale=float(dpi) / 72, rotation=0)) as bitmap:
        image = bitmap.to_pil().convert("RGB")
    return rotate_image(image, rotation_degrees)


def oriented_size(width: float, height: float, rotation_degrees: object) -> tuple[float, float]:
    return (height, width) if normalize_rotation(rotation_degrees) in (90, 270) else (width, height)


def rotate_bbox(bbox: Iterable[float], width: float, height: float,
                rotation_degrees: object) -> tuple[list[float], tuple[float, float]]:
    """Rotate an ``x0,y0,x1,y1`` box in a top-left-origin image space."""
    x0, y0, x1, y1 = (float(value) for value in bbox)
    angle = normalize_rotation(rotation_degrees)
    if angle == 0:
        rotated = [x0, y0, x1, y1]
    elif angle == 90:
        rotated = [y0, width-x1, y1, width-x0]
    elif angle == 180:
        rotated = [width-x1, height-y1, width-x0, height-y0]
    else:
        rotated = [height-y1, x0, height-y0, x1]
    return rotated, oriented_size(width, height, angle)


def scale_bbox(bbox: Iterable[float], from_dpi: int | float,
               to_dpi: int | float) -> list[float]:
    factor = float(to_dpi) / float(from_dpi)
    return [float(value) * factor for value in bbox]


def clamp_crop_bbox(bbox: Iterable[float], width: int | float,
                    height: int | float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    x0, x1 = max(0, x0), min(float(width), x1)
    y0, y1 = max(0, y0), min(float(height), y1)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Rotation-aware crop is empty")
    return round(x0), round(y0), round(x1), round(y1)


def page_orientation(page: dict) -> dict:
    """Return compact, JSON-safe orientation diagnostics for one OCR page."""
    diagnostics = {
        "page": page.get("page"),
        "pdf_rotation_degrees": page.get("pdf_rotation_degrees", 0),
        "rotation_degrees": page.get("rotation_degrees", 0),
        "rotation_confidence": page.get("rotation_confidence"),
        "rotation_source": page.get("rotation_source", "legacy_default"),
        "rotation_status": page.get("rotation_status", "not_recorded"),
    }
    if "rotation_candidate_degrees" in page:
        diagnostics["rotation_candidate_degrees"] = page["rotation_candidate_degrees"]
    if page.get("rotation_error"):
        diagnostics["rotation_error"] = str(page["rotation_error"])
    return diagnostics
