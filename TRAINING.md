# Multi-layout invoice model training

This repository now contains a private, source-verified training workflow. It does **not** contain invoice PDFs, annotations, predictions, or model weights. Keep all of those in a private Google Drive directory.

## Why verification comes before training

The supplied folder contains PDFs but no ground-truth JSON. Existing OCR output and cloud-model output are drafts, not labels. Training on unchecked drafts would teach their mistakes to the model. The workflow therefore refuses to export fewer than 80 human-verified, explicitly included documents and keeps supplier/layout groups out of more than one split.

The target is layout-independent extraction, not memorizing a supplier template. The SFT record supplies all document pages to a vision-language model and asks for one stable bilingual invoice schema. Supplier VAT or a manually assigned `layout_group` controls leakage-resistant train/validation/test grouping.

## Phase 1: private dataset

Open [`colab_dataset.ipynb`](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_dataset.ipynb) in a GPU Colab runtime.

1. Copy the local PDF folder to a **private** Google Drive folder.
2. Set `PDF_DIR`, `WORK_DIR`, and `VERIFIED_BY` in the configuration cell.
3. Prepare page images and generate resumable OCR drafts. Drafting uses the existing spatial pipeline in Fast mode; it never marks output as verified.
4. In the annotation dashboard, compare every field and every item row with the displayed source pages. Use **Verify + include** only for complete ground truth. Use **Verify + exclude** for duplicates, non-invoices, irrecoverably obscured documents, or documents whose source cannot be read reliably.
5. Run validation and export. Arithmetic differences are warnings because printed source values must not be silently replaced with calculated values.

The annotator also accepts a pasted app response, debug response, or cloud-style JSON and normalizes it into the training schema. Pasting is only a starting point; source verification is still required.

## Phase 2: adapter training and held-out evaluation

Start a fresh GPU runtime and open [`colab_train.ipynb`](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_train.ipynb). It uses the official Qwen VL training framework with `Qwen/Qwen3-VL-2B-Instruct`, a frozen vision encoder, LoRA on attention projections, deterministic evaluation, and SDPA for Colab compatibility.

The notebook follows this order:

1. Evaluate the untouched base model on the validation split.
2. Train only on the train split.
3. Evaluate the adapter on validation and require a non-regression gate.
4. Only after that gate passes, run the base and adapter once on the untouched test split.
5. Reject the adapter if it regresses, emits invalid JSON, invents more values for null targets, or misses the configured critical/exact-document thresholds.

The adapter is not automatically connected to the OCR application. Deployment is a separate step after its held-out report passes. Even a passing report measures this private sample only; it does not prove 100% accuracy on every future format. Production responses must continue to preserve evidence, arithmetic checks, nulls for uncertainty, and `needs_review` when confidence is insufficient.

## Command-line tools

```bash
python -m training.invoice_dataset prepare --pdf-dir /private/pdfs --work-dir /private/work
python -m training.invoice_dataset draft --work-dir /private/work
python -m training.invoice_dataset validate --work-dir /private/work
python -m training.invoice_dataset export-qwen --work-dir /private/work --min-verified 80
```

Training-data unit tests do not run OCR:

```bash
python -m unittest -v test_training_dataset
```

