"""Catálogo unificado: exercícios do free-exercise-db + aparelhos de cardio.

Fonte dos exercícios: https://github.com/yuhonas/free-exercise-db (Unlicense / domínio público),
~870 exercícios com músculos primários/secundários, equipamento, instruções e imagens.
Os aparelhos de cardio (data/cardio_machines.json) são unificados ao catálogo: os
registros de cardio equivalentes do free-exercise-db são mesclados neles (imagens).
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from .muscles import CATEGORY_PT, EQUIPMENT_PT, FORCE_PT, LEVEL_PT, MECHANIC_PT, muscle_pt

DATA = Path(__file__).resolve().parent / "data"
IMAGE_BASE = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"

# Cardio do free-exercise-db que vira aparelho unificado
CARDIO_MERGE = {
    "cardio_esteira_caminhada": ["Walking_Treadmill"],
    "cardio_esteira_corrida": ["Running_Treadmill", "Jogging_Treadmill"],
    "cardio_bike_vertical": ["Bicycling_Stationary"],
    "cardio_bike_horizontal": ["Recumbent_Bike"],
    "cardio_eliptico": ["Elliptical_Trainer"],
    "cardio_remo": ["Rowing_Stationary"],
    "cardio_escada": ["Stairmaster", "Step_Mill"],
    "cardio_corda": ["Rope_Jumping"],
}

OUTDOOR_SPORT_IDS = {"corrida_rua", "corrida_trilha", "corrida_pista", "ciclismo_estrada", "ciclismo_rolo",
                     "ciclismo_mtb", "natacao_piscina", "natacao_aguas_abertas", "remo_agua", "caminhada_rua",
                     "trilha_hiking"}

# Correções de dados do free-exercise-db (músculos errados na fonte)
MUSCLE_FIXES = {
    "Cable_Hip_Adduction": (["adductors"], ["glutes"]),
    # levantamento terra: cadeia posterior inteira (a fonte lista só a lombar como principal)
    "Barbell_Deadlift": (["hamstrings", "glutes", "lower back"], ["quadriceps", "traps", "forearms", "lats", "middle back"]),
}

# Nomes em português dos exercícios mais comuns de academia no Brasil
NAMES_PT = {
    "Barbell_Bench_Press_-_Medium_Grip": "Supino reto com barra",
    "Dumbbell_Bench_Press": "Supino reto com halteres",
    "Barbell_Incline_Bench_Press_-_Medium_Grip": "Supino inclinado com barra",
    "Incline_Dumbbell_Press": "Supino inclinado com halteres",
    "Decline_Barbell_Bench_Press": "Supino declinado com barra",
    "Decline_Dumbbell_Bench_Press": "Supino declinado com halteres",
    "Close-Grip_Barbell_Bench_Press": "Supino fechado (pegada fechada)",
    "Smith_Machine_Bench_Press": "Supino no Smith",
    "Machine_Bench_Press": "Supino máquina",
    "Leverage_Chest_Press": "Chest press articulado",
    "Dumbbell_Flyes": "Crucifixo reto com halteres",
    "Incline_Dumbbell_Flyes": "Crucifixo inclinado com halteres",
    "Butterfly": "Voador (peck deck)",
    "Cable_Crossover": "Crossover na polia",
    "Low_Cable_Crossover": "Crossover polia baixa",
    "Pushups": "Flexão de braço",
    "Push-Up_Wide": "Flexão de braço aberta",
    "Push-Ups_-_Close_Triceps_Position": "Flexão de braço fechada (diamante)",
    "Dips_-_Chest_Version": "Paralelas (foco peitoral)",
    "Dips_-_Triceps_Version": "Paralelas (foco tríceps)",
    "Bench_Dips": "Mergulho no banco",
    "Bent-Arm_Dumbbell_Pullover": "Pullover com halter",
    "Wide-Grip_Lat_Pulldown": "Puxada alta aberta (pulley frente)",
    "Close-Grip_Front_Lat_Pulldown": "Puxada alta fechada",
    "V-Bar_Pulldown": "Puxada com triângulo",
    "Underhand_Cable_Pulldowns": "Puxada supinada",
    "Wide-Grip_Pulldown_Behind_The_Neck": "Puxada atrás da nuca",
    "Straight-Arm_Pulldown": "Pulldown braços estendidos",
    "Pullups": "Barra fixa (pronada)",
    "Chin-Up": "Barra fixa supinada",
    "Seated_Cable_Rows": "Remada baixa sentada",
    "Bent_Over_Barbell_Row": "Remada curvada com barra",
    "Reverse_Grip_Bent-Over_Rows": "Remada curvada supinada",
    "One-Arm_Dumbbell_Row": "Remada unilateral com halter (serrote)",
    "T-Bar_Row_with_Handle": "Remada cavalinho (T-bar)",
    "Leverage_High_Row": "Remada alta articulada",
    "Leverage_Iso_Row": "Remada articulada",
    "Face_Pull": "Face pull",
    "Barbell_Deadlift": "Levantamento terra",
    "Sumo_Deadlift": "Levantamento terra sumô",
    "Romanian_Deadlift": "Stiff romeno (RDL)",
    "Stiff-Legged_Barbell_Deadlift": "Stiff com barra",
    "Stiff-Legged_Dumbbell_Deadlift": "Stiff com halteres",
    "Rack_Pulls": "Rack pull (terra parcial)",
    "Good_Morning": "Good morning (bom dia)",
    "Hyperextensions_Back_Extensions": "Hiperextensão lombar (banco romano)",
    "Superman": "Super-homem (extensão lombar no solo)",
    "Barbell_Shrug": "Encolhimento com barra",
    "Dumbbell_Shrug": "Encolhimento com halteres",
    "Upright_Barbell_Row": "Remada alta com barra",
    "Standing_Military_Press": "Desenvolvimento militar em pé",
    "Seated_Barbell_Military_Press": "Desenvolvimento com barra sentado",
    "Dumbbell_Shoulder_Press": "Desenvolvimento com halteres",
    "Seated_Dumbbell_Press": "Desenvolvimento sentado com halteres",
    "Arnold_Dumbbell_Press": "Desenvolvimento Arnold",
    "Machine_Shoulder_Military_Press": "Desenvolvimento máquina",
    "Side_Lateral_Raise": "Elevação lateral",
    "Seated_Side_Lateral_Raise": "Elevação lateral sentado",
    "Front_Dumbbell_Raise": "Elevação frontal com halteres",
    "Front_Cable_Raise": "Elevação frontal na polia",
    "Reverse_Flyes": "Crucifixo invertido",
    "Reverse_Machine_Flyes": "Voador invertido (peck deck inverso)",
    "Cable_Rear_Delt_Fly": "Crucifixo invertido na polia",
    "External_Rotation_with_Cable": "Rotação externa na polia",
    "Barbell_Curl": "Rosca direta com barra",
    "EZ-Bar_Curl": "Rosca direta barra W",
    "Dumbbell_Bicep_Curl": "Rosca bíceps com halteres",
    "Dumbbell_Alternate_Bicep_Curl": "Rosca alternada",
    "Hammer_Curls": "Rosca martelo",
    "Alternate_Hammer_Curl": "Rosca martelo alternada",
    "Concentration_Curls": "Rosca concentrada",
    "Preacher_Curl": "Rosca Scott",
    "Machine_Preacher_Curls": "Rosca Scott máquina",
    "Incline_Dumbbell_Curl": "Rosca inclinada com halteres",
    "Spider_Curl": "Rosca spider",
    "Reverse_Barbell_Curl": "Rosca inversa",
    "Cable_Hammer_Curls_-_Rope_Attachment": "Rosca martelo na corda",
    "Triceps_Pushdown": "Tríceps pulley (barra)",
    "Triceps_Pushdown_-_Rope_Attachment": "Tríceps corda",
    "Reverse_Grip_Triceps_Pushdown": "Tríceps pulley inverso",
    "EZ-Bar_Skullcrusher": "Tríceps testa barra W",
    "Lying_Triceps_Press": "Tríceps testa",
    "Standing_Dumbbell_Triceps_Extension": "Tríceps francês com halter",
    "Cable_Rope_Overhead_Triceps_Extension": "Tríceps francês na corda",
    "Tricep_Dumbbell_Kickback": "Tríceps coice",
    "Machine_Triceps_Extension": "Tríceps máquina",
    "Palms-Up_Barbell_Wrist_Curl_Over_A_Bench": "Rosca punho",
    "Barbell_Squat": "Agachamento livre com barra",
    "Barbell_Full_Squat": "Agachamento profundo com barra",
    "Front_Barbell_Squat": "Agachamento frontal",
    "Goblet_Squat": "Agachamento goblet",
    "Smith_Machine_Squat": "Agachamento no Smith",
    "Bodyweight_Squat": "Agachamento livre (peso corporal)",
    "Box_Squat": "Agachamento no banco (box squat)",
    "Hack_Squat": "Hack squat (máquina)",
    "Barbell_Hack_Squat": "Hack squat com barra",
    "Plie_Dumbbell_Squat": "Agachamento sumô com halter",
    "Leg_Press": "Leg press 45°",
    "Narrow_Stance_Leg_Press": "Leg press pés fechados",
    "Leg_Extensions": "Cadeira extensora",
    "Lying_Leg_Curls": "Mesa flexora",
    "Seated_Leg_Curl": "Cadeira flexora",
    "Dumbbell_Lunges": "Afundo / avanço com halteres",
    "Barbell_Lunge": "Afundo com barra",
    "Barbell_Walking_Lunge": "Passada (avanço caminhando) com barra",
    "Bodyweight_Walking_Lunge": "Passada (peso corporal)",
    "Dumbbell_Rear_Lunge": "Afundo reverso com halteres",
    "Split_Squat_with_Dumbbells": "Agachamento búlgaro / afundo estático",
    "Split_Squats": "Afundo estático",
    "Dumbbell_Step_Ups": "Subida no banco (step-up)",
    "Barbell_Hip_Thrust": "Elevação pélvica com barra",
    "Barbell_Glute_Bridge": "Ponte de glúteo com barra",
    "Single_Leg_Glute_Bridge": "Ponte de glúteo unilateral",
    "Glute_Kickback": "Coice de glúteo (4 apoios)",
    "One-Legged_Cable_Kickback": "Coice de glúteo na polia",
    "Thigh_Abductor": "Cadeira abdutora",
    "Thigh_Adductor": "Cadeira adutora",
    "Cable_Hip_Adduction": "Adução na polia",
    "Standing_Calf_Raises": "Panturrilha em pé (máquina)",
    "Seated_Calf_Raise": "Panturrilha sentado (gêmeos sentado)",
    "Calf_Press_On_The_Leg_Press_Machine": "Panturrilha no leg press",
    "Smith_Machine_Calf_Raise": "Panturrilha no Smith",
    "Standing_Dumbbell_Calf_Raise": "Panturrilha em pé com halter",
    "Donkey_Calf_Raises": "Panturrilha burrinho",
    "Kettlebell_One-Legged_Deadlift": "Stiff unilateral com kettlebell",
    "One-Arm_Kettlebell_Swings": "Swing com kettlebell (unilateral)",
    "Farmers_Walk": "Caminhada do fazendeiro",
    "Crunches": "Abdominal supra (crunch)",
    "Cable_Crunch": "Abdominal na polia (ajoelhado)",
    "Ab_Crunch_Machine": "Abdominal máquina",
    "Reverse_Crunch": "Abdominal infra (reverso)",
    "Decline_Crunch": "Abdominal declinado",
    "Oblique_Crunches": "Abdominal oblíquo",
    "Hanging_Leg_Raise": "Elevação de pernas na barra",
    "Flat_Bench_Lying_Leg_Raise": "Elevação de pernas no banco",
    "Plank": "Prancha",
    "Side_Bridge": "Prancha lateral",
    "Russian_Twist": "Russian twist (rotação de tronco)",
    "Ab_Roller": "Roda abdominal",
    "Dead_Bug": "Dead bug",
    "Pallof_Press": "Pallof press (antirrotação)",
    "Mountain_Climbers": "Escalador (mountain climber)",
    "Sit-Up": "Abdominal completo (sit-up)",
    "Box_Jump_Multiple_Response": "Salto na caixa (box jump)",
    "Freehand_Jump_Squat": "Agachamento com salto",
    "Power_Clean": "Power clean (arremesso)",
    "Clean_and_Press": "Clean and press",
}

# Glossário PT -> EN para busca ("supino" encontra "bench press" etc.)
SEARCH_PT = {
    "supino": "bench press", "agachamento": "squat", "terra": "deadlift", "stiff": "stiff",
    "remada": "row", "puxada": "pulldown", "pulley": "pulldown", "rosca": "curl", "triceps": "triceps",
    "desenvolvimento": "press", "elevacao lateral": "lateral raise", "elevacao frontal": "front raise",
    "crucifixo": "fly", "voador": "butterfly", "flexao": "push", "barra fixa": "pull", "afundo": "lunge",
    "avanco": "lunge", "passada": "lunge", "extensora": "leg extension", "flexora": "leg curl",
    "panturrilha": "calf", "gemeos": "calf", "abdominal": "crunch", "prancha": "plank",
    "elevacao pelvica": "hip thrust", "ponte": "bridge", "encolhimento": "shrug", "bulgaro": "split squat",
    "martelo": "hammer", "testa": "skull", "frances": "overhead triceps", "coice": "kickback",
    "abdutora": "abductor", "adutora": "adductor", "esteira": "treadmill", "bicicleta": "bike",
    "bike": "bike", "eliptico": "elliptical", "remo": "rowing", "escada": "stair", "corda": "rope",
    "alongamento": "stretch", "paralelas": "dips", "mergulho": "dips", "hiperextensao": "hyperextension",
    "lombar": "lower back", "maquina": "machine", "halter": "dumbbell", "halteres": "dumbbell",
    "barra": "barbell", "polia": "cable", "cabo": "cable", "elastico": "band", "kettlebell": "kettlebell",
    "salto": "jump", "pular": "jump",
}

MUSCLE_SEARCH_PT = {
    "peito": "chest", "peitoral": "chest", "ombro": "shoulders", "deltoide": "shoulders", "costas": "lats",
    "dorsal": "lats", "biceps": "biceps", "triceps": "triceps", "perna": "quadriceps", "quadriceps": "quadriceps",
    "coxa": "quadriceps", "posterior": "hamstrings", "gluteo": "glutes", "panturrilha": "calves",
    "abdomen": "abdominals", "abdominal": "abdominals", "trapezio": "traps", "antebraco": "forearms",
    "lombar": "lower back", "adutor": "adductors", "abdutor": "abductors", "pescoco": "neck",
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def _is_timed(ex: dict) -> bool:
    n = ex["name"].lower()
    return ex.get("force") == "static" or ex.get("category") == "stretching" or any(
        w in n for w in ("plank", "hold", "bridge", "wall sit", "isometric", "stretch", "carry", "walk")
    )


@lru_cache(maxsize=1)
def _load() -> tuple[list[dict], dict[str, dict]]:
    raw = json.loads((DATA / "exercises.json").read_text())
    gmap = json.loads((DATA / "garmin_map.json").read_text())
    cardio = json.loads((DATA / "cardio_machines.json").read_text())
    merged_ids = {i for ids in CARDIO_MERGE.values() for i in ids}
    by_raw = {e["id"]: e for e in raw}

    items: list[dict] = []
    for e in raw:
        if e["id"] in merged_ids:
            continue
        kind = "cardio" if e.get("category") == "cardio" else (
            "stretching" if e.get("category") == "stretching" else "strength")
        g = gmap.get(e["id"], {"category": "TOTAL_BODY", "exercise": "TOTAL_BODY", "garmin_name": "Total Body", "confidence": "generica"})
        items.append({
            "id": e["id"],
            "name": NAMES_PT.get(e["id"], e["name"]),
            "name_en": e["name"],
            "kind": kind,
            "category": e.get("category"),
            "category_pt": CATEGORY_PT.get(e.get("category"), e.get("category")),
            "equipment": e.get("equipment"),
            "equipment_pt": EQUIPMENT_PT.get(e.get("equipment"), e.get("equipment")),
            "level": e.get("level"),
            "level_pt": LEVEL_PT.get(e.get("level"), e.get("level")),
            "force": e.get("force"),
            "force_pt": FORCE_PT.get(e.get("force")),
            "mechanic": e.get("mechanic"),
            "mechanic_pt": MECHANIC_PT.get(e.get("mechanic")),
            "primaryMuscles": MUSCLE_FIXES.get(e["id"], (e.get("primaryMuscles", []),))[0],
            "secondaryMuscles": MUSCLE_FIXES[e["id"]][1] if e["id"] in MUSCLE_FIXES else e.get("secondaryMuscles", []),
            "instructions": e.get("instructions", []),
            "images": [IMAGE_BASE + i for i in e.get("images", [])],
            "garmin": g,
            "timed": _is_timed(e),
            "popular": e["id"] in NAMES_PT,
        })

    for c in cardio:
        imgs: list[str] = []
        for rid in CARDIO_MERGE.get(c["id"], []):
            if rid in by_raw:
                imgs += [IMAGE_BASE + i for i in by_raw[rid].get("images", [])]
        items.append({
            "id": c["id"],
            "name": c["name"],
            "name_en": c["name_en"],
            "kind": "cardio",
            "category": "cardio",
            "category_pt": "Cardio",
            "equipment": "cardio machine",
            "equipment_pt": EQUIPMENT_PT["cardio machine"],
            "level": "beginner",
            "level_pt": LEVEL_PT["beginner"],
            "force": None, "force_pt": None, "mechanic": None, "mechanic_pt": None,
            "primaryMuscles": c["primaryMuscles"],
            "secondaryMuscles": c["secondaryMuscles"],
            "instructions": [c.get("tips", "")],
            "images": imgs[:2],
            "garmin": {**c["garmin_step"], "garmin_name": c["name_en"], "confidence": "alta"},
            "fit_step": c["fit_step"],
            "fit_sport": c["fit_sport"],
            "connect_sport": c["connect_sport"],
            "impact": c["impact"],
            "met": c["met"],
            "params": c["params"],
            "timed": True,
            "popular": True,
            # aparelhos de academia; corrida na rua, natação etc. são modalidades (sports.py)
            "machine": c.get("id") not in OUTDOOR_SPORT_IDS,
            "endurance": bool(c.get("endurance")),
        })

    for it in items:
        it["primary_pt"] = [muscle_pt(m) for m in it["primaryMuscles"]]
        it["secondary_pt"] = [muscle_pt(m) for m in it["secondaryMuscles"]]
        it["_search"] = strip_accents(" ".join([it["name"], it["name_en"], it["equipment_pt"] or "",
                                                 " ".join(it["primary_pt"])]))
    return items, {i["id"]: i for i in items}


def all_exercises() -> list[dict]:
    return _load()[0]


def get(exercise_id: str) -> dict | None:
    return _load()[1].get(exercise_id)


def _translate(qn: str) -> str:
    out = qn
    for pt, en in sorted(SEARCH_PT.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"\b{re.escape(pt)}\b", en, out)
    return out


def _score(item: dict, qn: str, translated: str) -> int:
    text = item["_search"]
    if qn in text:
        return 6
    if translated != qn and translated in text:
        return 5
    for t in (qn, translated):
        words = t.split()
        if words and all(w in text for w in words):
            return 4
    hits = sum(1 for w in translated.split() if len(w) > 2 and w in text)
    return min(hits, 3)


def search(q: str = "", muscle: str | None = None, equipment: str | None = None,
           kind: str | None = None, level: str | None = None, limit: int = 60) -> list[dict]:
    items = all_exercises()
    if muscle:
        muscle = MUSCLE_SEARCH_PT.get(strip_accents(muscle), muscle)
        prim = sorted((i for i in items if muscle in i["primaryMuscles"]), key=lambda i: (not i["popular"], i["name"]))
        sec = sorted((i for i in items if muscle in i["secondaryMuscles"] and muscle not in i["primaryMuscles"]),
                     key=lambda i: (not i["popular"], i["name"]))
        items = prim + sec
    if equipment:
        items = [i for i in items if i["equipment"] == equipment]
    if kind:
        items = [i for i in items if i["kind"] == kind]
    if level:
        items = [i for i in items if i["level"] == level]
    if q:
        qn = strip_accents(q).strip()
        translated = _translate(qn)
        scored = [(_score(i, qn, translated), i) for i in items]
        scored = [s for s in scored if s[0] > 0]
        top = max((s[0] for s in scored), default=0)
        if top >= 4:  # há correspondência completa: descarta as parciais
            scored = [s for s in scored if s[0] >= 4]
        scored.sort(key=lambda s: (-s[0], not s[1]["popular"], len(s[1]["name"])))
        items = [s[1] for s in scored]
    elif not muscle:
        items = sorted(items, key=lambda i: (not i["popular"], i["name"]))
    return items[:limit]


def public(ex: dict) -> dict:
    return {k: v for k, v in ex.items() if not k.startswith("_")}


def best_match(name: str) -> dict | None:
    """Encontra o exercício mais parecido com um nome livre (importação de planilha)."""
    if not name:
        return None
    n = strip_accents(name)
    for it in all_exercises():
        if strip_accents(it["name"]) == n or strip_accents(it["name_en"]) == n or it["id"].lower() == n:
            return it
    translated = _translate(n)
    words = [w for w in translated.split() if len(w) > 2]
    best, best_s = None, 0.0
    for it in all_exercises():
        sc = _score(it, n, translated)
        if sc >= 4:
            sc = sc + (0.5 if it["popular"] else 0) - len(it["name"]) / 1000
        elif words:
            hits = sum(1 for w in words if w in it["_search"])
            sc = hits / len(words) * 3 if hits / len(words) >= 0.6 else 0
        if sc > best_s:
            best, best_s = it, sc
    return best
