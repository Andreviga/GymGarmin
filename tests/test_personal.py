"""Perfil detalhado: saúde, fase de vida, corpo, preferências, corrida e rotina mudando o treino."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from gymgarmin import alternative as A, catalog, clock, endurance as en, generator as G, personal, sports
from gymgarmin.limitations import check_exercise

BASE = {"training_months": 24, "goal": "hipertrofia", "available_days": [0, 1, 3, 4], "session_minutes": 60, "age": 40,
        "sex": "M", "weight_kg": 80, "height_cm": 178}
MONDAY = date(2026, 11, 16)


def _ids(pl):
    return {i["exercise_id"] for d in pl["days"] for i in d["items"] if (i.get("phase") or "principal") == "principal"}


def _all_ids(pl):
    return {i["exercise_id"] for d in pl["days"] for i in d["items"]}


def _run(prof, week=3, tier="intermediario", **c):
    p = {**prof, "endurance": {"sports": {"corrida": {"enabled": True, "env": "rua", "sessions": 4, "current_weekly": 25, **c}},
                               "anchors": {"run_vdot": 45}}}
    return en.week_plan(p, MONDAY + timedelta(weeks=week), MONDAY, tier)["sports"]


def test_no_answers_changes_nothing():
    assert _ids(G.generate(BASE)) == _ids(G.generate({**BASE, "personal": {}}))
    d = personal.derive(BASE)
    assert not d["notes"] and not d["avoid"] and not d["rpe_mode"]


def test_bone_condition_removes_loaded_spinal_flexion():
    pl = G.generate({**BASE, "personal": {"conditions": ["osteoporose"]}})
    flex = {"Crunches", "Cable_Crunch", "Ab_Crunch_Machine", "Reverse_Crunch", "Hanging_Leg_Raise", "AbCoaster_Machine"}
    assert not (_ids(pl) & flex)
    assert any(catalog.get(i)["primaryMuscles"] == ["abdominals"] for i in _ids(pl))   # o core continua (prancha, dead bug…)
    assert any("flexão" in r for r in pl["rationale"])


def test_high_bmi_no_jumps_and_run_walk():
    prof = {**BASE, "weight_kg": 105, "height_cm": 175}      # IMC ~34
    pl = G.generate({**prof, "age": 50, "sex": "F"})
    assert not (_all_ids(pl) & personal.JUMP_IDS)
    s = _run(prof, week=0, tier="avancado", current_weekly=12)["corrida"]["sessions"]
    assert any("Corre-caminha" in x["endurance"]["title"] for x in s)


def test_mobility_screens_change_exercise_choice():
    a = G.generate({**BASE, "personal": {"ankle": "nao", "overhead": "doi"}})
    assert "Barbell_Squat" not in _ids(a)
    assert not (_ids(a) & personal.OVERHEAD_BARBELL)


def test_disliked_exercises_never_appear():
    base = _ids(G.generate(BASE))
    hate = sorted(base)[:3]
    pl = G.generate({**BASE, "personal": {"dislikes": hate}})
    assert not (set(hate) & _ids(pl))
    d = G.generate(BASE)["days"][0]
    o = A.propose(d["items"], "variar", A.Ctx({**BASE, "personal": {"dislikes": hate}}), {}, d["name"])
    assert not (set(hate) & {i["exercise_id"] for i in o["items"]} - {i["exercise_id"] for i in d["items"]})


def test_beta_blocker_uses_perceived_effort():
    prof = {**BASE, "personal": {"meds": ["fc"]}}
    a = sports.anchors_of(prof)
    assert a.get("rpe_mode") and "hrmax" not in a
    t = sports.target_for("ciclismo", 2, a)
    assert t["type"] == "rpe" and "esforço" in t["label"]
    assert sports.target_for("corrida", 2, {**a, "run_vdot": 45})["type"] == "pace"   # ritmo continua valendo


def test_running_injuries_limit_speed_and_spikes():
    prof = {**BASE, "personal": {"run_injuries": ["aquiles"]}}
    for w in range(0, 8):
        types = [s["endurance"]["type"] for s in _run(prof, week=w)["corrida"]["sessions"]]
        assert not ({"subidas", "intervalado", "noruegues", "teste"} & set(types)), types
    c = en.config({**prof, "endurance": {"sports": {"corrida": {"enabled": True}}}})["corrida"]
    assert en.progression(c)[0] <= 1.05
    sess = _run(prof, week=1)["corrida"]["sessions"]
    assert "Aquiles" in " ".join(s["endurance"].get("note") or "" for s in sess)


def test_strong_running_pain_switches_to_bike():
    prof = {**BASE, "personal": {"run_injuries": ["canelite"], "run_pain": "forte"}}
    sess = _run(prof)["corrida"]["sessions"]
    assert sess and all(s["sport"] == "ciclismo" for s in sess)


def test_postpartum_early_is_gentle_and_no_running():
    prof = {**BASE, "sex": "F", "personal": {"phase": "pos_parto", "postpartum_weeks": 4, "cesarea": "sim"}}
    pl = G.generate(prof)
    assert all(float(i.get("rpe_target") or 0) <= 5 for d in pl["days"] for i in d["items"] if i.get("rpe_target"))
    assert all(s["sport"] == "ciclismo" for s in _run(prof)["corrida"]["sessions"])


def test_chest_symptoms_cap_intensity_until_cleared():
    prof = {**BASE, "personal": {"chest": "sim"}}
    pl = G.generate(prof)
    assert all(float(i.get("rpe_target") or 0) <= 6 for d in pl["days"] for i in d["items"] if i.get("rpe_target"))
    for w in range(0, 6):
        assert not any(s["endurance"]["hard"] for s in _run(prof, week=w)["corrida"]["sessions"])
    ok = {**BASE, "personal": {"chest": "liberado"}}
    assert any(s["endurance"]["hard"] for w in range(2, 6) for s in _run(ok, week=w)["corrida"]["sessions"])


def test_stress_and_physical_job_trim_accessory_volume():
    base = G.generate(BASE)
    tired = G.generate({**BASE, "personal": {"stress": "muito", "job": "fisico"}})
    sets = lambda p: sum(int(i["sets"]) for d in p["days"] for i in d["items"]  # noqa: E731
                         if i.get("phase") == "principal" and i.get("role") in ("sec", "acc") and i.get("sets"))
    assert sets(tired) < sets(base)


def test_long_runs_get_fueling_advice():
    s = en.build_session("corrida", "longo", 20, {"env": "rua"}, {"run_vdot": 40}, "intermediario")
    assert "carboidrato" in (s["endurance"]["note"] or "")


def test_sick_option():
    d = G.generate(BASE)["days"][0]
    with pytest.raises(ValueError):
        A.propose(d["items"], "doente", A.Ctx(BASE), {"sick": "febre"}, d["name"])
    o = A.propose(d["items"], "doente", A.Ctx(BASE), {"sick": "pescoco"}, d["name"])
    assert o["sets"]["after"] <= 0.65 * o["sets"]["before"]
    assert all(float(i["rpe_target"]) <= 6 for i in o["items"] if A.is_strength(i))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GYMGARMIN_HOME", str(tmp_path))
    monkeypatch.setenv("GYMGARMIN_DB", str(tmp_path / "p.sqlite3"))
    clock.freeze(datetime(2026, 11, 16, 9, 0))
    from gymgarmin.app import app

    with TestClient(app) as c:
        yield c
    clock.freeze(None)


def test_api_alerts_meta_and_comeback(client):
    client.put("/api/profile", json={**BASE, "personal": {"conditions": ["diabetes_insulina"], "meds": ["fc"]}})
    client.post("/api/generator/apply", json={})
    meta = client.get("/api/meta").json()
    assert meta["personal_questions"] and meta["hr_max"] is None
    sch = client.get("/api/schedule").json()
    e = next(x for x in sch["entries"] if x["kind"] == "forca")
    pre = client.get(f"/api/schedule/{e['id']}/prefill").json()
    txt = " ".join(pre["alerts"])
    assert "glicose" in txt and "dor ou aperto no peito" in txt
    assert sch["comeback"] is None
    for k in range(3):
        client.post("/api/sessions", json={"date": f"2026-11-0{k + 2}T08:00", "client_id": f"c{k}",
                                           "items": [{"exercise_id": "Barbell_Squat", "sets": [{"reps": 8, "load_kg": 60}]}]})
    assert client.get("/api/schedule").json()["comeback"]["days"] >= 7
    assert client.post("/api/alternative/preview", json={"entry_id": e["id"], "reason": "doente", "opts": {"sick": "febre"}}).status_code == 400
