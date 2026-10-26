# Colab Postman API

Run the existing `colab_setup.ipynb` cells through OCR runtime preparation.
Then execute this new cell in Colab (adjust the project path only if required):

```python
from pathlib import Path
import subprocess

PROJECT_DIR = Path('/content/OCR')
OCR_PYTHON = Path('/content/ocr-runtime/bin/python')
subprocess.run(
    [str(OCR_PYTHON), '-u', str(PROJECT_DIR / 'colab_api.py'),
     '--ocr-python', str(OCR_PYTHON)],
    cwd=PROJECT_DIR,
    check=True,
)
```

The cell prints a temporary `https://...trycloudflare.com/api/v1/extract` URL.
Use it in Postman with a `POST` request and `form-data` fields:

| Key | Type | Value |
| --- | --- | --- |
| `document` | File | One PDF |
| `languages` | Text | `eng+ara` |

Keep the cell running. The public URL ends when the Colab cell or runtime stops.

## OpenAPI / Swagger endpoint

For Swagger documentation and the same GitHub OCR pipeline, run this cell after
the runtime setup:

```python
from pathlib import Path
import subprocess

PROJECT_DIR = Path('/content/OCR')
OCR_PYTHON = Path('/content/ocr-runtime/bin/python')
subprocess.run(
    [str(OCR_PYTHON), '-u', str(PROJECT_DIR / 'colab_api.py'),
     '--ocr-python', str(OCR_PYTHON), '--openapi'],
    cwd=PROJECT_DIR,
    check=True,
)
```

The cell prints a temporary public URL. The upload endpoint is
`POST /api/v1/extract`, with form-data fields `document` (PDF) and `languages`
(default `eng+ara`). Swagger is available at the printed URL plus `/docs`.
