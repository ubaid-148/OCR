"""Publish source-verified public labels to GitHub without storing the token."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

from training.invoice_dataset import load_manifest, validate_annotation


EXPECTED_REMOTE = "https://github.com/ubaid-148/OCR"


def _git(project_dir: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", "-C", str(project_dir), *args], env=env,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {(result.stderr or result.stdout)[-1500:]}")
    return result.stdout.strip()


def push_verified_labels(project_dir: Path, work_dir: Path, token: str) -> dict:
    project_dir = project_dir.resolve()
    labels_dir = project_dir / "public_invoice_labels"
    if not token or not token.strip():
        raise ValueError("Add GITHUB_TOKEN to Colab Secrets with Contents: Read and write for ubaid-148/OCR")
    if _git(project_dir, "rev-parse", "--show-toplevel") != str(project_dir):
        raise ValueError("PROJECT_DIR is not the Git clone root")
    remote = _git(project_dir, "remote", "get-url", "origin").removesuffix(".git").rstrip("/")
    if remote.lower() != EXPECTED_REMOTE.lower():
        raise ValueError("Refusing to push invoice labels to an unexpected Git remote")
    existing_staged = _git(project_dir, "diff", "--cached", "--name-only")
    if existing_staged:
        raise ValueError("Commit or unstage existing changes before publishing labels")
    manifest = load_manifest(work_dir)
    if Path(manifest["labels_root"]).resolve() != labels_dir:
        raise ValueError("Manifest does not point to the public invoice labels directory")
    by_id = {record["doc_id"]: record for record in manifest["documents"]}
    labels = sorted(labels_dir.glob("*.json"))
    tracked_labels = set(_git(project_dir, "ls-files", "--", "public_invoice_labels").splitlines())
    valid = []
    for path in labels:
        relative = path.relative_to(project_dir).as_posix()
        annotation = json.loads(path.read_text(encoding="utf-8"))
        errors, _ = validate_annotation(annotation, require_verified=True)
        record = by_id.get(path.stem)
        if record is None or annotation.get("source_sha256") != record["source_sha256"] or annotation.get("source_filename") != record["source_filename"]:
            errors.append("source PDF identity does not match the current manifest")
        if annotation.get("verified") is True and errors:
            raise ValueError(f"{path.name}: " + "; ".join(errors))
        if annotation.get("verified") is not True and relative in tracked_labels:
            raise ValueError(f"{path.name}: a published label was edited but not re-verified; verify it before pushing another batch")
        if not errors:
            valid.append(path)
    if not valid:
        raise ValueError("No source-verified v2 labels to publish; use Verify + include/exclude in the dashboard")
    _git(project_dir, "add", "--", *(str(path.relative_to(project_dir)) for path in valid))
    staged = subprocess.run(["git", "-C", str(project_dir), "diff", "--cached", "--quiet"], check=False)
    if staged.returncode not in (0, 1):
        raise RuntimeError("Could not inspect staged labels")
    if staged.returncode == 1:
        _git(project_dir, "-c", "user.name=Invoice OCR Reviewer",
             "-c", "user.email=invoice-ocr@users.noreply.github.com",
             "commit", "-m", f"Add {len(valid)} source-verified invoice labels")
    if int(_git(project_dir, "rev-list", "--count", "origin/main..HEAD")) == 0:
        return {"verified_labels": len(valid), "pushed": False, "message": "GitHub already has these verified labels"}
    authorization = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GIT_CONFIG_COUNT="1",
               GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
               GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {authorization}")
    try:
        _git(project_dir, "push", "origin", "HEAD:main", env=env)
    except RuntimeError as error:
        raise RuntimeError(str(error).replace(token, "[redacted]").replace(authorization, "[redacted]")) from error
    return {"verified_labels": len(valid), "pushed": True,
            "commit": _git(project_dir, "rev-parse", "--short", "HEAD")}
