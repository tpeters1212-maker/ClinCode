"""clincurate command line.

  clincurate validate SCHEMA
  clincurate preview SCHEMA NOTES.csv --out preview.html
  clincurate guide SCHEMA --out guide.md
"""

from __future__ import annotations

import argparse
import csv
import html
import sys
from pathlib import Path

from .argilla_backend import render_guidelines
from .focus import render_focus_html
from .highlight import Highlighter
from .schema import SchemaError, load_schema


def read_notes(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        notes = list(csv.DictReader(fh))
    for i, n in enumerate(notes):
        if not n.get("note_id") or not n.get("text"):
            raise SystemExit(f"{path}: row {i + 2} needs note_id and text")
    return notes


def preview_html(schema_path: str, notes_path: str) -> str:
    project = load_schema(schema_path)
    hl = Highlighter(project)
    legend = "".join(
        f'<span style="background:{d.color};padding:2px 6px;margin-right:6px;border-radius:3px">'
        f"{html.escape(d.label)}</span>"
        for d in project.domains
    )
    cards = []
    for n in read_notes(notes_path):
        meta = " · ".join(html.escape(n[k]) for k in ("patient_id", "note_date", "note_type") if n.get(k))
        cards.append(
            f'<article><h3>{html.escape(n["note_id"])} <small>{meta}</small></h3>'
            f'{render_focus_html(n["text"], project, hl)}'
            f'<details><summary>Full note</summary><pre>{hl.render(n["text"])}</pre></details></article>'
        )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClinCurate preview</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:860px;margin:0 auto;padding:16px;background:#fff;color:#111}}
article{{border:1px solid #e2e8f0;border-radius:6px;padding:12px 16px;margin:16px 0}}
pre{{white-space:pre-wrap;font-family:inherit}}
.cc-snippet{{background:#f8fafc;padding:6px 8px;border-radius:4px}}
mark{{color:#111}}
</style></head><body>
<h1>{html.escape(project.title)}</h1><p>{legend}</p>{"".join(cards)}</body></html>"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clincurate")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="check a schema file")
    v.add_argument("schema")
    pv = sub.add_parser("preview", help="write an offline HTML preview of Focus Mode")
    pv.add_argument("schema")
    pv.add_argument("notes")
    pv.add_argument("--out", default="preview.html")
    g = sub.add_parser("guide", help="write the annotation guide generated from a schema")
    g.add_argument("schema")
    g.add_argument("--out", default="-")
    args = p.parse_args(argv)

    try:
        if args.cmd == "validate":
            proj = load_schema(args.schema)
            print(f"ok: {proj.id}, {len(proj.domains)} domains, {len(proj.fields)} fields")
        elif args.cmd == "preview":
            Path(args.out).write_text(preview_html(args.schema, args.notes), encoding="utf-8")
            print(f"wrote {args.out}")
        elif args.cmd == "guide":
            text = render_guidelines(load_schema(args.schema))
            sys.stdout.write(text) if args.out == "-" else Path(args.out).write_text(text, encoding="utf-8")
    except SchemaError as e:
        print(f"schema error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
