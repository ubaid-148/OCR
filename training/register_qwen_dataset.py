"""Register private train/validation JSON files in Qwen's official trainer."""
from __future__ import annotations

import argparse
from pathlib import Path


START = "# BEGIN PRIVATE_INVOICE_DATASET (managed by OCR repo)"
END = "# END PRIVATE_INVOICE_DATASET"


def register(qwen_root: Path, train_json: Path, validation_json: Path) -> Path:
    target = qwen_root.resolve() / "qwenvl" / "data" / "__init__.py"
    if not target.exists():
        raise ValueError(f"Qwen data registry not found: {target}")
    text = target.read_text(encoding="utf-8")
    if START in text:
        before, tail = text.split(START, 1)
        _, after = tail.split(END, 1)
        text = before + after.lstrip("\r\n")
    text = text.replace("    'private_invoice_train': PRIVATE_INVOICE_TRAIN,\n", "")
    text = text.replace("    'private_invoice_validation': PRIVATE_INVOICE_VALIDATION,\n", "")
    marker = "data_dict = {"
    if marker not in text:
        raise ValueError("Unsupported Qwen trainer registry: data_dict was not found")
    block = (
        f"{START}\n"
        f"PRIVATE_INVOICE_TRAIN = {{'annotation_path': {str(train_json.resolve())!r}, 'data_path': ''}}\n"
        f"PRIVATE_INVOICE_VALIDATION = {{'annotation_path': {str(validation_json.resolve())!r}, 'data_path': ''}}\n"
        f"{END}\n\n"
    )
    text = text.replace(marker, block + marker, 1)
    text = text.replace(marker, marker + "\n    'private_invoice_train': PRIVATE_INVOICE_TRAIN,\n    'private_invoice_validation': PRIVATE_INVOICE_VALIDATION,", 1)
    target.write_text(text, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qwen-root", type=Path, required=True)
    parser.add_argument("--train-json", type=Path, required=True)
    parser.add_argument("--validation-json", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(register(args.qwen_root, args.train_json, args.validation_json))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
