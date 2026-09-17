"""Conservative native-text route; image-bearing or rotated pages use OCR."""
import math
import unicodedata
from contextlib import closing


def native_text_rotation(page, tolerance_degrees=8):
    """Return the dominant text-object angle, or ``None`` when it is unsafe.

    Native extraction is permitted only for text whose baseline is established
    as upright. Pages with intrinsic PDF rotation stay on the raster route, where
    PDFium consumes ``/Rotate`` before residual visual orientation is classified.
    """
    if page.get_rotation():
        return None
    angles = []
    try:
        objects = list(page.get_objects())
    except Exception:
        return None
    if any(obj.type == 3 for obj in objects):
        return None
    for obj in objects:
        if obj.type != 1:
            continue
        try:
            points = obj.get_quad_points()
        except Exception:
            continue
        if len(points) < 2:
            continue
        dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
        if math.hypot(dx, dy) <= 0:
            continue
        raw = math.degrees(math.atan2(dy, dx)) % 360
        nearest = min((0, 90, 180, 270), key=lambda value: min(abs(raw-value), 360-abs(raw-value)))
        distance = min(abs(raw-nearest), 360-abs(raw-nearest))
        if distance <= tolerance_degrees:
            angles.append((nearest, math.hypot(dx, dy)))
    if not angles:
        return None
    weights = {angle: sum(weight for candidate, weight in angles if candidate == angle)
               for angle in (0, 90, 180, 270)}
    angle = max(weights, key=weights.get)
    return angle if weights[angle] >= sum(weights.values()) * .8 else None


def extract_native_words(page, dpi=200):
    # Do not silently call a /Rotate=0 page upright: text object baselines must
    # independently agree. Sideways or ambiguous text goes through raster OCR.
    if native_text_rotation(page) != 0:
        return []
    left, bottom, right, top = page.get_bbox()
    if left or bottom:
        return []  # Keep cropped-page transforms on the raster route.
    scale = dpi / 72
    words = []
    with closing(page.get_textpage()) as textpage:
        for index in range(textpage.count_rects()):
            x0, y0, x1, y1 = textpage.get_rect(index)
            text = textpage.get_text_bounded(x0, y0, x1, y1).strip()
            if not text:
                continue
            if (not all(math.isfinite(v) for v in (x0, y0, x1, y1))
                    or not (0 <= x0 < x1 <= right and 0 <= y0 < y1 <= top)):
                return []
            if any(unicodedata.category(c) in {"Co", "Cs", "Cn"} or c == "\ufffd" for c in text):
                return []
            words.append(dict(text=text, confidence=None, source="native_text",
                              left=x0*scale, top=(top-y1)*scale,
                              width=(x1-x0)*scale, height=(y1-y0)*scale,
                              polygon=[[x0*scale, (top-y1)*scale], [x1*scale, (top-y1)*scale],
                                       [x1*scale, (top-y0)*scale], [x0*scale, (top-y0)*scale]]))
    text = " ".join(w["text"] for w in words)
    if len(words) < 4 or sum(c.isalnum() for c in text) < 40:
        return []
    return words
