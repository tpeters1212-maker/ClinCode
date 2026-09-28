"""Turn raw annotator responses into analysis-ready rows.

`responses` is a plain dict of question name -> value, as the annotation
form saves it (str for single choice and text, list for multi choice,
list of span dicts for span questions).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import SCREEN_QUESTION, Field, Project

EVIDENCE_QUESTION = "evidence"
_NUM = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*(?:°|deg(?:rees?)?)?\s*$", re.IGNORECASE)
UNCLEAR_TOKENS = {"?", "unclear", "unc"}


@dataclass
class ResolvedRecord:
    record_id: str
    annotator: str
    values: dict[str, object]
    # How each value was obtained: answered, screened_out, blank, invalid
    provenance: dict[str, str]
    evidence: list[dict] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return [k for k, v in self.provenance.items() if v in {"blank", "invalid"}]


def _numeric(f: Field, raw: object, project: Project) -> tuple[object, str]:
    if raw is None or str(raw).strip() == "":
        # Blank numeric inside a documented domain: that side or position was
        # not measured. Common and expected (e.g. only one side examined).
        return project.missing["not_documented"], "blank_numeric"
    s = str(raw).strip()
    if s.lower() in UNCLEAR_TOKENS:
        return project.missing["unclear"], "answered"
    m = _NUM.match(s)
    if not m:
        return s, "invalid"
    val = float(m.group(1))
    if f.range and not (f.range[0] <= val <= f.range[1]):
        return val, "invalid"
    return (int(val) if val.is_integer() else val), "answered"


def resolve(project: Project, record_id: str, annotator: str, responses: dict) -> ResolvedRecord:
    documented = set(responses.get(SCREEN_QUESTION) or [])
    label_to_domain = {d.label: d.name for d in project.domains}
    documented = {label_to_domain.get(x, x) for x in documented}

    values: dict[str, object] = {}
    prov: dict[str, str] = {}
    for d in project.domains:
        for f in d.fields:
            raw = responses.get(f.name)
            if d.name not in documented:
                values[f.name] = project.missing["not_documented"]
                # An answer in a domain the annotator marked as absent is a
                # contradiction worth surfacing.
                prov[f.name] = "invalid" if raw not in (None, "", []) else "screened_out"
                continue
            if f.type == "numeric":
                values[f.name], prov[f.name] = _numeric(f, raw, project)
            elif raw in (None, "", []):
                values[f.name], prov[f.name] = None, "blank"
            else:
                values[f.name], prov[f.name] = raw, "answered"

    for q in project.record_questions:
        values[q.name] = responses.get(q.name)
        prov[q.name] = "answered" if values[q.name] not in (None, "", []) else ("blank" if q.required else "optional_blank")

    return ResolvedRecord(record_id, annotator, values, prov, list(responses.get(EVIDENCE_QUESTION) or []))


def wide_row(r: ResolvedRecord) -> dict[str, object]:
    row: dict[str, object] = {"record_id": r.record_id, "annotator": r.annotator}
    for k, v in r.values.items():
        row[k] = "; ".join(map(str, v)) if isinstance(v, list) else v
    return row


def long_rows(r: ResolvedRecord) -> list[dict[str, object]]:
    rows = []
    for k, v in r.values.items():
        for item in v if isinstance(v, list) else [v]:
            rows.append({"record_id": r.record_id, "annotator": r.annotator,
                         "field": k, "value": item, "provenance": r.provenance[k]})
    return rows


def disagreements(a: ResolvedRecord, b: ResolvedRecord) -> list[str]:
    """Field names where two annotators differ. Multi-choice compares as sets."""
    out = []
    for k in a.values:
        va, vb = a.values[k], b.values.get(k)
        if isinstance(va, list) or isinstance(vb, list):
            va, vb = set(va or []), set(vb or [])
        if va != vb:
            out.append(k)
    return out
