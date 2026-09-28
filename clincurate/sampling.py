"""Batch drawing and assignment.

A batch is planned as per-stratum quotas. Drawing picks notes at random
within each stratum from notes not already in another batch, and records how
many were available so later estimates can be weighted by sampling fraction.
"""

from __future__ import annotations

import json
import random

from .store import Store, now


class BatchError(ValueError):
    pass


def draw(store: Store, batch_id: int, seed: int | None = None) -> dict[str, int]:
    b = store.batch(batch_id)
    if b is None:
        raise BatchError("Batch not found.")
    if b["status"] != "planned":
        raise BatchError("Only planned batches can be redrawn.")
    quotas = {k: int(v) for k, v in json.loads(b["quotas"]).items() if int(v) > 0}
    if not quotas:
        raise BatchError("Set at least one stratum quota above zero.")
    rng = random.Random(seed)

    with store.tx() as db:
        db.execute("DELETE FROM batch_notes WHERE batch_id=?", (batch_id,))
        db.execute("DELETE FROM batch_strata WHERE batch_id=?", (batch_id,))
        chosen: list[str] = []
        drawn: dict[str, int] = {}
        for stratum, n in quotas.items():
            pool = [r[0] for r in db.execute(
                "SELECT note_id FROM notes WHERE stratum=? AND note_id NOT IN"
                " (SELECT note_id FROM batch_notes) ORDER BY note_id", (stratum,))]
            pick = rng.sample(pool, min(n, len(pool)))
            drawn[stratum] = len(pick)
            db.execute("INSERT INTO batch_strata VALUES (?,?,?,?)", (batch_id, stratum, len(pool), len(pick)))
            chosen += pick
        rng.shuffle(chosen)
        n_dual = round(len(chosen) * float(b["dual_fraction"]))
        # Dual notes are spread across strata by taking them from the
        # shuffled order rather than from one stratum.
        for seq, note_id in enumerate(chosen):
            db.execute("INSERT INTO batch_notes VALUES (?,?,?,?)", (batch_id, note_id, int(seq < n_dual), seq))
    return drawn


def assignment_plan(store: Store, batch_id: int) -> dict[str, list[str]]:
    """Primary notes round-robin across annotators; each dual note also goes
    to the next annotator in rotation, so the pair always differs."""
    b = store.batch(batch_id)
    people = json.loads(b["annotators"])
    notes = store.batch_notes(batch_id)
    if not people:
        raise BatchError("Choose at least one annotator.")
    if any(n["dual"] for n in notes) and len(people) < 2:
        raise BatchError("Double annotation needs at least two annotators.")
    plan: dict[str, list[str]] = {p: [] for p in people}
    for i, n in enumerate(notes):
        first = people[i % len(people)]
        plan[first].append(n["note_id"])
        if n["dual"]:
            plan[people[(i + 1) % len(people)]].append(n["note_id"])
    return plan


def start(store: Store, batch_id: int) -> None:
    b = store.batch(batch_id)
    if b["status"] != "planned":
        raise BatchError("Batch has already started.")
    if not store.batch_notes(batch_id):
        raise BatchError("Draw notes before starting.")
    plan = assignment_plan(store, batch_id)
    seq_of = {n["note_id"]: n["seq"] for n in store.batch_notes(batch_id)}
    with store.tx() as db:
        for person, note_ids in plan.items():
            for note_id in note_ids:
                db.execute("INSERT INTO items (batch_id, note_id, annotator, seq) VALUES (?,?,?,?)",
                           (batch_id, note_id, person, seq_of[note_id]))
        db.execute("UPDATE batches SET status='active', started_at=? WHERE id=?", (now(), batch_id))


def close(store: Store, batch_id: int) -> None:
    if store.batch(batch_id)["status"] != "active":
        raise BatchError("Only active batches can be closed.")
    store.update_batch(batch_id, status="closed", closed_at=now())


def reopen(store: Store, batch_id: int) -> None:
    if store.batch(batch_id)["status"] != "closed":
        raise BatchError("Only closed batches can be reopened.")
    store.update_batch(batch_id, status="active", closed_at=None)
