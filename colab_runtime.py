"""Idempotent Google Colab runtime preparation for the invoice notebook."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


OCR_ENV_DIR = Path("/content/ocr-runtime")
OCR_PYTHON = OCR_ENV_DIR / "bin" / "python"
READY_MARKER = OCR_ENV_DIR / ".invoice-ocr-ready.json"
PADDLE_VERSION = "3.3.1"
PADDLE_GPU_INDEX = "https://www.paddlepaddle.org.cn/packages/stable/cu126/"
APT_PACKAGES = (
    "tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-ara",
    "tesseract-ocr-urd", "ghostscript", "unpaper", "pngquant", "zstd",
)


def gpu_attached() -> bool:
    return bool(shutil.which("nvidia-smi")) and subprocess.run(
        ["nvidia-smi", "-L"], capture_output=True
    ).returncode == 0


def configure_environment(use_gpu: bool) -> None:
    os.environ["OCR_TARGETED_RETRY"] = "true"
    os.environ["OCR_FORCE_RASTER"] = "true"
    os.environ["USE_LOCAL_AI"] = "false"
    os.environ["OCR_DEVICE"] = "gpu:0" if use_gpu else "cpu"
    os.environ.pop("OCR_PYTHON_EXE", None)
    os.environ.setdefault("FLAGS_use_mkldnn", "0")


def _fingerprint(project_dir: Path, use_gpu: bool) -> str:
    digest = hashlib.sha256()
    digest.update((project_dir / "requirements.txt").read_bytes())
    digest.update(Path(__file__).read_bytes())
    digest.update(f"{sys.version_info[:2]}:{PADDLE_VERSION}:{use_gpu}".encode())
    return digest.hexdigest()


def _marker_matches(fingerprint: str) -> bool:
    if not OCR_PYTHON.is_file() or not READY_MARKER.is_file():
        return False
    try:
        return json.loads(READY_MARKER.read_text(encoding="utf-8")).get("fingerprint") == fingerprint
    except (OSError, ValueError, TypeError):
        return False


def _run_checks(project_dir: Path) -> None:
    verification = subprocess.run(
        [str(OCR_PYTHON), "-u", str(project_dir / "check_ocr_runtime.py")],
        cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    )
    verification_log = Path("/tmp/ocr-runtime-check.log")
    verification_log.write_text(verification.stdout, encoding="utf-8")
    print(verification.stdout, flush=True)
    if verification.returncode:
        raise RuntimeError(
            f"OCR runtime verification failed (exit {verification.returncode}). "
            f"Full log: {verification_log}. Copy the error below:\n\n"
            + verification.stdout[-12000:]
        )

    smoke = subprocess.run(
        [str(OCR_PYTHON), "-m", "unittest", "-q",
         "test_9480_regression.FaintSourceRegressionTests."
         "test_fresh_english_retry_evidence_recovers_printed_fields"],
        cwd=project_dir, env=os.environ.copy(), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, errors="replace",
    )
    print(smoke.stdout, flush=True)
    if smoke.returncode:
        raise RuntimeError("Invoice parser sample check failed. See the output above.")


def prepare_runtime(project_dir: str | Path, require_gpu: bool = True) -> str:
    """Install or reuse the isolated OCR environment, then verify it."""
    project_dir = Path(project_dir).resolve()
    requirements_path = project_dir / "requirements.txt"
    if not requirements_path.is_file():
        raise FileNotFoundError(f"{requirements_path} is missing. Load the project first.")

    use_gpu = gpu_attached()
    print("GPU attached:", use_gpu, flush=True)
    if require_gpu and not use_gpu:
        raise RuntimeError(
            "No GPU is attached. In Colab choose Runtime > Change runtime type > "
            "T4 GPU, reconnect, then run the cell again."
        )
    configure_environment(use_gpu)
    fingerprint = _fingerprint(project_dir, use_gpu)

    if _marker_matches(fingerprint):
        print("Reusing verified OCR environment.", flush=True)
    else:
        print("Preparing OCR environment. This can take several minutes.", flush=True)
        subprocess.run(["apt-get", "update", "-qq"], check=True)
        subprocess.run(["apt-get", "install", "-y", "-qq", *APT_PACKAGES], check=True)
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(OCR_ENV_DIR)],
            check=True,
        )
        ocr_pip = [sys.executable, "-m", "pip", "--python", str(OCR_PYTHON)]
        subprocess.run([*ocr_pip, "install", "-q", "--upgrade", "pip"], check=True)
        subprocess.run(
            [*ocr_pip, "install", "-q", "torch==2.9.1+cpu",
             "--index-url", "https://download.pytorch.org/whl/cpu"],
            check=True,
        )
        subprocess.run(
            [*ocr_pip, "uninstall", "-y", "paddlepaddle", "paddlepaddle-gpu"],
            check=True,
        )
        requirements = [
            line.strip()
            for line in requirements_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("paddlepaddle")
        ]
        subprocess.run([*ocr_pip, "install", "-q", *requirements], check=True)
        paddle = [*ocr_pip, "install", "-q"]
        if use_gpu:
            paddle += [
                f"paddlepaddle-gpu=={PADDLE_VERSION}", "-i", PADDLE_GPU_INDEX,
            ]
        else:
            paddle += [f"paddlepaddle=={PADDLE_VERSION}"]
        subprocess.run(paddle, check=True)

    try:
        _run_checks(project_dir)
    except Exception:
        READY_MARKER.unlink(missing_ok=True)
        raise
    READY_MARKER.write_text(
        json.dumps({"fingerprint": fingerprint, "gpu": use_gpu}),
        encoding="utf-8",
    )
    print("Parser sample check passed. Ready to upload your invoice.", flush=True)
    return str(OCR_PYTHON)
