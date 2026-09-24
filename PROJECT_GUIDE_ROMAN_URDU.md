# Multi-PDF Invoice OCR — poore project ki Roman Urdu guide

Updated: 24 September 2026. Yeh guide extraction code commit `a8ec543` ke flow ko explain karti hai.

## 1. Project kya karta hai?

Aap ek ya multiple invoice PDFs upload karte hain. System har PDF ke pages se text aur uski position leta hai, invoice fields aur item rows identify karta hai, phir structured JSON banata hai. `auto` mode mein original PDF ki images vision model bhi parhta hai. Financial checks aur source evidence ke basis par doubtful values review ke liye flag hoti hain.

Multiple files ka matlab: har uploaded PDF ka separate result. Ek PDF ke andar multiple independent invoices ki automatic segmentation abhi general-purpose taur par implement nahi hai.

## 2. PDF se JSON tak flow

```text
Colab notebook / browser upload / batch command
                     |
                     v
coordinate_ocr.py — har page ka text + coordinates
    |-- native_pdf.py: usable PDF text layer
    |-- scanned page: render + rotation + PaddleOCR
    |-- targeted_ocr.py: selected missing/weak regions ki OCR retry
                     |
                     v
local_ai_parser.py + layout_invoice.py
    labels, supplier/customer, table columns, rows, totals
                     |
           +---------+----------+
           |                    |
        fast mode            auto mode
      spatial result     visual_invoice.py
                         original page images + Ollama vision
                         visual_recovery.py: bounded rereads
                         OCR aur vision reconciliation
           |                    |
           +---------+----------+
                     |
          validation + evidence + mapping coverage
                     |
       invoice_result.py / invoice_response.py
                     |
      final JSON + review notes + diagnostic output
```

`fast` mode OCR/text aur position-based parsing use karta hai; vision inference skip hoti hai. `auto` mode spatial extraction ke saath original-page vision use karta hai. Auto result bhi independently verified ground truth nahi hota.

## 3. Entry points: aap kahan se system chalate hain?

| File | Is mein kya hota hai? |
| --- | --- |
| [colab_setup.ipynb](colab_setup.ipynb) | Main Colab notebook: GitHub code load, runtime prepare, PDFs upload, per-file extraction aur downloads. |
| [colab_runtime.py](colab_runtime.py) | OCR environment/dependencies prepare, device configuration aur runtime checks. |
| [colab_vision.py](colab_vision.py) | Ollama aur vision model runtime prepare karta hai. Default image-first model `qwen3-vl:4b` hai. |
| [check_ocr_runtime.py](check_ocr_runtime.py) | OCR runtime availability/configuration check karne ka helper. |
| [app.js](app.js) | Browser mein multiple files ki queue; ek waqt mein ek request, separate results, failure isolation. |
| [ocr_web.py](ocr_web.py) | Local web server, upload validation, OCR/parser calls, progress aur response. Searchable-PDF output ka separate OCRmyPDF route bhi yahan hai. |
| [tools/batch_local_ocr.py](tools/batch_local_ocr.py) | Folder ki PDFs process, results/cache save; default auto, optional fast mode. Failed vision results ko permanent successful cache nahi samajhta. |

## 4. Raw text aur coordinates kin files se milte hain?

| File | Zimmedari |
| --- | --- |
| [coordinate_ocr.py](coordinate_ocr.py) | PDF open karta hai, pages process karta hai, native text ya raster OCR choose karta hai, raw page words/positions/confidence return karta hai. |
| [native_pdf.py](native_pdf.py) | Usable digital text layer se words aur geometry nikalta hai. Har page par Paddle chalana lazmi nahi. |
| [page_rotation.py](page_rotation.py) | Page orientation, upright rendering, crop bounds aur coordinate scaling. |
| [targeted_ocr.py](targeted_ocr.py) | Missing/weak fields aur table areas ke liye focused OCR crops/retries plan aur merge karta hai. |
| [document_regions.py](document_regions.py) | Receipt/payment-slip region detect karke invoice parsing ke text se alag karne mein madad karta hai. |
| [pdf_errors.py](pdf_errors.py) | Invalid PDF processing ke error types. |

Raw word mein aam tor par `text`, `left`, `top`, `width`, `height`, `confidence` milte hain. Page record page number aur extraction/orientation metadata rakhta hai. Native text ka confidence model OCR confidence jaisa interpretation nahi rakhta.

Raw OCR mein `100` mil jana sirf itna batata hai ke text parha gaya. Yeh quantity, unit price, subtotal ya invoice reference hai — is ka faisla next mapping stage karti hai.

## 5. Value kis field ki hai: mapping kahan hoti hai?

| File | Zimmedari |
| --- | --- |
| [layout_invoice.py](layout_invoice.py) | Main spatial layout parser: header labels, supplier/customer regions, item column headings, rows aur totals. Position aur heading se amount ki role identify karta hai. |
| [local_ai_parser.py](local_ai_parser.py) | Spatial parsing/fallback combine karta hai, quality flags banata hai. `_validate()` arithmetic aur required-field checks karta hai. |
| [invoice_formatter.py](invoice_formatter.py) | Text/number normalization aur older geometric invoice parser; fallback mein use hota hai. |
| [invoice_details.py](invoice_details.py) | Optional printed details: address, CR, bank details, amount-in-words aur unfamiliar explicit `label: value` pairs. |
| [bbox_grouping.py](bbox_grouping.py) | Boxes ki geometry aur row grouping helpers. |
| [mapping_coverage.py](mapping_coverage.py) | Selected field evidence ko OCR page/text/box se associate karta hai. Unassigned OCR text `unmapped_text` mein expose hota hai. |

Example: `Dispatch Reference: 0007-A` ka dedicated canonical field na ho to `other_fields` mein label, value aur page preserve ho sakte hain. Isay zabardasti invoice number nahi banaya jata.

`unmapped_text` mein labels, logos aur already-understood context bhi aa sakta hai. Is list ka size missing business fields ki count nahi. Isi tarah assigned evidence semantic correctness ki guarantee nahi.

## 6. Vision AI aur recovery kahan hoti hai?

[visual_invoice.py](visual_invoice.py) image-first orchestration ka central module hai:

1. Spatial extraction ko fallback ke taur par prepare karta hai.
2. Original PDF pages ki images render karta hai.
3. Header aur item-table vision requests chalata hai.
4. Model JSON ko `normalize_full()` se supported schema mein normalize karta hai.
5. `merge_pages()` page readings combine karta hai aur conflicts report karta hai.
6. `reconcile_with_spatial()` missing supported fields ko positioned OCR se fill karta hai; disagreement ke review notes banata hai. Kuch role-checked VAT conflicts mein positioned OCR prefer hota hai.
7. Evidence, row count/order aur arithmetic compare karta hai. Unsafe candidate ki jagah spatial fallback return ho sakta hai; candidate diagnostics mein retain hota hai.
8. Page failure par later pages process karne ki koshish jaari rakhta hai.

[visual_recovery.py](visual_recovery.py) missing core header/totals/item data ke liye higher-resolution page aur crops se bounded reread karta hai. Existing nonempty reading ko quietly replace nahi karta. Item merge ke liye ordered unique anchors chahiye; ambiguous duplicate/reordered rows ko index se join nahi karta.

Truncated vision JSON par ek larger output-budget retry ho sakti hai. Budget bounded hai; unlimited long-table extraction implement nahi hai.

[ollama_http.py](ollama_http.py) Ollama HTTP requests ka shared helper hai.

Latest fix: vision schema/normalization mein bank details, supplier address/business type aur amount-in-words retain hote hain. Spatial `other_fields` bhi reconciliation mein preserve hote hain. Missing values fill ho sakti hain; conflicting optional readings review notes mein aati hain.

## 7. Validation aur evidence mein farq

| Check | Kahan | Kya establish hota hai? |
| --- | --- | --- |
| Arithmetic / missing fields | `local_ai_parser.py` ka `_validate()` | Quantity/price/line amounts aur totals reconcile hote hain ya nahi. |
| Source evidence | [invoice_evidence.py](invoice_evidence.py) | Extracted value ke liye OCR/source-position support aur disagreement. |
| JSON contract | [invoice_response.py](invoice_response.py) | Output keys/types/schema valid hain ya nahi. |
| OCR mapping coverage | `mapping_coverage.py` | Kaunsa OCR occurrence selected evidence mein use hua. |
| Expected source values | [tools/verify_cached_extraction.py](tools/verify_cached_extraction.py) | Supplied manually checked expectations se exact comparison. |

Arithmetic pass hone se supplier name ya invoice number correct prove nahi hota. JSON schema pass hone se values correct prove nahi hotin. High OCR confidence se field ki role correct prove nahi hoti.

## 8. Final JSON kin files mein banta hai?

| File/output | Kya milta hai? |
| --- | --- |
| [invoice_result.py](invoice_result.py) | Colab/batch ka compact output: invoice identifiers, parties, items, totals, notes, bank details, extra fields aur unmapped text. |
| [invoice_response.py](invoice_response.py) | Web ka fixed public schema `1.2`: `data`, status, field reviews, review notes, timing/device metadata aur errors. |
| `raw_ocr.json` | Colab run ke intermediate raw words/pages. |
| `invoice.json` | Colab run ka intermediate compact business result. Download ka naam source filename ke mutabiq hota hai. |
| `extraction_details.json` | Internal extraction, quality, evidence aur vision diagnostics. Downloadable diagnostics banane mein use hota hai. |

Multi-upload downloads mein filename prefix/index collisions kam karta hai. `DOWNLOAD_DIAGNOSTICS=True` se detailed diagnostic download enable hota hai.

Common status:

- `extracted`: available checks ne review requirement flag nahi ki; universal correctness certificate nahi.
- `needs_review`: missing, conflicting, unsupported ya unverified fields source PDF se check karein.
- `error`: processing failure; agar partial output available ho to usay verified result na samjhein.

Absent supported fields `null` reh sakte hain. Raw OCR aur detailed diagnostics is liye useful hain ke pata chale failure reading mein hua, mapping mein, ya final output conversion mein.

## 9. Alternate/older command-line path

Yeh files project mein maujood hain, lekin main Colab auto flow ko in sab se sequentially guzarta hua na samjhein.

| File | Kaam |
| --- | --- |
| [main.py](main.py) | Alternate CLI orchestration, rule-based document building aur optional LLM draft merging. |
| [label_value_pairing.py](label_value_pairing.py) | Labels ko nearby values se pair karta hai; page isolation aur label matching checks. |
| [table_extractor.py](table_extractor.py) | Alternate table extraction aur header/template helpers. |
| [canonical_schema.py](canonical_schema.py) | Alternate document ko canonical structure mein convert karta hai. |
| [validator.py](validator.py) | Is path ki document arithmetic validation. |
| [llm_extractor.py](llm_extractor.py) | OCR payload se optional text-based Ollama draft; original-image vision path se separate. |
| [pdf_fallback.py](pdf_fallback.py) | PDF-based fallback cross-check helper. |

## 10. Testing, debugging aur reports

| Files | Kis cheez ki testing/documentation? |
| --- | --- |
| `test_layout_invoice.py`, `test_missing_fields.py`, `test_label_value_mapping.py` | Table columns, labels, partial rows, units, tax rates aur field mapping. |
| `test_visual_invoice.py`, `test_visual_recovery.py`, `test_visual_optional_fields.py` | Vision orchestration, failures, recovery aur optional-field preservation. Model calls mocked hain. |
| `test_mapping_coverage.py`, `test_invoice_evidence.py` | Source associations aur unassigned text. |
| `test_invoice_result.py`, `test_json_contract.py` | Compact/public output behavior aur schema. |
| `test_batch_local_ocr.py`, `test_colab_notebook_upload.py` | Batch/cache aur notebook upload flow. |
| [tests/test_upload_queue.js](tests/test_upload_queue.js) | Browser sequential upload aur failure isolation. |
| `test_cached_verification.py` | Source mismatch ko failed verification maanna. |
| [tools/verify_cached_extraction.py](tools/verify_cached_extraction.py) | Cached raw OCR replay + schema + optional expected values. Mismatch ho to exit code nonzero. |
| `tools/ocr_diagnostics.py`, `tools/attribute_errors.py`, `tools/reliability_benchmark.py`, `tools/ocr_param_sweep.py`, `tools/replay_cached_invoices.py` | Diagnosis, error analysis, benchmarking aur experiment helpers. |
| [COMPLETION_REPORT.md](COMPLETION_REPORT.md) | Latest fixes aur actual verification status. |
| [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) | Earlier baseline; current status ke liye completion report dekhein. |
| [OCR_REVIEW.md](OCR_REVIEW.md), [README.md](README.md), [COLAB_TESTING.md](COLAB_TESTING.md) | Review findings, setup aur Colab testing instructions. |

Recorded latest run: **265 Python tests pass**, browser upload test pass. **111 cached documents** schema-valid aur parser exceptions zero. Selected source comparison **33/35** match karta hai. Yeh whole-document accuracy score nahi. `9480.pdf` ke invoice number aur quantity ki do source values old cached OCR mein missing hain; live recovery locally verify nahi hui.

## 11. Training aur bundle files

| Files | Kaam |
| --- | --- |
| [colab_train.ipynb](colab_train.ipynb) | Separate review/fine-tuning notebook; normal extraction notebook nahi. |
| `training/data.py`, `training/review.py` | Dataset prepare, page review, corrected labels aur export. |
| `training/run.py`, `training/generation.py`, `training/draft_checks.py` | Draft generation, training/evaluation aur draft checks. Details [training/README.md](training/README.md) mein. |
| [tools/build_colab_bundle.py](tools/build_colab_bundle.py) | Workspace snapshot ka ZIP + upload-based Colab notebook, checksums ke saath. |
| `dist/` | Generated bundle artifacts; Git mein tracked nahi. |
| `benchmark_outputs/` | Local verification/benchmark outputs; Git mein tracked nahi. |
| `uploads/` | Web processing ke local files. |
| `tessdata/` | Tesseract language data. |

Training chalane se model automatically production Ollama model ki jagah use nahi hota. Separate evaluation aur integration chahiye. PDF upload karna apne aap model training nahi karta.

## 12. Kisi value ke missing hone par kahan check karein?

| Symptom | Pehle yahan dekhein |
| --- | --- |
| Raw OCR mein value hi nahi | `coordinate_ocr.py`, `native_pdf.py`, `page_rotation.py`, `targeted_ocr.py`; original page ki readability bhi. |
| Raw OCR mein value hai, field galat/null | `layout_invoice.py`, `invoice_details.py`, label mapping aur field evidence. |
| Fast mein value hai, auto mein missing | `visual_invoice.py` normalization/reconciliation/fallback selection. |
| Vision response incomplete | Vision diagnostics, `ollama_http.py`, token budgets aur `visual_recovery.py`. |
| Internal data correct, final JSON missing | `invoice_result.py` ya `invoice_response.py` field selection/schema. |
| Multiple PDFs mein sirf ek result | `app.js` queue ya `colab_setup.ipynb` upload loop. |
| Ek PDF mein multiple different invoices mix | Document segmentation limitation; independent PDFs mein separate processing karein. |
| Totals match lekin text galat | Source evidence/visual review; arithmetic text verification ka replacement nahi. |

## 13. Colab mein ab kaise test karein?

[Updated notebook kholein](https://colab.research.google.com/github/ubaid-148/OCR/blob/main/colab_setup.ipynb).

1. Fresh runtime start karein aur T4 GPU select karein.
2. Cell 1 se run karein; printed Git commit check karein.
3. Setup complete hone dein aur `EXTRACTION_MODE="auto"` rakhein.
4. Debugging ke liye `DOWNLOAD_DIAGNOSTICS=True` karein.
5. Different-layout PDFs upload karein; har PDF ka JSON aur source compare karein.
6. Wrong/missing field ke saath us ka raw OCR aur diagnostic record dekhein.

Live Paddle/vision GPU inference ki accuracy yahan local regression tests se establish nahi hui. Headerless continuation pages, long tables, unreadable scans, handwriting aur bundled independent invoices par further source-based evaluation chahiye. Har mumkin PDF ke liye zero-missing guarantee abhi nahi hai.
