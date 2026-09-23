const form = document.querySelector('form');
const blank = (keys) => Object.fromEntries(keys.map((key) => [key, null]));
const emptyInvoiceData = () => ({
  source_filename: null, document_type: null, document_type_ar: null,
  amount_in_words_ar: null, handwritten_notes: null,
  supplier: blank(['name_ar', 'name_en', 'branch', 'vat_number', 'cr_number', 'building_no', 'street', 'area', 'post_code', 'additional_no', 'short_address', 'country', 'city', 'commercial_registration', 'address', 'business_type']),
  invoice: blank(['invoice_number', 'date', 'date_of_supply', 'hijri_date', 'time', 'ref_no', 'payment_method', 'page']),
  customer: blank(['customer_code', 'name', 'name_ar', 'name_en', 'vat_number', 'cr_number', 'building_no', 'street', 'area', 'post_code', 'additional_no', 'short_address', 'country', 'city', 'address', 'commercial_registration']),
  items: [],
  vat_summary: blank(['before_tax', 'tax_amount', 'inc_tax', 'tax_code']),
  totals: blank(['subtotal', 'discount', 'other_charges', 'taxable_amount', 'vat_rate', 'vat_amount', 'net_amount', 'currency', 'amount_in_words']),
  other_fields: null,
  bank_details: null,
  validation: {},
});
const clientError = (message) => ({
  schema_version: '1.2',
  status: 'error',
  pipeline_version: 'client',
  parser: 'not_started',
  local_ai_status: 'not_reported',
  local_ai_error: null,
  data: emptyInvoiceData(),
  field_reviews: [],
  review_notes: ['No extraction result was accepted.'],
  ocr_device: 'unknown',
  page_orientations: [],
  timings_seconds: {},
  error: {code: 'client_error', message},
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  const status = document.querySelector('#progress');
  const result = document.querySelector('#result');
  const selected = Array.from(form.querySelector('input[name="pdf"]').files);
  if (!selected.length) return;
  const started = Date.now();
  const results = [];
  button.disabled = true;
  result.textContent = '';
  try {
    for (const [index, file] of selected.entries()) {
      const id = crypto.randomUUID();
      const prefix = `${index + 1}/${selected.length} ${file.name}`;
      let polling = false;
      let finished = false;
      status.textContent = prefix + ' — Uploading PDF…';
      const timer = setInterval(async () => {
        if (polling) return;
        polling = true;
        try {
          const response = await fetch('/progress?id=' + id, {cache: 'no-store'});
          const data = await response.json();
          if (!finished) status.textContent = prefix + ' — ' + data.stage;
        } catch (_) {
          if (!finished) status.textContent = prefix + ' — Waiting for server connection…';
        } finally { polling = false; }
      }, 2000);
      let data;
      try {
        const body = new FormData(form);
        body.delete('pdf');
        body.append('pdf', file, file.name);
        const response = await fetch('/', {method: 'POST', body, headers: {'X-Progress-ID': id}});
        try { data = await response.json(); }
        catch (_) { data = clientError('Server returned a non-JSON response.'); }
        if (!response.ok && data.status !== 'error') data = clientError(`HTTP ${response.status}`);
      } catch (_) {
        data = clientError('Connection to the OCR server was lost.');
      } finally {
        finished = true;
        clearInterval(timer);
      }
      results.push({filename: file.name, result: data});
      result.textContent = JSON.stringify(selected.length === 1 ? data : results, null, 2);
    }
    const failed = results.filter(entry => entry.result.status === 'error').length;
    status.textContent = `${results.length - failed}/${selected.length} processed, ${failed} failed — ${Math.floor((Date.now() - started) / 1000)} seconds`;
  } finally { button.disabled = false; }
});
