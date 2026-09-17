const form = document.querySelector('form');
const blank = (keys) => Object.fromEntries(keys.map((key) => [key, null]));
const emptyInvoiceData = () => ({
  source_filename: null, document_type: null, document_type_ar: null,
  amount_in_words_ar: null, handwritten_notes: null,
  supplier: blank(['name_ar', 'name_en', 'branch', 'vat_number', 'cr_number', 'building_no', 'street', 'area', 'post_code', 'additional_no', 'short_address', 'country', 'city']),
  invoice: blank(['invoice_number', 'date', 'date_of_supply', 'hijri_date', 'time', 'ref_no', 'payment_method', 'page']),
  customer: blank(['customer_code', 'name', 'name_ar', 'name_en', 'vat_number', 'cr_number', 'building_no', 'street', 'area', 'post_code', 'additional_no', 'short_address', 'country', 'city', 'address']),
  items: [],
  vat_summary: blank(['before_tax', 'tax_amount', 'inc_tax', 'tax_code']),
  totals: blank(['subtotal', 'discount', 'other_charges', 'taxable_amount', 'vat_rate', 'vat_amount', 'net_amount', 'currency']),
  other_fields: null,
  validation: {},
});
const clientError = (message) => ({
  schema_version: '1.1',
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
  const id = crypto.randomUUID();
  const started = Date.now();
  let polling = false;
  let finished = false;
  button.disabled = true;
  result.textContent = '';
  status.textContent = 'Uploading PDF…';
  const timer = setInterval(async () => {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch('/progress?id=' + id, {cache: 'no-store'});
      const data = await response.json();
      if (finished) return;
      status.textContent = data.stage + ' — ' + Math.floor((Date.now() - started) / 1000) + ' seconds';
    } catch (_) {
      if (finished) return;
      status.textContent = 'Waiting for server connection…';
    } finally { polling = false; }
  }, 2000);
  try {
    const response = await fetch('/', {method: 'POST', body: new FormData(form), headers: {'X-Progress-ID': id}});
    const body = await response.text();
    let data;
    try {
      data = JSON.parse(body);
    } catch (_) {
      data = clientError('Server returned a non-JSON response.');
    }
    result.textContent = JSON.stringify(data, null, 2);
    status.textContent = (response.ok ? 'Completed' : 'Could not process PDF') + ' in ' + Math.floor((Date.now() - started) / 1000) + ' seconds';
  } catch (_) {
    result.textContent = JSON.stringify(clientError('Connection to the OCR server was lost.'), null, 2);
    status.textContent = 'Connection lost. Check the Colab runtime and OCR server log before retrying.';
  } finally {
    finished = true;
    clearInterval(timer);
    button.disabled = false;
  }
});
