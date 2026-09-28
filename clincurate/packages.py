"""Moving work between laptops.

Coordinator -> annotator: a batch package (.ccpkg) with the schema and that
annotator's notes. Annotator -> coordinator: a results file (.ccres) with
answers only, no note text.

Both are encrypted with AES-256-GCM under a key derived (scrypt) from the
project passphrase, so a file left on a shared drive or in an email
attachment is unreadable without it. The passphrase is entered once per
laptop and kept in that laptop's database, which already holds PHI and
relies on the laptop's disk encryption.
"""

from __future__ import annotations

import gzip
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .store import Store, now

MAGIC = b"CLINCURATE1"
PACKAGE_FORMAT = "clincurate-package"
RESULTS_FORMAT = "clincurate-results"


class PackageError(ValueError):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise PackageError("A project passphrase is required.")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def seal(payload: dict, passphrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, body, MAGIC)


def unseal(data: bytes, passphrase: str) -> dict:
    if not data.startswith(MAGIC):
        raise PackageError("This is not a ClinCurate file.")
    salt, nonce, ct = data[len(MAGIC):len(MAGIC) + 16], data[len(MAGIC) + 16:len(MAGIC) + 28], data[len(MAGIC) + 28:]
    try:
        body = AESGCM(_key(passphrase, salt)).decrypt(nonce, ct, MAGIC)
    except InvalidTag:
        raise PackageError("Wrong passphrase, or the file is damaged.") from None
    return json.loads(gzip.decompress(body))


def passphrase(store: Store) -> str:
    p = store.get("package_passphrase")
    if not p:
        raise PackageError("Set the project passphrase first.")
    return p


# coordinator -> annotator ------------------------------------------------------

def export_package(store: Store, batch_id: int, annotator: str) -> bytes:
    b = store.batch(batch_id)
    if b["status"] != "active":
        raise PackageError("Start the batch before sending packages.")
    items = store.items(batch_id=batch_id, annotator=annotator)
    if not items:
        raise PackageError(f"{annotator} has no notes in this batch.")
    out_items = []
    for it in items:
        n = store.note(it["note_id"])
        out_items.append({
            "note_id": it["note_id"], "seq": it["seq"], "status": it["status"],
            "responses": json.loads(it["responses"]), "seconds": it["seconds"], "updated_at": it["updated_at"],
            "note": {k: n[k] for k in ("patient_id", "note_date", "note_type", "text", "sha256")},
        })
    store.log("coordinator", "export_package", f"{b['name']} -> {annotator} ({len(items)} notes)")
    return seal({
        "format": PACKAGE_FORMAT, "version": 1, "created_at": now(),
        "schema_yaml": store.get("schema_yaml"),
        "batch": {"uid": b["uid"], "name": b["name"], "purpose": b["purpose"]},
        "annotator": annotator, "items": out_items,
    }, passphrase(store))


def import_package(store: Store, data: bytes, pass_in: str) -> dict:
    if store.role == "coordinator":
        raise PackageError("This laptop is the coordinator. Open packages on the annotator's laptop.")
    pkg = unseal(data, pass_in)
    if pkg.get("format") != PACKAGE_FORMAT:
        raise PackageError("This file is not a batch package.")
    if store.role is None:
        store.put("role", "annotator")
    store.put("package_passphrase", pass_in)
    if pkg["schema_yaml"] != store.get("schema_yaml"):
        store.set_schema(pkg["schema_yaml"])
    project = store.project
    who = pkg["annotator"]
    store.add_annotator(who)
    b = store.batch_by_uid(pkg["batch"]["uid"])
    batch_id = b["id"] if b else store.create_batch(
        pkg["batch"]["name"], pkg["batch"].get("purpose", ""), 0, uid=pkg["batch"]["uid"], status="active")
    added = 0
    with store.tx() as db:
        for it in pkg["items"]:
            n = it["note"]
            db.execute("INSERT OR IGNORE INTO notes VALUES (?,?,?,?,?,?,?,?)",
                       (it["note_id"], n["patient_id"], n["note_date"], n["note_type"], n["text"],
                        n["sha256"], project.stratum_of(n["text"]), now()))
            db.execute("INSERT OR IGNORE INTO batch_notes VALUES (?,?,?,?)", (batch_id, it["note_id"], 0, it["seq"]))
            cur = db.execute(
                "INSERT OR IGNORE INTO items (batch_id, note_id, annotator, seq, status, responses, seconds, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (batch_id, it["note_id"], who, it["seq"], it["status"], json.dumps(it["responses"]),
                 it["seconds"], it["updated_at"]))
            added += cur.rowcount
    store.log(who, "import_package", f"{pkg['batch']['name']} ({added} new notes)")
    return {"batch": pkg["batch"]["name"], "annotator": who, "added": added, "total": len(pkg["items"])}


# annotator -> coordinator ------------------------------------------------------

def unsent_changes(store: Store, batch_id: int, annotator: str) -> bool:
    """True when this laptop has answers newer than the last results file."""
    b = store.batch(batch_id)
    last = store.last_change(batch_id, annotator)
    saved = store.get(f"results_saved:{b['uid']}:{annotator}")
    return bool(last) and (saved is None or last > saved)


def export_results(store: Store, batch_id: int, annotator: str) -> bytes:
    b = store.batch(batch_id)
    items = store.items(batch_id=batch_id, annotator=annotator)
    store.log(annotator, "export_results", b["name"])
    # Records what the student has sent, so the app can say whether newer
    # answers are still unsent and when it is safe to remove the batch.
    store.put(f"results_saved:{b['uid']}:{annotator}", store.last_change(batch_id, annotator) or now())
    return seal({
        "format": RESULTS_FORMAT, "version": 1, "created_at": now(),
        "batch_uid": b["uid"], "batch_name": b["name"], "annotator": annotator,
        # Answers and offsets only. The note hash lets the coordinator confirm
        # evidence offsets refer to the same text; no note text is included.
        "items": [{
            "note_id": it["note_id"], "status": it["status"], "responses": json.loads(it["responses"]),
            "seconds": it["seconds"], "updated_at": it["updated_at"], "submitted_at": it["submitted_at"],
            "sha256": store.note(it["note_id"])["sha256"],
        } for it in items],
    }, passphrase(store))


def import_results(store: Store, data: bytes) -> dict:
    res = unseal(data, passphrase(store))
    if res.get("format") != RESULTS_FORMAT:
        raise PackageError("This file is not a results file.")
    b = store.batch_by_uid(res["batch_uid"])
    if b is None:
        raise PackageError(f"Batch {res['batch_name']!r} is not in this project.")
    who = res["annotator"]
    updated = unchanged = mismatched = 0
    with store.tx() as db:
        for it in res["items"]:
            local = db.execute("SELECT * FROM items WHERE batch_id=? AND note_id=? AND annotator=?",
                               (b["id"], it["note_id"], who)).fetchone()
            note = store.note(it["note_id"])
            if local is None or note is None or note["sha256"] != it["sha256"]:
                mismatched += 1
                continue
            if it["updated_at"] and (local["updated_at"] is None or it["updated_at"] > local["updated_at"]):
                db.execute("UPDATE items SET status=?, responses=?, seconds=?, updated_at=?, submitted_at=? WHERE id=?",
                           (it["status"], json.dumps(it["responses"]), it["seconds"], it["updated_at"],
                            it["submitted_at"], local["id"]))
                updated += 1
            else:
                unchanged += 1
    store.put(f"results_received:{b['uid']}:{who}", res["created_at"])
    store.log("coordinator", "import_results", f"{res['batch_name']} from {who}: {updated} updated")
    return {"batch": res["batch_name"], "annotator": who, "updated": updated,
            "unchanged": unchanged, "mismatched": mismatched}
