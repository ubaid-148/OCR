"""Prepare the local vision service inside a Colab runtime, never on import."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request


def _tags():
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=3) as response:
        return json.load(response)


def prepare_vision_runtime(model='qwen3-vl:4b'):
    if not Path('/content').is_dir():
        raise RuntimeError('Vision setup is intended for Google Colab.')
    os.environ['USE_LOCAL_AI']='false'
    if not shutil.which('ollama'):
        print('Installing local vision service...',flush=True)
        installer=Path('/tmp/invoice-ollama-install.sh')
        urllib.request.urlretrieve('https://ollama.com/install.sh',installer)
        subprocess.run(['sh',str(installer)],check=True)
    try:
        tags=_tags()
    except (OSError,ValueError):
        env=dict(os.environ,OLLAMA_HOST='127.0.0.1:11434',OLLAMA_NUM_PARALLEL='1')
        with Path('/tmp/invoice-ollama.log').open('ab') as log:
            process=subprocess.Popen(['ollama','serve'],env=env,stdout=log,stderr=log,
                                     start_new_session=True)
        for _ in range(60):
            try:
                tags=_tags()
                break
            except (OSError,ValueError):
                if process.poll() is not None:
                    raise RuntimeError('Vision service stopped. Check /tmp/invoice-ollama.log.')
                time.sleep(1)
        else:
            raise RuntimeError('Vision service did not start. Check /tmp/invoice-ollama.log.')
    env=dict(os.environ,OLLAMA_HOST='127.0.0.1:11434')
    if not any(entry.get('name')==model for entry in tags.get('models',[])):
        print('Downloading vision model (first setup can take several minutes):',model,flush=True)
        subprocess.run(['ollama','pull',model],env=env,check=True)
    from ollama_http import request_json
    info=request_json('http://127.0.0.1:11434/api/show',{'model':model},timeout=30)
    if 'vision' not in info.get('capabilities',[]):
        raise RuntimeError('Selected model does not support invoice images.')
    os.environ.update(USE_LOCAL_AI='true',OLLAMA_MODEL=model,
                      OLLAMA_URL='http://127.0.0.1:11434/api/chat')
    os.environ.setdefault('OLLAMA_TIMEOUT_SECONDS','300')
    print('General layout extraction ready: OCR + local vision ('+model+').',flush=True)
    return model
