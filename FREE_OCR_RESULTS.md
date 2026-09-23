> Historical OCR-only benchmark below. Current Colab/batch defaults use `auto`
> (local vision plus OCR) for unfamiliar layouts. Use `--mode fast` to reproduce
> the OCR-only batch configuration described here.

# Free local OCR update

This update does not claim complete extraction accuracy. The default Colab flow
uses Paddle OCR, focused retries, spatial parsing and review flags, with no Qwen
model download or vision generation. Experimental `auto` remains opt-in.

## Checks performed

102 focused regression tests passed, including batch resume/failure handling. All 111 saved
OCR documents were replayed without exceptions. This is cached recognition data,
not a fresh GPU run or a manually labelled accuracy benchmark.

| Cached parser check | Before | After |
| --- | ---: | ---: |
| Invoice number present | 74/111 | 88/111 |
| Documents with items | 71/111 | 88/111 |
| Three arithmetic checks pass | 13/111 | 20/111 |
| Parser exceptions | 0 | 0 |

`9479` and `9675` now recognize the explicit `Invoice #` label. No previously
present invoice number was lost. Explicit supplier CR labels are retained (29
cached documents). `Credit Day` is not treated as a payment value. These rules
use labels and coordinates, not PDF filenames or memorized reference values.

The final Colab JSON exposes supported extra header/party/item/totals fields,
notes, other labelled fields and bank details that were previously dropped at
the output boundary. This preserves extracted values; it does not recover words
that OCR never recognized.

Additional table rules recognize Item Name and Nature of Goods headers, keep long
descriptions out of inferred item-code columns, and distinguish stacked Tax Amount
from taxable amounts and tax codes. Known unit suffixes such as EA and BAG are
accepted in numeric cells. English month dates and additional invoice labels are
recognized. Recovered values for 9515 and 9522 were checked against page images.
No previously populated item table or invoice number was lost, and no previously
passing arithmetic check regressed in this replay. Presence is not accuracy.

## Known limits

The cached set still has 23 missing invoice numbers, 28 missing dates, 23 documents
without extracted items, and 53 missing net amounts. Old raw OCR may lack the newer
focused rereads, so fresh recognition must be measured before judging current
end-to-end coverage. On cached `9479`, the first VAT is misread as 17.05 rather than
7.05. The source reading remains visible and the result requires review; it is
not silently replaced using the known answer.

## Run fresh recognition of all samples

After preparing `colab_setup.ipynb`, run in a Code cell:

```python
import subprocess
subprocess.run([
    str(OCR_PYTHON), '-u', '-m', 'tools.batch_local_ocr',
    '--pdf-dir', str(PROJECT_DIR / 'public_invoice_pdfs'),
    '--output', str(PROJECT_DIR / 'benchmark_outputs/live-local'),
], cwd=str(PROJECT_DIR), check=True)
```

Models stay loaded within the batch process. Every PDF gets raw OCR, final fields,
checks and timing. Results are saved after each document; unchanged completed
sources are reusable on restart. Failed files are retried. The summary measures
missing fields/review requirements, not true accuracy. Read the PDFs to establish
correctness. Data stays in the local Colab runtime.

Cached replay can be reproduced with:
`python -m tools.replay_cached_invoices PATH_TO_RAW_JSON --output benchmark_outputs/replay.json`.
