"""End to end: coordinator laptop plans and sends a batch, annotator laptop
annotates, results come back, agreement and exports work."""

import json
import re
from pathlib import Path

import pytest

from clincurate import packages, reports, sampling
from clincurate.app import create_app
from clincurate.store import Store

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "clincurate" / "schemas" / "pediatric_foot_ankle.yaml"
DEMO = ROOT / "examples" / "synthetic_notes" / "demo_notes.csv"
PASS = "correct horse battery"


def client_for(path):
    app = create_app(path)
    return app, app.test_client(), app.extensions["clincurate_token"]


def post(c, tok, url, **kw):
    data = kw.pop("data", {})
    data["_token"] = tok
    return c.post(url, data=data, **kw)


@pytest.fixture
def coord(tmp_path):
    app, c, tok = client_for(tmp_path / "coord.db")
    r = post(c, tok, "/setup/coordinator", data={"schema_choice": "pediatric_foot_ankle",
                                                 "passphrase": PASS, "passphrase2": PASS})
    assert r.status_code == 302
    with DEMO.open("rb") as fh:
        post(c, tok, "/admin/notes/import", data={"notes": (fh, "demo.csv")}, content_type="multipart/form-data")
    for name in ("Avery Kim", "Blake Diaz"):
        post(c, tok, "/admin/annotators", data={"name": name})
    return app, c, tok


def test_security(tmp_path):
    app, c, tok = client_for(tmp_path / "x.db")
    assert c.post("/setup/coordinator", data={}).status_code == 403
    assert c.get("/", headers={"Host": "evil.example:8765"}).status_code == 403
    r = c.get("/")
    assert r.status_code == 200 and "script-src 'self'" in r.headers["Content-Security-Policy"]


def test_full_workflow(coord, tmp_path):
    app, c, tok = coord
    store: Store = app.extensions["clincurate_store"]
    assert store.note_count() == 240
    assert c.get("/admin").status_code == 200

    # plan batch 1 through the interface
    r = post(c, tok, "/admin/batches")
    bid = int(re.search(r"/admin/batches/(\d+)", r.headers["Location"]).group(1))
    plan = {"name": "Pilot", "purpose": "oversample nighttime", "dual_percent": "50", "action": "draw",
            "quota_explicit_nighttime": "6", "quota_afo_unspecified": "6", "quota_serial_casting": "0",
            "annotators": ["Avery Kim", "Blake Diaz"]}
    r = post(c, tok, f"/admin/batches/{bid}/plan", data=plan)
    assert r.status_code == 302
    assert len(store.batch_notes(bid)) == 12
    drawn = {r["stratum"]: r["drawn"] for r in store.batch_strata(bid)}
    assert drawn == {"explicit_nighttime": 6, "afo_unspecified": 6}
    page = c.get(f"/admin/batches/{bid}").get_data(as_text=True)
    assert "Start batch" in page

    # a second planned batch cannot reuse the same notes
    b2 = store.create_batch("Batch 2", quotas={"explicit_nighttime": 100}, annotators=["Avery Kim"])
    sampling.draw(store, b2)
    assert not {n["note_id"] for n in store.batch_notes(b2)} & {n["note_id"] for n in store.batch_notes(bid)}

    post(c, tok, f"/admin/batches/{bid}/start")
    assert store.batch(bid)["status"] == "active"
    items = store.items(batch_id=bid)
    assert len(items) == 12 + 6  # 50% dual

    # Avery annotates on her own laptop from a package
    r = c.get(f"/admin/batches/{bid}/package/Avery Kim")
    assert r.status_code == 200
    pkg = r.data
    assert b"AFO" not in pkg  # encrypted
    lap = tmp_path / "avery.db"
    app2, c2, tok2 = client_for(lap)
    r = post(c2, tok2, "/package/import", data={"package": (__import__("io").BytesIO(pkg), "p.ccpkg"),
                                                 "passphrase": "wrong passphrase"}, content_type="multipart/form-data")
    assert app2.extensions["clincurate_store"].role is None
    r = post(c2, tok2, "/package/import", data={"package": (__import__("io").BytesIO(pkg), "p.ccpkg"),
                                                 "passphrase": PASS}, content_type="multipart/form-data")
    s2 = app2.extensions["clincurate_store"]
    assert s2.role == "annotator"
    mine = s2.items(annotator="Avery Kim")
    assert len(mine) == 9
    q = c2.get("/annotate/Avery Kim")
    assert q.status_code == 200 and "9 notes left" in q.get_data(as_text=True)
    page = c2.get(f"/annotate/Avery Kim/item/{mine[0]['id']}").get_data(as_text=True)
    assert "cc-data" in page

    answer = {"note_usable": "Yes", "documented_domains": ["orthosis", "gait"], "brace_type": ["Nighttime AFO"],
              "brace_status": "Continuing", "toe_walking": "Intermittent", "heel_strike": "Present",
              "gait_change": "Stable", "evidence": [{"label": "orthosis", "start": 0, "end": 7}]}
    for it in mine:
        r = c2.post(f"/api/item/{it['id']}", json={"responses": answer, "status": "submitted", "seconds": 42},
                    headers={"X-CC-Token": tok2})
        assert r.get_json()["ok"]
    assert c2.post(f"/api/item/{mine[0]['id']}", json={"responses": {}, "status": "submitted"}).status_code == 403
    res = c2.get(f"/annotate/Avery Kim/results/{s2.items()[0]['batch_id']}")
    assert res.status_code == 200
    body = packages.unseal(res.data, PASS)
    assert "HISTORY" not in json.dumps(body)  # no note text leaves in results

    # Blake annotates on the coordinator laptop, differing on toe walking
    for it in store.items(batch_id=bid, annotator="Blake Diaz"):
        c.post(f"/api/item/{it['id']}", json={"responses": {**answer, "toe_walking": "Constant"}, "status": "submitted"},
               headers={"X-CC-Token": tok})

    r = post(c, tok, "/admin/results", data={"results": (__import__("io").BytesIO(res.data), "r.ccres")},
             content_type="multipart/form-data")
    assert all(i["status"] == "submitted" for i in store.items(batch_id=bid))

    agree = {a["field"]: a for a in reports.agreement(store, bid)}
    assert agree["toe_walking"]["n"] == 6 and agree["brace_status"]["value"] == 1.0
    assert agree["toe_walking"]["value"] == 0.0
    dis = reports.disagreement_list(store, bid)
    assert len(dis) == 6 and dis[0]["fields"][0]["name"] == "toe_walking"
    page = c.get(f"/admin/batches/{bid}").get_data(as_text=True)
    assert "Needs review" in page

    wide = c.get(f"/admin/export/wide.csv?batch={bid}").get_data(as_text=True)
    assert wide.count("\n") == 19 and "stratum" in wide.splitlines()[0]
    ev = c.get(f"/admin/export/evidence.csv?batch={bid}").get_data(as_text=True)
    assert "HISTORY" in ev

    post(c, tok, f"/admin/batches/{bid}/close")
    r = c.post(f"/api/item/{items[0]['id']}", json={"responses": answer, "status": "draft"}, headers={"X-CC-Token": tok})
    assert r.status_code == 409


def test_schema_locked_after_start(coord):
    app, c, tok = coord
    store = app.extensions["clincurate_store"]
    b = store.create_batch("B", quotas={"afo_unspecified": 2}, annotators=["Avery Kim"], dual_fraction=0)
    sampling.draw(store, b)
    sampling.start(store, b)
    post(c, tok, "/admin/schema", data={"schema_choice": "pediatric_foot_ankle"})
    assert store.batch(b)["status"] == "active"
    assert "locked" in c.get("/admin/project").get_data(as_text=True)


def test_dual_needs_two_annotators(coord):
    app, c, tok = coord
    store = app.extensions["clincurate_store"]
    b = store.create_batch("B", quotas={"afo_unspecified": 4}, annotators=["Avery Kim"], dual_fraction=0.5)
    sampling.draw(store, b)
    with pytest.raises(sampling.BatchError):
        sampling.start(store, b)
