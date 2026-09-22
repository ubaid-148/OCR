"""T4-oriented 4-bit Qwen3-VL LoRA training, drafting and held-out evaluation.

Run in a fresh CUDA environment; importing this module does not load a model.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from time import perf_counter

from training.generation import GenerationMonitor, complete_object
from training.draft_checks import draft_warnings
from training.data import digest, export, instruction, write_json, validate_schema, HEADER_SCHEMA, ITEMS_SCHEMA

BASE_MODEL = "Qwen/Qwen3-VL-4B-Instruct"


def load_runtime(adapter=None):
    import torch
    from transformers import AutoProcessor, BitsAndBytesConfig, Qwen3VLForConditionalGeneration
    if not torch.cuda.is_available():
        raise RuntimeError("A CUDA GPU is required. Select a T4 in a fresh Colab runtime.")
    print("Loading Qwen3-VL weights (first run may download)...", flush=True)
    load_started = perf_counter()
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        BASE_MODEL, quantization_config=BitsAndBytesConfig(load_in_4bit=True,
            bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16),
        torch_dtype=torch.float16, device_map={"": 0}, attn_implementation="sdpa")
    processor = AutoProcessor.from_pretrained(BASE_MODEL, min_pixels=256*32*32, max_pixels=768*32*32)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter)
    print(f"Model ready on {torch.cuda.get_device_name(0)} after {perf_counter()-load_started:.1f}s", flush=True)
    return model, processor


def messages(row, answer=False):
    result = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": row["prompt"]}]}]
    if answer:
        result.append({"role": "assistant", "content": [{"type": "text", "text": row["answer"]}]})
    return result


def read_image(workspace, row):
    from PIL import Image
    path = (Path(workspace) / row["image"]).resolve()
    if not path.is_relative_to(Path(workspace).resolve()) or digest(path) != row["image_sha256"]:
        raise ValueError("Image path/hash changed; rebuild reviewed dataset")
    with Image.open(path) as source:
        return source.convert("RGB").rotate(row["rotation_ccw"], expand=True)


def encode(processor, workspace, row, answer=False):
    text = processor.apply_chat_template(messages(row, answer), tokenize=False, add_generation_prompt=not answer)
    return processor(text=[text], images=[read_image(workspace, row)], return_tensors="pt", padding=False)


class ResponseCollator:
    def __init__(self, processor, workspace, max_tokens=4096):
        self.processor, self.workspace, self.max_tokens = processor, workspace, max_tokens

    def __call__(self, rows):
        # Batch size 1 keeps variable-resolution image grids aligned on a T4.
        if len(rows) != 1:
            raise ValueError("Vision training uses per_device_batch_size=1")
        import torch
        row = rows[0]
        batch = encode(self.processor, self.workspace, row, answer=True)
        prompt = encode(self.processor, self.workspace, row, answer=False)
        length = prompt["input_ids"].shape[1]
        if batch["input_ids"].shape[1] > self.max_tokens:
            raise ValueError(f"{row['pdf']} page {row['page']} exceeds token budget; no target was truncated")
        if not torch.equal(batch["input_ids"][:, :length], prompt["input_ids"]):
            raise ValueError("Chat template prefix mismatch; refusing incorrect response masking")
        labels = batch["input_ids"].clone()
        labels[:, :length] = -100
        if not (labels != -100).any():
            raise ValueError("No assistant tokens available for training")
        batch["labels"] = labels
        return batch


def rows_for(workspace, split):
    return [json.loads(line) for line in (Path(workspace)/f"{split}.jsonl").read_text().splitlines() if line]


def train(args):
    # Rebuild from verified, hash-bound labels; never trust stale exported rows.
    summary = export(args.workspace, args.pdf_dir)
    dataset_hashes = {split: digest(Path(args.workspace)/f"{split}.jsonl")
                      for split in ("train", "validation", "test")}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "dataset_hashes.json"
    if args.resume and (not manifest.exists() or json.loads(manifest.read_text()) != dataset_hashes):
        raise ValueError("Checkpoint dataset changed or manifest missing; start a new experiment")
    write_json(manifest, dataset_hashes)
    model, processor = load_runtime()
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import Trainer, TrainingArguments, set_seed
    set_seed(3407)
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
                                             gradient_checkpointing_kwargs={"use_reentrant": False})
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, lora_dropout=.05,
        target_modules=r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj)$",
        bias="none", task_type="CAUSAL_LM"))
    model.config.use_cache = False
    model.print_trainable_parameters()
    collator = ResponseCollator(processor, args.workspace, args.max_tokens)
    train_rows, val_rows = rows_for(args.workspace, "train"), rows_for(args.workspace, "validation")
    # Fail before a long run if a target is too long or template masking differs.
    for row in train_rows + val_rows:
        collator([row])
    settings = TrainingArguments(output_dir=args.output, per_device_train_batch_size=1,
        per_device_eval_batch_size=1, gradient_accumulation_steps=8,
        num_train_epochs=args.epochs, learning_rate=1e-4, warmup_ratio=.1,
        fp16=True, bf16=False, optim="adamw_bnb_8bit", gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=1, save_strategy="epoch", eval_strategy="epoch", save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="eval_loss",
        greater_is_better=False, remove_unused_columns=False, label_names=["labels"],
        report_to="none", seed=3407, dataloader_num_workers=0)
    trainer = Trainer(model=model, args=settings, train_dataset=train_rows,
                      eval_dataset=val_rows, data_collator=collator)
    result = trainer.train(resume_from_checkpoint=args.resume or None)
    adapter_dir = Path(args.output)/"adapter"
    trainer.save_model(adapter_dir)
    processor.save_pretrained(adapter_dir)
    write_json(Path(args.output)/"training_result.json", {
        "base_model": BASE_MODEL, "splits": summary, "metrics": result.metrics,
        "gpu": torch.cuda.get_device_name(0), "max_tokens": args.max_tokens,
        "max_memory_gb": torch.cuda.max_memory_allocated()/1024**3,
        "status": "trained_requires_held_out_evaluation"})
    print("Adapter saved:", adapter_dir)


def generate(model, processor, workspace, row, max_tokens, max_seconds=120, diagnostics=None):
    import torch
    from transformers import StoppingCriteria, StoppingCriteriaList
    encode_started = perf_counter()
    inputs = encode(processor, workspace, row).to("cuda")
    input_seconds = perf_counter() - encode_started
    prompt_length = inputs["input_ids"].shape[1]
    monitor = GenerationMonitor(max_seconds)
    started = perf_counter()

    class StopDraft(StoppingCriteria):
        def __call__(self, input_ids, scores, **kwargs):
            count = input_ids.shape[1] - prompt_length
            elapsed = perf_counter() - started
            # Decode periodically, or at the deadline. This is batch-size one.
            text = processor.decode(input_ids[0, prompt_length:], skip_special_tokens=True) if count % 16 == 0 or elapsed >= max_seconds else ""
            stop = monitor.check(text, count, elapsed)
            return torch.full((input_ids.shape[0],), stop, dtype=torch.bool, device=input_ids.device)

    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False,
                                use_cache=True, stopping_criteria=StoppingCriteriaList([StopDraft()]))
    continuation = output[0, prompt_length:]
    raw = processor.decode(continuation, skip_special_tokens=True)
    completed = complete_object(raw)
    elapsed = perf_counter() - started
    reason = monitor.reason or ("json_complete" if completed else
                                "token_limit" if len(continuation) >= max_tokens else "eos")
    if diagnostics is not None:
        diagnostics.update(generated_tokens=len(continuation), token_limit=max_tokens,
                           stop_reason=reason, input_seconds=round(input_seconds, 3),
                           raw_text=raw, generation_seconds=round(elapsed, 3))
    return completed or raw, elapsed


def draft(args):
    paths = sorted((Path(args.workspace)/"labels").glob("*.json"))
    selected = []
    for path in paths:
        record = json.loads(path.read_text())
        if args.pdf and record.get("pdf") != args.pdf:
            continue
        if record.get("status") == "verified":
            continue
        if not args.force and record.get("draft_model") and not record.get("draft_errors"):
            continue
        selected.append(path)
    if not selected:
        print("No matching unverified pages to draft. No model loaded.", flush=True)
        return
    model, processor = load_runtime()
    model.eval()
    paths = selected
    done = 0
    for path in paths:
        record = json.loads(path.read_text())
        candidate, errors = {}, []
        for scope in ("header", "items"):
            print(f"{path.name}: reading {scope}...", flush=True)
            row = dict(record, scope=scope, prompt=instruction(scope, record["page"], record["page_count"]))
            metrics = {}
            budget = min(args.max_tokens, args.header_tokens if scope == "header" else args.item_tokens)
            text, elapsed = generate(model, processor, args.workspace, row, budget,
                                     args.generation_seconds, diagnostics=metrics)
            print(f"{path.name}: {scope} {elapsed:.1f}s; {metrics['generated_tokens']} tokens; {metrics['stop_reason']}", flush=True)
            write_json(path.with_suffix(f".{scope}.prediction"), {"text": text, "seconds": elapsed, **metrics})
            try:
                if metrics["stop_reason"] in {"time_limit", "token_limit"}:
                    raise ValueError(f"Incomplete output: {metrics['stop_reason']} after {metrics['generated_tokens']} tokens")
                parsed = json.loads(text)
                validate_schema(parsed, HEADER_SCHEMA if scope == "header" else ITEMS_SCHEMA)
                candidate.update(parsed)
            except ValueError as error:
                errors.append(f"{scope}: {error}")
        # Preserve manual draft edits; generated candidates are always available alongside.
        record["suggested_target"] = candidate
        record["draft_model"] = BASE_MODEL
        record["draft_errors"] = errors
        record["draft_warnings"] = draft_warnings(candidate)
        write_json(path, record)
        print(path.name, "draft saved for review", errors, flush=True)
        done += 1
        if args.limit and done >= args.limit:
            break


def flatten(value, prefix=""):
    if isinstance(value, dict):
        return {k: v for key, child in value.items() for k, v in flatten(child, f"{prefix}.{key}" if prefix else key).items()}
    if isinstance(value, list):
        return {k: v for i, child in enumerate(value) for k, v in flatten(child, f"{prefix}[{i}]").items()}
    return {prefix: value}


def evaluate(args):
    export(args.workspace, args.pdf_dir)
    if args.adapter:
        manifest = Path(args.adapter).parent / "dataset_hashes.json"
        current = {split: digest(Path(args.workspace)/f"{split}.jsonl")
                   for split in ("train", "validation", "test")}
        if not manifest.exists() or json.loads(manifest.read_text()) != current:
            raise ValueError("Evaluation labels/splits differ from this adapter's training dataset")
    model, processor = load_runtime(args.adapter)
    model.eval()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows_for(args.workspace, "test"):
        text, seconds = generate(model, processor, args.workspace, row, args.max_tokens)
        truth = flatten(json.loads(row["answer"]))
        expected = {k: v for k, v in truth.items() if v is not None and v != ""}
        try:
            parsed = json.loads(text)
            validate_schema(parsed, HEADER_SCHEMA if row["scope"] == "header" else ITEMS_SCHEMA)
            prediction, valid = flatten(parsed), True
        except ValueError:
            parsed, prediction, valid = {}, {}, False
        results.append({"pdf": row["pdf"], "page": row["page"], "scope": row["scope"],
                        "json_valid": valid, "expected_fields": len(expected),
                        "correct_fields": sum(prediction.get(k) == v for k, v in expected.items()),
                        "missing_fields": [k for k in expected if prediction.get(k) in (None, "")],
                        "extra_fields": [k for k, v in prediction.items() if v not in (None, "") and k not in expected],
                        "expected_rows": len(json.loads(row["answer"]).get("items", [])),
                        "predicted_rows": len(parsed.get("items", [])),
                        "seconds": seconds, "prediction": text})
        write_json(output/"evaluation.json", {"base_model": BASE_MODEL, "adapter": args.adapter,
            "results": results, "complete": False})
    fields = sum(r["expected_fields"] for r in results)
    write_json(output/"evaluation.json", {"base_model": BASE_MODEL, "adapter": args.adapter,
        "complete": True, "field_exact_accuracy": sum(r["correct_fields"] for r in results)/fields if fields else None,
        "missing_field_count": sum(len(r["missing_fields"]) for r in results), "results": results})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("draft", "train", "evaluate"))
    parser.add_argument("--workspace", default="training_workspace/review")
    parser.add_argument("--pdf-dir", default="public_invoice_pdfs")
    parser.add_argument("--output", default="model_artifacts/invoice-qwen3-vl")
    parser.add_argument("--adapter")
    parser.add_argument("--resume")
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--pdf", help="Draft only this filename, e.g. 9479.pdf")
    parser.add_argument("--force", action="store_true", help="Refresh suggestions; never overwrite verified labels or manual targets")
    parser.add_argument("--header-tokens", type=int, default=1024)
    parser.add_argument("--item-tokens", type=int, default=2048)
    parser.add_argument("--generation-seconds", type=float, default=120)
    parser.add_argument("--limit", type=int, default=1, help="Draft pages per run; 0 = all")
    args = parser.parse_args()
    if min(args.max_tokens, args.header_tokens, args.item_tokens, args.generation_seconds) <= 0 or args.limit < 0:
        parser.error("Token/time limits must be positive; page limit must be nonnegative")
    {"draft": draft, "train": train, "evaluate": evaluate}[args.action](args)


if __name__ == "__main__":
    main()
