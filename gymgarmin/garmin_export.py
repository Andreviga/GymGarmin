"""Converte um treino da planilha em formatos que o Garmin entende.

1) Arquivo FIT de treino (workout file) — gerado com o SDK oficial da Garmin
   (`garmin-fit-sdk`). Copie para o relógio via USB em  GARMIN/NewFiles/
   e ele aparece em Treinos. Funciona offline, sem conta.

2) JSON do Garmin Connect (workout-service) — o mesmo formato que o editor web
   de treinos do Garmin Connect usa. É enviado pela API não-oficial
   (biblioteca `garminconnect`) e sincroniza com o relógio pelo app.

Estrutura de um treino de musculação (igual ao editor do Garmin Connect):
   [exercício (reps ou tempo) → descanso] × N séries   (bloco de repetição)
Itens de cardio dentro de um treino de musculação viram passos por tempo
(esteira/bike/elíptico/remo/escada do catálogo Garmin), com alvo de zona de FC.
Um treino só de cardio vira um treino de corrida/ciclismo/cardio com zonas.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from . import catalog

# ---------------------------------------------------------------------------
# Normalização dos itens da planilha
# ---------------------------------------------------------------------------


def parse_reps(reps) -> tuple[int, str | None]:
    """'8-12' -> (12, '8-12 reps'); '10' -> (10, None); 'falha' -> (0, 'até a falha')."""
    if reps is None or reps == "":
        return 10, None
    if isinstance(reps, (int, float)):
        return int(reps), None
    s = str(reps).strip().lower()
    nums = [int(n) for n in re.findall(r"\d+", s)]
    if "falha" in s or "fail" in s:
        return (nums[0] if nums else 0), "até a falha"
    if len(nums) >= 2:
        return max(nums), f"{min(nums)}-{max(nums)} reps"
    if nums:
        return nums[0], None
    return 10, s


def normalize_day(day: dict) -> list[dict]:
    """Enriquece os itens do dia com dados do catálogo; ignora exercícios desconhecidos."""
    out = []
    for it in day.get("items", []):
        ex = catalog.get(it.get("exercise_id"))
        if not ex:
            continue
        out.append({**it, "_ex": ex})
    return out


def is_cardio_only(items: list[dict]) -> bool:
    return bool(items) and all(i["_ex"]["kind"] == "cardio" for i in items)


def _garmin_override(ex: dict, overrides: dict | None) -> dict:
    if overrides and ex["id"] in overrides:
        return overrides[ex["id"]]
    return ex["garmin"]


def _step_label(ex: dict, it: dict, limit: int = 50) -> str:
    name = it.get("label") or ex["name"]
    if len(name) > limit:
        name = re.split(r" / | \(|, ", name)[0]
    return name[:limit].strip()


def _notes(it: dict, extra: str | None = None) -> str | None:
    parts = [p for p in (extra, it.get("notes")) if p]
    if it.get("rpe_target"):
        parts.append(f"RPE {it['rpe_target']}")
    if it.get("params"):
        parts.append(", ".join(f"{k}: {v}" for k, v in it["params"].items() if v not in (None, "")))
    s = " · ".join(p for p in parts if p)
    return s[:120] or None


def estimate_duration_s(items: list[dict]) -> int:
    total = 0
    for it in items:
        ex = it["_ex"]
        if ex["kind"] == "cardio":
            iv = it.get("intervals")
            if iv and iv.get("repeats"):
                total += int(iv["repeats"]) * (int(iv.get("work_s") or 0) + int(iv.get("rest_s") or 0))
                total += int(float(it.get("warmup_min") or 0) * 60 + float(it.get("cooldown_min") or 0) * 60)
            else:
                total += int(float(it.get("duration_min") or 0) * 60)
        else:
            sets = int(it.get("sets") or 3)
            reps, _ = parse_reps(it.get("reps"))
            work = int(it.get("duration_s") or 0) or max(20, reps * 3)
            total += sets * (work + int(it.get("rest_s") or 90))
    return total


# ---------------------------------------------------------------------------
# Garmin Connect JSON (workout-service)
# ---------------------------------------------------------------------------

STEP_TYPES = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
    "rest": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
    "repeat": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
}
COND_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
COND_REPS = {"conditionTypeId": 10, "conditionTypeKey": "reps", "displayOrder": 10, "displayable": True}
COND_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
COND_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
HR_ZONE_TARGET = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}
SPORTS = {
    "running": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "cycling": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
    "strength_training": {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5},
    "cardio_training": {"sportTypeId": 6, "sportTypeKey": "cardio_training", "displayOrder": 6},
}
WEIGHT_UNIT_KG = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


class _Order:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _exec_step(order: _Order, step_type: str, cond: dict, value: float | None, **extra) -> dict:
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order.next(),
        "stepType": STEP_TYPES[step_type],
        "endCondition": cond,
        "targetType": NO_TARGET,
    }
    if value is not None:
        step["endConditionValue"] = float(value)
    for k, v in extra.items():
        if v is not None:
            step[k] = v
    return step


def _zone_target(zone) -> dict:
    if not zone:
        return {}
    return {"targetType": HR_ZONE_TARGET, "zoneNumber": int(zone)}


def _cardio_steps_connect(order: _Order, it: dict, ex: dict, in_strength: bool, position: str,
                          overrides: dict | None) -> list[dict]:
    g = _garmin_override(ex, overrides)
    ex_fields = {"category": g["category"], "exerciseName": g["exercise"]} if in_strength else {}
    desc = _notes(it, ex["name"])
    steps: list[dict] = []
    iv = it.get("intervals") or {}
    if iv.get("repeats"):
        if it.get("warmup_min"):
            steps.append(_exec_step(order, "warmup", COND_TIME, float(it["warmup_min"]) * 60,
                                    description=desc, **ex_fields, **_zone_target(1 if not it.get("zone") else min(2, int(it["zone"])))))
        group_order = order.next()
        inner = [
            _exec_step(order, "interval", COND_TIME, int(iv.get("work_s") or 60), description=desc,
                       **ex_fields, **_zone_target(iv.get("work_zone") or 4)),
            _exec_step(order, "recovery", COND_TIME, int(iv.get("rest_s") or 60),
                       **ex_fields, **_zone_target(iv.get("rest_zone") or 1)),
        ]
        steps.append({
            "type": "RepeatGroupDTO", "stepOrder": group_order, "stepType": STEP_TYPES["repeat"],
            "numberOfIterations": int(iv["repeats"]), "workoutSteps": inner,
            "endCondition": COND_ITER, "endConditionValue": float(iv["repeats"]), "smartRepeat": False,
        })
        if it.get("cooldown_min"):
            steps.append(_exec_step(order, "cooldown", COND_TIME, float(it["cooldown_min"]) * 60,
                                    **ex_fields, **_zone_target(1)))
        return steps
    step_type = {"first": "warmup", "last": "cooldown"}.get(position, "interval")
    if not in_strength:
        step_type = "interval"
    steps.append(_exec_step(order, step_type, COND_TIME, float(it.get("duration_min") or 10) * 60,
                            description=desc, **ex_fields, **_zone_target(it.get("zone"))))
    return steps


def _strength_block_connect(order: _Order, it: dict, ex: dict, overrides: dict | None) -> dict:
    g = _garmin_override(ex, overrides)
    sets = max(1, int(it.get("sets") or 3))
    reps, reps_note = parse_reps(it.get("reps"))
    group_order = order.next()
    weight = {}
    if it.get("load_kg"):
        # o Garmin Connect guarda o peso em gramas com a unidade "kilogram" (fator 1000)
        weight = {"weightValue": float(it["load_kg"]) * 1000.0, "weightUnit": WEIGHT_UNIT_KG}
    desc = _notes(it, reps_note)
    timed = bool(it.get("duration_s")) or (ex.get("timed") and not it.get("reps"))
    if timed:
        work = _exec_step(order, "interval", COND_TIME, int(it.get("duration_s") or 30),
                          category=g["category"], exerciseName=g["exercise"], description=desc, **weight)
    elif reps > 0:
        work = _exec_step(order, "interval", COND_REPS, reps,
                          category=g["category"], exerciseName=g["exercise"], description=desc, **weight)
    else:  # até a falha: termina ao apertar a volta
        work = _exec_step(order, "interval", COND_LAP, None,
                          category=g["category"], exerciseName=g["exercise"], description=desc, **weight)
    rest_s = int(it.get("rest_s") or 90)
    inner = [work]
    if rest_s > 0:
        inner.append(_exec_step(order, "rest", COND_TIME, rest_s))
    return {
        "type": "RepeatGroupDTO", "stepOrder": group_order, "stepType": STEP_TYPES["repeat"],
        "numberOfIterations": sets, "workoutSteps": inner, "endCondition": COND_ITER,
        "endConditionValue": float(sets), "smartRepeat": False, "skipLastRestStep": False,
    }


def to_connect_json(day: dict, plan_name: str = "", overrides: dict | None = None) -> dict:
    items = normalize_day(day)
    if not items:
        raise ValueError("Treino sem exercícios reconhecidos")
    name = f"{plan_name} – {day.get('name', 'Treino')}".strip(" –") if plan_name else day.get("name", "Treino")
    order = _Order()
    steps: list[dict] = []
    if is_cardio_only(items):
        sport_key = items[0]["_ex"].get("connect_sport", "cardio_training")
        if any(i["_ex"].get("connect_sport") != sport_key for i in items):
            sport_key = "cardio_training"
        for idx, it in enumerate(items):
            steps += _cardio_steps_connect(order, it, it["_ex"], False, "", overrides)
    else:
        sport_key = "strength_training"
        for idx, it in enumerate(items):
            ex = it["_ex"]
            if ex["kind"] == "cardio":
                pos = "first" if idx == 0 else ("last" if idx == len(items) - 1 else "")
                steps += _cardio_steps_connect(order, it, ex, True, pos, overrides)
            else:
                steps.append(_strength_block_connect(order, it, ex, overrides))
    sport = SPORTS[sport_key]
    return {
        "workoutName": name[:80],
        "description": (day.get("notes") or "Gerado pelo GymGarmin")[:500],
        "sportType": sport,
        "estimatedDurationInSecs": estimate_duration_s(items),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": steps}],
    }


# ---------------------------------------------------------------------------
# Arquivo FIT (SDK oficial)
# ---------------------------------------------------------------------------


def _fit_profile():
    from garmin_fit_sdk import Profile

    return Profile


def _fit_exercise_ids(category: str | None, exercise: str | None) -> tuple[int | None, int | None]:
    """Converte ('BENCH_PRESS','BARBELL_BENCH_PRESS') nos códigos numéricos do perfil FIT."""
    if not category:
        return None, None
    types = _fit_profile()["types"]
    cat_key = category.lower()
    cat_num = next((k for k, v in types["exercise_category"].items() if v == cat_key), None)
    if cat_num is None:
        return None, None
    name_num = None
    names = types.get(f"{cat_key}_exercise_name", {})
    if exercise:
        ex_key = exercise.lower()
        name_num = next((k for k, v in names.items() if v == ex_key), None)
    return cat_num, name_num


def _fit_ids_for(ex: dict, overrides: dict | None) -> tuple[int | None, int | None]:
    if ex.get("machine") and ex.get("fit_step"):
        return _fit_exercise_ids(ex["fit_step"].get("category"), ex["fit_step"].get("exercise"))
    g = _garmin_override(ex, overrides)
    return _fit_exercise_ids(g.get("category"), g.get("exercise"))


def to_fit(day: dict, plan_name: str = "", overrides: dict | None = None,
           now: datetime | None = None) -> bytes:
    """Gera os bytes de um arquivo FIT do tipo 'workout'."""
    from garmin_fit_sdk import Encoder, Profile

    items = normalize_day(day)
    if not items:
        raise ValueError("Treino sem exercícios reconhecidos")
    MN = Profile["mesg_num"]
    name = (f"{plan_name} {day.get('name', '')}".strip() if plan_name else day.get("name", "Treino"))[:40]
    now = now or datetime.now(timezone.utc)

    steps: list[dict] = []
    titles: dict[tuple[int, int | None], str] = {}

    def add(step: dict) -> int:
        step["mesg_num"] = MN["WORKOUT_STEP"]
        step["message_index"] = len(steps)
        steps.append(step)
        return step["message_index"]

    def hr(zone) -> dict:
        if not zone:
            return {"target_type": "open"}
        return {"target_type": "heart_rate", "target_value": int(zone)}

    cardio_only = is_cardio_only(items)
    if cardio_only:
        fs = items[0]["_ex"].get("fit_sport", {"sport": "fitness_equipment", "sub_sport": "cardio_training"})
        if any(i["_ex"].get("fit_sport") != fs for i in items):
            fs = {"sport": "fitness_equipment", "sub_sport": "cardio_training"}
        sport, sub_sport = fs["sport"], fs["sub_sport"]
    else:
        sport, sub_sport = "training", "strength_training"

    for idx, it in enumerate(items):
        ex = it["_ex"]
        cat, exn = (None, None) if cardio_only else _fit_ids_for(ex, overrides)
        ex_fields = {}
        if cat is not None:
            ex_fields["exercise_category"] = cat
            if exn is not None:
                ex_fields["exercise_name"] = exn
            titles.setdefault((cat, exn), _step_label(ex, it, 30))
        label = _step_label(ex, it, 30)

        if ex["kind"] == "cardio":
            iv = it.get("intervals") or {}
            notes = _notes(it)
            if iv.get("repeats"):
                if it.get("warmup_min"):
                    add({"wkt_step_name": label, "duration_type": "time",
                         "duration_value": int(float(it["warmup_min"]) * 60_000), "intensity": "warmup",
                         **hr(1), **ex_fields})
                first = add({"wkt_step_name": label, "duration_type": "time",
                             "duration_value": int(iv.get("work_s") or 60) * 1000, "intensity": "active",
                             **hr(iv.get("work_zone") or 4), **ex_fields, **({"notes": notes} if notes else {})})
                add({"wkt_step_name": "Recuperação", "duration_type": "time",
                     "duration_value": int(iv.get("rest_s") or 60) * 1000, "intensity": "recovery",
                     **hr(iv.get("rest_zone") or 1), **ex_fields})
                add({"duration_type": "repeat_until_steps_cmplt", "duration_value": first,
                     "target_type": "open", "target_value": int(iv["repeats"])})
                if it.get("cooldown_min"):
                    add({"wkt_step_name": label, "duration_type": "time",
                         "duration_value": int(float(it["cooldown_min"]) * 60_000), "intensity": "cooldown",
                         **hr(1), **ex_fields})
            else:
                intensity = "active"
                if not cardio_only:
                    intensity = "warmup" if idx == 0 else ("cooldown" if idx == len(items) - 1 else "active")
                add({"wkt_step_name": label, "duration_type": "time",
                     "duration_value": int(float(it.get("duration_min") or 10) * 60_000), "intensity": intensity,
                     **hr(it.get("zone")), **ex_fields, **({"notes": notes} if notes else {})})
            continue

        sets = max(1, int(it.get("sets") or 3))
        reps, reps_note = parse_reps(it.get("reps"))
        notes = _notes(it, reps_note)
        timed = bool(it.get("duration_s")) or (ex.get("timed") and not it.get("reps"))
        step = {"wkt_step_name": label, "intensity": "active", "target_type": "open", **ex_fields}
        if timed:
            step.update(duration_type="time", duration_value=int(it.get("duration_s") or 30) * 1000)
        elif reps > 0:
            step.update(duration_type="reps", duration_value=reps)
        else:
            step.update(duration_type="open")
        if it.get("load_kg"):
            step.update(exercise_weight=float(it["load_kg"]), weight_display_unit="kilogram")
        if notes:
            step["notes"] = notes
        first = add(step)
        rest_s = int(it.get("rest_s") or 90)
        if rest_s > 0:
            add({"wkt_step_name": "Descanso", "duration_type": "time", "duration_value": rest_s * 1000,
                 "intensity": "rest", "target_type": "open"})
        if sets > 1:
            add({"duration_type": "repeat_until_steps_cmplt", "duration_value": first,
                 "target_type": "open", "target_value": sets})

    enc = Encoder()
    enc.write_mesg({
        "mesg_num": MN["FILE_ID"], "type": "workout", "manufacturer": "development", "product": 0,
        "serial_number": 0x47594D, "time_created": now,
    })
    enc.write_mesg({
        "mesg_num": MN["WORKOUT"], "wkt_name": name, "sport": sport, "sub_sport": sub_sport,
        "num_valid_steps": len(steps),
    })
    for s in steps:
        enc.write_mesg(s)
    for i, ((cat, exn), title) in enumerate(titles.items()):
        t = {"mesg_num": MN["EXERCISE_TITLE"], "message_index": i, "exercise_category": cat, "wkt_step_name": title}
        if exn is not None:
            t["exercise_name"] = exn
        enc.write_mesg(t)
    return enc.close()


def fit_filename(plan_name: str, day_name: str) -> str:
    base = f"{plan_name}_{day_name}" if plan_name else day_name
    base = catalog.strip_accents(base)
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_") or "treino"
    return f"{base[:40]}.fit"
