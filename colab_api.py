"""Run the invoice OCR HTTP API in Colab and expose it through a temporary URL.

Run this after ``colab_setup.ipynb`` has prepared the OCR runtime. The process
stays in the foreground deliberately: stopping the cell ends the public API.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-linux-amd64"
)
LOCAL_TUNNEL_URL = re.compile(r"https://[-a-z0-9]+\.loca\.lt", re.IGNORECASE)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--ocr-python", default=sys.executable,
                        help="Python interpreter with PaddleOCR installed")
    result.add_argument("--port", type=int, default=8765)
    result.add_argument(
        "--openapi",
        action="store_true",
        help="Run the Swagger/OpenAPI server instead of the legacy web handler.",
    )
    return result


def cloudflared(project: Path) -> str:
    installed = shutil.which("cloudflared")
    if installed:
        return installed
    target = project / ".colab-cloudflared"
    if not target.exists():
        print("Downloading Cloudflare temporary tunnel client...", flush=True)
        subprocess.run(
            ["curl", "-fsSL", "--connect-timeout", "20", "--max-time", "120",
             CLOUDFLARED_URL, "-o", str(target)],
            check=True,
        )
        target.chmod(0o755)
    return str(target)


def ensure_runtime_package(ocr_python: Path, module: str, requirement: str) -> None:
    """Install a required OCR-runtime wheel when a reused Colab venv lacks it."""
    check = subprocess.run(
        [str(ocr_python), "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode == 0:
        return
    print(f"Installing missing OCR dependency: {requirement}", flush=True)
    subprocess.run(
        [str(ocr_python), "-m", "pip", "install", "-q", requirement],
        check=True,
    )


def main() -> int:
    args = parser().parse_args()
    project = Path(__file__).resolve().parent
    ocr_python = Path(args.ocr_python).resolve()
    if not ocr_python.is_file():
        raise SystemExit(f"OCR Python was not found: {ocr_python}")

    server_name = "OpenAPI / Swagger" if args.openapi else "legacy OCR"
    print(f"Starting {server_name} server on port {args.port}...", flush=True)
    ensure_runtime_package(ocr_python, "pypdfium2", "pypdfium2==5.13.0")

    environment = os.environ.copy()
    environment["OCR_PYTHON_EXE"] = str(ocr_python)
    api_log = project / "ocr-api.log"
    with api_log.open("w", encoding="utf-8") as log:
        command = (
            [str(ocr_python), "-m", "uvicorn", "openapi_server:app",
             "--host", "127.0.0.1", "--port", str(args.port)]
            if args.openapi
            else [str(ocr_python), "-u", str(project / "ocr_web.py")]
        )
        api = subprocess.Popen(
            command, cwd=project, env=environment, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(2)
            if api.poll() is not None:
                raise RuntimeError("OCR API stopped during startup. Check " + str(api_log))

            tunnel_log_path = project / "cloudflared.log"
            tunnel_log = tunnel_log_path.open("w", encoding="utf-8")
            tunnel = subprocess.Popen(
                # Colab can pass QUIC pre-checks but still stall before issuing
                # a Quick Tunnel address. HTTP/2 is the documented TCP fallback.
                [cloudflared(project), "tunnel", "--protocol", "http2",
                 "--url", f"http://127.0.0.1:{args.port}"],
                stdout=tunnel_log, stderr=subprocess.STDOUT, text=True,
            )
            print("Creating public API URL...", flush=True)
            public_url = None
            # Recent cloudflared builds may run connectivity pre-checks for more
            # than one minute in Colab before printing the Quick Tunnel URL.
            deadline = time.monotonic() + 60
            next_progress_notice = time.monotonic() + 30
            while time.monotonic() < deadline:
                tunnel_log.flush()
                log_text = tunnel_log_path.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"https://[-a-z0-9]+\\.trycloudflare\\.com", log_text)
                if match:
                    public_url = match.group(0)
                    break
                if tunnel.poll() is not None:
                    raise RuntimeError("Tunnel stopped. Log:\n" + log_text[-2000:])
                if time.monotonic() >= next_progress_notice:
                    print("Still waiting for Cloudflare tunnel URL...", flush=True)
                    next_progress_notice += 30
                time.sleep(0.5)
            if not public_url:
                # Quick Tunnels sometimes finish their connectivity checks but
                # fail to provision an address. Keep the API usable in Colab.
                print("Cloudflare did not issue a URL; trying localtunnel fallback...", flush=True)
                if tunnel.poll() is None:
                    tunnel.send_signal(signal.SIGTERM)
                    try:
                        tunnel.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        tunnel.kill()
                tunnel_log.close()
                localtunnel_log_path = project / "localtunnel.log"
                tunnel_log = localtunnel_log_path.open("w", encoding="utf-8")
                npx = shutil.which("npx")
                if not npx:
                    raise RuntimeError(
                        "Cloudflare did not issue a URL and npx is unavailable. Cloudflare log:\n"
                        + log_text[-2000:]
                    )
                tunnel = subprocess.Popen(
                    [npx, "--yes", "localtunnel", "--port", str(args.port)],
                    stdout=tunnel_log, stderr=subprocess.STDOUT, text=True,
                )
                deadline = time.monotonic() + 90
                fallback_log = ""
                while time.monotonic() < deadline:
                    tunnel_log.flush()
                    fallback_log = localtunnel_log_path.read_text(encoding="utf-8", errors="replace")
                    match = LOCAL_TUNNEL_URL.search(fallback_log)
                    if match:
                        public_url = match.group(0)
                        break
                    if tunnel.poll() is not None:
                        raise RuntimeError("localtunnel stopped. Log:\n" + fallback_log[-2000:])
                    time.sleep(0.5)
                if not public_url:
                    raise RuntimeError("localtunnel did not issue a URL. Log:\n" + fallback_log[-2000:])

            print("\nPOSTMAN API URL (POST):", public_url + "/api/v1/extract", flush=True)
            if args.openapi:
                print("Swagger documentation:", public_url + "/docs", flush=True)
            print("Body: form-data | document: File (PDF) | languages: eng+ara", flush=True)
            print("Keep this cell running. Ctrl+C stops the API and closes the URL.\n", flush=True)
            while api.poll() is None and tunnel.poll() is None:
                time.sleep(1)
            raise RuntimeError("The OCR API or tunnel stopped. Check " + str(api_log))
        except KeyboardInterrupt:
            print("Stopping OCR API and tunnel.", flush=True)
        finally:
            if "tunnel_log" in locals():
                tunnel_log.close()
            for process in (locals().get("tunnel"), api):
                if process and process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
