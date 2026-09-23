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

## Supplied live Colab failure: follow-up

Replayed the user's actual 9480 raw OCR and supplied final vision values through
reconciliation (not fresh model inference). The positioned invoice date now wins
its conflicting vision date with an explicit review note: 2026-03-11. The missing
Arabic supplier name is retained from spatial extraction. A zero in a uniquely
matched item's optional financial cell is cleared when spatial extraction has
neither a value nor evidence; supported printed zeros remain intact. Vision prompts
now explicitly separate handwriting and phone lists from printed invoice fields.
269 regression tests pass. Replay output is local under
`benchmark_outputs/live-sample-fix/reconciliation.json`.

Customer VAT remains misrecognized in supplied raw OCR; customer name, item-code
spelling, amount-in-words and runtime performance are not resolved by these changes.
No source value is hard-coded. Fresh GPU inference remains necessary to evaluate
recognition and prompt changes. The previous live run took 657.59 seconds in vision,
including repeated truncated header generation; no speed improvement is claimed.

## Systemic row/cell evidence and identifier candidate fixes

Item numeric auditing no longer uses document-wide number occurrences. Shared
`item_scope.py` resolves a unique positioned table row by code or description,
then admits only that row's selected field evidence. Table cells use row midpoint
boundaries and nearest-column partitions; footer Discount labels terminate the
item region. The visual path clears unsupported numeric item fields to null and
revalidates afterwards; original vision values remain in candidate diagnostics.
Coverage discards stale item associations before rebuilding scoped evidence.
Repeated/ambiguous item anchors conservatively remain unresolved, rather than
borrowing evidence from another row. This can reduce populated fields where OCR
cannot establish table geometry; it is intentional under the strict evidence rule.

Invoice identifiers are ranked by same-page label alignment and normalized
geometric distance, with confidence only breaking distance ties. English and
Arabic value directions are respected. Candidate value, page, boxes, label,
distance and confidence remain in quality diagnostics; alternate values appear
in review notes. This rule applies to spatial and visual results.

The supplied live OCR is committed as `tests/fixtures/9480_live_ocr.json`.
Importantly, its Arabic invoice label is geometrically closer to 692 than the
English label is to 9480 (normalized distances approximately 1.56 and 3.01).
Therefore the generic rule selects 692 and flags 9480 as an alternate. The test
for a genuinely nearer 9480 verifies that it wins over a higher-confidence 692;
no identifier value or filename is hardcoded in production logic.

Validation: 276 Python regression tests pass, browser upload queue checks pass.
Cached replay covers 111 documents with zero parser exceptions and 111 valid
schemas; selected source checks remain 33/35. This is not fresh GPU inference.
