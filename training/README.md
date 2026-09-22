# Invoice model training

Status: training workflow prepared; no trained adapter or accuracy improvement has
been produced yet. A CUDA training run and verified page labels are required.
Use a **fresh Colab T4 runtime**, separate from Paddle/Ollama, with
[`colab_train.ipynb`](../colab_train.ipynb).

## Sample audit

All 111 source PDFs were opened and all 115 page thumbnails visually inspected.
There are 111 distinct file hashes; 47 pages contain embedded text and 68 have no
embedded text. Embedded text is not verified ground truth. `9525.pdf` has three
pages; `9616.pdf` and `9855.pdf` each have two. `9525` includes a delivery-note page,
so document type and page-local answers matter.

Layouts include repeated supplier templates, dense ruled tables, borderless
invoices, pale scans, handwritten notes, stamps and attached payment receipts.
Examples of repeated families: Asia Orouba (`9484`, `9495`, `9605`, `9679`, `9695`),
Ahmed Al Zahrani (`9477`, `9482`, `9601`, `9602`, `9604`, `9683`, `9844`), and
Alrajhi (`9498`, `9522`). These observations are layout review, not full
transcription verification. Keep each supplier/template family in one split.
The existing `9498_user_reference.json` is a partial reference, not a complete
page training label, and is not silently promoted into training data.

## Workflow

1. Prepare: `python -m training.data prepare`. Creates page images, contact sheets,
   source hashes and draft JSON in ignored `training_workspace/review/`.
2. Optional draft generation: `python -m training.run draft --limit 5` on T4.
   Qwen3-VL suggests page-local header and item JSON. `--limit 0` processes all
   remaining pages and can take hours. This is inference, not training.
3. Open `training.review.review(workspace)` in the notebook. Use **Load model
   draft**, compare with the page, correct fields, set a consistent layout group
   and reviewer, then save. Rotate pages if needed. Only mark a page verified
   after every readable field, row, footer, stamp and note was checked.
   Use `other_fields` for additional labelled text and `handwritten_notes` for
   annotations. Do not interpret payment scribbles or fix incorrect printed
   arithmetic. `cr_number` is the internal vision key. Preserve IDs as strings.
   If text is unreadable, keep the page as draft; do not train invented answers.
4. Export: `python -m training.data export`. Requires at least three reviewed
   layout groups so train/validation/test are disjoint. This minimum enables a
   smoke test; it is not evidence that the dataset is sufficient. Aim to review
   the whole collection. Hash mismatches, inconsistent groups within a PDF,
   duplicate pages and invalid targets are rejected. Inspect `split_summary.json`.
5. Baseline: `python -m training.run evaluate --output model_artifacts/base`.
6. Train: `python -m training.run train`. Qwen3-VL-4B-Instruct, 4-bit NF4 base,
   FP16 compute, language-attention LoRA rank 8, batch 1, accumulation 8, two
   epochs, checkpointing. Vision encoder is frozen for this first T4 experiment.
   Training loss covers assistant answers only. Inputs exceeding the token limit
   fail explicitly rather than dropping rows. T4 memory/throughput remains to be
   validated; reduce image pixels or review page crops if a run exhausts memory.
7. Evaluate the adapter on the same test set:
   `python -m training.run evaluate --adapter model_artifacts/invoice-qwen3-vl/adapter --output model_artifacts/tuned`.
   Compare field accuracy, missing/extra fields, valid JSON, row counts and timing.
   Exact field scoring intentionally includes Arabic spelling; it is stricter
   than numeric-only matching. Keep labels unchanged between baseline and tuned runs.
8. Save the workspace, adapter and checkpoints before Colab disconnects. The
   notebook has download cells; no Drive mount or external upload is automatic.
   Resume with `--resume /path/to/checkpoint-N`.

No model weights, rendered invoices, or predictions are committed to Git. The
adapter does not replace Ollama's `qwen3-vl:4b` automatically: test it through the
provided Transformers evaluator first. Production integration/export requires a
separate validated step. Existing OCR, arithmetic checks and review flags remain
necessary; no training set guarantees zero missing fields on arbitrary invoices.

## Implementation references

[Qwen3-VL model](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct),
[PEFT quantized training](https://huggingface.co/docs/peft/developer_guides/quantization).
The training dependencies are isolated in `training/requirements.txt`; the OCR
notebook and its dependencies are unchanged.
