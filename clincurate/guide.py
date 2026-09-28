"""Annotation guide generated from the same schema as the form."""

from __future__ import annotations

from .schema import Project


def guide_sections(project: Project) -> dict:
    nd, unc = project.missing["not_documented"], project.missing["unclear"]
    return {
        "title": project.title,
        "steps": [
            "Read the Focus panel. Highlighted words show where to look; they are not answers.",
            "Tick every topic the note documents. Questions for that topic appear.",
            f"Answer every question in the topics you ticked. Unticked topics are recorded as {nd} automatically.",
            "Select the supporting text in the note and click the topic it supports to save it as evidence.",
            "Answers save automatically. Click Submit when the note is finished.",
        ],
        "rules": [
            f"{nd}: the note does not say.",
            f"{unc}: the note says something, but you cannot tell what.",
            "A normal finding is an answer (for example Neutral or Absent), never Not documented.",
            "Answer from this note only. Do not open other notes for the same patient.",
        ],
        "domains": [
            {"label": d.label, "color": d.color, "fields": [
                {"label": f.label + (f" ({f.units})" if f.units else ""), "help": f.help,
                 "numeric": f.type == "numeric"} for f in d.fields]}
            for d in project.domains
        ],
        "numeric_rule": f"Numbers: type the value. Leave blank if not documented. Type ? if {unc.lower()}.",
    }


def render_markdown(project: Project) -> str:
    g = guide_sections(project)
    lines = [f"# {g['title']}", "", "## How to work a note"]
    lines += [f"{i}. {s}" for i, s in enumerate(g["steps"], 1)]
    lines += ["", *[f"- {r}" for r in g["rules"]], f"- {g['numeric_rule']}", ""]
    for d in g["domains"]:
        lines += [f"## {d['label']}", ""]
        for f in d["fields"]:
            lines.append(f"**{f['label']}**" + (f"  \n{f['help']}" if f["help"] else ""))
            lines.append("")
    return "\n".join(lines)
