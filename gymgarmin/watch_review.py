"""Treino gravado no relógio (Garmin ou qualquer outro) chegando para a pessoa conferir e corrigir exercício por exercício.

Três situações:
- o relógio mandou as séries com o exercício certo (treino enviado do app): só conferir cargas e repetições;
- mandou as séries com um palpite pela categoria ("Rosca") ou sem nome nenhum: a tela pede o exercício de cada bloco
  (o app separa os blocos onde a carga recomeça; dá para dividir mais);
- mandou só o tempo, a FC e as calorias (Apple, Amazfit, Xiaomi, a maioria dos relógios no Health Connect): a pessoa anota
  os exercícios (ou parte do treino planejado do dia) e o tempo/FC/calorias do relógio continuam valendo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import catalog, dedupe
from .watch_labels import CATEGORY_PT, UNKNOWN_ID, UNKNOWN_NAME, blocks, _clean

INBOX_DAYS = 4
GENERIC_STRENGTH = "Garmin_musculacao"
STRENGTH_WORDS = ("musculação", "musculacao", "strength", "força", "forca", "weight", "academia")

# Health Connect (Samsung, Google Pixel Watch e outros que gravam as séries): tipo do segmento → categoria do relógio
# (as mesmas do Garmin, para a resposta "Rosca = rosca martelo" valer para os dois) e o palpite do exercício.
HC_SEGMENTS = {
    "BENCH_PRESS": ("BENCH_PRESS", "Barbell_Bench_Press_-_Medium_Grip"),
    "ARM_CURL": ("CURL", "Dumbbell_Bicep_Curl"), "DUMBBELL_CURL_LEFT_ARM": ("CURL", "Dumbbell_Bicep_Curl"),
    "DUMBBELL_CURL_RIGHT_ARM": ("CURL", "Dumbbell_Bicep_Curl"),
    "BARBELL_SHOULDER_PRESS": ("SHOULDER_PRESS", "Standing_Military_Press"), "SHOULDER_PRESS": ("SHOULDER_PRESS", "Dumbbell_Shoulder_Press"),
    "DEADLIFT": ("DEADLIFT", "Barbell_Deadlift"),
    "DOUBLE_ARM_TRICEPS_EXTENSION": ("TRICEPS_EXTENSION", "Standing_Dumbbell_Triceps_Extension"),
    "DUMBBELL_TRICEPS_EXTENSION_LEFT_ARM": ("TRICEPS_EXTENSION", "Standing_Dumbbell_Triceps_Extension"),
    "DUMBBELL_TRICEPS_EXTENSION_RIGHT_ARM": ("TRICEPS_EXTENSION", "Standing_Dumbbell_Triceps_Extension"),
    "DUMBBELL_TRICEPS_EXTENSION_TWO_ARM": ("TRICEPS_EXTENSION", "Standing_Dumbbell_Triceps_Extension"),
    "SINGLE_ARM_TRICEPS_EXTENSION": ("TRICEPS_EXTENSION", "Standing_Dumbbell_Triceps_Extension"),
    "DUMBBELL_FRONT_RAISE": ("FRONT_RAISE", "Front_Dumbbell_Raise"), "FRONT_RAISE": ("FRONT_RAISE", "Front_Dumbbell_Raise"),
    "DUMBBELL_LATERAL_RAISE": ("LATERAL_RAISE", "Side_Lateral_Raise"), "LATERAL_RAISE": ("LATERAL_RAISE", "Side_Lateral_Raise"),
    "DUMBBELL_ROW": ("ROW", "One-Arm_Dumbbell_Row"),
    "HIP_THRUST": ("HIP_RAISE", "Barbell_Hip_Thrust"),
    "LAT_PULL_DOWN": ("LAT_PULLDOWN", "Wide-Grip_Lat_Pulldown"), "PULL_UP": ("PULL_UP", "Pullups"),
    "LEG_CURL": ("LEG_CURL", "Seated_Leg_Curl"), "LEG_EXTENSION": ("LEG_EXTENSION", "Leg_Extensions"),
    "LEG_PRESS": ("LEG_PRESS", "Leg_Press"), "LUNGE": ("LUNGE", "Dumbbell_Lunges"), "SQUAT": ("SQUAT", "Barbell_Squat"),
    "CRUNCH": ("CRUNCH", "Crunches"), "SIT_UP": ("SIT_UP", "Crunches"), "BENCH_SIT_UP": ("SIT_UP", "Crunches"),
    "PLANK": ("PLANK", "Plank"), "LEG_RAISE": ("LEG_RAISE", "Hanging_Leg_Raise"),
    "BACK_EXTENSION": ("HYPEREXTENSION", "Hyperextensions_Back_Extensions"),
    "KETTLEBELL_SWING": ("HIP_SWING", "One-Arm_Kettlebell_Swings"),
}
HC_UNNAMED = {"WEIGHTLIFTING", "OTHER_WORKOUT", "UNKNOWN", "STRENGTH", ""}
HC_SKIP = {"REST", "PAUSE"}
EXTRA_CATEGORY_PT = {"FRONT_RAISE": "Elevação frontal", "LAT_PULLDOWN": "Puxada", "LEG_PRESS": "Leg press"}


def category_pt(cat: str | None) -> str:
    return CATEGORY_PT.get(cat or "") or EXTRA_CATEGORY_PT.get(cat or "") or (cat or "").replace("_", " ").capitalize()


def segment_items(segments: list, cat_prefs: dict | None = None) -> list[dict]:
    """Séries que outro relógio gravou no Health Connect (um segmento = uma série) → itens com palpite ou sem nome.
    O Health Connect não tem carga: as séries chegam sem kg, para a pessoa completar."""
    prefs = cat_prefs or {}
    items: list[dict] = []
    for sg in segments or []:
        if not isinstance(sg, dict):
            continue
        t = str(sg.get("type") or "").upper()
        if t in HC_SKIP:
            continue
        try:
            reps = int(sg.get("reps") or 0)
        except (TypeError, ValueError):
            reps = 0
        dur = 0
        try:
            a, b = datetime.fromisoformat(str(sg.get("start"))), datetime.fromisoformat(str(sg.get("end")))
            dur = max(0, round((b - a).total_seconds()))
        except (TypeError, ValueError):
            pass
        if t not in HC_SEGMENTS and t not in HC_UNNAMED:
            continue   # corrida, bike, polichinelo… no meio da musculação: não é série de força
        if not reps and dur < 10:
            continue
        cat, guess = HC_SEGMENTS.get(t, (None, None))
        chosen = prefs.get(cat) if cat else None
        eid = chosen or guess or UNKNOWN_ID
        if not catalog.get(eid) and eid != UNKNOWN_ID:
            eid = UNKNOWN_ID
        entry = {"reps": reps, "load_kg": None, "duration_s": dur}
        if (catalog.get(eid) or {}).get("timed") and not reps:
            entry = {"reps": None, "load_kg": None, "duration_s": dur}
        prev = items[-1] if items else None
        if prev and prev["exercise_id"] == eid and (prev.get("garmin") or {}).get("category") == (cat or "UNKNOWN"):
            prev["sets"].append(entry)
            continue
        it = {"exercise_id": eid, "sets": [entry], "garmin": {"category": cat or "UNKNOWN", "name": None, "hc": t}}
        if eid == UNKNOWN_ID:
            it["_name"] = UNKNOWN_NAME
        elif chosen:
            it["labeled"] = "categoria"
        else:
            it["guess"] = True
        items.append(it)
    return items


def _is_watch(s: dict) -> bool:
    return dedupe._watch(s) and not str(s.get("ext_id") or "").startswith("passos:")


def _strength_items(s: dict) -> list[dict]:
    return [it for it in s.get("items") or [] if isinstance(it.get("sets"), list) and it["sets"]
            and ((catalog.get(it.get("exercise_id")) or {}).get("kind") == "strength" or it.get("exercise_id") == UNKNOWN_ID)]


def time_only_strength(s: dict) -> bool:
    """Relógio que só gravou o tempo de uma musculação (ou de "outro treino" sem tipo, que costuma ser academia)."""
    if not _is_watch(s) or _strength_items(s):
        return False
    items = s.get("items") or []
    if any(it.get("exercise_id") == GENERIC_STRENGTH or it.get("strength_min") for it in items):
        return True
    if dedupe.is_unknown_watch(s):
        mins = float(s.get("duration_min") or 0)
        return mins >= 15
    return False


def needs_review(s: dict) -> bool:
    if not _is_watch(s) or s.get("reviewed"):
        return False
    if s.get("merged") and not dedupe.is_unknown_watch(s) and any(it.get("sets") for it in s.get("items") or []) \
            and not any(it.get("guess") or it.get("exercise_id") == UNKNOWN_ID for it in s.get("items") or []):
        return False   # já juntado com o que a pessoa anotou no app
    return bool(_strength_items(s)) or time_only_strength(s)


def summary(s: dict) -> dict:
    st = _strength_items(s)
    sets = sum(len(_clean(it["sets"])) for it in st)
    guesses = sum(1 for it in st if it.get("guess"))
    unknown = sum(len(_clean(it["sets"])) for it in st if it.get("exercise_id") == UNKNOWN_ID)
    no_load = sum(1 for it in st if it.get("exercise_id") != UNKNOWN_ID for x in _clean(it["sets"])
                  if x.get("load_kg") is None and not (catalog.get(it["exercise_id"]) or {}).get("timed"))
    gs = s.get("garmin_summary") or {}
    gen = next((it for it in s.get("items") or [] if not it.get("sets")), {})
    src = str(s.get("source") or "")
    label = "Garmin" if s.get("garmin_activity_id") or src == "garmin" else "Apple Saúde" if src == "apple" else "relógio (Health Connect)" if src == "android" else "relógio"
    return {"id": s.get("id"), "date": str(s.get("date"))[:16], "title": s.get("day_name") or s.get("notes") or "Musculação",
            "source": label, "duration_min": round(float(s.get("duration_min") or gs.get("duration_min") or gen.get("duration_min") or 0)),
            "avg_hr": gs.get("avg_hr") or gen.get("avg_hr"), "kcal": gs.get("kcal") or gen.get("kcal"),
            "sets": sets, "exercises": len([it for it in st if it.get("exercise_id") != UNKNOWN_ID]), "guesses": guesses,
            "unknown_sets": unknown, "no_load": no_load, "time_only": not st}


def inbox(sessions: list[dict], today: date, app_sessions: list[dict] | None = None) -> list[dict]:
    """Treinos do relógio dos últimos dias que ainda não foram conferidos (mais novos primeiro).
    Fica de fora o que já tem pergunta "é o mesmo treino que você anotou?" (a pessoa responde lá)."""
    lo = (today - timedelta(days=INBOX_DAYS)).isoformat()
    app = [x for x in (app_sessions if app_sessions is not None else sessions) if not _is_watch(x)]
    out = []
    for s in sessions:
        if str(s.get("date"))[:10] < lo or not needs_review(s):
            continue
        if any(dedupe.same_time_watch(s, a) for a in app):
            continue
        out.append(summary(s))
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:4]


def edit_blocks(s: dict) -> list[dict]:
    """Os itens de força do treino, na ordem, com os blocos sem nome já separados (cada bloco, um exercício provável)."""
    out = []
    for idx, it in enumerate(s.get("items") or []):
        if it not in _strength_items(s):
            continue
        if it.get("exercise_id") == UNKNOWN_ID:
            for a, b in blocks(it["sets"]):
                sets = _clean(it["sets"][a:b])
                if sets:
                    out.append({"unknown": True, "sets": sets, "src": idx})
        else:
            out.append({"unknown": False, "item": it, "sets": _clean(it["sets"]) or it["sets"], "src": idx})
    return out


def lift_measures(session: dict, drop) -> dict:
    """O item genérico do relógio ("52 min de musculação") sai quando a pessoa anota os exercícios: o tempo, a FC e as
    calorias sobem para o resumo do treino (como ao juntar com o relógio), para não contar duas vezes."""
    summ = dict(session.get("garmin_summary") or {})
    for gi in drop:
        for k in ("avg_hr", "max_hr", "kcal", "duration_min"):
            if gi.get(k) and not summ.get(k):
                summ[k] = gi[k]
    return summ


def is_generic(it: dict) -> bool:
    return not it.get("sets") and (it.get("exercise_id") in (GENERIC_STRENGTH, dedupe.UNKNOWN_ACTIVITY) or bool(it.get("strength_min")))
