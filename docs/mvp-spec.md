# ClinCurate MVP specification

Status: draft 2, 2026-09-28. Replaces draft 1 (Argilla-based).

Tags: **[MVP]** built and tested, **[Soon]** next, **[Future]** after pilot data.

## 1. Requirements that drive the design

- Runs on PHI-compliant laptops. No server, no cloud, no Docker, no admin rights.
- No command line for anyone. Coordinators and annotators use a point-and-click interface.
- The coordinator plans batches in the interface: which strata, how many notes, who annotates, what share is double-annotated, in what order.
- Students see how much is left, can stop anytime, and resume without losing work.

## 2. Why the Argilla plan was dropped

Argilla needs Docker plus Elasticsearch, which does not fit locked-down clinical laptops. Its interface also could not show or hide questions based on earlier answers, and could not highlight the same text students select as evidence. The backend-neutral core written for draft 1 (schema, highlighting, Focus Mode, export, statistics) carries over unchanged.

## 3. Architecture

One Python application per laptop, packaged as a double-click app (`ClinCurate.exe` on Windows, `ClinCurate.app` on macOS).

- Starting the app runs a small web server bound to `127.0.0.1` and opens the default browser. No other computer can connect.
- All data lives in one SQLite file in a `ClinCurate` folder in the user's home directory. The laptop's full-disk encryption protects it at rest.
- The interface uses only local files. No CDN, fonts, analytics or update checks.
- Requests with a Host header other than `127.0.0.1` or `localhost` are refused (DNS rebinding). Every POST needs a per-launch token (cross-site requests). Pages cannot be framed.
- Quit closes the server from inside the app.

Modules:

- `schema.py`: YAML project schema: domains, fields, cues, regex cues, sampling strata.
- `highlight.py`, `focus.py`: neutral cue highlighting and Focus snippets.
- `store.py`: SQLite storage: notes, annotators, batches, assignments, answers, audit log.
- `sampling.py`: per-stratum draws, assignment, batch lifecycle.
- `packages.py`: encrypted batch and results files.
- `export.py`, `reports.py`, `stats.py`: provenance, CSV exports, agreement, bootstrap intervals.
- `app.py`, `web/`: the interface.
- `launcher.py`: double-click entry point.

## 4. Roles and laptops

The first screen asks which role this laptop has.

- **Coordinator laptop** [MVP]. Holds the full note set and plans every batch. People can also annotate on it (Annotate here).
- **Annotator laptop** [MVP]. Set up by opening a batch file. It holds only that student's assigned notes.

Moving work between laptops [MVP]:

1. The coordinator starts a batch and clicks "Download for <name>". The download is a `.ccpkg` file with the schema and that student's notes, encrypted with AES-256-GCM under a key derived from the project passphrase (scrypt).
2. The file travels through UCSF-approved storage. The passphrase is shared in person or by phone.
3. The student opens the file in ClinCurate and enters the passphrase once.
4. The student clicks "Save results file for coordinator" and gets a `.ccres` file: answers, evidence offsets and note hashes, with **no note text**, also encrypted.
5. The coordinator imports results files (several at once). A newer answer replaces an older one. An answer whose note hash does not match is skipped and reported.

If everyone annotates on one coordinator laptop, steps 1 to 5 are not needed.

Data custody [MVP]. The coordinator laptop is the system of record; student laptops hold temporary working copies.

- The student's queue warns whenever answers exist that are newer than the last results file.
- "Remove from this laptop" appears once every answer has been saved to a results file. It asks the student to confirm that the coordinator imported the file, then deletes the batch, its notes and answers. Secure delete, VACUUM and a WAL checkpoint ensure note text does not remain in the database files (tested).
- The coordinator's batch page shows when each student's latest results file was imported.
- The dashboard shows the time of the last backup and offers an encrypted backup (`.ccbak`, full database, AES-256-GCM under the project passphrase). The welcome screen restores a backup onto a new laptop; a laptop that already has a project refuses a restore.

## 5. Coordinator workflow

**Setup [MVP].** Choose the bundled pediatric foot and ankle schema (or upload a YAML), set the project passphrase, import notes from CSV (`note_id`, `text`, optional `patient_id`, `note_date`, `note_type`), add annotators. Each note gets a sampling stratum from the schema on import.

**Batches page [MVP].** Every batch in planned order with status (Planned, Active, Closed), note count and progress. Up and down arrows reorder. The page also shows how many notes each stratum has and how many are not yet in any batch.

**Planning a batch [MVP].**

1. Plan: name, purpose, double-annotation percent, notes per stratum (with live total and availability), annotators.
2. Draw notes: random within each stratum, never reusing a note from another batch, planned or started. Redraw freely until started. Available and drawn counts per stratum are stored for weighting.
3. Review: notes per annotator and the drawn notes list; any note opens in a preview with highlighting.
4. Start: assigns notes round-robin; each double-annotated note also goes to the next person, so a pair never repeats one annotator. The draw is then locked.

Several batches can be planned ahead and started one at a time.

**Monitoring [MVP].** Per annotator: progress bar, left, drafts, flagged, average minutes per note. Buttons: batch file download, results import. Below that:

- Agreement on double-annotated notes, per question. Single choice: Cohen's kappa with a 95% interval from a patient-clustered bootstrap, shown from 5 pairs. Multi choice: exact agreement. Numbers: agreement within 5 degrees.
- Needs review: each note where the two annotators differ, with both answers side by side.
- Incomplete answers: submitted notes with blanks in ticked topics or unreadable numbers.
- Export: one row per note, one row per answer, or evidence with quoted text (CSV).
- Close batch (answers then read-only) and Reopen.

**Schema lock [MVP].** The schema cannot be replaced after any batch starts, so answers across batches stay comparable.

**Activity log [MVP].** Imports, exports, package downloads, submissions and batch actions with timestamps.

## 6. Annotator workflow

**My notes [MVP].** Per batch: a large progress bar, "N notes left", started-but-unsubmitted count, and the list of notes with To do, Started, Done. "Continue annotating" opens the first started note, then the first untouched one.

**Annotation screen [MVP].**

- Status bar: name, batch, progress, notes left, save state.
- Left pane, **Focus** tab: every sentence with a highlighted cue, plus one sentence either side, merged and shown once in note order, each snippet tagged with its topics. **Full note** tab: the whole note with the same highlighting and a color legend.
- Evidence: select text in either tab, then click a topic in the popup. The quote appears under that topic in the form and is underlined in the note; Remove deletes it.
- Right pane, the form:
  1. Is this note usable? Any "No" hides the rest.
  2. What does this note document? Topic buttons. A topic's questions appear only once it is ticked.
  3. Topic questions as clickable options. Not documented and Unclear are dashed and exclude other choices. Numbers validate as typed (range, "?" for unclear, blank for not documented). A "?" next to a question opens its instructions.
  4. Flag for review, Comment.
- Answers autosave about half a second after each change. Leaving the page saves.
- Submit checks every question in ticked topics, marks gaps in red with a list, and on success opens the next note. Ctrl+Enter submits.
- Time on each note counts only while the window is visible and someone moved the mouse or typed in the last 60 seconds.

**Guide [MVP].** Generated from the schema, opened from the top bar, downloadable as text.

## 7. Schema

`clincurate/schemas/pediatric_foot_ankle.yaml`: 6 topics, 17 questions, 3 note-level questions, 5 sampling strata. Content is as in draft 1; `docs/annotation-guide.md` lists every question.

Sampling strata, in priority order (the first match wins):

1. Explicit nighttime AFO or night splint
2. Serial casting
3. SMO
4. AFO, timing not stated
5. Other

## 8. Storage and export

- `items`: one row per note per annotator per batch: status, answers (JSON), evidence offsets, active seconds, timestamps.
- `batch_strata`: available and drawn counts per stratum per batch, for inverse-probability weights.
- `notes.sha256`: guards evidence offsets across laptops.
- Exports resolve answers with the draft 1 rules: unticked topic means *Not documented* (`screened_out`), blank in a ticked topic is `blank`, bad number is `invalid`, answer in an unticked topic is `invalid`.

## 9. Validation integration

- [MVP] Human-human agreement per question per batch with patient-clustered intervals.
- [Soon] Model versus human: import model predictions as a CSV keyed by note and question. They are held apart from annotators and never enter packages. The report shows sensitivity, PPV, F1 and kappa per question with intervals.
- [Soon] Stopping check: per-question target interval width set in the interface; batch page shows which questions have reached it.
- [Soon] Weighted estimates using the stored sampling fractions.
- [Future] Adaptive sampling on model uncertainty.

## 10. Distribution to laptops

- GitHub Actions builds `ClinCurate.exe` (Windows) and `ClinCurate.app` (macOS) on every push. The Linux build runs from the same spec file.
- The builds are unsigned. Windows SmartScreen and macOS Gatekeeper ask once on first launch; the README gives the clicks. Use is covered by the study IRB approval.
- Updating: replace the app file. The data folder is untouched.

## 11. Next steps

1. [Soon] Adjudication screen: pick the final answer per disagreeing question, with a reason; original answers kept.
2. [Soon] Model prediction import and model-versus-human report.
3. [Soon] Per-question precision targets and stopping display.
4. [Soon] Coordinator password, for when students annotate on the coordinator laptop.
5. [Future] Weighted estimates, adaptive sampling, assisted-mode experiment.

## 12. Open questions

1. Which approved storage carries batch, results and backup files?
2. Batch size and double-annotation share (set per batch; 20 percent suggested).
3. Per-question precision targets (EBSD).
