# Test the updated build in Google Colab

## Run from GitHub

1. Open [the repository notebook in Colab](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb).
2. Start a fresh runtime, select **T4 GPU**, and run from cell 1 to load the latest
   published code. An already running session can retain older Python modules.
3. Let setup finish, keep `EXTRACTION_MODE` set to `auto`, and upload your PDFs in
   the extraction cell. Each PDF produces its own result.
4. Enable `DOWNLOAD_DIAGNOSTICS` for problematic PDFs and compare the JSON with
   the source pages, including review notes and `unmapped_text`.

## Alternative: upload a local code bundle

Build the two files under `dist/` with `python -m tools.build_colab_bundle`:

1. Open `Invoice_OCR_Colab.ipynb` in Colab using **File → Upload notebook**.
2. Choose **Runtime → Change runtime type → T4 GPU** and run all cells.
3. At step 1, upload `invoice-ocr-code.zip`. This contains the current workspace
   fixes, independently of GitHub main. The loader verifies file checksums and
   prints the exact build identifier.
4. Let step 2 install OCR and local vision. Initial model downloads take time.
5. At step 3, upload your invoice PDFs. Each result is processed separately and
   saved/downloaded. Enable `DOWNLOAD_DIAGNOSTICS` when testing a problematic PDF.

The bundle notebook tests the workspace snapshot packaged in its matching ZIP;
the regular GitHub notebook downloads the published `main` branch.

## What this build adds

- Page-specific focused image rereads when the first vision pass misses invoice
  identifiers/dates/party names, financial totals or item fields/rows.
- Higher-resolution original-page context plus upper/lower crops. Rereads only
  fill missing values; conflicting non-empty readings remain flagged.
- Item rereads require unique, ordered item-code anchors (or descriptions when
  neither reading has codes). Duplicate/reordered rows are not merged by index.
- A truncated vision response gets at most one larger generation budget, capped
  at 8192 tokens and half the configured context. Truncated JSON is rejected even
  after retry. This improves bounded long-table handling; it is not unlimited
  table pagination or proof that every row was read.
- The prior mapping, multi-PDF, partial-page fallback and `unmapped_text` fixes.

`unmapped_text` includes labels/logos as well as potentially missed values. Its
presence does not establish which business fields are missing. Review notes and
financial checks remain authoritative signals to inspect the source. No automatic
source verification of every name, address or handwritten value is claimed.

## Local checks and limits

258 regression tests and browser upload queue checks pass. The bundle loader is
executed in tests, verifies extracted source hashes, and rejects traversal paths.
The prepared OCR parser smoke test is also run from an extracted bundle. No live
Paddle or Ollama inference was run locally; GPU extraction accuracy and latency
must be tested in Colab. Source-image recovery is not a universal accuracy guarantee.

Build another matching notebook/archive after changing code:

```sh
python -m tools.build_colab_bundle
```

To limit additional image rereads, set `VISION_RECOVERY=false` in the extraction
process environment. Fast mode skips vision altogether. Failed rereads retain the
initial extraction and a review reason instead of fabricating values.
