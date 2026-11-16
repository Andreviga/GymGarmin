import json
from datetime import datetime, timedelta

import pytest

from gymgarmin import analysis, catalog, garmin_export, limitations, plans_io, templates

NOW = datetime(2026, 11, 16, 20, 0)


def sess(days_ago, items):
    return {"date": (NOW - timedelta(days=days_ago)).isoformat(), "items": items}


def st(eid, n, reps=10, kg=40, rpe=8):
    return {"exercise_id": eid, "sets": [{"reps": reps, "load_kg": kg, "rpe": rpe}] * n}


DAY = {"name": "A - Peito", "items": [
    {"exercise_id": "cardio_esteira_caminhada", "duration_min": 10, "zone": 2},
    {"exercise_id": "Barbell_Bench_Press_-_Medium_Grip", "sets": 4, "reps": "8-12", "load_kg": 60, "rest_s": 120},
    {"exercise_id": "Plank", "sets": 3, "duration_s": 45, "rest_s": 60},
    {"exercise_id": "Triceps_Pushdown", "sets": 3, "reps": "falha", "rest_s": 60},
    {"exercise_id": "cardio_bike_vertical", "duration_min": 20, "zone": 3},
]}


# ---------------------------------------------------------------- catálogo
def test_catalog_unifies_cardio_and_names():
    ids = {e["id"] for e in catalog.all_exercises()}
    assert len(ids) > 850
    assert "cardio_remo" in ids and "Rowing_Stationary" not in ids  # mesclado
    assert all(k in ids for k in catalog.NAMES_PT)
    assert catalog.get("cardio_remo")["images"]


@pytest.mark.parametrize("q,expected", [
    ("supino reto com barra", "Barbell_Bench_Press_-_Medium_Grip"),
    ("rosca martelo", "Hammer_Curls"),
    ("cadeira extensora", "Leg_Extensions"),
    ("esteira", "cardio_esteira_corrida"),
    ("remada baixa", "Seated_Cable_Rows"),
])
def test_portuguese_search(q, expected):
    assert expected in [e["id"] for e in catalog.search(q, limit=3)]


def test_garmin_mapping_core_exercises():
    pairs = {
        "Barbell_Bench_Press_-_Medium_Grip": ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
        "Barbell_Squat": ("SQUAT", "BARBELL_BACK_SQUAT"),
        "Barbell_Deadlift": ("DEADLIFT", "BARBELL_DEADLIFT"),
        "Seated_Cable_Rows": ("ROW", "SEATED_CABLE_ROW"),
        "Pullups": ("PULL_UP", "PULL_UP"),
    }
    for eid, (cat, ex) in pairs.items():
        g = catalog.get(eid)["garmin"]
        assert (g["category"], g["exercise"]) == (cat, ex)
    gcat = json.loads((catalog.DATA / "garmin_catalog.json").read_text())
    valid = {(g["category"], g["exercise"]) for g in gcat}
    for e in catalog.all_exercises():
        assert (e["garmin"]["category"], e["garmin"]["exercise"]) in valid, e["id"]


# ------------------------------------------------------------- limitações
def test_limitations():
    knee = [{"key": "joelho", "severity": "evitar"}]
    assert limitations.check_exercise(catalog.get("Box_Jump_Multiple_Response"), knee)["status"] == "evitar"
    assert limitations.check_exercise(catalog.get("Barbell_Squat"), knee)["status"] == "cuidado"
    assert limitations.check_exercise(catalog.get("cardio_esteira_corrida"), knee)["status"] == "evitar"
    assert limitations.check_exercise(catalog.get("cardio_bike_vertical"), knee)["status"] == "ok"
    mild = [{"key": "joelho", "severity": "cuidado"}]
    assert limitations.check_exercise(catalog.get("Box_Jump_Multiple_Response"), mild)["status"] == "cuidado"
    custom = [{"key": "personalizado", "custom_avoid": "leg press"}]
    assert limitations.check_exercise(catalog.get("Leg_Press"), custom)["status"] == "evitar"
    assert limitations.max_zone([{"key": "hipertensao"}, {"key": "cardiaco"}]) == 2


def test_template_swaps_avoided_exercises():
    plan = templates.build("ppl", {"limitations": [{"key": "lombar", "severity": "evitar"}]})
    lims = [{"key": "lombar", "severity": "evitar"}]
    for d in plan["days"]:
        for it in d["items"]:
            assert limitations.check_exercise(catalog.get(it["exercise_id"]), lims)["status"] != "evitar"


# ---------------------------------------------------------------- análise
def test_weekly_report_detects_imbalance_and_neglect():
    sessions = [
        sess(1, [st("Barbell_Bench_Press_-_Medium_Grip", 4), st("Incline_Dumbbell_Press", 4), st("Triceps_Pushdown", 4)]),
        sess(3, [st("Barbell_Squat", 4), st("Leg_Extensions", 4)]),
        sess(4, [{"exercise_id": "cardio_esteira_caminhada", "duration_min": 40, "zone": 2}]),
        sess(5, [st("Barbell_Bench_Press_-_Medium_Grip", 4), st("Dumbbell_Shoulder_Press", 4)]),
    ]
    r = analysis.weekly_report(sessions, {"weight_kg": 80}, NOW)
    by = {m["muscle"]: m for m in r["muscles"]}
    assert by["chest"]["sets_7d"] == 12.0
    assert by["lats"]["status"] in ("esquecido", "abaixo")
    bal = {b["id"]: b for b in r["balance"]}
    assert bal["push_pull"]["status"] == "baixo"
    assert bal["ham_quad"]["status"] == "baixo"
    assert r["cardio"]["moderate"] == 40 and r["cardio"]["kcal_estimate"] > 0
    assert any(rec["muscle"] == "hamstrings" for rec in r["recommendations"])
    assert by["chest"]["recovery"]["ready_pct"] < 100


def test_plan_report_scales_by_frequency():
    plan = {"days_per_week": 6, "days": [{"name": "A", "items": [{"exercise_id": "Barbell_Curl", "sets": 3}]}]}
    r = analysis.plan_report(plan, {})
    by = {m["muscle"]: m for m in r["muscles"]}
    assert by["biceps"]["sets_week"] == 18.0


# ------------------------------------------------------------------ Garmin
def test_fit_file_valid_with_official_decoder():
    from garmin_fit_sdk import Decoder, Stream

    data = garmin_export.to_fit(DAY, "Plano")
    dec = Decoder(Stream.from_byte_array(bytearray(data)))
    assert dec.is_fit() and dec.check_integrity()
    msgs, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
    assert errors == []
    w = msgs["workout_mesgs"][0]
    assert w["sport"] == "training" and w["sub_sport"] == "strength_training"
    steps = msgs["workout_step_mesgs"]
    assert w["num_valid_steps"] == len(steps)
    bench = steps[1]
    assert bench["exercise_category"] == "bench_press" and bench["duration_type"] == "reps"
    assert bench["duration_value"] == 12 and bench["exercise_weight"] == 60.0
    repeat = steps[3]
    assert repeat["duration_type"] == "repeat_until_steps_cmplt" and repeat["target_value"] == 4
    assert steps[0]["intensity"] == "warmup" and steps[-1]["intensity"] == "cooldown"
    assert steps[0]["target_hr_zone"] == 2
    assert msgs["exercise_title_mesgs"]


def test_fit_cardio_only_intervals():
    from garmin_fit_sdk import Decoder, Stream

    day = {"name": "HIIT", "items": [{"exercise_id": "cardio_remo", "warmup_min": 5, "cooldown_min": 5,
                                      "intervals": {"repeats": 6, "work_s": 60, "work_zone": 4, "rest_s": 60}}]}
    msgs, errors = Decoder(Stream.from_byte_array(bytearray(garmin_export.to_fit(day)))).read()
    assert not errors
    assert msgs["workout_mesgs"][0]["sport"] == "rowing"
    assert any(s.get("target_value") == 6 and s["duration_type"] == "repeat_until_steps_cmplt"
               for s in msgs["workout_step_mesgs"])


def test_connect_json_matches_garminconnect_models():
    from garminconnect.workout import StrengthWorkout

    payload = garmin_export.to_connect_json(DAY, "Plano")
    assert payload["sportType"]["sportTypeKey"] == "strength_training"
    steps = payload["workoutSegments"][0]["workoutSteps"]
    orders = []

    def walk(ss):
        for s in ss:
            orders.append(s["stepOrder"])
            walk(s.get("workoutSteps", []))
    walk(steps)
    assert len(orders) == len(set(orders))
    bench = steps[1]
    assert bench["type"] == "RepeatGroupDTO" and bench["numberOfIterations"] == 4
    assert bench["workoutSteps"][0]["exerciseName"] == "BARBELL_BENCH_PRESS"
    assert bench["workoutSteps"][0]["weightValue"] == 60000.0
    failure = steps[3]["workoutSteps"][0]
    assert failure["endCondition"]["conditionTypeKey"] == "lap.button"
    # o modelo tipado da própria biblioteca aceita o payload
    StrengthWorkout(**payload)


def test_connect_json_cardio_only():
    day = {"name": "Z2", "items": [{"exercise_id": "cardio_esteira_caminhada", "duration_min": 45, "zone": 2}]}
    p = garmin_export.to_connect_json(day)
    assert p["sportType"]["sportTypeKey"] == "running"
    step = p["workoutSegments"][0]["workoutSteps"][0]
    assert step["zoneNumber"] == 2 and step["endConditionValue"] == 2700.0


# --------------------------------------------------------------------- CSV
def test_csv_roundtrip():
    text = ("treino;exercicio;series;reps;carga_kg;descanso_s;tempo_s;duracao_min;zona;obs\n"
            "A;Esteira caminhada;;;;;;10;2;aquecimento\n"
            "A;Supino reto com barra;4;8-12;60;120;;;;\n"
            "B;Remada baixa;3;10;50;90;;;;\n"
            "B;Exercicio que nao existe xyzzy;3;10;;;;;;\n")
    res = plans_io.import_csv(text)
    plan = res["plan"]
    assert [d["name"] for d in plan["days"]] == ["A", "B"]
    assert plan["days"][0]["items"][1]["exercise_id"] == "Barbell_Bench_Press_-_Medium_Grip"
    assert plan["days"][0]["items"][0]["duration_min"] == 10
    assert len(res["unmatched"]) == 1
    again = plans_io.import_csv(plans_io.export_csv(plan))
    assert again["plan"]["days"] == plan["days"]
