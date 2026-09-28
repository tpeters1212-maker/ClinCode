# ClinCurate MVP specification

Status: draft 1, 2026-09-28. Scope: pediatric foot and ankle project, gold standard mode only.

Tags used throughout: **[MVP]** required before students start, **[Soon]** after the first pilot batch, **[Future]** after real usage data exists.

## 0. What was verified

Everything below marked *verified* ran against a local Argilla 2.8.0 server (`argilla/argilla-hf-spaces:v2.8.0`) with the synthetic notes in `examples/synthetic_notes/`.

- Dataset creation from `templates/pediatric_foot_ankle.yaml`: 2 fields, 22 questions. *Verified.*
- Highlighted Focus panel renders inside Argilla's native annotation view through a `CustomField`. *Verified*, screenshot in `docs/img/argilla_annotation_view.png`.
- Numbered keyboard shortcuts on every option, Save as draft (Ctrl+S), Submit, per-annotator progress bar. Native Argilla. *Verified.*
- Evidence spans submitted on the full note round-trip with character offsets. *Verified.*
- Two annotators on one note, pulled back through `clincurate.export.resolve`, disagreements detected per field. *Verified.*

Constraints found in the Argilla 2.8 SDK that shaped the design:

1. No numeric question type. Numeric fields are text questions validated on export.
2. No conditional questions. Replaced by a screening question plus export rules (section 4).
3. Task distribution is overlap-only (`min_submitted`). No per-user assignment. Replaced by one workspace per annotator (section 7).
4. Span selection works only on a plain `TextField`. Highlighting lives in a separate `CustomField`. The student reads highlights in Focus and selects evidence in Full note.

## 1. Annotator workflow, screen by screen

**Login [MVP].** Username and password issued by the project admin. Argilla native.

**Home [MVP].** The student sees only their own workspace, containing only their current batch dataset, named like `afo_batch03_jsmith`. The card shows percent complete.

**Record view [MVP].** One note per screen.

- Left, top: **Focus**. Cue-bearing sentences with one sentence of context either side, grouped under domain headers (Orthosis and casting, Gait, and so on), cue words colored by domain.
- Left, bottom: **Full note**, plain text, selectable for evidence.
- Right: the form. First question: *What does this note document?* (tick the topics). Then the domain questions in domain order. Then Evidence, Is this note usable?, Flag for review, Comment.
- Bottom right: progress bar and submitted count.
- Metadata visible to the student: patient ID, note date, note type. Hidden: batch, sampling stratum, note hash.

**Keys [MVP].** Number keys pick options in the focused question, Tab moves between questions, Ctrl+S saves a draft, Enter submits. Submit opens the next pending record.

**Resume [MVP].** The Pending filter is the default view and shows exactly what is left. The Draft filter shows half-finished notes. Nothing is ever lost on logout.

**Flags [MVP].** "Flag for review" plus comment. Flagged notes go to the adjudicator queue regardless of dual annotation.

**Unusable notes [MVP].** "Is this note usable?" with reasons (wrong patient or specialty, template or empty, other). The telephone note in the synthetic set is the test case. An unusable note needs no other answers.

**Time per note [Soon].** Argilla stores response timestamps but no open time. First pass: difference between consecutive submit times per student, capped at 20 minutes. Precise timing needs a small JS hook in the Focus field [Future].

**Assisted mode, LLM suggestions shown [Future].** Separate datasets, never mixed with the gold standard.

## 2. Initial annotation schema

File: `templates/pediatric_foot_ankle.yaml`. 6 domains, 17 fields, 3 record-level questions. Generated guide: `docs/annotation-guide.md`.

- Orthosis and casting: current device (multi), status of primary device.
- Brace use and tolerance: adherence, tolerance problems (multi).
- Range of motion and contracture: DF knee extended L and R, DF knee flexed L and R (numeric, degrees, signed), equinus type.
- Gait: toe walking, heel strike, gait compared with prior visit.
- Alignment and imaging: hindfoot alignment, arch, imaging reviewed.
- Treatment escalation: escalation (multi), procedure named.

Deliberately excluded from v1 to keep the form short: brace hours per night and nights per week, calcaneal pitch and other radiographic angles, foot progression angle value, skeletal maturity, functional limitation, balance. Each goes back in if the pilot shows it is documented often enough to validate. Plantarflexion is out because no study endpoint uses it.

Rules that apply everywhere:

- Every choice question offers *Not documented*; most also offer *Unclear*.
- A normal finding is an answer (Neutral, Absent, Normal), never *Not documented*.
- Findings describe this visit only. "Gait compared with prior visit" records only what the note itself says; the student does not open earlier notes.
- DF sign convention: past neutral is positive, "lacks 10 to neutral" is -10, "to neutral" is 0.

## 3. Highlighting dictionary

Defined per domain in the schema (`cues:`) plus `regex_cues:`. Implementation: `clincurate/highlight.py`.

- Word-boundary matching; hyphens and spaces interchangeable ("toe-walking" matches "toe walking").
- Short all-caps abbreviations (DF, PF, SMO, TAL, XR, FPA) match case-sensitively so "df" inside other text does not fire.
- On overlap the longest match wins ("serial casting" beats "casting"; "DF 5°" beats "5°").
- Regex cues [MVP]: angles with a degree unit or sign, "DF 5" style values, "to neutral" and "lacks N to neutral", frequency expressions ("5 nights per week"), FPA values.
- Negation is not handled. "No orthotics needed" highlights "orthotics". The student decides; a highlight marks location, not presence. Negation dimming [Future], only after checking it does not hide true positives.

Neutrality rule: cues come only from the schema. Nothing in the highlighting path reads LLM output. `build_settings` refuses any mode other than `gold_standard`, and records never carry suggestions (tested).

## 4. Focus Mode logic

Implementation: `clincurate/focus.py`.

1. Split the note into sentences with character offsets. Breaks on sentence punctuation, blank lines, bullet lines and ALL-CAPS section headers. No break after decimals or common abbreviations (Pt., y.o., Dr.).
2. Find all cue hits.
3. For each hit, take its sentence plus `context_sentences_before` and `context_sentences_after` (default 1 and 1).
4. Within a domain, merge overlapping or adjacent windows.
5. Cap at `max_snippets_per_domain` (default 6). Group by domain in schema order.
6. Inside each snippet, highlight every cue from every domain, so a gait sentence that mentions a brace still shows the brace.
7. No hits: show "No cue terms found. Read the full note below."

Known behaviour from the synthetic set: a sentence can appear under two domains (the flatfoot note shows the gait sentence under Orthosis because it says "No orthotics needed"). Acceptable for v1; the pilot decides between domain grouping and a single note-order list.

Conditional questions, implemented without conditional UI [MVP]. The required first question is *What does this note document?*. On export (`clincurate/export.py`):

- Domain not ticked: every field in it becomes *Not documented*, provenance `screened_out`.
- Domain ticked, choice field blank: provenance `blank`, sent back to the student as incomplete.
- Domain ticked, numeric blank: *Not documented* (one side is often unmeasured), provenance `blank_numeric`.
- Answer given in an unticked domain: provenance `invalid`, sent back.
- Numeric not parseable or outside the schema range: `invalid`. `?` means *Unclear*.

True show/hide conditional questions need a frontend change [Future].

## 5. Argilla integration architecture

```
YAML schema ──► clincurate.schema ──► Project
notes.csv ─────► clincurate.highlight + focus ──► record payload (focus HTML, note, metadata)
Project ───────► clincurate.argilla_backend ──► Argilla dataset per annotator per batch
Argilla ───────► fetch_responses ──► clincurate.export.resolve ──► wide / long tables
tables + LLM output ──► clincurate.stats ──► agreement, accuracy, stopping check
```

- `argilla_backend.py` is the only module that imports argilla. Everything else is backend neutral and tested without a server.
- Pin `argilla==2.8.*` server and SDK. Argilla is reported to be in maintenance mode; pinning avoids surprise changes and the adapter boundary keeps a later move to Label Studio or a custom UI contained.
- Record id = note id. Metadata: patient_id, note_type, note_date (shown); batch, stratum, note_sha256 (hidden).
- Guidelines panel in Argilla carries the generated annotation guide.

Deployment [MVP]: self-hosted Docker inside a UCSF-approved environment, HTTPS, no public exposure. The all-in-one `argilla-hf-spaces` image is fine for the pilot; production uses the documented server + PostgreSQL + Elasticsearch stack. Disable telemetry (`HF_HUB_DISABLE_TELEMETRY=1`, and Argilla's own telemetry setting). Confirm with UCSF IT which environment may hold PHI before any real note is loaded.

## 6. Storage model

One row per note per annotator per field (long), plus a wide view.

- Identity: record_id, patient_id, note_date, annotator, response status (draft, submitted, discarded), batch, stratum.
- Value: the answer; multi-choice keeps a list in long form and `; `-joined in wide form.
- Provenance per field: `answered`, `screened_out`, `blank`, `blank_numeric`, `invalid`, `optional_blank`.
- Evidence: list of `{label: domain, start, end}` character offsets into the note, plus note_sha256 so a changed note invalidates old offsets.
- Adjudication [Soon]: adjudicated value, adjudicator, reason; the two original answers are never overwritten.
- LLM predictions: stored outside Argilla in their own table keyed by record_id and field. Joined only at analysis time.

Evidence is per domain in v1, not per field: one highlighted sentence usually supports several fields. Per-field evidence [Future] if error analysis needs it.

## 7. Administrator workflow

MVP uses the Python API plus three CLI commands already in the repo (`validate`, `preview`, `guide`). Planned CLI [MVP unless marked]:

1. `clincurate validate schema.yaml` (exists).
2. `clincurate preview schema.yaml notes.csv` (exists): offline HTML of Focus Mode for checking cues before students see anything.
3. `clincurate sample` draws a batch: stratified by cue stratum, with a dual-annotation subset.
4. `clincurate push` creates one workspace per annotator and one dataset per annotator per batch; dual-annotation notes go into two datasets under the same record id. Argilla has no per-user assignment, so workspace membership does the assigning.
5. `clincurate status`: per student completed, pending, drafts, flagged, and fields with most `blank` or `invalid`.
6. `clincurate export`: wide and long CSV, plus a problems list to send back to students.
7. `clincurate agreement` [Soon]: human-human and human-LLM statistics per field.

Admin dashboard [Soon]: Streamlit page over the export tables. Not built until the CLI outputs prove which numbers matter.

## 8. Dual annotation and adjudication

- [MVP] 20 percent of each batch goes to two students, chosen within strata so rare categories are represented. Default configurable.
- [MVP] `export.disagreements` lists differing fields per dual note. Multi-choice compares as sets.
- [Soon] Adjudication dataset in Argilla: one record per disagreeing note. The Focus field shows the note, both answers side by side and both students' evidence highlighted. Questions: final value per disagreeing field only, reason (note ambiguous, schema unclear, annotator error). Original answers stay untouched.
- [Soon] Disagreement patterns feed back into the guide: a field that keeps disagreeing gets a clearer help text or is dropped.

## 9. Sequential validation integration

Two separate questions, reported separately:

1. Is the human reference reliable? Human-human agreement on the dual subset: Cohen's kappa for categorical fields; ICC or mean absolute difference for DF.
2. Is the LLM accurate? LLM versus the adjudicated human answer: sensitivity, PPV, F1 per category; kappa; MAE for DF.

Procedure [MVP for computation, Soon for automation]:

- Batches of 10 to 20 notes.
- After each batch, per field: point estimate and 95 percent interval by percentile bootstrap, resampling patients rather than notes (`clincurate/stats.py`), since notes from one patient are correlated.
- Stopping check: interval half-width at or below a per-field target. Targets live in a validation config agreed with EBSD, never in code.
- Fields reach precision at different times. A field that has reached target leaves the form for later batches only if EBSD agrees; otherwise it stays so the dataset remains complete.

Enriched sampling [Soon]: every note carries its stratum and the stratum's sampling fraction. Population-level estimates use inverse-probability weights with stratified bootstrap. Until that exists, reports show per-stratum results and label pooled numbers as unweighted. Open for EBSD: whether precision-based stopping with repeated looks needs any adjustment for the intended claims.

## 10. Staged implementation plan

**Stage 0, done in this commit.** Schema loader, highlighter, Focus Mode, export resolver, agreement statistics, Argilla adapter, offline preview, generated guide, 10 synthetic notes, 16 tests. End-to-end loop checked on a local Argilla server.

**Stage 1, pilot readiness (about 1 to 2 weeks).**
- UCSF deployment spike: which approved environment can run the Argilla container with PHI.
- CLI `sample`, `push`, `status`, `export`.
- Two students, 20 synthetic notes each, observed; fix the guide and form order.

**Stage 2, first real batch.**
- 20 real notes per student, 20 percent dual.
- First agreement report per field. Remove or rewrite fields with poor agreement or near-zero prevalence.
- Measure time per note.

**Stage 3, steady state.**
- Adjudication dataset. Sequential validation report after every batch. Streamlit status page.

**Stage 4, research features [Future].**
- Adaptive sampling on LLM uncertainty, weighted estimates, assisted versus unassisted experiment, true conditional UI, per-field evidence, other clinical templates.

## 11. Decisions log

- Unit of annotation: the note. Patient-level phenotypes are derived at analysis.
- Laterality: side-specific fields for DF only. Categorical findings count if present on either side. Revisit if an endpoint needs sided categoricals.
- Several values for the same measurement in one note: record the one from this visit's exam; if still ambiguous, record the last stated and flag.
- Not documented versus normal: normal is always an explicit option.
- Custom frontend: not needed for MVP. The native UI covers drafts, progress, shortcuts, spans and custom HTML.
- Highlighting versus span selection: two panels (highlighted Focus, selectable Full note). Highlighting the selectable note needs a frontend fork [Future].
- Model output in gold standard mode: excluded in code.
- Thresholds: config only.

## 12. Open questions for the team

1. Which UCSF environment can host Argilla with PHI, and who administers it?
2. Batch size and dual fraction: defaults 15 and 20 percent; EBSD to confirm.
3. Per-field precision targets.
4. Whether brace frequency (hours, nights per week) belongs in v1.
5. Domain-grouped Focus versus a single note-order list: decide after pilot.
