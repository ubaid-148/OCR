# Missing-field and flow fixes — 24 September 2026

## Follow-up verification

The image-first path now retains bank details, supplier address/business type,
amount-in-words, and explicit unknown OCR references. Previously these could be
present in spatial extraction yet disappear when the vision candidate was used.
Missing optional values are filled from positioned OCR; disagreements retain the
vision value and produce review notes. Later-page bank details are merged with
conflict reporting. Regression tests cover normalization, public JSON and the
auto-mode orchestration; model inference is mocked.

265 Python tests and the browser upload queue check pass. Cached replay still
produces 111 schema-valid documents with zero parser exceptions, and matches
33/35 selected source values. The verification command now exits nonzero when
supplied source expectations fail, even if every document parsed successfully.
The two recognition omissions remain unresolved with the old cached OCR. Live
Paddle/vision extraction remains unverified; no universal accuracy claim is made.

## Earlier mapping fixes

The confirmed five mapping omissions in the prior source check are fixed. This
is not a claim that all possible PDFs now extract completely or correctly.

## Changes

- Item units and printed tax rates survive wrapped cells and quantity/unit cells.
- Taxable amounts and other charges are extracted when labelled, including zero.
- Line tax rates, mixed-rate invoices and non-zero charges participate in validation.
- Partially recognized tables retain independently readable fields. Missing amounts
  are not calculated just to fill JSON. Merged header/grid geometry is constrained;
  VAT identifiers and stacked tax-rate headings cannot become price/VAT columns.
- Bilingual total and discount footers stop table parsing before amount-in-words rows.
- Unfamiliar inline labelled values remain in `other_fields`. All unassigned OCR
  text is exposed with its page in `unmapped_text`; coordinates and selected field
  associations are in `mapping_coverage` diagnostics. This is an audit trail, not
  proof of semantic correctness or a count of missing business fields.
- Web schema **1.2** preserves supported optional party, bank and amount-in-words
  fields. The existing compact Colab schema includes the same unassigned text.
- A page-level vision failure no longer stops later pages. OCR fallback and partial
  header readings remain available, with explicit review/errors. Batch restart
  retries transient vision failures instead of caching them as permanently finished.

## Verification

251 Python regression tests pass. Browser multi-upload tests pass. All 111 cached
OCR documents produce schema-valid output without parser exceptions. The selected
source-PDF comparison improved from 28/35 matching fields to **33/35**. Recovered
fields include `9515` item unit/tax rate and `9522` item tax rate/other charges/
taxable amount. Source checks cover selected fields, not whole-document accuracy.

Item tables are now present in 93 cached results, versus 88 previously. These may
be partial rows requiring review; row presence is not accuracy. Source inspection
of newly exposed tables caught and corrected price/VAT column shifts before
finalizing. Unknown fields remain visibly unresolved instead of being guessed.

## Remaining verification limits

`9480.pdf`'s printed number 692 and quantity 2 are absent from its old cached OCR.
They correctly remain null with that input. The existing recovered-OCR fixture
passes for both fields, and targeted retry planning is tested, but fresh local
recovery was not run. Paddle is not installed in the available local environment;
Ollama is not running. Live recognition/vision performance remains unverified.
Headerless continuation pages and PDFs bundling separate invoices still need
source review; this change does not implement general document segmentation.

Artifacts: `benchmark_outputs/completion/summary.json`, per-document JSON and
`regression-tests.log`. The earlier `VERIFICATION_REPORT.md` is the pre-fix baseline.
The verification helper can replay raw OCR without overwriting its input directory.

Use the updated Colab runtime (rerun setup) or restart the web server to load
these changes. `auto` requires the local vision setup; `fast` is explicitly OCR-only.
