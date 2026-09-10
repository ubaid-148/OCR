"""Conservative native-text route; image-bearing or rotated pages use OCR."""
import math
import unicodedata
from contextlib import closing


def extract_native_words(page, dpi=200):
    if page.get_rotation() or any(obj.type == 3 for obj in page.get_objects()):
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
