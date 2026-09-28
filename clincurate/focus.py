"""Focus Mode: cue-bearing sentences plus surrounding context, grouped by domain."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .highlight import Highlighter, Hit
from .schema import Project

# Abbreviations common in orthopaedic notes that end in a period but do not
# end a sentence.
_NO_BREAK = {"dr", "mr", "mrs", "ms", "pt", "approx", "vs", "e.g", "i.e", "hx", "yo", "y.o", "wk", "wks", "b/l"}
_BOUNDARY = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9\"(])|\n\s*\n|\n(?=\s*[-*•]|\s*[A-Z][A-Z /&]+:)")


@dataclass
class Sentence:
    start: int
    end: int


@dataclass
class Snippet:
    domain: str
    start: int
    end: int
    hits: list[Hit]


def split_sentences(text: str) -> list[Sentence]:
    """Character-offset sentence split tuned for clinical notes: blank lines,
    bullet lines and ALL-CAPS section headers break; decimals and common
    abbreviations do not."""
    sents, start = [], 0
    for m in _BOUNDARY.finditer(text):
        prev = text[start:m.start()].rstrip()
        last_word = prev.rsplit(None, 1)[-1].rstrip(".").lower() if prev else ""
        if prev.endswith(".") and last_word in _NO_BREAK:
            continue
        if text[start:m.start()].strip():
            sents.append(Sentence(start, m.start()))
        start = m.end()
    if text[start:].strip():
        sents.append(Sentence(start, len(text)))
    # Trim surrounding whitespace so offsets point at visible characters.
    out = []
    for s in sents:
        seg = text[s.start:s.end]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg) - len(seg.rstrip())
        out.append(Sentence(s.start + lead, s.end - trail))
    return out


def build_snippets(text: str, project: Project, highlighter: Highlighter) -> dict[str, list[Snippet]]:
    hits = highlighter.find(text)
    sents = split_sentences(text)
    before = project.focus["context_sentences_before"]
    after = project.focus["context_sentences_after"]
    cap = project.focus["max_snippets_per_domain"]

    def sent_index(pos: int) -> int:
        for i, s in enumerate(sents):
            if s.start <= pos < s.end or pos < s.start:
                return i
        return len(sents) - 1

    by_domain: dict[str, list[tuple[int, int]]] = {}
    for h in hits:
        i = sent_index(h.start)
        lo, hi = max(0, i - before), min(len(sents) - 1, i + after)
        by_domain.setdefault(h.domain, []).append((lo, hi))

    result: dict[str, list[Snippet]] = {}
    for d in project.domains:
        windows = sorted(by_domain.get(d.name, []))
        merged: list[list[int]] = []
        for lo, hi in windows:
            if merged and project.focus["merge_overlapping"] and lo <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        snippets = []
        for lo, hi in merged[:cap]:
            s, e = sents[lo].start, sents[hi].end
            snippets.append(Snippet(d.name, s, e, [h for h in hits if s <= h.start and h.end <= e]))
        if snippets:
            result[d.name] = snippets
    return result


def render_focus_html(text: str, project: Project, highlighter: Highlighter) -> str:
    """HTML for the Focus panel. Every hit in every snippet is highlighted in
    its own domain color, so cross-domain evidence stays visible."""
    groups = build_snippets(text, project, highlighter)
    all_hits = highlighter.find(text)
    parts = ['<div class="cc-focus">']
    if not groups:
        parts.append('<p class="cc-empty">No cue terms found. Read the full note below.</p>')
    for d in project.domains:
        if d.name not in groups:
            continue
        parts.append(
            f'<section class="cc-domain"><h4 style="border-left:6px solid {d.color};'
            f'padding-left:6px">{html.escape(d.label)}</h4>'
        )
        for sn in groups[d.name]:
            inner = [h for h in all_hits if sn.start <= h.start and h.end <= sn.end]
            parts.append(
                f'<p class="cc-snippet" data-start="{sn.start}" data-end="{sn.end}">'
                f"{highlighter.render(text[sn.start:sn.end], inner, offset=sn.start)}</p>"
            )
        parts.append("</section>")
    parts.append("</div>")
    return "".join(parts)


@dataclass
class MergedSnippet:
    start: int
    end: int
    domains: list[str]


def merged_snippets(text: str, project: Project, highlighter: Highlighter) -> list[MergedSnippet]:
    """Focus windows from all domains merged and shown once, in note order.
    Avoids repeating a sentence under every topic it touches."""
    groups = build_snippets(text, project, highlighter)
    spans = sorted((sn.start, sn.end, d) for d, sns in groups.items() for sn in sns)
    out: list[MergedSnippet] = []
    for s, e, d in spans:
        if out and s <= out[-1].end + 1:
            out[-1].end = max(out[-1].end, e)
            if d not in out[-1].domains:
                out[-1].domains.append(d)
        else:
            out.append(MergedSnippet(s, e, [d]))
    order = {d.name: i for i, d in enumerate(project.domains)}
    for m in out:
        m.domains.sort(key=order.get)
    return out
