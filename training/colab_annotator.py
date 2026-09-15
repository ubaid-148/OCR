"""Private in-notebook annotation UI for Google Colab/Jupyter."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from training.invoice_dataset import load_manifest, new_annotation, normalize_data, validate_annotation


def _read_candidate(work_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    label = work_dir / "labels" / f"{record['doc_id']}.json"
    draft = work_dir / "drafts" / f"{record['doc_id']}.json"
    path = label if label.exists() else draft
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return new_annotation(record["source_filename"], record["source_sha256"])


def _coerce_editor_json(value: str, record: dict[str, Any]) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Editor content must be one JSON object")
    if parsed.get("annotation_version"):
        annotation = parsed
        annotation["data"] = normalize_data(annotation.get("data"))
    else:
        annotation = new_annotation(record["source_filename"], record["source_sha256"], parsed)
    annotation["annotation_version"] = "invoice-ocr-annotation-v1"
    annotation["source_filename"] = record["source_filename"]
    annotation["source_sha256"] = record["source_sha256"]
    return annotation


def launch(work_dir: str | Path, verified_by: str = ""):
    """Display the annotation dashboard and return its root widget."""
    try:
        import ipywidgets as widgets
        from IPython.display import display
    except ImportError as error:
        raise RuntimeError("Install ipywidgets before launching the annotator") from error

    root = Path(work_dir).resolve()
    manifest = load_manifest(root)
    records = manifest["documents"]
    if not records:
        raise ValueError("Workspace contains no documents")
    by_id = {record["doc_id"]: record for record in records}

    chooser = widgets.Dropdown(
        options=[(f"{index + 1:03d}  {record['source_filename']}", record["doc_id"]) for index, record in enumerate(records)],
        description="Invoice:", layout=widgets.Layout(width="650px"),
    )
    verifier = widgets.Text(value=verified_by, description="Verified by:", layout=widgets.Layout(width="500px"))
    page = widgets.IntSlider(value=1, min=1, max=1, description="Page:", continuous_update=False)
    image = widgets.Image(format="png", layout=widgets.Layout(width="720px", max_height="1050px", object_fit="contain"))
    editor = widgets.Textarea(layout=widgets.Layout(width="720px", height="820px"))
    status = widgets.HTML()
    progress = widgets.HTML()
    previous = widgets.Button(description="Previous")
    next_button = widgets.Button(description="Next")
    save = widgets.Button(description="Save draft", button_style="info")
    verify = widgets.Button(description="Verify + include", button_style="success")
    exclude = widgets.Button(description="Verify + exclude", button_style="warning")

    def current_record() -> dict[str, Any]:
        return by_id[chooser.value]

    def update_progress() -> None:
        verified_count = included_count = 0
        for record in records:
            label = root / "labels" / f"{record['doc_id']}.json"
            if not label.exists():
                continue
            try:
                value = json.loads(label.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            verified_count += value.get("verified") is True
            included_count += value.get("verified") is True and value.get("include_in_training") is True
        progress.value = f"<b>Progress:</b> {verified_count}/{len(records)} verified; {included_count} included for training"

    def show_page(*_args) -> None:
        record = current_record()
        paths = record["pages"]
        page.max = max(1, len(paths))
        page.value = min(page.value, page.max)
        image.value = Path(paths[page.value - 1]).read_bytes()

    def load(*_args) -> None:
        record = current_record()
        annotation = _read_candidate(root, record)
        editor.value = json.dumps(annotation, ensure_ascii=False, indent=2)
        page.max = max(1, len(record["pages"]))
        page.value = 1
        show_page()
        status.value = "<span style='color:#555'>Compare every field and every item row with the PDF before verification.</span>"
        update_progress()

    def persist(include: bool | None) -> bool:
        record = current_record()
        try:
            annotation = _coerce_editor_json(editor.value, record)
            if include is not None:
                name = verifier.value.strip()
                if not name:
                    raise ValueError("Enter Verified by before final verification")
                annotation.update(
                    verified=True,
                    include_in_training=include,
                    verified_by=name,
                    verified_at=datetime.now(timezone.utc).isoformat(),
                )
            else:
                annotation.update(verified=False, include_in_training=False, verified_by=None, verified_at=None)
            errors, warnings = validate_annotation(annotation, require_verified=include is not None)
            if errors:
                raise ValueError("; ".join(errors))
            existing_label = root / "labels" / f"{record['doc_id']}.json"
            # Editing a previously verified label invalidates its approval until
            # the reviewer explicitly verifies the corrected version again.
            destination = existing_label if include is not None or existing_label.exists() else root / "drafts" / f"{record['doc_id']}.json"
            destination.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
            editor.value = json.dumps(annotation, ensure_ascii=False, indent=2)
            warning_html = "<br>".join(warnings)
            status.value = "<b style='color:green'>Saved.</b>" + (f"<br><span style='color:#9a6700'>{warning_html}</span>" if warnings else "")
            update_progress()
            return True
        except (OSError, ValueError, json.JSONDecodeError) as error:
            status.value = f"<b style='color:#b00020'>Not saved:</b> {str(error)}"
            return False

    def move(offset: int) -> None:
        index = next(i for i, record in enumerate(records) if record["doc_id"] == chooser.value)
        chooser.value = records[max(0, min(len(records) - 1, index + offset))]["doc_id"]

    chooser.observe(load, names="value")
    page.observe(show_page, names="value")
    previous.on_click(lambda _button: move(-1))
    next_button.on_click(lambda _button: move(1))
    save.on_click(lambda _button: persist(None))
    verify.on_click(lambda _button: persist(True) and move(1))
    exclude.on_click(lambda _button: persist(False) and move(1))
    controls = widgets.HBox([previous, next_button, save, verify, exclude])
    dashboard = widgets.VBox([
        widgets.HTML("<h3>Private invoice ground-truth review</h3>"),
        progress, chooser, verifier, page, controls,
        widgets.HBox([image, editor], layout=widgets.Layout(align_items="flex-start")), status,
    ])
    load()
    display(dashboard)
    return dashboard
