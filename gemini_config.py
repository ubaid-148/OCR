"""Load Gemini settings from the project .env without executing its contents."""
import os
from pathlib import Path


def load_gemini_env(path=None):
    path = Path(path) if path is not None else Path(__file__).resolve().parent / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = (part.strip() for part in line.split('=', 1))
        if name not in {'GEMINI_API_KEY', 'GEMINI_MODEL'}:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value and not os.environ.get(name, '').strip():
            os.environ[name] = value
