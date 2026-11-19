"""Descanso pela evidência, séries de aquecimento do próprio exercício e passos por tempo manuais no relógio."""
from gymgarmin import garmin_export, training_rules

ELITE = {"training_months": 72, "goal": "hipertrofia"}
PUXAR = {"name": "Qua – Puxar (variação)", "items": [
    {"exercise_id": "cardio_bike_vertical", "duration_min": 5, "zone": 2, "phase": "preparo"},
    {"exercise_id": "External_Rotation_with_Band", "sets": 1, "duration_s": 40, "rest_s": 10, "phase": "preparo"},
    {"exercise_id": "Cat_Stretch", "sets": 1, "duration_s": 40, "rest_s": 10, "phase": "preparo"},
    {"exercise_id": "Wide-Grip_Lat_Pulldown", "sets": 5, "reps": "4-6", "rest_s": 210, "phase": "principal", "role": "main",
     "notes": "aproximação: barra×8, ~50%×5, ~70%×3, ~85%×1–2 antes das séries válidas", "load_kg": 90},
    {"exercise_id": "One-Arm_Dumbbell_Row", "sets": 5, "reps": "4-6", "rest_s": 210, "phase": "principal", "role": "main"},
    {"exercise_id": "T-Bar_Row_with_Handle", "sets": 2, "reps": "6-10", "rest_s": 150, "phase": "principal", "role": "sec"},
    {"exercise_id": "Incline_Dumbbell_Curl", "sets": 2, "reps": "10-15", "rest_s": 75, "phase": "principal", "role": "acc"},
    {"exercise_id": "Standing_Biceps_Stretch", "sets": 1, "duration_s": 30, "rest_s": 5, "phase": "pos"},
]}


def _by_id(items):
    return {i["exercise_id"]: i for i in items}


def test_rest_follows_evidence_and_preference():
    assert training_rules.rest_for("main", "elite", 1.0, None) == 150     # antes 210 s
    assert training_rules.rest_for("sec", "elite", 1.0, None) == 120      # antes 150 s
    assert training_rules.rest_for("acc", "elite", 1.0, None) == 60       # antes 75 s
    assert training_rules.rest_for("main", "elite", 1.0, "curto") < 150
    assert training_rules.rest_for("main", "avancado", 1.25, None) >= 180  # força pesada continua com 3 min
    it = _by_id(training_rules.apply(PUXAR["items"], ELITE))
    assert it["Wide-Grip_Lat_Pulldown"]["rest_s"] == 150 and it["Incline_Dumbbell_Curl"]["rest_s"] == 60
    custom = training_rules.apply([{**PUXAR["items"][3], "rest_custom": True}], ELITE)[0]
    assert custom["rest_s"] == 210  # descanso editado à mão é respeitado


def test_warmup_sets_ramp_first_compound_only_where_needed():
    it = _by_id(training_rules.apply(PUXAR["items"], ELITE))
    ramp = it["Wide-Grip_Lat_Pulldown"]["warmup_sets"]
    assert [w["pct"] for w in ramp] == [50, 70, 85]          # 1º multiarticular pesado: rampa de 3
    assert "aproximação" not in (it["Wide-Grip_Lat_Pulldown"].get("notes") or "")
    assert len(it["One-Arm_Dumbbell_Row"]["warmup_sets"]) == 1   # mesmo músculo, mas pesado: 1 série de ajuste
    assert it["T-Bar_Row_with_Handle"]["warmup_sets"] == []      # músculo já aquecido
    assert it["Incline_Dumbbell_Curl"]["warmup_sets"] == []      # isolador
    off = _by_id(training_rules.apply(PUXAR["items"], {**ELITE, "warmup_sets": "off"}))
    assert off["Wide-Grip_Lat_Pulldown"]["warmup_sets"] == []
    assert training_rules.warm_load(90, 50, "cable") == 45 and training_rules.warm_load(31, 70, "dumbbell") == 22


def _flat(steps):
    for s in steps:
        yield s
        yield from _flat(s.get("workoutSteps") or [])


def test_watch_timed_steps_wait_for_lap_and_warmups_are_sent():
    j = garmin_export.to_connect_json(PUXAR, "Plano", prefs=ELITE)
    steps = list(_flat(j["workoutSegments"][0]["workoutSteps"]))
    rot = next(s for s in steps if s.get("exerciseName") == garmin_export._garmin_override(
        garmin_export.catalog.get("External_Rotation_with_Band"), None)["exercise"] and s["stepType"]["stepTypeKey"] == "warmup")
    assert rot["endCondition"]["conditionTypeKey"] == "lap.button" and "40 s" in rot["description"]
    warm = [s for s in steps if (s.get("description") or "").startswith("Aquecimento")]
    assert len(warm) == 4 and warm[0]["endCondition"]["conditionTypeKey"] == "reps" and warm[0]["weightValue"] == 45000.0
    rests = [s["endConditionValue"] for s in steps if s["stepType"]["stepTypeKey"] == "rest" and s.get("endConditionValue", 0) > 100]
    assert max(rests) == 150  # sem os 210 s antigos
    # quem prefere que o relógio avance sozinho continua tendo passos por tempo
    auto = list(_flat(garmin_export.to_connect_json(PUXAR, "Plano", prefs={**ELITE, "watch_timed": "auto"})["workoutSegments"][0]["workoutSteps"]))
    assert not any(s["endCondition"]["conditionTypeKey"] == "lap.button" and s["stepType"]["stepTypeKey"] == "warmup" for s in auto)
    # sem preferências (chamadas antigas) nada muda
    old = list(_flat(garmin_export.to_connect_json(PUXAR, "Plano")["workoutSegments"][0]["workoutSteps"]))
    assert not any((s.get("description") or "").startswith("Aquecimento") for s in old)


def test_fit_with_manual_steps_and_warmups_decodes():
    from garmin_fit_sdk import Decoder, Stream
    data = garmin_export.to_fit(PUXAR, "Plano", prefs=ELITE)
    msgs, errs = Decoder(Stream.from_byte_array(bytearray(data))).read()
    assert not errs
    steps = msgs["workout_step_mesgs"]
    assert any(s.get("duration_type") == "open" and s.get("intensity") == "warmup" for s in steps)
    assert sum(1 for s in steps if (s.get("notes") or "").startswith("Aquecimento")) == 4
