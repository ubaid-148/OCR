"""Native Colab upload flow and invoice result downloads."""
from html import escape
from pathlib import Path


def upload_values(value):
    entries = value.values() if isinstance(value, dict) else value
    return {entry['metadata']['name'] if 'metadata' in entry else entry['name']:
            bytes(entry['content']) for entry in entries}


def show_download(path, label='Download JSON'):
    from IPython.display import HTML, display
    from google.colab import files
    if label == 'Download invoice JSON':
        import json
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        status = escape(str(data.get('status', 'Result ready')).replace('_', ' '))
        number = escape(str(data.get('invoice_number') or 'Not established'))
        display(HTML('<div style="padding:14px;border:1px solid #dbe3ee;border-radius:10px">'
            '<b>'+escape(Path(path).name)+'</b><p>Invoice: '+number+' · '+status+'</p>'
            '<details><summary>View JSON</summary><pre style="max-height:360px;overflow:auto;white-space:pre-wrap">'
            +escape(json.dumps(data,ensure_ascii=False,indent=2))+'</pre></details></div>'))
    print(label + ': ' + Path(path).name, flush=True)
    files.download(str(path))


def show_upload_form(process, mode='auto', language='eng+ara', diagnostics=False):
    """Use Colab's native uploader without widget value/click synchronization."""
    from contextlib import nullcontext
    from IPython.display import clear_output
    from google.colab import files

    clear_output(wait=True)
    print('INVOICE OCR — native Colab upload')
    print('Mode:', 'Accuracy (OCR + vision)' if mode == 'auto' else 'Fast (OCR only)')
    print('Language:', language)
    print('Choose your PDFs below. Processing starts when upload finishes.', flush=True)
    uploaded = files.upload()
    if not uploaded:
        print('No files uploaded. Run this cell again to choose PDFs.')
        return
    invalid = [name for name, data in uploaded.items()
               if Path(name).suffix.lower() != '.pdf' or not bytes(data).startswith(b'%PDF-')]
    for name in invalid:
        print('Skipped invalid PDF:', name)
    uploaded = {name: bytes(data) for name, data in uploaded.items() if name not in invalid}
    if not uploaded:
        print('No valid PDFs uploaded. Run this cell again and select PDF files.')
        return

    def update(index, total, name):
        print(f'Processing {index}/{total}: {name}', flush=True)

    try:
        process(uploaded, mode, language, diagnostics, update, nullcontext())
    except Exception as error:
        print('Processing failed:', str(error), flush=True)
        raise
    print('Processing finished. Check the results and any file errors above.')
