"""Match the official Qwen trainer's image-pixel configuration in eval/serving."""
from __future__ import annotations


def load_processor(model_id: str, max_pixels: int, min_pixels: int = 12544):
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(model_id)
    image = processor.image_processor
    if hasattr(image, "min_pixels"):
        image.min_pixels = min_pixels
    if hasattr(image, "max_pixels"):
        image.max_pixels = max_pixels
    if isinstance(getattr(image, "size", None), dict):
        image.size["shortest_edge"] = min_pixels
        image.size["longest_edge"] = max_pixels
    return processor
