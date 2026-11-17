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

from . import catalog, sports

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


def _phase(it: dict) -> str:
    return it.get("phase") or "principal"


def is_cardio_only(items: list[dict]) -> bool:
    main = [i for i in items if _phase(i) == "principal"]
    return bool(main) and all(i["_ex"]["kind"] == "cardio" for i in main)


def _phase_step_type(it: dict, default: str) -> str:
    return {"preparo": "warmup", "pos": "cooldown"}.get(_phase(it), default)


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
    from .generator import item_time

    return int(sum(item_time(i)["total"] for i in items))


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
    "other": {"sportTypeId": 3, "sportTypeKey": "other", "displayOrder": 3},
    "swimming": {"sportTypeId": 4, "sportTypeKey": "swimming", "displayOrder": 4},
    "strength_training": {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5},
    "cardio_training": {"sportTypeId": 6, "sportTypeKey": "cardio_training", "displayOrder": 6},
    "multi_sport": {"sportTypeId": 10, "sportTypeKey": "multi_sport", "displayOrder": 10},
}
COND_DISTANCE = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
COND_FIXED_REST = {"conditionTypeId": 8, "conditionTypeKey": "fixed.rest", "displayOrder": 8, "displayable": True}
POWER_TARGET = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone", "displayOrder": 2}
PACE_TARGET = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}
UNIT_KM = {"unitId": 2, "unitKey": "kilometer", "factor": 100000.0}
UNIT_M = {"unitId": 1, "unitKey": "meter", "factor": 100.0}
END_STEP_TYPE = {"warmup": "warmup", "cooldown": "cooldown", "interval": "interval", "steady": "interval",
                 "recovery": "recovery", "rest": "rest"}
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
    if not in_strength or it.get("phase"):
        step_type = _phase_step_type(it, "interval")
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


def _phase_step_connect(order: _Order, it: dict, ex: dict, overrides: dict | None, with_exercise: bool) -> dict:
    g = _garmin_override(ex, overrides)
    secs = int(it.get("duration_s") or 30) * max(1, int(it.get("sets") or 1))
    extra = {"category": g["category"], "exerciseName": g["exercise"]} if with_exercise else {}
    return _exec_step(order, _phase_step_type(it, "warmup"), COND_TIME, secs,
                      description=_notes(it, ex["name"]), **extra)


# ---------------------------------------------------------------- resistência

def endurance_items(day: dict) -> list[dict]:
    return [i for i in day.get("items", []) if i.get("endurance")]


def _connect_target(sport: str, tgt: dict | None) -> dict:
    """Alvo no formato do workout-service. Ritmo em m/s (lento = valor 1)."""
    t = tgt or {}
    kind = t.get("type")
    if kind == "pace" and sport == "corrida":
        return {"targetType": PACE_TARGET, "targetValueOne": round(1000 / t["slow_s_km"], 4),
                "targetValueTwo": round(1000 / t["fast_s_km"], 4)}
    if kind == "swim_pace":
        return {"targetType": PACE_TARGET, "targetValueOne": round(100 / t["slow_s_100"], 4),
                "targetValueTwo": round(100 / t["fast_s_100"], 4)}
    if kind == "power":
        return {"targetType": POWER_TARGET, "targetValueOne": float(t["low_w"]), "targetValueTwo": float(t["high_w"])}
    if kind == "hr":
        return {"targetType": HR_ZONE_TARGET, "targetValueOne": float(t["low_bpm"]), "targetValueTwo": float(t["high_bpm"])}
    if kind == "hr_zone":
        return {"targetType": HR_ZONE_TARGET, "zoneNumber": int(t["zone"])}
    return {}  # remo (split) e "máximo": vai na descrição do passo


def _end_desc(st: dict) -> str | None:
    parts = [p for p in ((st.get("target") or {}).get("label"), st.get("note")) if p]
    return " · ".join(parts)[:120] or None


def _end_steps_connect(order: _Order, it: dict) -> list[dict]:
    end = it["endurance"]
    sport = end["sport"]
    swim = sport == "natacao"

    def one(st: dict) -> dict:
        if st["kind"] == "repeat":
            group_order = order.next()
            inner = [one(x) for x in st["steps"]]
            return {"type": "RepeatGroupDTO", "stepOrder": group_order, "stepType": STEP_TYPES["repeat"],
                    "numberOfIterations": int(st["n"]), "workoutSteps": inner, "endCondition": COND_ITER,
                    "endConditionValue": float(st["n"]), "smartRepeat": False}
        stype = END_STEP_TYPE.get(st["kind"], "interval")
        if st["kind"] == "rest" or (swim and st["kind"] == "recovery"):
            return _exec_step(order, "rest", COND_FIXED_REST if swim else COND_TIME, float(st["dur_s"]),
                              description=st.get("note"))
        use_dist = bool(st.get("dist_m")) and sport in ("corrida", "natacao", "remo", "caminhada") and st["kind"] != "recovery"
        step = _exec_step(order, stype, COND_DISTANCE if use_dist else COND_TIME,
                          float(st["dist_m"] if use_dist else st["dur_s"]), description=_end_desc(st))
        if use_dist:
            step["preferredEndConditionUnit"] = UNIT_M if sport in ("natacao", "remo") else UNIT_KM
        step.update(_connect_target(sport, st.get("target")))
        return step

    return [one(st) for st in end["steps"]]


def endurance_connect_json(day: dict, plan_name: str = "") -> dict:
    items = endurance_items(day)
    name = f"{plan_name} – {day.get('name', 'Treino')}".strip(" –") if plan_name else day.get("name", "Treino")
    segments = []
    for i, it in enumerate(items):
        order = _Order()
        sk = sports.connect_sport(it["endurance"]["sport"], it["endurance"].get("env"))
        segments.append({"segmentOrder": i + 1, "sportType": SPORTS.get(sk, SPORTS["other"]),
                         "workoutSteps": _end_steps_connect(order, it)})
    sport = segments[0]["sportType"] if len(segments) == 1 else SPORTS["multi_sport"]
    out = {
        "workoutName": name[:80],
        "description": (" · ".join(x for x in (items[0]["endurance"].get("note"), "Gerado pelo GymGarmin") if x))[:500],
        "sportType": sport,
        "estimatedDurationInSecs": int(sum(i["endurance"]["dur_s"] for i in items)),
        "workoutSegments": segments,
    }
    dist = sum(i["endurance"].get("dist_m") or 0 for i in items)
    if dist:
        out["estimatedDistanceInMeters"] = float(dist)
    pool = next((i["endurance"].get("pool_m") for i in items if i["endurance"].get("pool_m")), None)
    if pool:
        out["poolLength"] = float(pool)
        out["poolLengthUnit"] = UNIT_M
    return out


def to_connect_json(day: dict, plan_name: str = "", overrides: dict | None = None) -> dict:
    if endurance_items(day):
        return endurance_connect_json(day, plan_name)
    items = normalize_day(day)
    if not items:
        raise ValueError("Treino sem exercícios reconhecidos")
    name = f"{plan_name} – {day.get('name', 'Treino')}".strip(" –") if plan_name else day.get("name", "Treino")
    order = _Order()
    steps: list[dict] = []
    if is_cardio_only(items):
        cardio_items = [i for i in items if i["_ex"]["kind"] == "cardio"]
        sport_key = cardio_items[0]["_ex"].get("connect_sport", "cardio_training")
        if any(i["_ex"].get("connect_sport") != sport_key for i in cardio_items):
            sport_key = "cardio_training"
        for idx, it in enumerate(items):
            if it["_ex"]["kind"] == "cardio":
                steps += _cardio_steps_connect(order, it, it["_ex"], False, "", overrides)
            else:
                steps.append(_phase_step_connect(order, it, it["_ex"], overrides, False))
    else:
        sport_key = "strength_training"
        for idx, it in enumerate(items):
            ex = it["_ex"]
            if ex["kind"] == "cardio":
                pos = "first" if idx == 0 else ("last" if idx == len(items) - 1 else "")
                steps += _cardio_steps_connect(order, it, ex, True, pos, overrides)
            elif _phase(it) in ("preparo", "pos"):
                steps.append(_phase_step_connect(order, it, ex, overrides, True))
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


def endurance_fit(day: dict, plan_name: str = "", now: datetime | None = None) -> bytes:
    """FIT de resistência: passos por distância/tempo com alvo de ritmo, potência ou FC.

    Valores brutos do FIT: distância em cm, tempo em ms, velocidade em mm/s,
    potência personalizada = W + 1000, FC personalizada = bpm + 100.
    O FIT de treino é de uma modalidade só: brick/evento usam a primeira (o Connect leva tudo).
    """
    from garmin_fit_sdk import Encoder, Profile

    MN = Profile["mesg_num"]
    it = endurance_items(day)[0]
    end = it["endurance"]
    sport = end["sport"]
    fs, fss = sports.fit_sport(sport, end.get("env"))
    name = (f"{plan_name} {day.get('name', '')}".strip() if plan_name else day.get("name", "Treino"))[:40]
    now = now or datetime.now(timezone.utc)
    steps: list[dict] = []

    def add(step: dict) -> int:
        step["mesg_num"] = MN["WORKOUT_STEP"]
        step["message_index"] = len(steps)
        steps.append(step)
        return step["message_index"]

    def target(st: dict) -> dict:
        t = st.get("target") or {}
        k = t.get("type")
        if sport == "natacao" and st["kind"] in ("interval", "steady", "warmup", "cooldown"):
            return {"target_type": "swim_stroke", "target_value": 0}  # livre; ritmo vai nas notas
        if k == "pace":
            return {"target_type": "speed", "target_value": 0, "custom_target_value_low": int(1_000_000 / t["slow_s_km"]),
                    "custom_target_value_high": int(1_000_000 / t["fast_s_km"])}
        if k == "split":
            return {"target_type": "speed", "target_value": 0, "custom_target_value_low": int(500_000 / t["slow_s_500"]),
                    "custom_target_value_high": int(500_000 / t["fast_s_500"])}
        if k == "power":
            return {"target_type": "power", "target_value": 0, "custom_target_value_low": int(t["low_w"]) + 1000,
                    "custom_target_value_high": int(t["high_w"]) + 1000}
        if k == "hr":
            return {"target_type": "heart_rate", "target_value": 0, "custom_target_value_low": int(t["low_bpm"]) + 100,
                    "custom_target_value_high": int(t["high_bpm"]) + 100}
        if k == "hr_zone":
            return {"target_type": "heart_rate", "target_value": int(t["zone"])}
        return {"target_type": "open"}

    intensity = {"warmup": "warmup", "cooldown": "cooldown", "interval": "active", "steady": "active",
                 "recovery": "recovery", "rest": "rest"}
    label = {"warmup": "Aquecimento", "cooldown": "Desaquecimento", "interval": "Tiro", "steady": "Contínuo",
             "recovery": "Recuperação", "rest": "Descanso"}

    def emit(st: dict) -> None:
        if st["kind"] == "repeat":
            first = len(steps)
            for x in st["steps"]:
                emit(x)
            add({"duration_type": "repeat_until_steps_cmplt", "duration_value": first, "target_type": "open",
                 "target_value": int(st["n"])})
            return
        use_dist = bool(st.get("dist_m")) and sport in ("corrida", "natacao", "remo", "caminhada") and st["kind"] not in ("recovery", "rest")
        step = {"wkt_step_name": label.get(st["kind"], "Passo"), "intensity": intensity.get(st["kind"], "active")}
        if use_dist:
            step.update(duration_type="distance", duration_value=int(st["dist_m"] * 100))
        else:
            step.update(duration_type="time", duration_value=int(st["dur_s"] * 1000))
        step.update(target(st) if st["kind"] != "rest" else {"target_type": "open"})
        desc = _end_desc(st)
        if desc:
            step["notes"] = desc
        add(step)

    for st in end["steps"]:
        emit(st)
    enc = Encoder()
    enc.write_mesg({"mesg_num": MN["FILE_ID"], "type": "workout", "manufacturer": "development", "product": 0,
                    "serial_number": 0x47594D, "time_created": now})
    wk = {"mesg_num": MN["WORKOUT"], "wkt_name": name, "sport": fs, "sub_sport": fss, "num_valid_steps": len(steps)}
    if end.get("pool_m"):
        wk.update(pool_length=float(end["pool_m"]), pool_length_unit="metric")  # o SDK aplica a escala deste campo
    enc.write_mesg(wk)
    for st in steps:
        enc.write_mesg(st)
    return enc.close()


def to_fit(day: dict, plan_name: str = "", overrides: dict | None = None,
           now: datetime | None = None) -> bytes:
    """Gera os bytes de um arquivo FIT do tipo 'workout'."""
    from garmin_fit_sdk import Encoder, Profile

    if endurance_items(day):
        return endurance_fit(day, plan_name, now)

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
        cardio_items = [i for i in items if i["_ex"]["kind"] == "cardio"]
        fs = cardio_items[0]["_ex"].get("fit_sport", {"sport": "fitness_equipment", "sub_sport": "cardio_training"})
        if any(i["_ex"].get("fit_sport") != fs for i in cardio_items):
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
                if it.get("phase") in ("preparo", "pos"):
                    intensity = "warmup" if it["phase"] == "preparo" else "cooldown"
                elif not cardio_only and not it.get("phase"):
                    intensity = "warmup" if idx == 0 else ("cooldown" if idx == len(items) - 1 else "active")
                add({"wkt_step_name": label, "duration_type": "time",
                     "duration_value": int(float(it.get("duration_min") or 10) * 60_000), "intensity": intensity,
                     **hr(it.get("zone")), **ex_fields, **({"notes": notes} if notes else {})})
            continue

        if _phase(it) in ("preparo", "pos"):
            secs = int(it.get("duration_s") or 30) * max(1, int(it.get("sets") or 1))
            add({"wkt_step_name": label, "duration_type": "time", "duration_value": secs * 1000,
                 "intensity": "warmup" if _phase(it) == "preparo" else "cooldown", "target_type": "open",
                 **({} if cardio_only else ex_fields)})
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
