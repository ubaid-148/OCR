# Direct Gemini invoice trial

Create a key at https://aistudio.google.com/api-keys. The chat URL is not an API
endpoint and does not supply a key. For free testing, select a project on the
Free tier; the client cannot determine your billing tier from an API key.

From this directory:

```sh
python3 -m pip install -r requirements-gemini.txt
python3 -m tools.test_gemini_invoice public_invoice_pdfs/9480.pdf
```

Paste the key after `GEMINI_API_KEY=` in the project's `.env` file and save it.
This local file is ignored by Git and excluded from the Colab bundle. The loader
finds it beside `run_gemini_web.py`, regardless of the terminal's working directory.
Restart the server after changing it. Alternatively use the hidden prompt or
`GEMINI_API_KEY` in the process environment (which takes precedence).
`--model` or `GEMINI_MODEL` can select another
compatible model. The default is `gemini-3.8-flash`.

This sends the PDF directly to Gemini, with no Paddle/Colab/GPU dependency.
The trial limits PDFs to 10 MB to keep inline requests small. Results go to
`benchmark_outputs/gemini-trial/`, including source filename, invoice page groups,
structured fields and elapsed time. Multiple invoices are kept as separate entries.
Model output is schema-validated but not independently source-verified; it is
always marked `needs_review`. An empty invoice array is not successful extraction.

For the web upload app run `python3 run_gemini_web.py`, enter your key in the
hidden terminal prompt, then open http://127.0.0.1:8765. Gemini is selected by
default with this launcher. The key is never rendered in browser HTML.
The hidden prompt is only used when neither the environment nor `.env` has a key.
Gemini responses use `schema_version: gemini-trial-1` and an `invoices` array,
so multiple invoices are not flattened. Existing local modes retain schema 1.2.
A live extraction requires your key and model quota.
Completed web results are atomically saved in `benchmark_outputs/gemini-web`
before being sent to the browser. A disconnected browser does not cause repeated
error writes. Uploading the same PDF again reuses the saved result when the model,
prompt and schema match, including after server restart. `cache_hit: true` means
no new API call; `seconds` remains the original extraction time. Remove the
corresponding saved JSON to force a fresh test. Failed requests are not cached.
Automatic SDK retries are disabled for HTTP 429, so a quota rejection
does not wait through repeated Retry-After delays. Only transient server failures
(500/502/503/504) remain retryable, with at most one retry. The web endpoint preserves
HTTP 429. A daily quota error cannot be fixed by retrying every minute or changing
the API key in the same project; check the project's limits in AI Studio.
Free-tier content may be used for product improvement: use public/sample invoices
for initial testing. `store=False` disables interaction storage but does not change
the provider's free-tier data-use terms.

References:
- https://ai.google.dev/gemini-api/docs/document-processing
- https://ai.google.dev/gemini-api/docs/structured-output
- https://ai.google.dev/gemini-api/docs/pricing
