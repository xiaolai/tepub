from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

TEMPLATE_PACKAGE = "web_templates"


@dataclass
class BookData:
    title: str
    spine: list[dict]
    toc: list[dict]
    documents: dict[str, str] | None = None


def _read_template(name: str) -> str:
    template_path = resources.files(TEMPLATE_PACKAGE) / name
    return template_path.read_text(encoding="utf-8")


def _copy_directory(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def copy_static_assets(output_root: Path) -> None:
    package_root = resources.files(TEMPLATE_PACKAGE)

    assets_source = package_root / "assets"
    with resources.as_file(assets_source) as src_path:
        _copy_directory(src_path, output_root / "assets")


def render_index(output_root: Path, data: BookData) -> None:
    index_template = _read_template("index.html")
    book_data_json = json.dumps(
        {
            "title": data.title,
            "spine": data.spine,
            "toc": data.toc,
            "documents": data.documents or {},
        },
        ensure_ascii=False,
    )
    contents = index_template.replace("{{BOOK_DATA}}", book_data_json)
    (output_root / "index.html").write_text(contents, encoding="utf-8")
