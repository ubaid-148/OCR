"""Publish an approved adapter to a public GitHub Release, not regular Git history."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from training.adapter_service import load_approval


REPO = "ubaid-148/OCR"
ASSET_NAME = "invoice-adapter-v2.zip"
API = f"https://api.github.com/repos/{REPO}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request(url: str, token: str | None = None, data: bytes | None = None,
             content_type: str | None = None) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "OCR-Colab-Training"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and data is None:
            raise FileNotFoundError(url) from error
        detail = error.read(2000).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {error.code}: {detail}") from error


def bundle_adapter(approval_path: Path, output: Path) -> tuple[Path, str]:
    approval = load_approval(approval_path)
    adapter = Path(approval["adapter_dir"])
    source = json.loads(approval_path.read_text(encoding="utf-8"))
    def source_path(value: str) -> Path:
        path = Path(value)
        return (path if path.is_absolute() else approval_path.parent / path).resolve()
    base = source_path(source["base_test_metrics"])
    candidate = source_path(source["adapter_test_metrics"])
    portable = dict(source, adapter_dir="adapter",
                    base_test_metrics="base-test-metrics.json",
                    adapter_test_metrics="adapter-test-metrics.json")
    tag = f"invoice-adapter-v2-{approval['adapter_sha256'][:12]}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(adapter.iterdir()):
            if path.name == "adapter_config.json" or path.name.startswith("adapter_model."):
                archive.write(path, f"adapter/{path.name}")
        archive.write(base, "base-test-metrics.json")
        archive.write(candidate, "adapter-test-metrics.json")
        archive.writestr("adapter_approval.json", json.dumps(portable, indent=2))
    return output, tag


def publish_bundle(archive: Path, tag: str, token: str) -> dict:
    if not token or not token.strip():
        raise ValueError("GITHUB_TOKEN is required in Colab Secrets to publish the approved adapter")
    url = f"{API}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    try:
        release = _request(url, token)
    except FileNotFoundError:
        body = json.dumps({"tag_name": tag, "target_commitish": "main", "name": tag,
                           "body": "Experimental source-verified invoice adapter; inspect held-out metrics before use.",
                           "draft": False, "prerelease": True}).encode()
        release = _request(f"{API}/releases", token, body, "application/json")
    existing = next((item for item in release.get("assets", []) if item.get("name") == ASSET_NAME), None)
    if existing:
        if existing.get("digest") == f"sha256:{_sha256(archive)}":
            return {"tag": tag, "release_url": release["html_url"], "already_uploaded": True}
        raise ValueError("Release tag already has a different adapter asset; refusing to overwrite it")
    upload_url = release["upload_url"].split("{", 1)[0]
    upload_url += "?" + urllib.parse.urlencode({"name": ASSET_NAME})
    asset = _request(upload_url, token, archive.read_bytes(), "application/zip")
    if asset.get("state") != "uploaded":
        raise RuntimeError("GitHub did not confirm that the adapter asset was uploaded")
    return {"tag": tag, "release_url": release["html_url"],
            "asset_url": asset.get("browser_download_url"), "already_uploaded": False}


def download_bundle(tag: str, destination: Path) -> Path:
    if not tag.startswith("invoice-adapter-v2-") or not tag.removeprefix("invoice-adapter-v2-").isalnum():
        raise ValueError("Enter the exact invoice-adapter-v2 release tag printed by the training notebook")
    destination = destination.resolve()
    approval = destination / "adapter_approval.json"
    if approval.is_file():
        load_approval(approval)
        return approval
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Adapter destination is not empty; use a fresh Colab runtime")
    release = _request(f"{API}/releases/tags/{urllib.parse.quote(tag, safe='')}")
    asset = next((item for item in release.get("assets", []) if item.get("name") == ASSET_NAME), None)
    if not asset:
        raise ValueError(f"Release {tag} has no {ASSET_NAME} asset")
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / ASSET_NAME
    request = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "OCR-Colab-Setup"})
    with urllib.request.urlopen(request, timeout=900) as response, archive_path.open("wb") as output:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            output.write(chunk)
    if asset.get("digest") and asset["digest"] != f"sha256:{_sha256(archive_path)}":
        raise ValueError("Downloaded GitHub release asset digest does not match")
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            target = (destination / member.filename).resolve()
            if destination not in target.parents or target.exists():
                raise ValueError("Unexpected or duplicate path in adapter archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
    load_approval(approval)
    return approval
