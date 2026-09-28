"""Neutral cue detection and HTML highlighting.

Cues locate evidence; they never propose an answer. Nothing here reads model
output, so highlighting is safe in gold standard mode.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .schema import Project


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    domain: str
    cue: str


def _cue_pattern(cue: str) -> re.Pattern:
    # Short all-caps abbreviations (DF, PF, SMO, TAL, XR) match case-sensitively
    # so "df" inside ordinary words or lowercase prose does not fire.
    body = r"[\s-]+".join(re.escape(tok) for tok in re.split(r"[\s-]+", cue.strip()))
    flags = 0 if (cue.isupper() and len(cue) <= 4) else re.IGNORECASE
    return re.compile(rf"(?<![\w]){body}(?![\w])", flags)


class Highlighter:
    def __init__(self, project: Project):
        self.project = project
        self._patterns: list[tuple[str, str, re.Pattern]] = []
        for d in project.domains:
            for cue in d.cues:
                self._patterns.append((d.name, cue, _cue_pattern(cue)))
        for rc in project.regex_cues:
            self._patterns.append((rc.domain, rc.name, rc.pattern))

    def find(self, text: str) -> list[Hit]:
        """Return non-overlapping hits; on overlap the longest match wins."""
        raw = [
            Hit(m.start(), m.end(), domain, cue)
            for domain, cue, pat in self._patterns
            for m in pat.finditer(text)
            if m.end() > m.start()
        ]
        raw.sort(key=lambda h: (-(h.end - h.start), h.start))
        taken: list[Hit] = []
        for h in raw:
            if all(h.end <= t.start or h.start >= t.end for t in taken):
                taken.append(h)
        return sorted(taken, key=lambda h: h.start)

    def render(self, text: str, hits: list[Hit] | None = None, offset: int = 0) -> str:
        """Escape text and wrap hits in <mark>. `offset` shifts hit positions
        when rendering a substring of the note."""
        if hits is None:
            hits = self.find(text)
        colors = {d.name: d.color for d in self.project.domains}
        out, pos = [], 0
        for h in hits:
            s, e = h.start - offset, h.end - offset
            if s < pos or e > len(text):
                continue
            out.append(html.escape(text[pos:s]))
            out.append(
                f'<mark class="cc-{h.domain}" style="background:{colors[h.domain]}" '
                f'title="{html.escape(h.domain)}">{html.escape(text[s:e])}</mark>'
            )
            pos = e
        out.append(html.escape(text[pos:]))
        return "".join(out)
