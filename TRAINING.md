# Multi-layout invoice model training

This repository contains the 111 invoice PDFs that the user authorized for public distribution. Google Drive is **not required**. Source-verified labels are committed to the public `public_invoice_labels/` folder; rendered pages and drafts are temporary in Colab; a trained adapter is published as a public GitHub Release **only if** the held-out gate passes. At present the public PDFs are not ground-truth labels, so the model has not been trained on them.

## Why verification comes before training

The supplied folder contains PDFs but no ground-truth JSON. Existing OCR output and cloud-model output are drafts, not labels. Training on unchecked drafts would teach their mistakes to the model. The workflow therefore refuses to export fewer than 80 human-verified, explicitly included documents and keeps supplier/layout groups out of more than one split. The same PDF hash also stays in one split. Validation and test must each have at least 10 documents at the default 80-label minimum.

The target is layout-independent extraction, not memorizing a supplier template. The SFT record supplies all document pages to a vision-language model and asks for the app's **full** bilingual invoice schema: seller/customer address and CR fields, supply date, all item columns, VAT summary, totals and other printed fields. The earlier v1 labels omitted many of these fields. Old v1 labels are shown as unverified when opened in the dashboard; they must be checked again against the source before v2 export. Supplier VAT and a manually assigned `layout_group` both constrain leakage-resistant train/validation/test grouping.

## Phase 1: public GitHub labels

Open [`colab_dataset.ipynb`](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_dataset.ipynb) in a GPU Colab runtime.

1. Sign in to Colab and add a fine-grained `GITHUB_TOKEN` in **Colab Secrets**. Restrict it to `ubaid-148/OCR` with **Contents: Read and write**. Never paste the token into notebook cells, Git remotes or chat. [GitHub token permissions](https://docs.github.com/en/rest/releases/releases).
2. Use a GPU runtime and run dataset cells 1-6. They clone the public repo, render the PDFs and generate OCR drafts in temporary `/content/invoice_ocr_work`. No PDF or Drive upload is needed.
3. In the dashboard, compare every field and item row with the source pages. Check seller versus customer address, all printed rows and columns, handwritten notes and dates. Use **Verify + include** only for complete ground truth. Use **Verify + exclude** for duplicates, non-invoices and unreadable sources. Leave genuinely absent fields `null`; do not trust unchecked OCR/cloud drafts.
4. After each review batch, run cell 7 to commit and push verified labels to the public repo. Do this **before** Colab disconnects. Cells 8-9 show validation progress and an optional local export preview. Arithmetic differences are warnings because printed values must not be silently replaced by calculations.

**Start training only after at least 80 verified/included v2 labels have been pushed to GitHub.** Verification of all 111 PDFs is preferable. Export also requires independent supplier/layout groups and at least 10 validation plus 10 test documents. If a group dominates, add more independent formats rather than weakening the gate.

The annotator also accepts a pasted app response, debug response, or cloud-style JSON and normalizes it into the training schema. Pasting is only a starting point; source verification is still required.

## Phase 2: adapter training and held-out evaluation

Start a fresh GPU runtime and open [`colab_train.ipynb`](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_train.ipynb). It clones the PDFs and pushed labels, rebuilds images/exports without Drive, then uses the official Qwen VL training framework with `Qwen/Qwen3-VL-2B-Instruct`, LoRA on attention projections, deterministic evaluation and SDPA for Colab compatibility. Keep `GITHUB_TOKEN` in Colab Secrets so the approved adapter can be published after training.

The notebook follows this order:

1. Rebuild the full-schema v2 export from committed labels and check that every training image/answer fits the configured model and generation token budgets. Qwen's official data collator truncates over-length sequences; training must stop before that happens. [Qwen source](https://github.com/QwenLM/Qwen3-VL/blob/main/qwen-vl-finetune/qwenvl/data/data_processor.py).
2. Evaluate the untouched base model on the validation split.
3. Train only on the train split.
4. Evaluate the adapter on validation and require a non-regression gate.
5. Only after that gate passes, run the base and adapter once on the untouched test split.
6. Reject the adapter if it regresses, emits invalid/truncated JSON, misses documents, invents more values for null targets, or misses the configured critical/exact-document thresholds. Item-row exactness is reported separately.

The adapter is **not** automatically connected to the OCR application. After a passing final test, cell 10 writes approval metadata and cell 11 publishes a ZIP containing the adapter and held-out metrics as a public GitHub Release. GitHub blocks ordinary Git files over 100 MiB; release assets avoid storing model weights in repository history. [GitHub file limits](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github), [release assets](https://docs.github.com/en/rest/releases/assets).

To use a passing adapter, open [`colab_setup.ipynb`](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb), set `USE_TRAINED_ADAPTER=True` and paste the printed `TRAINED_RELEASE_TAG`. The notebook downloads the public release, rechecks adapter and metric hashes, starts the CUDA-backed service separately from PaddleOCR, and routes Accuracy mode through it. With the option off, stock Ollama is unchanged. If the adapter fails or returns incomplete JSON, the app falls back to spatial OCR and marks `needs_review`.

A passing gate is necessary but not proof of Cloud-level accuracy or 100% accuracy on future formats; it is a held-out result on these verified formats. Until the labels are source-verified, Colab training and held-out scores have **not** happened. Production responses must continue to preserve evidence, arithmetic checks, nulls for uncertainty and `needs_review` when confidence is insufficient.

## Command-line tools

```bash
python -m training.invoice_dataset prepare --pdf-dir /content/OCR/public_invoice_pdfs --work-dir /content/invoice_ocr_work --labels-dir /content/OCR/public_invoice_labels --allow-public-pdf-dir --allow-public-labels
python -m training.invoice_dataset draft --work-dir /content/invoice_ocr_work
python -m training.invoice_dataset validate --work-dir /content/invoice_ocr_work
python -m training.invoice_dataset export-qwen --work-dir /content/invoice_ocr_work --min-verified 80
```

Training-data unit tests do not run OCR:

```bash
python -m unittest -v test_training_dataset
```
