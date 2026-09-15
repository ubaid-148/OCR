"""Adapt Qwen's official trainer to SDPA/FP16 for a single Colab GPU.

The upstream training logic and dataset processor remain unchanged. This only
removes the unconditional FlashAttention import and makes --fp16 load FP16
weights, which is required for a 16 GB runtime.
"""
from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "# OCR_COLAB_SDPA_PATCH_V1"


def patch(qwen_root: Path) -> Path:
    target = qwen_root.resolve() / "qwenvl" / "train" / "train_qwen.py"
    if not target.exists():
        raise ValueError(f"Qwen training entry point not found: {target}")
    text = target.read_text(encoding="utf-8")
    if MARKER in text:
        return target
    import_line = "from trainer import replace_qwen2_vl_attention_class"
    if import_line not in text or 'train(attn_implementation="flash_attention_2")' not in text:
        raise ValueError("Unsupported upstream Qwen trainer; inspect changes before training")
    text = text.replace(import_line, f"{MARKER}\ndef replace_qwen2_vl_attention_class():\n    return None", 1)
    old_dtype = "dtype=(torch.bfloat16 if training_args.bf16 else None),"
    new_dtype = "dtype=(torch.bfloat16 if training_args.bf16 else torch.float16 if training_args.fp16 else None),"
    if old_dtype not in text:
        raise ValueError("Expected upstream dtype expression was not found")
    text = text.replace(old_dtype, new_dtype)
    text = text.replace('train(attn_implementation="flash_attention_2")', 'train(attn_implementation="sdpa")', 1)
    target.write_text(text, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(patch(args.qwen_root))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
