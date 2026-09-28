from pathlib import Path

import pytest

import csv

from clincurate.guide import render_markdown
from clincurate.export import disagreements, long_rows, resolve, wide_row
from clincurate.focus import build_snippets, split_sentences
from clincurate.highlight import Highlighter
from clincurate.schema import SchemaError, load_schema
from clincurate.stats import bootstrap, cohen_kappa, precision_reached

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "clincurate" / "schemas" / "pediatric_foot_ankle.yaml"
NOTES = ROOT / "examples" / "synthetic_notes" / "notes.csv"


def read_notes(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def project():
    return load_schema(SCHEMA)


@pytest.fixture(scope="module")
def hl(project):
    return Highlighter(project)


def test_schema_loads(project):
    assert {d.name for d in project.domains} >= {"orthosis", "rom", "gait"}
    assert all(not f.required for f in project.fields)
    opts = project.choice_options(next(f for f in project.fields if f.name == "toe_walking"))
    assert opts[-2:] == ["Not documented", "Unclear"]


def test_schema_rejects_bad_type(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("domains:\n  d:\n    fields:\n      x: {type: slider}\n")
    with pytest.raises(SchemaError):
        load_schema(bad)


def test_abbreviation_is_case_sensitive(hl):
    cues = {(h.cue, h.domain) for h in hl.find("DF 5 with knee extended")}
    assert ("DF", "rom") in cues or ("df_numeric", "rom") in cues
    assert not hl.find("the pdf was reviewed")


def test_longest_match_wins(hl):
    hits = hl.find("Start nighttime AFOs. Serial casting discussed.")
    texts = ["Start nighttime AFOs. Serial casting discussed."[h.start:h.end] for h in hits]
    assert "Serial casting" in texts
    assert "casting" not in texts


def test_degree_regex(hl):
    text = "lacks 10 degrees to neutral; DF 5°"
    spans = [text[h.start:h.end] for h in hl.find(text)]
    assert any("10 degrees" in s for s in spans)
    assert any("5°" in s for s in spans)


def test_render_escapes_html(hl):
    out = hl.render("<b>AFO</b>")
    assert "&lt;b&gt;" in out and "<mark" in out


def test_sentence_split_keeps_decimals_and_abbrev():
    text = "Pt. is 7 y.o. today. DF is 2.5 degrees.\n\nPLAN: AFO."
    sents = [text[s.start:s.end] for s in split_sentences(text)]
    assert "Pt. is 7 y.o. today." in sents
    assert "DF is 2.5 degrees." in sents
    assert sents[-1] == "PLAN: AFO."


def test_focus_snippets_cover_hits(project, hl):
    note = read_notes(NOTES)[0]["text"]
    groups = build_snippets(note, project, hl)
    assert {"orthosis", "rom", "gait"} <= set(groups)
    for snippets in groups.values():
        for sn in snippets:
            assert sn.hits and note[sn.start:sn.end].strip()


def test_telephone_note_has_no_snippets(project, hl):
    note = next(n for n in read_notes(NOTES) if n["note_id"] == "SYN-008")
    assert build_snippets(note["text"], project, hl) == {}


def test_resolve_screening_and_numeric(project):
    r = resolve(project, "SYN-003", "A", {
        "documented_domains": ["rom", "gait"],
        "df_knee_extended_right": "-10",
        "df_knee_extended_left": "?",
        "df_knee_flexed_left": "abc",
        "toe_walking": "Constant",
        "note_usable": "Yes",
    })
    v, p = r.values, r.provenance
    assert v["brace_type"] == "Not documented" and p["brace_type"] == "screened_out"
    assert v["df_knee_extended_right"] == -10
    assert v["df_knee_extended_left"] == "Unclear"
    assert v["df_knee_flexed_right"] == "Not documented"
    assert p["df_knee_flexed_left"] == "invalid"
    assert p["heel_strike"] == "blank"
    assert set(r.problems) >= {"df_knee_flexed_left", "heel_strike"}


def test_answer_in_unticked_domain_is_flagged(project):
    r = resolve(project, "x", "A", {"documented_domains": [], "brace_type": ["SMO"], "note_usable": "Yes"})
    assert r.provenance["brace_type"] == "invalid"


def test_disagreements_and_exports(project):
    a = resolve(project, "x", "A", {"documented_domains": ["orthosis"], "brace_type": ["SMO", "Nighttime AFO"], "note_usable": "Yes"})
    b = resolve(project, "x", "B", {"documented_domains": ["orthosis"], "brace_type": ["Nighttime AFO", "SMO"], "brace_status": "Continuing", "note_usable": "Yes"})
    assert disagreements(a, b) == ["brace_status"]
    assert wide_row(a)["brace_type"] == "SMO; Nighttime AFO"
    assert sum(r["field"] == "brace_type" for r in long_rows(a)) == 2


def test_kappa_and_bootstrap():
    a = ["y", "y", "n", "n", "y", "n", "y", "n"] * 5
    b = ["y", "y", "n", "n", "y", "n", "n", "n"] * 5
    assert cohen_kappa(a, a) == 1.0
    k = cohen_kappa(a, b)
    assert 0.7 < k < 0.8
    est = bootstrap(a, b, clusters=[i // 2 for i in range(len(a))], reps=300)
    assert est.lo <= k <= est.hi
    assert precision_reached(est, 1.0) and not precision_reached(est, 0.0)


def test_guide_generated(project):
    g = render_markdown(project)
    assert "Lacks 10 to neutral" in g and "## Gait" in g


def test_strata(project):
    notes = {n["note_id"]: n["text"] for n in read_notes(NOTES)}
    assert project.stratum_of(notes["SYN-001"]) == "explicit_nighttime"
    assert project.stratum_of(notes["SYN-007"]) == "explicit_nighttime"
    assert project.stratum_of(notes["SYN-003"]) == "serial_casting"
    assert project.stratum_of(notes["SYN-005"]) == "smo"
    assert project.stratum_of(notes["SYN-008"]) == "other"
    assert project.stratum_of("Has bilateral AFOs.") == "afo_unspecified"



def test_merged_snippets_no_repeats(project, hl):
    from clincurate.focus import merged_snippets
    note = read_notes(NOTES)[0]["text"]
    ms = merged_snippets(note, project, hl)
    assert all(a.end < b.start for a, b in zip(ms, ms[1:]))
    assert {"orthosis", "rom", "gait"} <= {d for m in ms for d in m.domains}
