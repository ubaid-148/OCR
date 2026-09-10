"""Text-based receipt isolation; bounds are conservative, not proof of occlusion."""
import re
from invoice_formatter import center, contains


def receipt_region(words, width=None, height=None):
    width = width or max((w.get('left', 0)+w.get('width', 0) for w in words), default=1)
    height = height or max((w.get('top', 0)+w.get('height', 0) for w in words), default=1)
    strong = [w for w in words if center(w)[1]<height*.4 and
              (contains(w.get('text',''),('mada','مدى')) or re.search(r'\d{3,}[*+]{3,}\d+',w.get('text','')))]
    if len(strong) < 2:
        return None
    cx=sum(center(w)[0] for w in strong)/len(strong)
    anchors=strong+[w for w in words if center(w)[1]<height*.4 and abs(center(w)[0]-cx)<width*.2 and contains(w.get('text',''),('purchase','شراء'))]
    x0 = max(0, min(w['left'] for w in anchors)-width*.025)
    x1 = min(width, max(w['left']+w['width'] for w in anchors)+width*.025)
    if x1-x0 > width*.55:
        return None
    y1 = min(height, max(w['top']+w['height'] for w in anchors)+height*.025)
    return [x0, 0, x1, y1]


def invoice_words(page):
    words = page.get('words', [])
    dpi = page.get('render_dpi',200)/72
    region = page.get('receipt_region') or receipt_region(words,
                 page.get('width',0)*dpi or None, page.get('height',0)*dpi or None)
    if not region:
        return list(words), None
    x0,y0,x1,y1 = region
    return [w for w in words if not (x0 <= center(w)[0] <= x1 and y0 <= center(w)[1] <= y1)], region
