"""Verify the OCR runtime in stages without downloading recognition models."""
from __future__ import annotations

import os
import sys


def main() -> None:
    # Set flags before Paddle is imported, as in the web application.
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    print(f"Python: {sys.version}", flush=True)
    print(f"Requested OCR device: {os.environ.get('OCR_DEVICE', 'auto')}", flush=True)
    print("[1/3] Importing Paddle", flush=True)
    import paddle

    print(f"Paddle: {paddle.__version__}; CUDA build: {paddle.is_compiled_with_cuda()}", flush=True)
    print("[2/3] Importing PDF and OCR dependencies", flush=True)
    from coordinate_ocr import get_ocr_device

    device = get_ocr_device()
    print(f"[3/3] Running tensor operations on {device}", flush=True)
    paddle.set_device(device)
    # Exercise actual computation and transfer back to the host, rather than
    # relying solely on a CUDA build flag or enumerating visible GPUs.
    values = paddle.ones([2, 2], dtype="float32")
    result = paddle.matmul(values, values).numpy()
    if not (result == 2).all():
        raise RuntimeError(f"Paddle computation produced an unexpected result: {result}")
    print(f"OCR device: {device}", flush=True)
    print("Runtime checks passed (full OCR inference is tested on upload).", flush=True)


if __name__ == "__main__":
    main()
