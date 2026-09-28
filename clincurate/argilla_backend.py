"""Argilla adapter. The only module that imports argilla, so another backend
can replace it without touching schema, highlighting or statistics."""

from __future__ import annotations

import hashlib

from .export import EVIDENCE_QUESTION
from .focus import render_focus_html
from .highlight import Highlighter
from .schema import SCREEN_QUESTION, Field, Project

FOCUS_TEMPLATE = """
<style>
.cc-focus{font-size:15px;line-height:1.5}
.cc-domain h4{margin:14px 0 4px;font-size:14px}
.cc-snippet{margin:4px 0 8px;padding:6px 8px;background:#f8fafc;border-radius:4px}
.cc-empty{color:#64748b}
mark{padding:0 2px;border-radius:2px}
</style>
<div>{{{record.fields.focus.html}}}</div>
"""


def render_guidelines(project: Project) -> str:
    """Markdown annotation guide generated from the same schema as the form."""
    nd, unc = project.missing["not_documented"], project.missing["unclear"]
    lines = [
        f"# {project.title}",
        "",
        "## How to work a note",
        "1. Read the **Focus** panel. Highlighted words show where to look; they are not answers.",
        "2. Tick every topic the note documents in **What does this note document?**",
        "3. Answer questions only for the topics you ticked. Unticked topics are recorded as "
        f"*{nd}* automatically.",
        "4. Select supporting sentences in the full note with the **Evidence** labels.",
        "5. **Submit**, or **Save draft** to finish later.",
        "",
        f"*{nd}*: the note does not say. *{unc}*: the note says something but you cannot tell what. "
        "A normal finding is an answer (for example Neutral or Absent), never Not documented.",
        "",
    ]
    for d in project.domains:
        lines += [f"## {d.label}", ""]
        for f in d.fields:
            lines.append(f"**{f.label}**" + (f" ({f.units})" if f.units else ""))
            if f.help:
                lines.append(f"  {f.help}")
            if f.type == "numeric":
                lines.append(f"  Type a number. Leave blank if not documented. Type ? if {unc.lower()}.")
            lines.append("")
    return "\n".join(lines)


def _question(project: Project, f: Field):
    import argilla as rg

    desc = f.help or None
    if f.type == "single_choice":
        return rg.LabelQuestion(f.name, labels=project.choice_options(f), title=f.label,
                                description=desc, required=f.required)
    if f.type == "multi_choice":
        return rg.MultiLabelQuestion(f.name, labels=project.choice_options(f), title=f.label,
                                     description=desc, required=f.required)
    # Argilla has no numeric question; numeric fields are text validated on export.
    if f.type == "numeric":
        hint = "Number only. Blank = not documented. ? = unclear."
        desc = f"{desc} {hint}" if desc else hint
    return rg.TextQuestion(f.name, title=f.label, description=desc, required=f.required)


def build_settings(project: Project):
    import argilla as rg

    if project.mode != "gold_standard":
        raise NotImplementedError("only gold_standard mode is implemented")

    fields = [
        rg.CustomField("focus", title="Focus", template=FOCUS_TEMPLATE, required=True),
        rg.TextField("note", title="Full note", use_markdown=False, required=True),
    ]
    questions = [
        rg.MultiLabelQuestion(
            SCREEN_QUESTION,
            labels={d.name: d.label for d in project.domains},
            title="What does this note document?",
            description="Tick every topic the note mentions. Unticked topics export as Not documented.",
            required=True,
        )
    ]
    questions += [_question(project, f) for f in project.fields]
    questions.append(
        rg.SpanQuestion(
            EVIDENCE_QUESTION,
            field="note",
            labels={d.name: d.label for d in project.domains},
            title="Evidence",
            description="Select the sentence that supports your answers and pick its topic.",
            allow_overlapping=True,
            required=False,
        )
    )
    questions += [_question(project, q) for q in project.record_questions]

    metadata = [
        rg.TermsMetadataProperty("patient_id", title="Patient"),
        rg.TermsMetadataProperty("note_type", title="Note type"),
        rg.TermsMetadataProperty("note_date", title="Note date"),
        rg.TermsMetadataProperty("batch", title="Batch", visible_for_annotators=False),
        rg.TermsMetadataProperty("stratum", title="Sampling stratum", visible_for_annotators=False),
    ]
    return rg.Settings(
        fields=fields,
        questions=questions,
        metadata=metadata,
        guidelines=render_guidelines(project),
        allow_extra_metadata=True,
        distribution=rg.TaskDistribution(min_submitted=1),
    )


def record_payload(project: Project, note: dict, highlighter: Highlighter | None = None) -> dict:
    """Backend-neutral record content; kept separate so it is testable
    without an Argilla server."""
    hl = highlighter or Highlighter(project)
    text = note["text"]
    return {
        "id": str(note["note_id"]),
        "fields": {"focus": {"html": render_focus_html(text, project, hl)}, "note": text},
        "metadata": {
            **{k: str(note[k]) for k in ("patient_id", "note_type", "note_date", "batch", "stratum") if note.get(k)},
            # Evidence spans are character offsets; the hash detects any later
            # change to the note text that would invalidate them.
            "note_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
    }


def build_records(project: Project, notes: list[dict]):
    import argilla as rg

    hl = Highlighter(project)
    return [rg.Record(**record_payload(project, n, hl)) for n in notes]


def push_dataset(client, project: Project, notes: list[dict], name: str, workspace: str):
    """Create (or reuse) a dataset and log the notes as records."""
    import argilla as rg

    ws = client.workspaces(workspace)
    if ws is None:
        ws = rg.Workspace(name=workspace, client=client).create()
    ds = client.datasets(name=name, workspace=ws)
    if ds is None:
        ds = rg.Dataset(name=name, workspace=ws, settings=build_settings(project), client=client).create()
    ds.records.log(build_records(project, notes))
    return ds


def fetch_responses(dataset):
    """Yield (record_id, username, status, responses) per annotator per record.
    Drafts are yielded too; callers decide whether to count them."""
    users = {}
    for rec in dataset.records(with_responses=True):
        per_user: dict = {}
        for r in rec.responses:
            status = getattr(r.status, "value", r.status)
            per_user.setdefault(r.user_id, {"status": status, "values": {}})
            per_user[r.user_id]["values"][r.question_name] = r.value
        for uid, d in per_user.items():
            if uid not in users:
                u = dataset._client.users(id=uid)
                users[uid] = u.username if u else str(uid)
            yield rec.id, users[uid], d["status"], d["values"]
