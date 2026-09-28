# ClinCurate

ClinCurate is a local, offline chart abstraction app for clinical notes. It runs entirely on one laptop, opens in the browser, and never sends data over the network. Nobody needs a command line.

- **Coordinators** import notes, add annotators, plan batches by sampling stratum with a double-annotated share, send out batches, collect results, and check agreement and exports.
- **Annotators** work through their notes with highlighted evidence sentences (Focus), save evidence by selecting text, answer a form that shows only the relevant questions, and always see how many notes are left. Work autosaves.

The first use case is pediatric foot and ankle phenotypes at UCSF (orthoses, dorsiflexion, gait, escalation).

![Batch planning](docs/img/coordinator_batch_plan.png)
![Annotation workspace](docs/img/annotator_workspace.png)

## Running it

Coordinators and annotators: open `ClinCurate.exe` (Windows) or `ClinCurate.app` (macOS). The app opens a browser tab. Use **Quit** in the top bar to close it. Data stays in a `ClinCurate` folder in your home directory.

The builds come from GitHub Actions (`test-and-build` workflow, artifacts `ClinCurate-Windows` and `ClinCurate-macOS`). They are unsigned; on managed laptops, IT needs to sign or allow-list them.

## Moving work between laptops

Batch files (`.ccpkg`) and results files (`.ccres`) are encrypted with the project passphrase. Results files contain answers only, no note text. Move them only through UCSF-approved storage, and share the passphrase separately.

## Development

```
pip install -e ".[dev]"
python -m clincurate          # starts the app and opens a browser
pytest
pyinstaller packaging/clincurate.spec
```

Demo data: `examples/synthetic_notes/demo_notes.csv` (240 fabricated notes). Never commit real clinical text.

## Documents

- `docs/mvp-spec.md`: design, workflows, security model, next steps
- `docs/annotation-guide.md`: annotator guide generated from the schema
- `clincurate/schemas/pediatric_foot_ankle.yaml`: questions, highlight cues, sampling strata

## Layout

- `clincurate/schema.py`: project schema
- `clincurate/highlight.py`, `focus.py`: highlighting and Focus snippets
- `clincurate/store.py`: SQLite storage
- `clincurate/sampling.py`: batch draws and assignment
- `clincurate/packages.py`: encrypted batch and results files
- `clincurate/export.py`, `reports.py`, `stats.py`: exports and agreement
- `clincurate/app.py`, `clincurate/web/`: the interface
- `clincurate/launcher.py`, `packaging/`: double-click app
