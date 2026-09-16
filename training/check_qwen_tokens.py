"""Fail before training if full invoice answers would be silently truncated."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from training.invoice_dataset import PROMPT
from training.qwen_processor import load_processor


def check(dataset: Path, model_id: str, max_pixels: int, max_length: int,
          max_new_tokens: int) -> dict:
    rows = json.loads(dataset.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Training export is empty or invalid")
    processor = load_processor(model_id, max_pixels)
    lengths = []
    failures = []
    for index, row in enumerate(rows):
        images = row.get("image")
        images = [images] if isinstance(images, str) else images
        if not isinstance(images, list) or not images or any(not Path(path).is_file() for path in images):
            failures.append(f"row {index}: source image is missing")
            continue
        turns = row.get("conversations") or []
        if (len(turns) != 2 or turns[0].get("value") != "\n".join("<image>" for _ in images) + "\n" + PROMPT
                or turns[1].get("from") != "gpt"):
            failures.append(f"row {index}: prompt or answer format differs from exporter")
            continue
        answer = turns[1]["value"]
        messages = [
            {"role": "user", "content": [{"type": "image", "image": str(Path(path).resolve())} for path in images]
             + [{"type": "text", "text": PROMPT}]},
            {"role": "assistant", "content": [{"type": "text", "text": answer}]},
        ]
        encoded = processor.apply_chat_template(messages, tokenize=True, return_dict=True, return_tensors="pt")
        token_count = encoded["input_ids"].shape[-1]
        answer_count = len(processor.tokenizer(answer, add_special_tokens=False)["input_ids"])
        lengths.append(token_count)
        if token_count > max_length:
            failures.append(f"row {index}: {token_count} input tokens exceed model_max_length={max_length}")
        if answer_count > max_new_tokens:
            failures.append(f"row {index}: {answer_count} answer tokens exceed max_new_tokens={max_new_tokens}")
    report = {"documents": len(rows), "max_input_tokens": max(lengths, default=0),
              "max_length": max_length, "max_new_tokens": max_new_tokens, "failures": failures}
    if failures:
        raise ValueError(json.dumps(report, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model-id", default="Qwen/Qwen3-VL-2B-Instruct")
    parser.add_argument("--max-pixels", type=int, default=602112)
    parser.add_argument("--max-length", type=int, default=8192)
    parser.add_argument("--max-new-tokens", type=int, default=4096)
    args = parser.parse_args()
    print(json.dumps(check(args.dataset, args.model_id, args.max_pixels, args.max_length,
                           args.max_new_tokens), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
