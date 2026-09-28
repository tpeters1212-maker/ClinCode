"""Agreement, disagreement and export tables built from stored answers."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict

from .export import ResolvedRecord, disagreements, long_rows, resolve, wide_row
from .stats import bootstrap, cohen_kappa, percent_agreement
from .store import Store

MIN_PAIRS_FOR_CI = 5


def _resolved(store: Store, batch_id: int | None) -> list[tuple[dict, ResolvedRecord]]:
    project = store.project
    out = []
    for it in store.items(batch_id=batch_id):
        if it["status"] != "submitted":
            continue
        r = resolve(project, it["note_id"], it["annotator"], json.loads(it["responses"]))
        out.append((dict(it), r))
    return out


def dual_pairs(store: Store, batch_id: int | None = None) -> list[tuple[ResolvedRecord, ResolvedRecord, str]]:
    """Pairs of submitted answers on the same note in the same batch."""
    by_note = defaultdict(list)
    for it, r in _resolved(store, batch_id):
        by_note[(it["batch_id"], it["note_id"])].append(r)
    pairs = []
    for (_, note_id), rs in sorted(by_note.items()):
        if len(rs) >= 2:
            n = store.note(note_id)
            pairs.append((rs[0], rs[1], n["patient_id"] or note_id))
    return pairs


def agreement(store: Store, batch_id: int | None = None) -> list[dict]:
    """Per field human-human agreement on double-annotated notes.
    Single choice: Cohen's kappa with patient-clustered 95% interval.
    Multi choice: exact set agreement. Numeric: agreement within tolerance."""
    project = store.project
    pairs = dual_pairs(store, batch_id)
    rows = []
    fields = project.fields + [q for q in project.record_questions if q.type != "text"]
    for f in fields:
        a = [p[0].values.get(f.name) for p in pairs]
        b = [p[1].values.get(f.name) for p in pairs]
        clusters = [p[2] for p in pairs]
        row = {"field": f.name, "label": f.label, "domain": f.domain or "", "type": f.type, "n": len(pairs),
               "metric": "", "value": None, "lo": None, "hi": None}
        if not pairs:
            rows.append(row)
            continue
        if f.type == "single_choice":
            a2 = ["" if v is None else str(v) for v in a]
            b2 = ["" if v is None else str(v) for v in b]
            row["metric"] = "kappa"
            if len(pairs) >= MIN_PAIRS_FOR_CI:
                est = bootstrap(a2, b2, cohen_kappa, clusters=clusters, reps=1000)
                row.update(value=est.value, lo=est.lo, hi=est.hi)
            else:
                row["value"] = cohen_kappa(a2, b2)
        elif f.type == "multi_choice":
            a2 = [frozenset(v) if isinstance(v, list) else v for v in a]
            b2 = [frozenset(v) if isinstance(v, list) else v for v in b]
            row.update(metric="exact agreement", value=percent_agreement(a2, b2))
        elif f.type == "numeric":
            tol = 5
            same = [
                (isinstance(x, (int, float)) and isinstance(y, (int, float)) and abs(x - y) <= tol) or x == y
                for x, y in zip(a, b)
            ]
            row.update(metric=f"agree within {tol}", value=sum(same) / len(same))
        rows.append(row)
    return rows


def disagreement_list(store: Store, batch_id: int | None = None) -> list[dict]:
    labels = {f.name: f.label for f in store.project.fields + store.project.record_questions}
    out = []
    for a, b, _ in dual_pairs(store, batch_id):
        diff = [k for k in disagreements(a, b) if k != "comment"]
        if diff:
            out.append({"note_id": a.record_id, "annotators": (a.annotator, b.annotator),
                        "fields": [{"name": k, "label": labels.get(k, k),
                                    "a": a.values.get(k), "b": b.values.get(k)} for k in diff]})
    return out


def problems(store: Store, batch_id: int | None = None) -> list[dict]:
    """Submitted answers with blank or invalid fields, for sending back."""
    out = []
    for it, r in _resolved(store, batch_id):
        if r.problems:
            out.append({"note_id": it["note_id"], "annotator": it["annotator"], "fields": r.problems})
    return out


def _csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    keys = list(dict.fromkeys(k for r in rows for k in r))
    w = csv.DictWriter(buf, fieldnames=keys)
    w.writeheader()
    for r in rows:
        w.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in r.items()})
    return buf.getvalue()


def export_csv(store: Store, shape: str, batch_id: int | None = None) -> str:
    rows = []
    for it, r in _resolved(store, batch_id):
        n = store.note(it["note_id"])
        meta = {"batch": it["batch_name"], "patient_id": n["patient_id"], "note_date": n["note_date"],
                "note_type": n["note_type"], "stratum": n["stratum"], "seconds": round(it["seconds"])}
        if shape == "wide":
            w = wide_row(r)
            rows.append({"record_id": w.pop("record_id"), "annotator": w.pop("annotator"), **meta, **w,
                         "evidence": r.evidence})
        elif shape == "long":
            rows += [{**lr, "batch": it["batch_name"]} for lr in long_rows(r)]
        elif shape == "evidence":
            for ev in r.evidence:
                rows.append({"record_id": r.record_id, "annotator": r.annotator, "batch": it["batch_name"],
                             "domain": ev.get("label"), "start": ev.get("start"), "end": ev.get("end"),
                             "text": n["text"][ev.get("start", 0):ev.get("end", 0)]})
        else:
            raise ValueError(shape)
    return _csv(rows)
