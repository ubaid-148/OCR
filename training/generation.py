"""Bound draft generation without repairing incomplete or malformed JSON."""
import json


def complete_object(text):
    """Return the first complete object, allowing only an optional JSON code fence."""
    text = text.strip()
    if text.startswith('```json'):
        text = text[7:].lstrip()
    elif text.startswith('```'):
        text = text[3:].lstrip()
    if not text.startswith('{'):
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    return text[:end] if isinstance(value, dict) else None


class GenerationMonitor:
    def __init__(self, seconds):
        if seconds <= 0:
            raise ValueError('Generation seconds must be positive')
        self.seconds = seconds
        self.reason = None
        self.last_progress = 0

    def check(self, text, tokens, elapsed):
        if elapsed - self.last_progress >= 15:
            print(f'  generated {tokens} tokens in {elapsed:.1f}s', flush=True)
            self.last_progress = elapsed
        if complete_object(text) is not None:
            self.reason = 'json_complete'
        elif elapsed >= self.seconds:
            self.reason = 'time_limit'
        return self.reason is not None
