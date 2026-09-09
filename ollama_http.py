"""Small shared HTTP client that preserves Ollama's error response."""
import json
import urllib.error
import urllib.request


def request_json(url, payload, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            detail = error.read(12000).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = str(parsed.get("error", detail)) if isinstance(parsed, dict) else detail
        except ValueError:
            pass
        raise RuntimeError(f"Ollama HTTP {error.code}: {detail or error.reason}") from error
    if not isinstance(result, dict):
        raise ValueError("Ollama returned a non-object response")
    if result.get("error"):
        raise RuntimeError(f"Ollama: {result['error']}")
    return result


def preload(model, context=8192):
    """A failed optimization must not disable AI or prevent starting OCR."""
    try:
        result = request_json("http://127.0.0.1:11434/api/chat", {
            "model": model, "messages": [], "stream": False,
            "keep_alive": "30m", "options": {"num_ctx": context},
        }, timeout=180)
        if result.get("done") is not True:
            raise ValueError("Ollama did not confirm that model loading completed")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"AI preload failed: {error}", flush=True)
        print("OCR setup can continue. AI remains enabled and will retry on an invoice that needs it. "
              "If it fails again, the result will be marked for review. Check /tmp/ollama.log.", flush=True)
        return False
    print("AI model loaded.", flush=True)
    return True
