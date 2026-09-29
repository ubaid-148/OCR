"""Start Gemini using the project .env, environment, or a hidden key prompt."""
from getpass import getpass
import os


def main():
    from gemini_config import load_gemini_env
    load_gemini_env()
    from google import genai  # Fail before prompting if dependencies are missing.
    import jsonschema
    key = os.environ.get('GEMINI_API_KEY', '').strip() or getpass('Google AI Studio API key (hidden): ').strip()
    if not key:
        raise SystemExit('API key required. Create one at https://aistudio.google.com/api-keys')
    os.environ['GEMINI_API_KEY'] = key
    os.environ['OCR_DEFAULT_MODE'] = 'gemini'
    from ocr_web import run_server
    print('Gemini selected. PDFs are sent to Google; use a Free-tier project for free testing.', flush=True)
    print('Key loaded. The app does not print or write your key.', flush=True)
    run_server()


if __name__ == '__main__':
    main()
