"""Outra opção para hoje (por motivo), treino gerado na hora e próximo exercício sugerido."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from gymgarmin import alternative as A, catalog, clock, generator as G
from gymgarmin.limitations import check_exercise

PROF = {"training_months": 18, "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 60, "age": 35,
        "weight_kg": 80}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "t.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 9, 0))  # segunda
    from gymgarmin.app import app

    with TestClient(app) as c:
        c.put("/api/profile", json=PROF)
        c.post("/api/generator/apply", json={})
        yield c
    clock.freeze(None)


def _ids(items):
    return [(i["exercise_id"], i.get("sets")) for i in items]


def _today_strength(c):
    sch = c.get("/api/schedule").json()
    return next(e for e in sch["entries"] if e["date"] == "2026-11-16" and e.get("kind") == "forca")


def _day(i=0, prof=PROF):
    return G.generate(prof)["days"][i]


def test_every_reason_respects_limits_and_time():
    prof = {**PROF, "limitations": [{"key": "joelho", "severity": "evitar"}]}
    for d in G.generate(prof)["days"]:
        for r in A.REASON_IDS:
            opts = {"sick": "pescoco"} if r == "doente" else {"region": "ombro"} if r == "dor" else {"muscles": ["peito"]} if r == "dolorido" else \
                {"kit": ["halteres", "elastico"]} if r == "fora" else {"minutes": 30} if r == "tempo" else {}
            ctx = A.Ctx(prof, balance=[{"key": "hamstrings", "missing": 6}])
            o = A.propose(d["items"], r, ctx, opts, d["name"])
            st = [i for i in o["items"] if A.is_strength(i)]
            ids = [i["exercise_id"] for i in o["items"] if (i.get("phase") or "principal") == "principal"]
            assert len(ids) == len(set(ids)), (r, ids)
            lims = prof["limitations"] + ([{"key": "ombro", "severity": "evitar"}] if r == "dor" else [])
            assert all(check_exercise(catalog.get(i["exercise_id"]), lims)["status"] != "evitar" for i in st), r
            assert o["minutes"]["after"] <= o["minutes"]["budget"] * 1.1 + 1, (r, o["minutes"])
            assert o["why"] and o["base"]


def test_tired_cuts_volume_and_drops_axial_loading():
    d = _day(1)  # inferior: agachamento livre
    o = A.propose(d["items"], "cansado", A.Ctx(PROF), {}, d["name"])
    assert o["sets"]["after"] <= 0.75 * o["sets"]["before"]
    assert not any(i["exercise_id"] in A.AXIAL for i in o["items"])
    assert all(float(i.get("rpe_target") or 0) <= 7 for i in o["items"] if A.is_strength(i))


def test_short_on_time_keeps_main_lifts():
    d = _day(0)
    mains = [i["exercise_id"] for i in d["items"] if i.get("role") == "main"]
    o = A.propose(d["items"], "tempo", A.Ctx(PROF), {"minutes": 30}, d["name"])
    kept = {i["exercise_id"] for i in o["items"]}
    assert set(mains) <= kept
    assert o["minutes"]["after"] <= 33


def test_sore_legs_on_leg_day_becomes_upper_body():
    d = _day(1)
    o = A.propose(d["items"], "dolorido", A.Ctx(PROF), {"muscles": ["pernas"]}, d["name"])
    legs = set(A.MUSCLE_GROUPS["pernas"]["muscles"])
    for i in o["items"]:
        if A.is_strength(i) and i.get("role") in ("main", "sec"):
            assert not set(catalog.get(i["exercise_id"])["primaryMuscles"]) & legs


def test_away_from_gym_uses_only_the_kit():
    d = _day(0)
    o = A.propose(d["items"], "fora", A.Ctx(PROF), {"kit": []}, d["name"])
    for i in o["items"]:
        ex = catalog.get(i["exercise_id"])
        if (i.get("phase") or "principal") == "principal":
            assert ex["equipment"] in (None, "body only"), ex["id"]
    o2 = A.propose(d["items"], "fora", A.Ctx(PROF), {"kit": ["halteres"]}, d["name"])
    assert any(catalog.get(i["exercise_id"])["equipment"] == "dumbbell" for i in o2["items"])


def test_busy_gym_avoids_barbells():
    d = _day(0)
    o = A.propose(d["items"], "ocupado", A.Ctx(PROF), {}, d["name"])
    assert not any(catalog.get(i["exercise_id"])["equipment"] in ("barbell", "e-z curl bar") for i in o["items"]
                   if A.is_strength(i))


def test_vary_keeps_main_and_changes_accessories_and_seed_rotates():
    d = _day(0)
    o = A.propose(d["items"], "variar", A.Ctx(PROF), {}, d["name"])
    o2 = A.propose(d["items"], "variar", A.Ctx(PROF, seed=1), {}, d["name"])
    mains = {i["exercise_id"] for i in d["items"] if i.get("role") == "main"}
    assert mains <= {i["exercise_id"] for i in o["items"]}
    assert any(c["type"] == "troca" for c in o["changes"])
    assert [i["exercise_id"] for i in o["items"]] != [i["exercise_id"] for i in o2["items"]]
    assert A.propose(d["items"], "variar", A.Ctx(PROF), {}, d["name"])["items"] == o["items"]  # determinístico


def test_missing_trains_what_is_behind():
    d = _day(0)
    o = A.propose(d["items"], "falta", A.Ctx(PROF, balance=[{"key": "hamstrings", "missing": 8}, {"key": "calves", "missing": 6}]),
                  {}, d["name"])
    prim = {m for i in o["items"] if A.is_strength(i) for m in catalog.get(i["exercise_id"])["primaryMuscles"]}
    assert "hamstrings" in prim and "calves" in prim


def test_next_exercise_sequence_is_coherent_and_stops():
    ctx = A.Ctx(PROF)
    seq, regions = [], []
    for _ in range(14):
        r = A.next_exercises(seq, ctx, n=1)
        if not r["suggestions"]:
            assert r["done"]
            break
        s = r["suggestions"][0]
        assert s["exercise_id"] not in {x["exercise_id"] for x in seq}
        seq.append({"exercise_id": s["exercise_id"], "sets": s["sets"]})
    first = catalog.get(seq[0]["exercise_id"])
    assert first["mechanic"] == "compound"  # começa pelo composto
    assert sum(int(x["sets"]) for x in seq) <= A.SESSION_SETS_CAP + 4
    up = sum(1 for x in seq if set(catalog.get(x["exercise_id"])["primaryMuscles"]) & G._UPPER_M)
    lo = sum(1 for x in seq if set(catalog.get(x["exercise_id"])["primaryMuscles"]) & G._LOWER_M)
    assert min(up, lo) <= 1  # não mistura supino no meio do treino de perna


def test_next_after_leg_exercises_stays_on_legs():
    ctx = A.Ctx(PROF)
    r = A.next_exercises([{"exercise_id": "Barbell_Squat", "sets": 4}, {"exercise_id": "Romanian_Deadlift", "sets": 3}], ctx, n=3)
    for s in r["suggestions"]:
        assert not set(catalog.get(s["exercise_id"])["primaryMuscles"]) & G._UPPER_M, s["exercise_id"]


def test_api_preview_apply_undo(client):
    e = _today_strength(client)
    opts = client.get("/api/alternative/options").json()
    assert {r["id"] for r in opts["reasons"]} == A.REASON_IDS
    pv = client.post("/api/alternative/preview", json={"entry_id": e["id"], "reason": "tempo", "opts": {"minutes": 30}}).json()
    assert pv["preview"] and pv["minutes"]["after"] <= 33
    saved = client.post("/api/alternative/apply", json={"entry_id": e["id"], "reason": "tempo", "opts": {"minutes": 30}}).json()
    assert saved["alt"]["reason"] == "tempo" and _ids(saved["alt_backup"]["items"]) == _ids(e["items"])
    # trocar de ideia: outro motivo parte do treino original, não do já trocado
    again = client.post("/api/alternative/apply", json={"entry_id": e["id"], "reason": "variar"}).json()
    assert _ids(again["alt_backup"]["items"]) == _ids(e["items"]) and again["alt"]["reason"] == "variar"
    pre = client.get(f"/api/schedule/{e['id']}/prefill").json()
    assert [i["exercise_id"] for i in pre["items"] if i["phase"] == "principal"] == \
        [i["exercise_id"] for i in again["items"] if (i.get("phase") or "principal") == "principal" and catalog.get(i["exercise_id"])]
    back = client.post(f"/api/alternative/undo/{e['id']}").json()
    assert _ids(back["items"]) == _ids(e["items"]) and "alt" not in back
    assert client.post(f"/api/alternative/undo/{e['id']}").status_code == 400


def test_api_errors(client):
    e = _today_strength(client)
    assert client.post("/api/alternative/preview", json={"entry_id": e["id"], "reason": "x"}).status_code == 400
    assert client.post("/api/alternative/preview", json={"entry_id": e["id"], "reason": "dor"}).status_code == 400


def test_api_draft_and_suggest(client):
    d = client.post("/api/alternative/draft", json={"reason": "falta"}).json()
    assert d["items"] and d["generated"] and "(gerado)" in d["day_name"]
    r = client.post("/api/suggest/next", json={"items": [{"exercise_id": "Barbell_Bench_Press", "sets": 4}], "n": 2}).json()
    assert 1 <= len(r["suggestions"]) <= 2
    for s in r["suggestions"]:
        assert s["why"] and s["item"]["exercise_id"] == s["exercise_id"] and s["item"]["sets"]


# ---------------------------------------------------------------- academia premium / de elite
def _mains_and_all(prof):
    pl = G.generate(prof)
    items = [i for d in pl["days"] for i in d["items"] if (i.get("phase") or "principal") == "principal" and i.get("role")]
    return pl, items


def test_premium_gym_prefers_modern_machines_and_explains():
    base = {**PROF, "available_days": [0, 1, 3, 4]}
    pl, items = _mains_and_all({**base, "gym_type": "premium"})
    _, normal = _mains_and_all({**base, "gym_type": "completa"})
    rare = {e for v in G.PREMIUM_EXTRA.values() for e in v}
    ids = {i["exercise_id"] for i in items}
    assert ids & rare and not ({i["exercise_id"] for i in normal} & rare)   # pêndulo, remada DY… só na premium
    machine = lambda xs: sum(1 for i in xs if catalog.get(i["exercise_id"])["equipment"] in ("machine", "cable"))  # noqa: E731
    assert machine(items) > machine(normal)
    assert any("Academia premium" in r for r in pl["rationale"])
    # terra romeno e flexora sentada continuam (alongamento do posterior)
    assert "Romanian_Deadlift" in ids and "Seated_Leg_Curl" in ids


def test_premium_strength_goal_keeps_barbell_mains():
    _, items = _mains_and_all({**PROF, "goal": "forca", "gym_type": "premium", "available_days": [0, 1, 3, 4]})
    mains = [catalog.get(i["exercise_id"])["equipment"] for i in items if i["role"] == "main"]
    assert mains.count("barbell") >= 2


def test_basic_gym_skips_uncommon_machines():
    _, items = _mains_and_all({**PROF, "gym_type": "basica", "available_days": [0, 1, 3, 4]})
    assert not ({i["exercise_id"] for i in items} & G.MEDIUM_MACHINES)


def test_premium_respects_limitations_and_alternatives_use_premium():
    prof = {**PROF, "gym_type": "premium", "limitations": [{"key": "ombro", "severity": "evitar"}]}
    _, items = _mains_and_all(prof)
    assert all(check_exercise(catalog.get(i["exercise_id"]), prof["limitations"])["status"] != "evitar" for i in items)
    d = G.generate(prof)["days"][0]
    o = A.propose(d["items"], "variar", A.Ctx(prof), {}, d["name"])
    assert all(check_exercise(catalog.get(i["exercise_id"]), prof["limitations"])["status"] != "evitar" for i in o["items"] if A.is_strength(i))
