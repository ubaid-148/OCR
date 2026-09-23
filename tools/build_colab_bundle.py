"""Package the exact workspace code for Colab without publishing it to GitHub."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

LOADER = '''#@title 1. Upload the updated OCR code ZIP
import hashlib
import io
import json
import os
import pathlib
import stat
import zipfile
from google.colab import files

uploaded_code = files.upload()
if len(uploaded_code) != 1:
    raise ValueError('Upload the single invoice-ocr-code.zip file first. PDFs are uploaded in step 3.')
archive_name, archive_bytes = next(iter(uploaded_code.items()))
if not archive_name.lower().endswith('.zip'):
    raise ValueError('Step 1 needs invoice-ocr-code.zip, not a PDF.')
bundle_id = hashlib.sha256(archive_bytes).hexdigest()[:12]
bundle_root = pathlib.Path('/content') / ('invoice-ocr-' + bundle_id)
with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
    for entry in archive.infolist():
        relative = pathlib.PurePosixPath(entry.filename)
        if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'OCR' or stat.S_ISLNK(entry.external_attr >> 16):
            raise ValueError('Invalid code archive path: ' + entry.filename)
    manifest = json.loads(archive.read('OCR/BUNDLE_MANIFEST.json'))
    for name, expected_hash in manifest['files'].items():
        if hashlib.sha256(archive.read('OCR/' + name)).hexdigest() != expected_hash:
            raise ValueError('Code archive checksum mismatch: ' + name)
    archive.extractall(bundle_root)
PROJECT_DIR = bundle_root / 'OCR'
os.chdir(PROJECT_DIR)
print('Loaded updated OCR build:', manifest['build_id'])
print('Project:', PROJECT_DIR)
print('This notebook uses the uploaded code, not GitHub main.')
'''


def build(output):
    root = Path(__file__).resolve().parents[1]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    suffixes = {'.py', '.json', '.js', '.md', '.ipynb', '.txt', '.traineddata'}
    sources = [p for p in root.iterdir() if p.is_file() and not p.name.startswith('.')
               and (p.suffix in {'.py', '.js', '.md', '.ipynb'} or p.name == 'requirements.txt')]
    for directory in ('tools','templates','tests','training','tessdata'):
        sources.extend(p for p in (root/directory).rglob('*') if p.is_file() and p.suffix in suffixes
                       and '__pycache__' not in p.parts)
    sources = sorted(set(sources))
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    build_id = hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()[:16]
    manifest = {'build_id': build_id, 'files': hashes}
    archive_path = output/'invoice-ocr-code.zip'
    with zipfile.ZipFile(archive_path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sources:
            archive.write(path, 'OCR/' + path.relative_to(root).as_posix())
        archive.writestr('OCR/BUNDLE_MANIFEST.json',json.dumps(manifest,indent=2))
    notebook = json.loads((root/'colab_setup.ipynb').read_text(encoding='utf-8'))
    notebook['cells'][0]['source'] = [
        '# Updated Invoice OCR — Colab test bundle\n',
        'Select a **T4 GPU**. Run all cells. Step 1 asks for **invoice-ocr-code.zip**; '
        'step 2 installs OCR and local vision; step 3 asks for your PDFs. '
        'Results retain review flags and unassigned OCR text. First setup downloads models.\n']
    notebook['cells'][1]['source'] = LOADER.splitlines(keepends=True)
    for cell in notebook['cells'][2:]:
        cell['source'] = [line.replace("PROJECT_DIR = pathlib.Path('/content/OCR')",
                                      "PROJECT_DIR = pathlib.Path(globals().get('PROJECT_DIR', '/content/OCR'))")
                          for line in cell['source']]
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            compile(''.join(cell['source']), 'colab-cell', 'exec')
            cell['outputs'] = []
            cell['execution_count'] = None
    notebook_path = output/'Invoice_OCR_Colab.ipynb'
    notebook_path.write_text(json.dumps(notebook,ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    (output/'BUILD.json').write_text(json.dumps({'build_id':build_id,'files':len(sources),
        'archive_sha256':hashlib.sha256(archive_path.read_bytes()).hexdigest()},indent=2))
    return {'build_id':build_id,'archive':str(archive_path),'notebook':str(notebook_path),'files':len(sources)}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('dist'))
    args=parser.parse_args()
    print(json.dumps(build(args.output),indent=2))
