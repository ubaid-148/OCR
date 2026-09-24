"""Keep OCR models in one isolated Python process for a Colab upload batch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class InvoiceWorker:
    def __init__(self, python, project):
        self.python, self.project = str(python), Path(project)
        self.process = None
        self.log = None

    def __enter__(self):
        return self

    def run(self, pdf, raw, output, details, filename, language, mode):
        if self.process is None:
            self.log = tempfile.TemporaryFile(mode='w+b')
            self.process = subprocess.Popen(
                [self.python, '-u', str(self.project / 'colab_worker.py')],
                cwd=self.project, env=os.environ.copy(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=self.log, text=True, encoding='utf-8',
            )
        request = dict(pdf=str(pdf), raw=str(raw), output=str(output), details=str(details),
                       filename=filename, language=language, mode=mode)
        try:
            self.process.stdin.write(json.dumps(request) + '\n')
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError('OCR worker stopped. Retry this batch.') from error
        while True:
            line = self.process.stdout.readline()
            if not line:
                self.log.seek(0)
                detail = self.log.read().decode('utf-8', errors='replace')[-12000:]
                raise RuntimeError(detail or 'OCR worker stopped before returning a result.')
            event = json.loads(line)
            if event.get('stage'):
                print(event['stage'], flush=True)
            elif event.get('error'):
                raise RuntimeError(event['error'])
            elif event.get('done'):
                return

    def __exit__(self, *args):
        if self.process is not None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()
        if self.log is not None:
            self.log.close()


def process_request(request, emit):
    # Imports and Paddle's module-level model cache survive successive requests.
    from coordinate_ocr import extract_pdf
    from invoice_result import extract_result
    payload = extract_pdf(Path(request['pdf']), request['language'],
                          progress=lambda stage: emit({'stage': stage}))
    details = {}
    emit({'stage': 'Extracting and validating invoice fields'})
    invoice = extract_result(payload, request['filename'], request['language'],
                             pdf_path=request['pdf'], mode=request['mode'], details=details)
    for name, data in (('raw', payload), ('output', invoice), ('details', details)):
        Path(request[name]).write_text(json.dumps(data, ensure_ascii=False, allow_nan=False),
                                       encoding='utf-8')


def serve(stream, emit, process=process_request):
    for line in stream:
        try:
            process(json.loads(line), emit)
        except Exception as error:
            emit({'error': str(error) or type(error).__name__})
        else:
            emit({'done': True})


if __name__ == '__main__':
    # Native libraries can write directly to fd 1. Keep those logs out of JSON.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    serve(sys.stdin, lambda event: print(json.dumps(event), file=protocol, flush=True))
