"""In-notebook side-by-side page and JSON review; no external service required."""
import json
from pathlib import Path

from training.draft_checks import draft_warnings
from training.data import FULL_SCHEMA, validate_schema, write_json


def review(workspace):
    import ipywidgets as widgets
    from IPython.display import display, clear_output
    from PIL import Image

    workspace = Path(workspace)
    paths = sorted((workspace / "labels").glob("*.json"))
    if not paths:
        raise ValueError("Run data preparation first")
    picker = widgets.Dropdown(options=[(p.stem, str(p)) for p in paths], description="Page")
    editor = widgets.Textarea(layout=widgets.Layout(width="100%", height="650px"))
    group = widgets.Text(description="Layout group", placeholder="Same supplier/template = same group")
    reviewer = widgets.Text(description="Reviewer")
    rotation = widgets.Dropdown(options=[0, 90, 180, 270], description="Rotate CCW")
    verified = widgets.Checkbox(description="I checked all visible fields, rows, notes and extra labels")
    save = widgets.Button(description="Save review")
    suggestion = widgets.Button(description="Load model draft")
    page_output, messages = widgets.Output(), widgets.Output()
    state = {}

    def show_image(*_):
        with page_output:
            clear_output(wait=True)
            with Image.open(workspace / state["record"]["image"]) as image:
                display(image.rotate(rotation.value, expand=True))

    def show_warnings(target):
        with messages:
            clear_output()
            for error in state["record"].get("draft_errors", []):
                print("Generation error:", error)
            for warning in draft_warnings(target):
                print("Review:", warning)

    def load(*_):
        record = json.loads(Path(picker.value).read_text())
        state["record"] = record
        editor.value = json.dumps(record["target"], ensure_ascii=False, indent=2)
        group.value = record.get("layout_group", "")
        reviewer.value = record.get("reviewer", "") or reviewer.value
        verified.value = record.get("status") == "verified"
        rotation.value = record.get("rotation_ccw", 0)
        show_image()
        show_warnings(record["target"])

    def persist(_):
        with messages:
            clear_output()
            try:
                target = json.loads(editor.value)
                validate_schema(target, FULL_SCHEMA)
                if verified.value and (not group.value.strip() or not reviewer.value.strip()):
                    raise ValueError("Enter reviewer and layout group before marking verified")
                record = dict(state["record"], target=target, layout_group=group.value.strip(),
                              reviewer=reviewer.value.strip(), rotation_ccw=rotation.value,
                              status="verified" if verified.value else "draft",
                              all_visible_fields_checked=verified.value)
                if verified.value and record.get("unreadable_fields"):
                    raise ValueError("Resolve unreadable_fields in the label file before verification")
                write_json(picker.value, record)
                state["record"] = record
                print("Saved", Path(picker.value).name, record["status"])
            except (ValueError, TypeError) as error:
                print(error)

    def use_suggestion(_):
        candidate = state["record"].get("suggested_target")
        if candidate is not None:
            target = dict(state["record"]["target"], **candidate)
            editor.value = json.dumps(target, ensure_ascii=False, indent=2)
            verified.value = False
            show_warnings(target)

    suggestion.on_click(use_suggestion)
    picker.observe(load, names="value")
    rotation.observe(show_image, names="value")
    save.on_click(persist)
    load()
    display(widgets.VBox([picker, group, reviewer, rotation, verified, suggestion, save, messages,
                         widgets.HBox([widgets.Box([page_output], layout=widgets.Layout(width="55%", height="700px", overflow="auto")),
                                       widgets.Box([editor], layout=widgets.Layout(width="45%"))])]))
