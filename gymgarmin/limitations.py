"""Limitações do usuário (lesões, dores, condições) e como elas filtram exercícios.

Cada limitação define regras:
  avoid   – padrões (regex sobre nome EN/PT/id) que devem ser EVITADOS
  caution – padrões que pedem CUIDADO (carga menor, amplitude controlada, técnica)
  avoid_categories / caution_categories – categorias inteiras do catálogo
  caution_force – tipo de força (ex.: isometria para hipertensão)
  cardio_avoid_impact – impacto de aparelho de cardio a evitar ("alto", "moderado")
  max_zone – zona máxima de FC recomendada
  alternatives – ids de exercícios/aparelhos sugeridos no lugar

As regras são conservadoras e educativas — NÃO substituem avaliação de médico ou
fisioterapeuta. O usuário pode ajustar a severidade e adicionar palavras próprias.
"""

from __future__ import annotations

import re

from .catalog import strip_accents

LIMITATIONS: dict[str, dict] = {
    "joelho": {
        "label": "Joelho (dor patelar, condromalácia, menisco, LCA)",
        "avoid": [r"jump", r"hop", r"bound", r"pistol", r"sissy", r"depth", r"lunge sprint", r"tuck jump", r"skater"],
        "caution": [r"squat", r"lunge", r"leg extension", r"step.?up", r"split", r"hack", r"leg press", r"kneeling",
                    r"stair", r"agachamento", r"afundo", r"extensora"],
        "avoid_categories": ["plyometrics"],
        "cardio_avoid_impact": ["alto"],
        "alternatives": ["cardio_bike_horizontal", "cardio_eliptico", "Barbell_Hip_Thrust", "Lying_Leg_Curls"],
        "note": "Prefira amplitude sem dor, cadência controlada e cardio de baixo impacto (bike, elíptico).",
    },
    "lombar": {
        "label": "Coluna lombar (lombalgia, hérnia/protrusão de disco)",
        "avoid": [r"good morning", r"jefferson", r"\bbarbell deadlift", r"sumo deadlift", r"deficit deadlift", r"rack pull",
                  r"bent over (two|barbell)", r"t-bar", r"sit.?up", r"superman", r"windmill", r"behind the neck"],
        "caution": [r"squat", r"deadlift", r"row", r"hyperextension", r"back extension", r"crunch", r"leg raise",
                    r"overhead", r"military", r"clean", r"snatch", r"russian twist", r"leg press"],
        "avoid_categories": ["olympic weightlifting", "strongman"],
        "caution_categories": ["powerlifting"],
        "cardio_caution": ["cardio_remo", "cardio_esteira_corrida"],
        "alternatives": ["Dead_Bug", "Pallof_Press", "Plank", "Seated_Cable_Rows", "cardio_bike_horizontal"],
        "note": "Priorize estabilização (dead bug, pallof, prancha) e exercícios com apoio do tronco.",
    },
    "ombro": {
        "label": "Ombro (impacto, manguito rotador, instabilidade)",
        "avoid": [r"behind the neck", r"upright row", r"dips?\b", r"guillotine", r"handstand", r"snatch", r"kipping"],
        "caution": [r"overhead", r"military", r"shoulder press", r"arnold", r"bench press", r"flye?s?\b", r"pullover",
                    r"pull.?up", r"chin.?up", r"jerk", r"lateral raise", r"push.?up"],
        "avoid_categories": ["olympic weightlifting"],
        "alternatives": ["External_Rotation_with_Cable", "Face_Pull", "Dumbbell_Bench_Press_with_Neutral_Grip",
                         "Seated_Cable_Rows"],
        "note": "Pegada neutra, amplitude confortável e reforço de manguito (rotação externa, face pull).",
    },
    "cotovelo": {
        "label": "Cotovelo (epicondilite lateral/medial)",
        "avoid": [r"skull ?crusher", r"french"],
        "caution": [r"curl", r"triceps", r"tricep", r"extension", r"pushdown", r"chin.?up", r"pull.?up", r"dip",
                    r"close.?grip", r"wrist"],
        "alternatives": ["Hammer_Curls", "Triceps_Pushdown_-_Rope_Attachment"],
        "note": "Pegada neutra (martelo/corda) costuma ser mais tolerada; reduza carga e aumente repetições.",
    },
    "punho": {
        "label": "Punho / mão (tendinite, túnel do carpo)",
        "avoid": [r"handstand", r"wrist curl"],
        "caution": [r"push.?up", r"front squat", r"clean", r"plank", r"bench press", r"dips?\b", r"burpee",
                    r"mountain climber", r"snatch"],
        "alternatives": ["Machine_Bench_Press", "Dumbbell_Bench_Press_with_Neutral_Grip"],
        "note": "Use pegada neutra, faixas de punho e apoio em halteres em vez da mão espalmada.",
    },
    "cervical": {
        "label": "Coluna cervical / pescoço",
        "avoid": [r"behind the neck", r"neck", r"headstand", r"neck bridge"],
        "caution": [r"shrug", r"sit.?up", r"crunch", r"overhead", r"military", r"upright row"],
        "alternatives": ["Cable_Crunch", "Plank", "Dumbbell_Shoulder_Press"],
        "note": "Evite carga direta na cabeça e tração do pescoço nos abdominais.",
    },
    "quadril": {
        "label": "Quadril (impacto femoroacetabular, bursite, artrose)",
        "avoid": [r"sumo", r"jump", r"plie"],
        "caution": [r"squat", r"lunge", r"split", r"adduct", r"abduct", r"leg press", r"deadlift", r"step.?up"],
        "avoid_categories": ["plyometrics"],
        "cardio_avoid_impact": ["alto"],
        "alternatives": ["cardio_bike_vertical", "Barbell_Glute_Bridge", "Seated_Leg_Curl"],
        "note": "Limite a profundidade do agachamento à amplitude sem dor; fortaleça glúteo médio.",
    },
    "tornozelo": {
        "label": "Tornozelo / pé (entorse, fascite plantar, tendão de Aquiles)",
        "avoid": [r"jump", r"hop", r"bound", r"sprint", r"skip"],
        "caution": [r"calf", r"lunge", r"stair", r"step"],
        "avoid_categories": ["plyometrics"],
        "cardio_avoid_impact": ["alto"],
        "cardio_caution": ["cardio_escada"],
        "alternatives": ["cardio_bike_vertical", "cardio_remo", "Seated_Calf_Raise"],
        "note": "Cardio sem impacto; panturrilha sentado costuma ser bem tolerado.",
    },
    "hipertensao": {
        "label": "Hipertensão arterial",
        "avoid": [r"headstand", r"handstand", r"inverted"],
        "caution": [r"decline", r"overhead", r"military", r"deadlift", r"squat", r"leg press"],
        "caution_categories": ["powerlifting", "strongman"],
        "caution_force": ["static"],
        "max_zone": 3,
        "alternatives": ["cardio_bike_vertical", "cardio_eliptico"],
        "note": "Evite manobra de Valsalva (prender a respiração), isometrias longas e cargas máximas. "
                "Mantenha o cardio até Z3 salvo liberação médica.",
    },
    "cardiaco": {
        "label": "Condição cardíaca / restrição médica de intensidade",
        "avoid": [],
        "caution": [],
        "avoid_categories": ["powerlifting", "olympic weightlifting", "strongman", "plyometrics"],
        "max_zone": 2,
        "alternatives": ["cardio_esteira_caminhada", "cardio_bike_horizontal"],
        "note": "Treine somente com liberação médica; intensidade de cardio limitada à Z2.",
    },
    "gestante": {
        "label": "Gestação",
        "avoid": [r"crunch", r"sit.?up", r"jump", r"v.?up", r"jackknife", r"leg raise", r"russian twist"],
        "caution": [r"lying", r"bench press", r"supine", r"deadlift", r"squat", r"overhead"],
        "avoid_categories": ["plyometrics", "olympic weightlifting", "strongman", "powerlifting"],
        "cardio_avoid_impact": ["alto"],
        "max_zone": 3,
        "alternatives": ["cardio_bike_vertical", "cardio_eliptico", "Seated_Cable_Rows"],
        "note": "Após o 1º trimestre evite decúbito dorsal prolongado; siga a orientação do obstetra.",
    },
}


def catalog() -> list[dict]:
    return [{"id": k, "label": v["label"], "note": v["note"]} for k, v in LIMITATIONS.items()]


def _text(ex: dict) -> str:
    return strip_accents(" ".join([ex.get("name_en", ""), ex.get("name", ""), ex.get("id", "").replace("_", " ")]))


def check_exercise(ex: dict, user_limitations: list[dict]) -> dict:
    """Retorna {'status': 'ok'|'cuidado'|'evitar', 'reasons': [...]} para um exercício.

    user_limitations: [{"key": "joelho", "severity": "evitar"|"cuidado", "custom_avoid": "leg press, ..."}]
    severity "cuidado" rebaixa as regras "avoid" para "cuidado" (limitação leve).
    """
    text = _text(ex)
    status, reasons = "ok", []

    def bump(level: str, reason: str) -> None:
        nonlocal status
        order = {"ok": 0, "cuidado": 1, "evitar": 2}
        if order[level] > order[status]:
            status = level
        reasons.append(reason)

    for ul in user_limitations or []:
        key = ul.get("key")
        rule = LIMITATIONS.get(key, {})
        label = rule.get("label", ul.get("label") or key or "limitação")
        mild = ul.get("severity") == "cuidado"
        avoid_level = "cuidado" if mild else "evitar"

        for pat in rule.get("avoid", []):
            if re.search(pat, text):
                bump(avoid_level, f"{label}: movimento de risco para esta condição")
                break
        else:
            for pat in rule.get("caution", []):
                if re.search(pat, text):
                    bump("cuidado", f"{label}: exige cuidado/técnica e carga moderada")
                    break
        if ex.get("category") in rule.get("avoid_categories", []):
            bump(avoid_level, f"{label}: categoria {ex.get('category_pt')} não recomendada")
        elif ex.get("category") in rule.get("caution_categories", []):
            bump("cuidado", f"{label}: categoria {ex.get('category_pt')} pede cautela")
        if ex.get("force") in rule.get("caution_force", []):
            bump("cuidado", f"{label}: isometria — não prenda a respiração")
        if ex.get("machine"):
            if ex.get("impact") in rule.get("cardio_avoid_impact", []):
                bump(avoid_level, f"{label}: impacto {ex.get('impact')} — prefira bike/elíptico")
            if ex["id"] in rule.get("cardio_caution", []):
                bump("cuidado", f"{label}: atenção à postura/impacto neste aparelho")
            if rule.get("max_zone"):
                reasons.append(f"{label}: mantenha a FC até a zona {rule['max_zone']}")

        for word in [w.strip() for w in (ul.get("custom_avoid") or "").split(",") if w.strip()]:
            if strip_accents(word) in text:
                bump("evitar", f"Você marcou para evitar: “{word}”")
        for m in ul.get("avoid_muscles") or []:
            if m in ex.get("primaryMuscles", []):
                bump(avoid_level, f"{label}: músculo poupado ({m})")

    return {"status": status, "reasons": reasons}


def max_zone(user_limitations: list[dict]) -> int | None:
    zones = [LIMITATIONS[u["key"]]["max_zone"] for u in user_limitations or []
             if u.get("key") in LIMITATIONS and LIMITATIONS[u["key"]].get("max_zone")]
    return min(zones) if zones else None


def alternatives(user_limitations: list[dict]) -> list[str]:
    out: list[str] = []
    for u in user_limitations or []:
        for a in LIMITATIONS.get(u.get("key"), {}).get("alternatives", []):
            if a not in out:
                out.append(a)
    return out
