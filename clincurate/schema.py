"""Load and validate a ClinCurate project schema (YAML)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FIELD_TYPES = {"single_choice", "multi_choice", "numeric", "text"}
SCREEN_QUESTION = "documented_domains"


class SchemaError(ValueError):
    pass


@dataclass
class Field:
    name: str
    label: str
    type: str
    domain: str | None = None
    values: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    help: str = ""
    units: str | None = None
    range: tuple[float, float] | None = None
    required: bool = False


@dataclass
class Domain:
    name: str
    label: str
    color: str
    cues: list[str]
    fields: list[Field]


@dataclass
class RegexCue:
    name: str
    domain: str
    pattern: re.Pattern


@dataclass
class Project:
    id: str
    title: str
    unit: str
    mode: str
    missing: dict[str, str]
    domains: list[Domain]
    regex_cues: list[RegexCue]
    record_questions: list[Field]
    focus: dict[str, Any]

    def domain(self, name: str) -> Domain:
        for d in self.domains:
            if d.name == name:
                return d
        raise KeyError(name)

    @property
    def fields(self) -> list[Field]:
        return [f for d in self.domains for f in d.fields]

    def choice_options(self, f: Field) -> list[str]:
        """Options shown to the annotator, including missing-value options."""
        opts = list(f.values)
        opts.append(self.missing["not_documented"])
        for key in f.allow:
            opts.append(self.missing[key])
        return opts


def _parse_field(name: str, raw: dict, domain: str | None) -> Field:
    ftype = raw.get("type")
    if ftype not in FIELD_TYPES:
        raise SchemaError(f"{name}: type must be one of {sorted(FIELD_TYPES)}, got {ftype!r}")
    if ftype in {"single_choice", "multi_choice"} and not raw.get("values"):
        raise SchemaError(f"{name}: choice fields need values")
    rng = raw.get("range")
    return Field(
        name=name,
        label=raw.get("label", name),
        type=ftype,
        domain=domain,
        values=[str(v) for v in raw.get("values", [])],
        allow=list(raw.get("allow", [])),
        help=(raw.get("help") or "").strip(),
        units=raw.get("units"),
        range=tuple(rng) if rng else None,
        # Domain fields are optional so the screening question can stand in
        # for conditional display. Record-level questions default to required.
        required=raw.get("required", domain is None),
    )


def load_schema(path: str | Path) -> Project:
    raw = yaml.safe_load(Path(path).read_text())
    proj = raw.get("project") or {}
    missing = {
        "not_documented": "Not documented",
        "unclear": "Unclear",
        "not_applicable": "Not applicable",
        **(proj.get("missing_values") or {}),
    }

    domains = []
    seen: set[str] = {SCREEN_QUESTION}
    for dname, draw in (raw.get("domains") or {}).items():
        fields = []
        for fname, fraw in (draw.get("fields") or {}).items():
            if fname in seen:
                raise SchemaError(f"duplicate field name {fname!r}")
            seen.add(fname)
            f = _parse_field(fname, fraw, dname)
            for key in f.allow:
                if key not in missing:
                    raise SchemaError(f"{fname}: unknown allow value {key!r}")
            fields.append(f)
        domains.append(
            Domain(
                name=dname,
                label=draw.get("label", dname),
                color=draw.get("color", "#fef08a"),
                cues=[str(c) for c in draw.get("cues", [])],
                fields=fields,
            )
        )
    if not domains:
        raise SchemaError("schema defines no domains")

    dnames = {d.name for d in domains}
    regex_cues = []
    for rc in raw.get("regex_cues") or []:
        if rc["domain"] not in dnames:
            raise SchemaError(f"regex cue {rc['name']}: unknown domain {rc['domain']!r}")
        try:
            pat = re.compile(rc["pattern"])
        except re.error as e:
            raise SchemaError(f"regex cue {rc['name']}: {e}") from e
        regex_cues.append(RegexCue(rc["name"], rc["domain"], pat))

    record_questions = []
    for qname, qraw in (raw.get("record_questions") or {}).items():
        if qname in seen:
            raise SchemaError(f"duplicate field name {qname!r}")
        seen.add(qname)
        record_questions.append(_parse_field(qname, qraw, None))

    focus = {
        "context_sentences_before": 1,
        "context_sentences_after": 1,
        "merge_overlapping": True,
        "max_snippets_per_domain": 6,
        **(raw.get("focus_mode") or {}),
    }

    return Project(
        id=proj.get("id", Path(path).stem),
        title=proj.get("title", Path(path).stem),
        unit=proj.get("unit", "note"),
        mode=proj.get("mode", "gold_standard"),
        missing=missing,
        domains=domains,
        regex_cues=regex_cues,
        record_questions=record_questions,
        focus=focus,
    )
