"""Test one PDF directly with Azure F0, without Colab or local OCR models.

Run: python -m tools.test_azure_invoice public_invoice_pdfs/9480.pdf
"""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path

from azure_invoice_trial import analyze_pdf, validate_endpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--output', type=Path, default=Path('benchmark_outputs/azure-trial'))
    args = parser.parse_args()
    if not args.pdf.is_file():
        parser.error('PDF file does not exist.')
    pdf = args.pdf.read_bytes()
    if not pdf.startswith(b'%PDF-') or len(pdf) > 4_000_000:
        parser.error('Choose a PDF under 4 MB for the F0 trial.')
    import pypdfium2 as pdfium
    with pdfium.PdfDocument(pdf) as document:
        pages = len(document)
    if pages not in (1, 2):
        parser.error('Choose a 1–2 page PDF for this F0 trial.')
    endpoint = validate_endpoint(os.environ.get('AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT')
                                 or input('Azure endpoint: '))
    print('Verify Pricing tier = Free F0 in Azure Portal. The API key cannot verify the tier.')
    if input('Type F0 to confirm: ').strip().upper() != 'F0':
        parser.error('Stopped. No PDF was submitted.')
    key = os.environ.get('AZURE_DOCUMENT_INTELLIGENCE_KEY') or getpass('Azure Key 1 (hidden): ')
    if not key.strip():
        parser.error('Azure key is required. No PDF was submitted.')
    args.output.mkdir(parents=True, exist_ok=True)
    print(f'Sending {args.pdf.name} ({pages} pages) to Azure...', flush=True)
    try:
        result, raw = analyze_pdf(pdf, args.pdf.name, endpoint, key, confirmed_f0=True)
    except Exception as error:
        print('Test failed:', str(error).replace(key, '[redacted]'))
        return 1
    for suffix, data in (('json', result), ('raw.json', raw)):
        path = args.output / f'{args.pdf.stem}.{suffix}'
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Saved:', path.resolve())
    print(f"Azure response: {result['seconds']} seconds; {len(result['documents'])} invoice(s).")
    print('Compare saved fields with the source PDF; this is not an accuracy score.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
