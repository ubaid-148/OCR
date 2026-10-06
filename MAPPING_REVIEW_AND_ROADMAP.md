# Invoice Extraction: Mapping Review, Changes and Roadmap to Model Training

_Prepared 2026-10-05. Covers the local Fast-mode pipeline (PaddleOCR followed by rule-based mapping)._

## 1. Summary

We ran 10 sample sale invoices through the app and checked every printed field against the source PDF. For each wrong or missing field we asked one question: did **OCR** fail to read the text, or did the **mapper** fail to put correctly read text into the right JSON field?

- **The mapper was the main problem.** It caused about 80% of the errors. In most failures, PaddleOCR had read the value correctly.
- **After the mapping fixes, correct fields rose from 122 to 197 out of 223** (55% → 88%). Mapping failures dropped from 81 to 6. The OCR code and settings were not changed.
- **Rules alone can't handle every invoice layout.** Every new wording or layout needs new code. The way forward is a local model that proposes the mapping, with the existing rules checking every value it returns, and then fine-tuning that model on our own reviewed invoices.

## 2. Setup and how we measured

**Environment**

| Item | Setting |
|---|---|
| Python | 3.10 in `.paddle-venv` (the README asks for 3.11+, but 3.10 works) |
| OCR | PaddleOCR 3.7 / PaddlePaddle 3.3.1 on CPU, mobile models, 200 DPI |
| Tesseract / OCRmyPDF | Not needed for the web app's JSON output |
| Mode tested | Fast (OCR plus rules). Accuracy mode (Qwen3-VL) needs a GPU and runs only in Colab. |

**Samples.** Ten invoices chosen for layout variety: 9480, 9484, 9498, 9522, 9525 (3 pages, including a delivery note), 9605 and 9616 (card receipt stuck over the header), 9660, 9844 (unreliable embedded text layer), and 9866 (seller and customer side by side).

**Method**

1. We read each PDF by eye and wrote down every printed field: 223 fields in total.
2. We saved the raw PaddleOCR output and the parsed JSON for each invoice.
3. A script classified every field into one of three outcomes:
   - **Correct:** the extracted value matches the document.
   - **Mapping failure:** the value is present in the raw OCR text, but it ended up in no field or the wrong one.
   - **OCR failure:** the value was misread or never detected.

   Doubtful cases were checked by hand. For example, an ID with one wrong digit counts as an OCR misread, not a mapping miss.

Because the raw OCR is saved, every mapping change can be re-scored in seconds without re-running OCR.

## 3. Findings (before changes)

| Result | Fields | Share |
|---|---|---|
| Correct | 122 | 55% |
| Mapping failure (OCR had the value) | 81 | 36% |
| OCR failure | 20 | 9% |

### Mapping issues

| # | Issue | Example |
|---|---|---|
| M1 | Labelled address/ID grids ignored (CR, building, street, district, city, post code, additional no., short address) | Alrajhi 9498/9522, Arcon 9866 |
| M2 | Side-by-side seller and customer blocks mixed up | 9866: customer name was the seller's name |
| M3 | Unfamiliar table layout, so no items at all | 9616: 0 items, no totals |
| M4 | Totals whose label wasn't recognised were dropped ("Total (SAR)", "Balance Due", "Total Price Excl VAT") | 9525, 9660, 9844, 9866 |
| M5 | Amount in words and the VAT-summary box never filled | 7 invoices |
| M6 | Handwritten received-date chosen over the printed date | 9525 |
| M7 | Very low-confidence OCR used as a value | 9844: a tick mark read as quantity "67" at 19% confidence |
| M8 | Item descriptions dropped or in the wrong word order | 9844: all 12 missing; 9605 scrambled |
| M9 | Wrong candidate chosen for invoice number or payment method | 9866: "819" instead of "8109"; 9616: "SPAN" from the receipt |
| M10 | Bank details mis-parsed | 9525: bank name = "Name", IBAN missing |
| M11 | Printed fields with no place in the JSON format | Salesman, delivery-order no., PO no., contact person, round-off |

### OCR issues (not yet addressed)

| # | Issue | Example |
|---|---|---|
| O1 | Small or light grid text never detected | 9498: entire customer grid missing |
| O2 | Digit misreads in IDs that still look valid | 9480: customer VAT `…5200003` instead of `…5100003` |
| O3 | Arabic names and descriptions garbled or truncated | 9498, 9522 descriptions |
| O4 | Neighbouring text merged into one line | printed and handwritten dates merged |
| O5 | Numbers cut by table borders | 9844: unit price `700` instead of `2700` |

The OCR configuration is a likely factor: `eng+ara` uses only the Arabic mobile recognition model (English text is read by the Arabic model), at 200 DPI.

## 4. Changes made (mapper only)

No OCR code, model or setting was changed. The new code never overwrites a value another step already found; it only fills empty fields. Any value that is weak, reconciled from the arithmetic, or conflicting is flagged in `needs_review` with a reason.

| Issue | Change | Where |
|---|---|---|
| M1, M2 | New label-grid extractor. It finds the seller and customer sections (stacked or side by side), pairs each English/Arabic label with the value on the same row, checks the value's type, and builds the address from the grid. | `party_fields.py` (new) |
| M3 | Table headers printed over two lines are joined, only as a retry when no table was found. A combined "code / description" cell is split. | `layout_invoice.py` |
| M4 | Missing totals are filled only from a printed amount that makes subtotal − discount + VAT = net hold to the cent. | `totals_reconcile.py` (new) |
| M5 | Amount in words (English and Arabic) is converted back to a number and checked against the net total. The VAT-summary row is now stored. | `amount_words.py` (new), `layout_invoice.py` |
| M6 | Dates like `11-Mar-2026` and `19.05.2026` are recognised; the date nearest its label wins over handwriting. | `layout_invoice.py` |
| M7 | Item numbers read below 50% confidence are left empty and flagged. | `local_ai_parser.py` |
| M8 | When one of the two internal parsers is chosen, empty header fields and item descriptions are filled from the other instead of discarded. Each line of a mixed Arabic/English description is ordered by its own script. | `local_ai_parser.py`, `layout_invoice.py` |
| M9 | A weak invoice-number reading that only dropped digits yields to a confident one. Payment words and "Terms" labels added. SPAN card receipts are detected. | `label_value_pairing.py`, `layout_invoice.py`, `document_regions.py` |
| M10 | Bank label patterns fixed; SWIFT added. | `invoice_details.py` |

### Results by invoice

| Invoice | Fields | Before | After |
|---|---|---|---|
| 9480 | 17 | 14 | 14 |
| 9484 | 23 | 21 | 23 |
| 9498 | 33 | 13 | 24 |
| 9522 | 27 | 16 | 24 |
| 9525 | 17 | 11 | 17 |
| 9605 | 13 | 10 | 13 |
| 9616 | 22 | 3 | 17 |
| 9660 | 21 | 14 | 21 |
| 9844 | 18 | 9 | 13 |
| 9866 | 32 | 11 | 31 |
| **Total** | **223** | **122** | **197** |

The 26 fields still wrong break down as:
- **20 OCR failures**, unchanged by design: this phase didn't touch OCR.
- **6 mapping cases:**
  - Two on 9616 have no usable label.
  - Four on 9844 are mostly OCR problems in disguise.

The test suite has no new failures. Six tests failed before this work and still do, for unrelated reasons. All outputs pass the response schema validator.

### Known limits of these changes

- **Small sample.** Ten invoices could hide overfitting. The rules must be re-scored on the remaining ~100 sample PDFs and on new suppliers.
- **Label dictionaries.** The mapper only recognises label wordings it has been given. A new wording will still be missed.
- **OCR-typo entries.** A few entries match OCR misspellings seen in these samples (`الجي`, `المريد`, `ربال`, `النهاني`, `القيمة الضريية`). They should be replaced by fuzzy label matching.
- **Saudi-specific checks.** The validators (15-digit VAT, 10-digit CR, SAR/halala words) are ZATCA domain rules, not template rules. Other countries need their own.
- **No unit tests yet** for the new modules.
- **The scoring harness isn't versioned.** It lives in the git-ignored `benchmark_outputs/sample_review/`.

## 5. Why rules alone are not enough

A rule-based mapper decides field meaning from hand-written label lists and layout rules. That makes it fast, predictable and runnable on a CPU. But every unseen wording or layout needs new code, which is the template-by-template growth we want to avoid.

A language model already understands that "VAT No.", "Tax Reg. #", "TRN" and "الرقم الضريبي" mean the same thing, and can read layouts it has never seen. The risk is that it can invent values. So the target design gives each part the job it does best:

```
OCR boxes (+ page image)
        │
        ▼
Local model proposes the full JSON      ← flexible: understands labels and layouts
        │
        ▼
Rule-based checker verifies each value  ← strict: proves each value is real
  • value appears in an OCR box or the image region
  • correct shape (VAT, CR, dates, amounts)
  • arithmetic holds (items → subtotal → VAT → net; amount in words)
  • failure → field left empty + needs_review
        │
        ▼
Final JSON
```

Most of what this phase built carries over into the checker: the type checks, totals reconciliation, amount-in-words check and evidence tracing. When no GPU is available, the current rules remain the fallback.

The repository already contains the building blocks, all running locally through Ollama:
- `llm_extractor.py`: sends OCR text to a model and gets JSON back.
- `visual_invoice.py`: Accuracy mode, using the Qwen3-VL vision model.
- `invoice_evidence.py`: checks whether a value appears in the OCR.
- `training/`: a fine-tuning workflow.

## 6. Roadmap

### Phase 1: Consolidate the rule-based baseline (short term)

1. Add regression tests for `party_fields`, `totals_reconcile`, `amount_words`, the draft merge and stacked headers.
2. Move the scoring harness into `tools/` so it is versioned and repeatable.
3. Replace the OCR-typo label entries with fuzzy label matching.
4. Score the full sample set (~111 PDFs). Fix only **general** failures, never one supplier's layout.
5. Decide which new fields to add to the JSON format (salesman, PO number, delivery-order number, contact, phone, round-off, SWIFT). Until then they stay in `other_fields`.

### Phase 2: OCR improvements (in parallel)

Measure each option with the same harness, using `tools/ocr_param_sweep.py`:
- server models instead of mobile models
- higher DPI
- an English recognition pass on Latin regions
- targeted re-reads of faint grid cells (O1–O5)

### Phase 3: Model-based mapping with a mandatory checker

1. Choose the model input: OCR boxes with positions (text model, cheaper) or the page image (vision model, more robust on poor scans). We expect **both**: the image for reading, OCR boxes as evidence.
2. Make the model output the exact public JSON format.
3. Route every model value through the checker; values that fail are left empty and flagged.
4. Compare against the rule baseline on the scoring harness and on unseen suppliers. Track accuracy, missed fields, invented values (target: zero accepted), and time per page.

### Phase 4: Model training (fine-tuning on our invoices)

The `training/` workflow is prepared, but no model has been trained yet. Steps:

1. **Labelled data.** Review pages in the notebook review tool (`training.review`) and mark a page verified only after every field and row was checked.
   - Store values exactly as printed: do not fix printed arithmetic or interpret handwriting.
   - The rule-based output plus the checker can pre-fill drafts, so reviewers correct rather than type.
2. **Splits by supplier family.** All pages from one supplier or template go into the same split, so the test set measures performance on unseen layouts. `python -m training.data export` enforces this; it requires at least three reviewed layout groups.
3. **Baseline first.** Evaluate the untrained model on the test split:
   `python -m training.run evaluate --output model_artifacts/base`
4. **Fine-tune.**
   - Model: Qwen3-VL-4B-Instruct, 4-bit, with LoRA (a small adapter trained on top of the frozen model).
   - Hardware: a fresh Colab T4.
   - Command: `python -m training.run train`
5. **Compare.** Evaluate the adapter on the same test split:
   `python -m training.run evaluate --adapter model_artifacts/invoice-qwen3-vl/adapter --output model_artifacts/tuned`
   Compare field accuracy, missing and extra fields, valid JSON, row counts and timing.
6. **Grow the data from production.** Every invoice that comes back `needs_review` and is corrected by a person becomes a new training example. This is how the system improves on new suppliers without new code.

**What we need**

- **People's time** to review and verify labels. This is the main cost. The existing `training/README.md` audit covers 111 PDFs (115 pages) with recurring supplier families.
- **GPU time** on Colab T4 for training and for Accuracy-mode inference.
- **An accuracy target agreed in advance**, so the decision to ship the model is objective.

### Principles that apply in every phase

- Invoices never leave the machine: OCR and models run locally.
- No supplier-specific rules.
- Never invent a value: unproven means empty and `needs_review`.
- Every change is measured on the scoring harness before and after.

## Appendix: reproducing the scores

```powershell
# Re-parse the saved OCR with the current mapper and score all 10 invoices
$env:PYTHONUTF8 = '1'
.\.paddle-venv\Scripts\python.exe benchmark_outputs\sample_review\rescore.py -v

# Full test suite (PYTHONUTF8 is required on Windows)
.\.paddle-venv\Scripts\python.exe -m unittest discover -p "test_*.py"
```

`benchmark_outputs/sample_review/` holds, per invoice:
- `raw_ocr.json`: the saved PaddleOCR output
- `parsed.json`: the mapped JSON
- `expected.json`: hand-read values (first 5 invoices)

It also holds `attribute.py` (the scorer, with hand-read expected values for all 10) and `attribution.json` (per-field verdicts).
