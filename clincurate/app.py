"""Local web app: coordinator console and annotator workspace.

Serves only on 127.0.0.1. Requests with any other Host header are refused
(blocks DNS rebinding), and every POST needs the per-launch token (blocks
other web pages from submitting forms to this app).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)

from . import backup, packages, reports, sampling
from .focus import merged_snippets
from .guide import guide_sections, render_markdown
from .highlight import Highlighter
from .schema import SCREEN_QUESTION, OTHER_STRATUM, SchemaError
from .store import Store, now as _now

HERE = Path(__file__).parent
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def bundled_schemas() -> dict[str, Path]:
    return {p.stem: p for p in sorted((HERE / "schemas").glob("*.yaml"))}


def create_app(db_path: str | Path, testing: bool = False) -> Flask:
    app = Flask(__name__, template_folder=str(HERE / "web" / "templates"),
                static_folder=str(HERE / "web" / "static"))
    app.secret_key = secrets.token_hex(32)
    app.config.update(TESTING=testing, MAX_CONTENT_LENGTH=512 * 1024 * 1024,
                      SESSION_COOKIE_SAMESITE="Strict")
    store = Store(db_path)
    token = secrets.token_urlsafe(32)
    app.extensions["clincurate_store"] = store
    app.extensions["clincurate_token"] = token
    highlighter_cache: dict = {}

    def highlighter() -> Highlighter:
        key = id(store.project)
        if key not in highlighter_cache:
            highlighter_cache.clear()
            highlighter_cache[key] = Highlighter(store.project)
        return highlighter_cache[key]

    # security -----------------------------------------------------------
    @app.before_request
    def guard():
        host = (request.host or "").rsplit(":", 1)[0].strip("[]")
        if host not in _LOCAL_HOSTS:
            abort(403)
        if request.method == "POST":
            sent = request.headers.get("X-CC-Token") or request.form.get("_token")
            if not sent or not secrets.compare_digest(sent, token):
                abort(403)
        g.store = store

    @app.after_request
    def headers(resp: Response):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return resp

    @app.template_filter("answer")
    def answer_filter(v):
        if v is None or v == "":
            return "(blank)"
        return ", ".join(map(str, v)) if isinstance(v, list) else str(v)

    @app.context_processor
    def inject():
        return {"csrf_token": token, "role": store.role,
                "project_title": store.project.title if store.project else "ClinCurate"}

    def coordinator_only(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if store.role != "coordinator":
                flash("That page is only on the coordinator laptop.")
                return redirect(url_for("home"))
            return fn(*a, **kw)
        return wrapper

    def batch_or_404(batch_id: int):
        b = store.batch(batch_id)
        if b is None:
            abort(404)
        return b

    # home ---------------------------------------------------------------
    @app.get("/")
    def home():
        if store.role is None:
            return render_template("welcome.html", schemas=bundled_schemas())
        if store.role == "coordinator":
            return redirect(url_for("dashboard"))
        return redirect(url_for("annotate_home"))

    @app.post("/setup/coordinator")
    def setup_coordinator():
        if store.role is not None:
            abort(400)
        try:
            text = _schema_from_request(request)
            _check_passphrase(request.form.get("passphrase", ""), request.form.get("passphrase2", ""))
            store.set_schema(text)
        except (SchemaError, ValueError) as e:
            flash(str(e), "error")
            return redirect(url_for("home"))
        store.put("package_passphrase", request.form["passphrase"])
        store.put("role", "coordinator")
        store.log("coordinator", "setup", store.project.id)
        flash("Project created. Next: import notes and add annotators.")
        return redirect(url_for("project_page"))

    @app.post("/quit")
    def quit_app():
        if not app.config["TESTING"]:
            threading.Timer(0.5, lambda: os._exit(0)).start()
        return render_template("bye.html")

    # coordinator: dashboard and project ------------------------------------
    @app.get("/admin")
    @coordinator_only
    def dashboard():
        batches = []
        for b in store.batches():
            prog = store.progress(b["id"])
            total = sum(p["total"] for p in prog)
            done = sum(p["submitted"] or 0 for p in prog)
            batches.append({"b": b, "total": total, "done": done,
                            "notes": len(store.batch_notes(b["id"]))})
        return render_template("dashboard.html", batches=batches, strata=store.strata_counts(),
                               last_backup=store.get("last_backup"),
                               note_count=store.note_count(), annotators=store.annotators())

    @app.get("/admin/project")
    @coordinator_only
    def project_page():
        return render_template("project.html", strata=store.strata_counts(), note_count=store.note_count(),
                               annotators=store.annotators(active_only=False), schemas=bundled_schemas(),
                               schema_yaml=store.get("schema_yaml"))

    @app.post("/admin/notes/import")
    @coordinator_only
    def import_notes():
        f = request.files.get("notes")
        if not f or not f.filename:
            flash("Choose a CSV file.", "error")
            return redirect(url_for("project_page"))
        try:
            res = store.import_notes_csv(f.read())
        except (ValueError, UnicodeDecodeError) as e:
            flash(f"Import failed: {e}", "error")
            return redirect(url_for("project_page"))
        store.log("coordinator", "import_notes", json.dumps({k: res[k] for k in ("added", "skipped", "error_count")}))
        msg = f"Imported {res['added']} notes. {res['skipped']} already present."
        if res["error_count"]:
            msg += f" {res['error_count']} rows skipped: " + "; ".join(res["errors"][:3])
        flash(msg)
        return redirect(url_for("project_page"))

    @app.post("/admin/schema")
    @coordinator_only
    def replace_schema():
        if any(b["status"] != "planned" for b in store.batches()):
            flash("The schema is locked once a batch has started, so answers stay comparable. "
                  "Start a new project for a new schema version.", "error")
            return redirect(url_for("project_page"))
        try:
            store.set_schema(_schema_from_request(request))
        except (SchemaError, ValueError) as e:
            flash(str(e), "error")
            return redirect(url_for("project_page"))
        highlighter_cache.clear()
        store.log("coordinator", "replace_schema", store.project.id)
        flash("Schema updated. Strata were recalculated.")
        return redirect(url_for("project_page"))

    @app.post("/admin/passphrase")
    @coordinator_only
    def change_passphrase():
        try:
            _check_passphrase(request.form.get("passphrase", ""), request.form.get("passphrase2", ""))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("project_page"))
        store.put("package_passphrase", request.form["passphrase"])
        flash("Passphrase changed. Packages already sent still use the old one.")
        return redirect(url_for("project_page"))

    @app.post("/admin/annotators")
    @coordinator_only
    def add_annotator():
        try:
            store.add_annotator(request.form.get("name", ""))
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("project_page") + "#annotators")

    @app.post("/admin/annotators/<name>/toggle")
    @coordinator_only
    def toggle_annotator(name):
        row = next((a for a in store.annotators(active_only=False) if a["name"] == name), None)
        if row:
            store.set_annotator_active(name, not row["active"])
        return redirect(url_for("project_page") + "#annotators")

    @app.get("/guide")
    def guide():
        if store.project is None:
            return redirect(url_for("home"))
        return render_template("guide.html", g=guide_sections(store.project))

    @app.get("/guide.md")
    def guide_md():
        return Response(render_markdown(store.project), mimetype="text/markdown",
                        headers={"Content-Disposition": "attachment; filename=annotation-guide.md"})

    # coordinator: batches ---------------------------------------------------
    @app.post("/admin/batches")
    @coordinator_only
    def new_batch():
        n = len(store.batches()) + 1
        people = [a["name"] for a in store.annotators()]
        bid = store.create_batch(f"Batch {n}", annotators=people)
        return redirect(url_for("batch_page", batch_id=bid))

    @app.get("/admin/batches/<int:batch_id>")
    @coordinator_only
    def batch_page(batch_id):
        b = batch_or_404(batch_id)
        notes = store.batch_notes(batch_id)
        plan = None
        if b["status"] == "planned" and notes:
            try:
                plan = sampling.assignment_plan(store, batch_id)
            except sampling.BatchError as e:
                plan = {"error": str(e)}
        labels = store.project.stratum_labels()
        drawn = {r["stratum"]: dict(r) for r in store.batch_strata(batch_id)}
        return render_template(
            "batch.html", b=b, quotas=json.loads(b["quotas"]), chosen=json.loads(b["annotators"]),
            strata=store.strata_counts(), labels=labels, drawn=drawn, notes=notes, plan=plan,
            progress=store.progress(batch_id), annotators=store.annotators(),
            received={p["annotator"]: store.get(f"results_received:{b['uid']}:{p['annotator']}")
                      for p in store.progress(batch_id)},
            agreement=reports.agreement(store, batch_id) if b["status"] != "planned" else [],
            disagreements=reports.disagreement_list(store, batch_id) if b["status"] != "planned" else [],
            problems=reports.problems(store, batch_id) if b["status"] != "planned" else [],
            other=OTHER_STRATUM)

    @app.post("/admin/batches/<int:batch_id>/plan")
    @coordinator_only
    def save_plan(batch_id):
        b = batch_or_404(batch_id)
        if b["status"] != "planned":
            abort(400)
        quotas = {}
        for key, val in request.form.items():
            if key.startswith("quota_"):
                try:
                    quotas[key[6:]] = max(0, int(val or 0))
                except ValueError:
                    flash(f"Quota for {key[6:]} must be a whole number.", "error")
                    return redirect(url_for("batch_page", batch_id=batch_id))
        try:
            dual = min(1.0, max(0.0, float(request.form.get("dual_percent", 20)) / 100))
        except ValueError:
            dual = 0.2
        store.update_batch(batch_id, name=request.form.get("name", b["name"]).strip() or b["name"],
                           purpose=request.form.get("purpose", ""), dual_fraction=dual, quotas=quotas,
                           annotators=request.form.getlist("annotators"))
        if request.form.get("action") == "draw":
            try:
                drawn = sampling.draw(store, batch_id)
                flash(f"Drew {sum(drawn.values())} notes.")
            except sampling.BatchError as e:
                flash(str(e), "error")
        else:
            flash("Plan saved.")
        return redirect(url_for("batch_page", batch_id=batch_id))

    @app.post("/admin/batches/<int:batch_id>/<action>")
    @coordinator_only
    def batch_action(batch_id, action):
        b = batch_or_404(batch_id)
        try:
            if action == "start":
                sampling.start(store, batch_id)
                flash(f"{b['name']} started. Send each annotator their package, or they can annotate on this laptop.")
            elif action == "close":
                sampling.close(store, batch_id)
                flash(f"{b['name']} closed.")
            elif action == "reopen":
                sampling.reopen(store, batch_id)
            elif action == "delete":
                if b["status"] != "planned":
                    raise sampling.BatchError("Only planned batches can be deleted.")
                store.delete_batch(batch_id)
                flash(f"{b['name']} deleted.")
                return redirect(url_for("dashboard"))
            elif action in ("up", "down"):
                store.move_batch(batch_id, -1 if action == "up" else 1)
                return redirect(url_for("dashboard"))
            else:
                abort(404)
        except sampling.BatchError as e:
            flash(str(e), "error")
        store.log("coordinator", f"batch_{action}", b["name"])
        return redirect(url_for("batch_page", batch_id=batch_id))

    @app.get("/admin/batches/<int:batch_id>/package/<annotator>")
    @coordinator_only
    def download_package(batch_id, annotator):
        b = batch_or_404(batch_id)
        try:
            data = packages.export_package(store, batch_id, annotator)
        except packages.PackageError as e:
            flash(str(e), "error")
            return redirect(url_for("batch_page", batch_id=batch_id))
        name = _safe(f"{b['name']}_{annotator}") + ".ccpkg"
        return send_file(_bytes(data), as_attachment=True, download_name=name,
                         mimetype="application/octet-stream")

    @app.post("/admin/results")
    @coordinator_only
    def upload_results():
        files = [f for f in request.files.getlist("results") if f and f.filename]
        if not files:
            flash("Choose one or more results files.", "error")
        for f in files:
            try:
                r = packages.import_results(store, f.read())
                msg = f"{f.filename}: {r['updated']} answers updated from {r['annotator']} ({r['batch']})."
                if r["mismatched"]:
                    msg += f" {r['mismatched']} did not match this project and were skipped."
                flash(msg)
            except packages.PackageError as e:
                flash(f"{f.filename}: {e}", "error")
        return redirect(request.referrer or url_for("dashboard"))

    @app.get("/admin/export/<shape>.csv")
    @coordinator_only
    def export(shape):
        if shape not in ("wide", "long", "evidence"):
            abort(404)
        batch_id = request.args.get("batch", type=int)
        text = reports.export_csv(store, shape, batch_id)
        store.log("coordinator", "export", f"{shape} batch={batch_id}")
        return Response(text, mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=clincurate_{shape}.csv"})

    @app.get("/admin/notes/<path:note_id>")
    @coordinator_only
    def preview_note(note_id):
        n = store.note(note_id)
        if n is None:
            abort(404)
        return render_template("annotate.html", data=_note_payload(n, None, readonly=True), who=None)

    @app.get("/admin/activity")
    @coordinator_only
    def activity():
        rows = store.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 300").fetchall()
        return render_template("activity.html", rows=rows)

    # annotator ------------------------------------------------------------
    @app.get("/annotate")
    def annotate_home():
        if store.role is None:
            return redirect(url_for("home"))
        names = sorted({it["annotator"] for it in store.items()})
        if store.role == "annotator" and len(names) == 1:
            return redirect(url_for("queue", who=names[0]))
        return render_template("annotate_home.html", names=names)

    @app.post("/package/import")
    def import_package():
        f = request.files.get("package")
        if not f or not f.filename:
            flash("Choose the package file you were sent.", "error")
            return redirect(request.referrer or url_for("home"))
        try:
            r = packages.import_package(store, f.read(), request.form.get("passphrase", ""))
        except packages.PackageError as e:
            flash(str(e), "error")
            return redirect(request.referrer or url_for("home"))
        highlighter_cache.clear()
        flash(f"Opened {r['batch']}: {r['added']} new notes for {r['annotator']}.")
        return redirect(url_for("queue", who=r["annotator"]))

    @app.get("/annotate/<who>")
    def queue(who):
        items = store.items(annotator=who)
        if not items:
            abort(404)
        batches: dict = {}
        for it in items:
            bs = batches.setdefault(it["batch_id"], {"name": it["batch_name"], "status": it["batch_status"], "rows": []})
            bs["rows"].append(it)
        for bs in batches.values():
            bs["done"] = sum(i["status"] == "submitted" for i in bs["rows"])
            bs["drafts"] = sum(i["status"] == "draft" for i in bs["rows"])
        if store.role == "annotator":
            for bid, bs in batches.items():
                bs["unsent"] = packages.unsent_changes(store, bid, who)
                bs["sent"] = store.get(f"results_saved:{store.batch(bid)['uid']}:{who}")
        return render_template("queue.html", who=who, batches=batches)

    @app.get("/annotate/<who>/next")
    def next_item(who):
        nid = _next_open(who)
        if nid is None:
            flash("Nothing left to annotate. Thank you.")
            return redirect(url_for("queue", who=who))
        return redirect(url_for("annotate_item", who=who, item_id=nid))

    @app.get("/annotate/<who>/item/<int:item_id>")
    def annotate_item(who, item_id):
        it = store.item(item_id)
        if it is None or it["annotator"] != who:
            abort(404)
        b = store.batch(it["batch_id"])
        n = store.note(it["note_id"])
        return render_template("annotate.html", who=who,
                               data=_note_payload(n, it, readonly=b["status"] == "closed"))

    @app.post("/api/item/<int:item_id>")
    def save_item(item_id):
        it = store.item(item_id)
        if it is None:
            abort(404)
        if store.batch(it["batch_id"])["status"] == "closed":
            return jsonify(ok=False, error="This batch is closed."), 409
        body = request.get_json(force=True, silent=True) or {}
        status = body.get("status", "draft")
        responses = body.get("responses")
        if not isinstance(responses, dict) or status not in ("draft", "submitted"):
            return jsonify(ok=False, error="Bad request"), 400
        # A resubmitted note stays submitted even when later edits autosave.
        if status == "draft" and it["status"] == "submitted":
            status = "submitted"
        store.save_item(item_id, responses, status, float(body.get("seconds", 0) or 0))
        if body.get("status") == "submitted":
            store.log(it["annotator"], "submit", it["note_id"])
        return jsonify(ok=True, status=status, next=_next_open(it["annotator"], after=item_id),
                       progress=_progress(it["annotator"], it["batch_id"]))

    @app.get("/annotate/<who>/results/<int:batch_id>")
    def download_results(who, batch_id):
        b = batch_or_404(batch_id)
        try:
            data = packages.export_results(store, batch_id, who)
        except packages.PackageError as e:
            flash(str(e), "error")
            return redirect(url_for("queue", who=who))
        return send_file(_bytes(data), as_attachment=True, download_name=_safe(f"{b['name']}_{who}_results") + ".ccres",
                         mimetype="application/octet-stream")

    @app.post("/annotate/<who>/remove/<int:batch_id>")
    def remove_batch(who, batch_id):
        if store.role != "annotator":
            abort(403)
        b = batch_or_404(batch_id)
        if packages.unsent_changes(store, batch_id, who):
            flash("Save a results file first: some answers have not been sent.", "error")
            return redirect(url_for("queue", who=who))
        n = store.purge_batch(batch_id)
        store.log(who, "remove_batch", f"{b['name']} ({n} notes deleted)")
        flash(f"{b['name']} and its {n} notes were removed from this laptop.")
        return redirect(url_for("annotate_home") if store.items() else url_for("done"))

    @app.get("/done")
    def done():
        return render_template("done.html")

    @app.get("/admin/backup")
    @coordinator_only
    def download_backup():
        data = backup.make_backup(store)
        store.put("last_backup", _now())
        return send_file(_bytes(data), as_attachment=True, mimetype="application/octet-stream",
                         download_name=_safe(f"clincurate_backup_{_now()[:10]}") + ".ccbak")

    @app.post("/setup/restore")
    def restore():
        f = request.files.get("backup")
        if not f or not f.filename:
            flash("Choose a backup file.", "error")
            return redirect(url_for("home"))
        try:
            backup.restore_backup(store, f.read(), request.form.get("passphrase", ""))
        except packages.PackageError as e:
            flash(str(e), "error")
            return redirect(url_for("home"))
        highlighter_cache.clear()
        flash("Project restored from backup.")
        return redirect(url_for("home"))

    # helpers ---------------------------------------------------------------
    def _next_open(who: str, after: int | None = None) -> int | None:
        rows = [it for it in store.items(annotator=who)
                if it["batch_status"] == "active" and it["status"] != "submitted"]
        if after is not None:
            later = [r for r in rows if r["id"] != after]
            rows = later
        drafts = [r for r in rows if r["status"] == "draft"]
        return (drafts or rows)[0]["id"] if (drafts or rows) else None

    def _progress(who: str, batch_id: int) -> dict:
        items = store.items(batch_id=batch_id, annotator=who)
        done = sum(i["status"] == "submitted" for i in items)
        return {"total": len(items), "done": done, "left": len(items) - done,
                "drafts": sum(i["status"] == "draft" for i in items)}

    def _note_payload(n, it, readonly: bool) -> dict:
        p = store.project
        text = n["text"]
        hl = highlighter()
        hits = hl.find(text)
        form = {
            "screen": SCREEN_QUESTION,
            "missing": p.missing,
            "domains": [{"name": d.name, "label": d.label, "color": d.color, "fields": [
                {"name": f.name, "label": f.label, "type": f.type, "help": f.help, "units": f.units,
                 "range": f.range, "options": p.choice_options(f) if f.type.endswith("choice") else []}
                for f in d.fields]} for d in p.domains],
            "record": [{"name": q.name, "label": q.label, "type": q.type, "required": q.required,
                        "options": q.values if q.type.endswith("choice") else []} for q in p.record_questions],
        }
        data = {
            "readonly": readonly,
            "note": {"id": n["note_id"], "patient_id": n["patient_id"], "date": n["note_date"],
                     "type": n["note_type"], "text": text},
            "hits": [{"s": h.start, "e": h.end, "d": h.domain} for h in hits],
            "snippets": [{"s": m.start, "e": m.end, "d": m.domains} for m in merged_snippets(text, p, hl)],
            "form": form,
            "item": None,
        }
        if it is not None:
            data["item"] = {"id": it["id"], "status": it["status"], "responses": json.loads(it["responses"]),
                            "who": it["annotator"], "batch": store.batch(it["batch_id"])["name"],
                            "progress": _progress(it["annotator"], it["batch_id"]),
                            "save_url": url_for("save_item", item_id=it["id"]),
                            "queue_url": url_for("queue", who=it["annotator"]),
                            "item_url": url_for("queue", who=it["annotator"]) + "/item/"}
        return data

    return app


def _schema_from_request(req) -> str:
    f = req.files.get("schema_file")
    if f and f.filename:
        return f.read().decode("utf-8-sig")
    choice = req.form.get("schema_choice")
    schemas = bundled_schemas()
    if choice in schemas:
        return schemas[choice].read_text(encoding="utf-8")
    raise ValueError("Choose a project template or upload a schema file.")


def _check_passphrase(p1: str, p2: str) -> None:
    if len(p1) < 10:
        raise ValueError("Use a passphrase of at least 10 characters.")
    if p1 != p2:
        raise ValueError("The two passphrases do not match.")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "clincurate"


def _bytes(data: bytes):
    import io
    return io.BytesIO(data)
