"""Run deterministic Qwen3-VL JSON extraction on an exported split."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from training.invoice_dataset import PROMPT, normalize_data
from training.qwen_processor import load_processor


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_id(model_id: str, adapter_dir: Path | None, split: str, max_pixels: int,
            max_new_tokens: int, manifest_path: Path) -> str:
    parts = [model_id, split, str(max_pixels), str(max_new_tokens), PROMPT, _file_digest(manifest_path)]
    if adapter_dir:
        adapter_dir = adapter_dir.resolve()
        files = sorted(path for path in adapter_dir.rglob("*") if path.is_file() and path.suffix in {".json", ".bin", ".safetensors"})
        if not files:
            raise ValueError(f"No adapter files found under {adapter_dir}")
        parts.extend(f"{path.relative_to(adapter_dir)}:{_file_digest(path)}" for path in files)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.DOTALL)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("model returned a non-object JSON value")
    return normalize_data(parsed)


def run(
    manifest_path: Path,
    output_path: Path,
    split: str,
    model_id: str,
    adapter_dir: Path | None,
    max_new_tokens: int,
    max_pixels: int,
) -> None:
    import torch
    from transformers import AutoModelForImageTextToText

    try:
        from peft import PeftModel
    except ImportError:
        PeftModel = None

    rows = [row for row in _read_jsonl(manifest_path) if row.get("split") == split]
    if not rows:
        raise ValueError(f"No {split} rows in {manifest_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = _run_id(model_id, adapter_dir, split, max_pixels, max_new_tokens, manifest_path)
    completed: set[str] = set()
    if output_path.exists():
        previous = _read_jsonl(output_path)
        if any(row.get("run_id") != run_id for row in previous):
            raise ValueError(f"{output_path} belongs to another model/configuration; use a new output path")
        completed = {row.get("doc_id") for row in previous}
    processor = load_processor(model_id, max_pixels)
    model = AutoModelForImageTextToText.from_pretrained(
        model_id, torch_dtype=torch.float16, device_map="auto", attn_implementation="sdpa"
    )
    if adapter_dir:
        if PeftModel is None:
            raise RuntimeError("peft is required to load a LoRA adapter")
        model = PeftModel.from_pretrained(model, str(adapter_dir.resolve()))
    model.eval()
    for index, row in enumerate(rows, start=1):
        if row["doc_id"] in completed:
            continue
        try:
            # Match Qwen's official trainer message format: the image value is
            # an absolute local path, not an in-memory PIL object.
            content = [{"type": "image", "image": str(Path(path).resolve())} for path in row["pages"]]
            content.append({"type": "text", "text": PROMPT})
            messages = [{"role": "user", "content": content}]
            inputs = processor.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt"
            ).to(model.device)
            with torch.inference_mode():
                generated = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
            trimmed = generated[:, inputs["input_ids"].shape[-1]:]
            raw = processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
            try:
                if trimmed.shape[-1] >= max_new_tokens:
                    raise ValueError(f"generation reached max_new_tokens={max_new_tokens}; output may be truncated")
                prediction = _parse_json(raw)
                error = None
            except (ValueError, json.JSONDecodeError) as parse_error:
                prediction = None
                error = f"{type(parse_error).__name__}: {parse_error}"
            result = {"doc_id": row["doc_id"], "run_id": run_id, "prediction": prediction, "error": error, "raw": raw}
        except Exception as error:  # Keep a resumable record for every failed document.
            result = {"doc_id": row["doc_id"], "run_id": run_id, "prediction": None, "error": f"{type(error).__name__}: {error}"}
        with output_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(f"[{index}/{len(rows)}] {row['doc_id']}: {'ok' if result['prediction'] else result['error']}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", default="test", choices=("validation", "test"))
    parser.add_argument("--model-id", default="Qwen/Qwen3-VL-2B-Instruct")
    parser.add_argument("--adapter-dir", type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=3072)
    parser.add_argument("--max-pixels", type=int, default=602112)
    args = parser.parse_args()
    run(args.manifest, args.output, args.split, args.model_id, args.adapter_dir, args.max_new_tokens, args.max_pixels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
