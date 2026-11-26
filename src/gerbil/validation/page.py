"""Write the annotation bundle into the single-file page annotators open in a browser."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

PAGE_FILE = "annotate.html"
_PLACEHOLDER = "__GERBIL_BUNDLE_JSON__"


def render_page(bundle: dict[str, Any]) -> str:
    template = (
        resources.files("gerbil.validation").joinpath(PAGE_FILE).read_text("utf-8")
    )
    # A "</script" or "<!--" in a source line would end or derail the script
    # element that carries the bundle.
    data = json.dumps(bundle, separators=(",", ":")).replace("<", "\\u003c")
    return template.replace(_PLACEHOLDER, data)


def write_page(bundle: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_page(bundle), encoding="utf-8")
