"""Gerador automático de treino.

Entradas (perfil): tempo de prática (meses), objetivo, dias disponíveis na semana,
minutos por sessão, equipamentos disponíveis, limitações, meta de cardio.

Como decide:
1. Tempo de prática → NÍVEL (séries, reps, RPE, descanso, volume-alvo, dias máximos de força,
   tamanho do ciclo). Objetivo → ajusta reps/descanso/volume/cardio (força, hipertrofia,
   emagrecimento, condicionamento, saúde, híbrido).
2. Nº de dias de força → DIVISÃO (full body, superior/inferior, push/pull/legs…). Dias
   disponíveis que sobram viram dias de cardio (zona 2 / intervalado).
3. Cada treino é uma sequência de PADRÕES DE MOVIMENTO (agachar, dobrar quadril, empurrar,
   puxar…). Para cada padrão escolhe o exercício liberado pelas limitações e pelo
   equipamento, preferindo máquinas para iniciantes e pesos livres para avançados, e
   variando entre os dias A/B e entre ciclos.
4. Todo treino tem PREPARO (aquecimento cardio leve + mobilidade/ativação dos músculos do
   dia + séries de aproximação no 1º exercício) e PÓS (alongamento dos músculos treinados).
5. Ajusta ao TEMPO disponível (remove/encurta acessórios) e BALANCEIA o volume semanal de
   cada músculo em direção ao alvo do nível, sem passar do máximo recuperável.
6. Distribui o CARDIO da semana (aquecimentos + finalizadores + dias de cardio) até a meta,
   respeitando zona máxima e impacto permitidos pelas limitações. No modo híbrido, controla a
   interferência: intervalado nunca na véspera/dia de pernas, cardio sempre após a força.
"""

from __future__ import annotations

import copy

import math
from collections import defaultdict

from . import catalog, clock, endurance
from .limitations import check_exercise, max_zone
from .muscles import HR_ZONES, MUSCLES, PRIMARY_WEIGHT, SECONDARY_WEIGHT

WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

# ------------------------------------------------------------------ níveis ---
TIERS = {
    "iniciante": {
        "label": "Iniciante", "months": (0, 6), "max_strength_days": 3,
        "sets": {"main": 3, "sec": 2, "acc": 2}, "reps": {"main": "10-12", "sec": "10-12", "acc": "12-15"},
        "rest": {"main": 120, "sec": 90, "acc": 60}, "rpe": 7, "cycle_weeks": 6, "ramp_sets": 0,
        "volume": lambda m: m["mev"] + (m["mav"][0] - m["mev"]) * 0.4, "timed_s": 30,
        "equipment_pref": ["machine", "cable", "dumbbell", "body only", "kettlebells", "e-z curl bar", "barbell"],
        "level": "iniciante",
    },
    "intermediario": {
        "label": "Intermediário", "months": (6, 24), "max_strength_days": 4,
        "sets": {"main": 4, "sec": 3, "acc": 3}, "reps": {"main": "6-10", "sec": "8-12", "acc": "10-15"},
        "rest": {"main": 150, "sec": 120, "acc": 75}, "rpe": 8, "cycle_weeks": 5, "ramp_sets": 1,
        "volume": lambda m: m["mav"][0], "timed_s": 40,
        "equipment_pref": None, "level": "intermediario",
    },
    "avancado": {
        "label": "Avançado", "months": (24, 60), "max_strength_days": 5,
        "sets": {"main": 4, "sec": 3, "acc": 3}, "reps": {"main": "5-8", "sec": "8-12", "acc": "10-15"},
        "rest": {"main": 180, "sec": 120, "acc": 75}, "rpe": 8.5, "cycle_weeks": 4, "ramp_sets": 1,
        "volume": lambda m: (m["mav"][0] + m["mav"][1]) / 2, "timed_s": 45,
        "equipment_pref": None, "level": "avancado",
    },
    "elite": {
        "label": "Muito avançado", "months": (60, 10_000), "max_strength_days": 6,
        "sets": {"main": 5, "sec": 4, "acc": 3}, "reps": {"main": "4-6", "sec": "6-10", "acc": "10-15"},
        "rest": {"main": 210, "sec": 150, "acc": 75}, "rpe": 9, "cycle_weeks": 4, "ramp_sets": 2,
        "volume": lambda m: m["mav"][1] * 0.9, "timed_s": 60,
        "equipment_pref": None, "level": "avancado",
    },
}

# ---------------------------------------------------------------- objetivos ---
GOALS = {
    "hipertrofia": {"label": "Hipertrofia", "reps": None, "rest": 1.0, "volume": 1.0, "cardio_target": 150,
                    "hiit": 0, "finisher": False},
    "forca": {"label": "Força", "reps": {"main": "3-5", "sec": "6-8", "acc": "8-12"}, "rest": 1.25, "volume": 0.85,
              "cardio_target": 120, "hiit": 0, "finisher": False},
    "emagrecimento": {"label": "Emagrecimento", "reps": {"main": "8-12", "sec": "10-15", "acc": "12-15"}, "rest": 0.7,
                      "volume": 0.9, "cardio_target": 250, "hiit": 1, "finisher": True},
    "condicionamento": {"label": "Condicionamento", "reps": {"main": "10-12", "sec": "12-15", "acc": "15-20"},
                        "rest": 0.6, "volume": 0.85, "cardio_target": 200, "hiit": 2, "finisher": True},
    "saude": {"label": "Saúde geral", "reps": {"main": "8-12", "sec": "10-12", "acc": "12-15"}, "rest": 0.9,
              "volume": 0.8, "cardio_target": 150, "hiit": 0, "finisher": True},
    "hibrido": {"label": "Híbrido (força + resistência)", "reps": None, "rest": 1.0, "volume": 0.85,
                "cardio_target": 240, "hiit": 1, "finisher": True, "hybrid": True},
}

# fração de cada treino de força reservada ao cardio quando os dias de cardio não bastam
CARDIO_SHARE = {"hipertrofia": 0.1, "forca": 0.05, "emagrecimento": 0.3, "condicionamento": 0.35, "saude": 0.25,
                "hibrido": 0.25}

# ---------------------------------------------------- padrões de movimento ---
PATTERNS = {
    "squat": ["Barbell_Squat", "Hack_Squat", "Leg_Press", "Smith_Machine_Squat", "Goblet_Squat", "Front_Barbell_Squat", "Dumbbell_Squat"],
    "hinge": ["Romanian_Deadlift", "Stiff-Legged_Dumbbell_Deadlift", "Barbell_Deadlift", "Hyperextensions_Back_Extensions", "Pull_Through"],
    "adductor": ["Thigh_Adductor", "Cable_Hip_Adduction"],
    "single_leg": ["Split_Squat_with_Dumbbells", "Dumbbell_Lunges", "Dumbbell_Step_Ups", "Dumbbell_Rear_Lunge", "Barbell_Walking_Lunge"],
    "leg_curl": ["Lying_Leg_Curls", "Seated_Leg_Curl"],
    "leg_ext": ["Leg_Extensions"],
    "glute": ["Barbell_Hip_Thrust", "Barbell_Glute_Bridge", "One-Legged_Cable_Kickback", "Thigh_Abductor"],
    "calf": ["Standing_Calf_Raises", "Seated_Calf_Raise", "Calf_Press_On_The_Leg_Press_Machine", "Standing_Dumbbell_Calf_Raise"],
    "h_push": ["Barbell_Bench_Press_-_Medium_Grip", "Dumbbell_Bench_Press", "Machine_Bench_Press", "Leverage_Chest_Press", "Pushups"],
    "incline_push": ["Incline_Dumbbell_Press", "Barbell_Incline_Bench_Press_-_Medium_Grip", "Leverage_Incline_Chest_Press"],
    "chest_fly": ["Cable_Crossover", "Butterfly", "Dumbbell_Flyes", "Low_Cable_Crossover"],
    "v_push": ["Dumbbell_Shoulder_Press", "Standing_Military_Press", "Machine_Shoulder_Military_Press", "Seated_Dumbbell_Press", "Arnold_Dumbbell_Press"],
    "lateral": ["Side_Lateral_Raise", "Cable_Seated_Lateral_Raise", "Seated_Side_Lateral_Raise"],
    "rear_delt": ["Face_Pull", "Reverse_Machine_Flyes", "Cable_Rear_Delt_Fly", "Reverse_Flyes"],
    "v_pull": ["Pullups", "Wide-Grip_Lat_Pulldown", "Close-Grip_Front_Lat_Pulldown", "Chin-Up", "V-Bar_Pulldown", "Underhand_Cable_Pulldowns"],
    "h_pull": ["Bent_Over_Barbell_Row", "Seated_Cable_Rows", "One-Arm_Dumbbell_Row", "T-Bar_Row_with_Handle", "Leverage_Iso_Row", "Leverage_High_Row"],
    "traps": ["Dumbbell_Shrug", "Barbell_Shrug"],
    "biceps": ["Barbell_Curl", "Dumbbell_Bicep_Curl", "Hammer_Curls", "EZ-Bar_Curl", "Preacher_Curl", "Machine_Preacher_Curls", "Incline_Dumbbell_Curl", "Cable_Hammer_Curls_-_Rope_Attachment"],
    "triceps": ["Triceps_Pushdown_-_Rope_Attachment", "Cable_Rope_Overhead_Triceps_Extension", "EZ-Bar_Skullcrusher", "Triceps_Pushdown", "Machine_Triceps_Extension", "Standing_Dumbbell_Triceps_Extension"],
    "core": ["Cable_Crunch", "Plank", "Dead_Bug", "Pallof_Press", "Hanging_Leg_Raise", "Ab_Crunch_Machine", "Reverse_Crunch", "Side_Bridge"],
}

DAY_TEMPLATES = {
    "FB_A": ("Full body A", [("squat", "main"), ("h_push", "main"), ("h_pull", "main"), ("hinge", "sec"), ("lateral", "acc"), ("biceps", "acc"), ("core", "acc")]),
    "FB_B": ("Full body B", [("hinge", "main"), ("v_pull", "main"), ("incline_push", "main"), ("single_leg", "sec"), ("rear_delt", "acc"), ("triceps", "acc"), ("calf", "acc")]),
    "FB_C": ("Full body C", [("squat", "main"), ("v_push", "main"), ("v_pull", "main"), ("leg_curl", "sec"), ("chest_fly", "acc"), ("glute", "acc"), ("core", "acc")]),
    "UP_A": ("Superior A", [("h_push", "main"), ("v_pull", "main"), ("v_push", "sec"), ("h_pull", "sec"), ("lateral", "acc"), ("biceps", "acc"), ("triceps", "acc")]),
    "UP_B": ("Superior B", [("incline_push", "main"), ("h_pull", "main"), ("v_pull", "sec"), ("chest_fly", "acc"), ("rear_delt", "acc"), ("triceps", "acc"), ("biceps", "acc"), ("core", "acc")]),
    "LO_A": ("Inferior A", [("squat", "main"), ("hinge", "sec"), ("single_leg", "sec"), ("leg_curl", "acc"), ("calf", "acc"), ("adductor", "acc"), ("core", "acc")]),
    "LO_B": ("Inferior B", [("hinge", "main"), ("squat", "sec"), ("leg_ext", "acc"), ("leg_curl", "acc"), ("glute", "acc"), ("calf", "acc"), ("core", "acc")]),
    "PUSH": ("Empurrar", [("h_push", "main"), ("incline_push", "sec"), ("v_push", "sec"), ("lateral", "acc"), ("chest_fly", "acc"), ("triceps", "acc"), ("triceps", "acc")]),
    "PULL": ("Puxar", [("v_pull", "main"), ("h_pull", "main"), ("h_pull", "sec"), ("rear_delt", "acc"), ("biceps", "acc"), ("biceps", "acc"), ("traps", "acc")]),
    "LEGS": ("Pernas", [("squat", "main"), ("hinge", "main"), ("single_leg", "sec"), ("leg_curl", "acc"), ("leg_ext", "acc"), ("calf", "acc"), ("adductor", "acc"), ("core", "acc")]),
}
LOWER_DAYS = {"LO_A", "LO_B", "LEGS", "FB_A", "FB_B", "FB_C"}
HEAVY_LEGS = {"LO_A", "LO_B", "LEGS"}

# ---------------------------------------------------- preparo e pós-treino ---
MOBILITY = {
    "superior": ["Arm_Circles", "Band_Pull_Apart", "External_Rotation_with_Band", "Cat_Stretch", "Shoulder_Circles"],
    "inferior": ["Worlds_Greatest_Stretch", "Standing_Hip_Circles", "Inchworm", "Single_Leg_Glute_Bridge", "Ankle_Circles"],
    "core": ["Dead_Bug", "Cat_Stretch"],
}
STRETCHES = {
    "chest": ["Chest_And_Front_Of_Shoulder_Stretch", "Behind_Head_Chest_Stretch"],
    "shoulders": ["Shoulder_Stretch", "Round_The_World_Shoulder_Stretch"],
    "triceps": ["Triceps_Stretch", "Overhead_Triceps"],
    "biceps": ["Standing_Biceps_Stretch", "Seated_Biceps"],
    "lats": ["Overhead_Lat", "One_Arm_Against_Wall"],
    "middle back": ["Middle_Back_Stretch", "Upper_Back_Stretch"],
    "traps": ["Side_Neck_Stretch"],
    "lower back": ["Childs_Pose", "Hug_Knees_To_Chest"],
    "quadriceps": ["Quad_Stretch", "Kneeling_Hip_Flexor"],
    "hamstrings": ["Hamstring_Stretch", "Seated_Floor_Hamstring_Stretch"],
    "glutes": ["Lying_Glute", "Seated_Glute"],
    "calves": ["Calf_Stretch_Hands_Against_Wall", "Standing_Soleus_And_Achilles_Stretch"],
    "adductors": ["Side_Lying_Groin_Stretch", "Groin_and_Back_Stretch"],
    "abductors": ["IT_Band_and_Glute_Stretch"],
    "abdominals": ["Standing_Lateral_Stretch", "Overhead_Stretch"],
    "forearms": ["Kneeling_Forearm_Stretch"],
}

CARDIO_DEFAULT = ["cardio_bike_vertical", "cardio_eliptico", "cardio_esteira_caminhada", "cardio_remo"]
HIIT_PREF = ["cardio_air_bike", "cardio_bike_vertical", "cardio_remo", "cardio_eliptico", "cardio_spinning"]

SEC_PER_REP = 3.5        # tempo médio por repetição (excêntrica + concêntrica)
SUPERSET_SWITCH_S = 15   # troca entre os dois exercícios do bi-set
# bi-sets de antagonistas/regiões diferentes nos acessórios: economiza o descanso de um deles
SUPERSET_PAIRS = [("biceps", "triceps"), ("leg_curl", "leg_ext"), ("lateral", "rear_delt"), ("chest_fly", "rear_delt"),
                  ("calf", "core"), ("glute", "core"), ("adductor", "calf"), ("traps", "core"), ("biceps", "lateral"),
                  ("triceps", "rear_delt"), ("calf", "glute")]
TRANSITION_S = 45        # troca de aparelho/ajuste de carga entre exercícios
APPROACH_S = 120         # séries de aproximação do 1º exercício principal


# =============================================================== utilidades ===

def tier_for(months: float | None) -> str:
    m = float(months or 0)
    for key, t in TIERS.items():
        if t["months"][0] <= m < t["months"][1]:
            return key
    return "elite"


def _reps_bounds(reps) -> tuple[int, int]:
    if reps is None:
        return 10, 10
    nums = [int(n) for n in str(reps).replace("–", "-").split("-") if n.strip().isdigit()]
    if not nums:
        return 10, 10
    return min(nums), max(nums)


def item_time(it: dict) -> dict:
    """Tempo estimado (s) de um item, separado em trabalho, descanso e fase."""
    ex = catalog.get(it.get("exercise_id")) or {}
    phase = it.get("phase") or "principal"
    if ex.get("kind") == "cardio":
        iv = it.get("intervals") or {}
        if iv.get("repeats"):
            work = int(iv["repeats"]) * (int(iv.get("work_s") or 0) + int(iv.get("rest_s") or 0))
            work += int(float(it.get("warmup_min") or 0) * 60 + float(it.get("cooldown_min") or 0) * 60)
        else:
            work = int(float(it.get("duration_min") or 0) * 60)
        return {"phase": phase, "kind": "cardio", "work": work, "rest": 0, "total": work}
    sets = int(it.get("sets") or 1)
    if it.get("duration_s"):
        per = int(it["duration_s"])
    else:
        _, hi = _reps_bounds(it.get("reps"))
        per = int(hi * SEC_PER_REP)
    rest = int(it.get("rest_s") if it.get("rest_s") is not None else 60)
    work = sets * per
    rest_total = max(0, sets - 1) * rest + (TRANSITION_S if phase == "principal" else 5)
    if it.get("superset") == "A":  # 1º do bi-set: só a troca para o exercício B (o descanso vem depois do B)
        rest_total = sets * SUPERSET_SWITCH_S
    if it.get("approach_sets"):
        work += APPROACH_S
    return {"phase": phase, "kind": "forca", "work": work, "rest": rest_total, "total": work + rest_total}


def time_breakdown(items: list[dict], budget_min: float | None = None) -> dict:
    out = {"preparo": 0, "forca_trabalho": 0, "forca_descanso": 0, "cardio": 0, "pos": 0}
    for it in items:
        t = item_time(it)
        if t["phase"] == "preparo":
            out["preparo"] += t["total"]
        elif t["phase"] == "pos":
            out["pos"] += t["total"]
        elif t["kind"] == "cardio":
            out["cardio"] += t["total"]
        else:
            out["forca_trabalho"] += t["work"]
            out["forca_descanso"] += t["rest"]
    total = sum(out.values())
    res = {k: round(v / 60, 1) for k, v in out.items()}
    res["total"] = round(total / 60, 1)
    if budget_min:
        res["orcamento"] = budget_min
        res["folga"] = round(budget_min - total / 60, 1)
    return res


def _ok(ex_id: str, lims: list[dict], equipment: set[str] | None) -> str | None:
    """Status de limitação ('ok'/'cuidado') ou None se o exercício não pode ser usado."""
    ex = catalog.get(ex_id)
    if not ex:
        return None
    if equipment and ex.get("equipment") not in equipment and ex.get("equipment") not in (None, "body only", "other", "cardio machine"):
        return None
    st = check_exercise(ex, lims)["status"]
    return None if st == "evitar" else st


class _Picker:
    """Escolhe exercícios por padrão respeitando limitações, equipamento, nível e variedade."""

    def __init__(self, tier: dict, lims: list[dict], equipment: set[str] | None, seed: int):
        self.tier, self.lims, self.equipment, self.seed = tier, lims, equipment, seed
        self.used_week: dict[str, list[str]] = defaultdict(list)
        self.strength_focus = False

    def candidates(self, pattern: str) -> list[tuple[str, str]]:
        out = []
        pool = PATTERNS[pattern]
        if self.strength_focus and pattern == "hinge":
            pool = ["Barbell_Deadlift"] + [e for e in pool if e != "Barbell_Deadlift"]
        for eid in pool:
            st = _ok(eid, self.lims, self.equipment)
            if st:
                out.append((eid, st))
        pref = self.tier["equipment_pref"]
        if pref:
            rank = {e: i for i, e in enumerate(pref)}
            out.sort(key=lambda c: rank.get(catalog.get(c[0]).get("equipment"), 99))
        # "cuidado" vai para o fim da fila
        out.sort(key=lambda c: c[1] != "ok")
        return out

    def pick(self, pattern: str, role: str, used_day: set[str], variant: int) -> tuple[str, str] | None:
        cands = [c for c in self.candidates(pattern) if c[0] not in used_day]
        if not cands:
            return None
        fresh = [c for c in cands if c[0] not in self.used_week[pattern]]
        pool = fresh or cands
        if role == "main":
            idx = (self.seed) % min(2, len(pool))
        else:
            idx = (variant + self.seed) % len(pool)
        choice = pool[idx]
        self.used_week[pattern].append(choice[0])
        return choice


def rotate_accessories(items: list[dict], cycle: int, profile: dict, tier_key: str | None) -> list[dict]:
    """Variação por ciclo: nos ciclos pares, cada acessório troca por outra opção do mesmo padrão de movimento
    (respeitando limitações e equipamento); nos ímpares volta ao original. Os principais nunca mudam —
    são eles que medem a progressão — e cada variação mantém o próprio histórico de carga."""
    if not cycle or cycle < 2 or cycle % 2:
        return items
    tier = TIERS.get(tier_key or "intermediario") or TIERS["intermediario"]
    lims = profile.get("limitations") or []
    equipment = set(profile.get("equipment") or []) or None
    picker = _Picker(tier, lims, equipment, 0)
    out = copy.deepcopy(items)
    used = {it.get("exercise_id") for it in out}
    for it in out:
        if it.get("role") != "acc" or not it.get("pattern") or (it.get("phase") or "principal") != "principal":
            continue
        cands = [c for c, st in picker.candidates(it["pattern"]) if c not in used and st == "ok"]
        if not cands:
            continue
        it["rotated_from"] = it["exercise_id"]
        it["exercise_id"] = cands[0]
        used.add(cands[0])
    return out


PATTERN_MUSCLES = {
    "squat": ["quadriceps", "glutes"], "hinge": ["hamstrings", "glutes"], "single_leg": ["quadriceps", "glutes"],
    "leg_curl": ["hamstrings"], "leg_ext": ["quadriceps"], "glute": ["glutes"], "calf": ["calves"], "adductor": ["adductors"],
    "h_push": ["chest"], "incline_push": ["chest"], "chest_fly": ["chest"], "v_push": ["shoulders"],
    "lateral": ["shoulders"], "rear_delt": ["shoulders"], "v_pull": ["lats"], "h_pull": ["middle back", "lats"],
    "traps": ["traps"], "biceps": ["biceps"], "triceps": ["triceps"], "core": ["abdominals"],
}


def _heavy(template: str) -> set[str]:
    return {m for pat, role in DAY_TEMPLATES[template][1] if role in ("main", "sec") for m in PATTERN_MUSCLES[pat]}


def _adjacent_conflicts(days: list[int], split: list[str]) -> int:
    by = dict(zip(days, split))
    n = 0
    for d, t in by.items():
        nxt = by.get((d + 1) % 7)
        if nxt and _heavy(t) & _heavy(nxt):
            n += 1
    return n


def _choose_split(days: list[int], tier_key: str, goal: dict) -> list[str]:
    """Escolhe a divisão e a ordem que evitam treinar os mesmos músculos em dias seguidos."""
    import itertools

    n = len(days)
    preferred = _split(n, tier_key, goal)
    candidates = [preferred]
    alts = {2: [["UP_A", "LO_A"]], 3: [["PUSH", "PULL", "LEGS"], ["UP_A", "LO_A", "FB_C"]],
            4: [["PUSH", "PULL", "LEGS", "UP_A"]], 5: [["PUSH", "PULL", "LEGS", "UP_A", "LO_A"]]}
    if n == 3:
        alts[3] = [["UP_A", "LO_A", "FB_C"], ["FB_A", "FB_B", "FB_C"], ["PUSH", "PULL", "LEGS"]]  # PPL só se nada mais evita colar
    candidates += [c for c in alts.get(n, []) if c != preferred]
    best, best_key = preferred, None
    for ci, cand in enumerate(candidates):
        seen = set()
        for perm in itertools.permutations(cand):
            if perm in seen:
                continue
            seen.add(perm)
            key = (_adjacent_conflicts(days, list(perm)), ci, perm != tuple(cand))
            if best_key is None or key < best_key:
                best, best_key = list(perm), key
        if best_key and best_key[0] == 0:
            break
    return best


def _split(n: int, tier_key: str, goal: dict) -> list[str]:
    if n <= 1:
        return ["FB_A"]
    if n == 2:
        return ["FB_A", "FB_B"]
    if n == 3:
        # 2× por semana rende mais que 1× (PPL fica como alternativa para 3 dias seguidos)
        return ["UP_A", "LO_A", "FB_C"] if tier_key in ("avancado", "elite") and not goal.get("hybrid") else ["FB_A", "FB_B", "FB_C"]
    if n == 4:
        return ["UP_A", "LO_A", "UP_B", "LO_B"]
    if n == 5:
        return ["UP_A", "LO_A", "PUSH", "PULL", "LEGS"]
    return ["PUSH", "PULL", "LEGS", "PUSH", "PULL", "LEGS"]


def _spread(days: list[int], n: int) -> list[int]:
    """Escolhe n dias bem espaçados entre os disponíveis (ex.: 3 de seg–sex → seg, qua, sex)."""
    days = sorted(days)
    if n >= len(days):
        return days
    if n <= 0:
        return []
    if n == 1:
        return [days[0]]
    step = (len(days) - 1) / (n - 1)
    return sorted({days[int(i * step + 0.5)] for i in range(n)})


def _cardio_machine(lims, prefs, rotation: int, hiit: bool = False) -> str:
    base = HIIT_PREF if hiit else (prefs or CARDIO_DEFAULT)
    ok = [m for m in base if catalog.get(m) and check_exercise(catalog.get(m), lims)["status"] != "evitar"]
    if not ok:
        ok = [m for m in CARDIO_DEFAULT if check_exercise(catalog.get(m), lims)["status"] != "evitar"] or ["cardio_bike_horizontal"]
    return ok[rotation % len(ok)]


def _prep_items(regions: set[str], lims, rotation: int, warm_machine: str, warm_min: int) -> list[dict]:
    items = [{"exercise_id": warm_machine, "duration_min": warm_min, "zone": 1 if warm_min <= 5 else 2,
              "phase": "preparo", "notes": "aquecimento geral: elevar temperatura e FC"}]
    chosen: list[str] = []
    for region in ["inferior", "superior", "core"]:
        if region not in regions:
            continue
        pool = [m for m in MOBILITY[region] if catalog.get(m) and check_exercise(catalog.get(m), lims)["status"] != "evitar"]
        k = 2 if region != "core" else 1
        for i in range(k):
            if pool:
                cand = pool[(rotation + i) % len(pool)]
                if cand not in chosen:
                    chosen.append(cand)
    for eid in chosen[:3 if warm_min and warm_min <= 4 else 4]:  # sessão curta: preparo mais enxuto
        items.append({"exercise_id": eid, "sets": 1, "duration_s": 40, "rest_s": 10, "phase": "preparo",
                      "notes": "mobilidade/ativação dinâmica"})
    return items


def _post_items(muscle_load: dict[str, float], lims, minutes: float = 5) -> list[dict]:
    top = [m for m, v in sorted(muscle_load.items(), key=lambda kv: -kv[1]) if m in STRETCHES and v >= 1.5][:6]
    items = []
    per = 30
    for m in top:
        for sid in STRETCHES[m]:
            ex = catalog.get(sid)
            if ex and check_exercise(ex, lims)["status"] != "evitar":
                items.append({"exercise_id": sid, "sets": 1, "duration_s": per, "rest_s": 5, "phase": "pos",
                              "notes": "alongamento estático, respiração lenta (cada lado)"})
                break
    return items


def _muscle_load(items: list[dict]) -> dict[str, float]:
    load: dict[str, float] = defaultdict(float)
    for it in items:
        if (it.get("phase") or "principal") != "principal":
            continue
        ex = catalog.get(it.get("exercise_id"))
        if not ex or ex["kind"] != "strength":
            continue
        n = int(it.get("sets") or 0)
        for m in ex["primaryMuscles"]:
            load[m] += n * PRIMARY_WEIGHT
        for m in ex["secondaryMuscles"]:
            load[m] += n * SECONDARY_WEIGHT
    return load


# Músculos que recebem bastante estímulo indireto ou não precisam de volume direto alto
OPTIONAL_MUSCLES = {"traps", "forearms", "adductors", "abductors", "lower back"}


def weekly_targets(tier_key: str, goal_key: str, volume_factor: float = 1.0) -> dict[str, float]:
    t = TIERS[tier_key]
    g = GOALS.get(goal_key, GOALS["hipertrofia"])
    out = {}
    for m, info in MUSCLES.items():
        if m == "neck":
            continue
        v = t["volume"](info)
        if m in OPTIONAL_MUSCLES:
            v = min(v, info["mev"] + (info["mav"][0] - info["mev"]) * 0.3)
        elif m == "abdominals":  # recebe muito estímulo indireto (estabilização) nos compostos
            v = min(v, info["mev"] + (info["mav"][0] - info["mev"]) * 0.6)
        out[m] = round(v * g["volume"] * float(volume_factor or 1), 1)
    return out


# ================================================================== gerador ===

def _pair_supersets(items: list[dict]) -> int:
    """Junta acessórios em bi-sets (A → B → descanso) e os deixa lado a lado. Retorna o número de pares."""
    acc = [i for i in items if i.get("role") == "acc" and not i.get("superset") and not i.get("duration_s")]
    pairs = 0
    for a_pat, b_pat in SUPERSET_PAIRS:
        a = next((i for i in acc if i.get("pattern") == a_pat and not i.get("superset")), None)
        b = next((i for i in acc if i.get("pattern") == b_pat and not i.get("superset") and i is not a), None)
        if not a or not b:
            continue
        a["superset"], b["superset"] = "A", "B"
        a["rest_s"] = SUPERSET_SWITCH_S
        a["notes"] = ((a.get("notes") or "") + " · bi-set: emende com o próximo exercício").strip(" ·")
        b["notes"] = ((b.get("notes") or "") + " · bi-set: descanse só depois deste").strip(" ·")
        items.remove(b)
        items.insert(items.index(a) + 1, b)
        pairs += 1
    return pairs


def _fix_supersets(items: list[dict], acc_rest: int) -> None:
    """Desfaz bi-set que perdeu o par (o ajuste de tempo pode ter tirado um dos dois)."""
    for k, it in enumerate(items):
        if it.get("superset") == "A" and not (k + 1 < len(items) and items[k + 1].get("superset") == "B"):
            it.pop("superset")
            it["rest_s"] = acc_rest
            it["notes"] = (it.get("notes") or "").replace(" · bi-set: emende com o próximo exercício", "").replace(
                "bi-set: emende com o próximo exercício", "") or None
        if it.get("superset") == "B" and not (k > 0 and items[k - 1].get("superset") == "A"):
            it.pop("superset")
            it["notes"] = (it.get("notes") or "").replace(" · bi-set: descanse só depois deste", "").replace(
                "bi-set: descanse só depois deste", "") or None


def _leg_penalty(days: list[int], split: list[str], hard_leg_days: set[int]) -> int:
    """Perna pesada na véspera (ou no mesmo dia) de longão/qualidade de corrida, bike, remo ou trilha."""
    n = 0
    for d, t in zip(days, split):
        if t in HEAVY_LEGS and ((d + 1) % 7 in hard_leg_days or d in hard_leg_days):
            n += 1
    return n


def _choose_split_endurance(days: list[int], tier_key: str, goal: dict, hard_leg_days: set[int]) -> list[str]:
    import itertools

    base = _choose_split(days, tier_key, goal)
    best, best_key = base, None
    for perm in set(itertools.permutations(base)):
        by = dict(zip(days, perm))
        leg_leg = sum(1 for d, t in by.items() if t in HEAVY_LEGS and by.get((d + 1) % 7) in HEAVY_LEGS)  # perna 2 dias seguidos
        key = (_leg_penalty(days, list(perm), hard_leg_days) * 2 + _adjacent_conflicts(days, list(perm)) + 2 * leg_leg,
               list(perm) != base)
        if best_key is None or key < best_key:
            best, best_key = list(perm), key
    return best


def generate(profile: dict, seed: int = 0, recent: dict | None = None, today=None) -> dict:
    p = profile or {}
    lims = p.get("limitations") or []
    tier_key = tier_for(p.get("training_months") if p.get("training_months") is not None else
                        {"iniciante": 3, "intermediario": 12, "avancado": 36}.get(p.get("level"), 12))
    tier = TIERS[tier_key]
    goal_key = p.get("goal") if p.get("goal") in GOALS else "hipertrofia"
    goal = GOALS[goal_key]
    budget = float(p.get("session_minutes") or 60)
    # musculação + corrida (ou outro esporte) no mesmo treino: os minutos valem para o dia todo
    combine = bool(p.get("same_session"))
    avail = sorted(set(p.get("available_days") or _default_days(int(p.get("days_per_week") or 3))))
    equipment = set(p.get("equipment") or []) or None
    cardio_prefs = [c for c in (p.get("cardio_machines") or []) if catalog.get(c)]
    cardio_target = float(p.get("cardio_target_min") or goal["cardio_target"])
    mz = max_zone(lims) or 5
    hard_zone = min(mz, 4)
    rationale: list[str] = []

    # ---- modalidades de resistência (corrida, bike, natação, remo, caminhada, trilha, evento)
    from datetime import date as _date, timedelta as _td

    end_on = endurance.active(p)
    wp, alloc = None, None
    today = today or clock.today()
    week0 = today - _td(days=today.weekday())
    n_avail = len(avail)
    sess_override = None
    if end_on:
        # no máximo 2 treinos por dia: o que não couber nos dias livres sai (das modalidades com mais sessões)
        cfg0 = endurance.config(p)
        ev0 = ((p.get("endurance") or {}).get("event") or {}).get("type")
        n_str_guess = int(p.get("strength_days") or (2 if ev0 or n_avail < 4 else 3))
        n_str_guess = max(1, min(n_str_guess, tier["max_strength_days"], n_avail))
        room = 2 * n_avail - n_str_guess
        counts = {k: int(c["sessions"]) for k, c in cfg0.items()}
        if sum(counts.values()) > room:
            before = dict(counts)
            while sum(counts.values()) > room and any(v > 1 for v in counts.values()):
                k = max(counts, key=lambda x: (counts[x], x != "natacao"))
                counts[k] -= 1
            sess_override = counts
            cut = ", ".join(f"{endurance.sp.SPORTS[k]['name']} {before[k]}→{counts[k]}" for k in counts if counts[k] != before[k])
            rationale.append(f"Sessões de resistência demais para {n_avail} dias (máx. 2 treinos por dia): {cut}. "
                             "Libere mais dias para manter todas.")
        for k, c in cfg0.items():
            w = endurance.goal_warning(k, c)
            if w:
                rationale.append(w)
        # sessões fortes: deixa pelo menos 1 dia sem longo/forte de perna
        cfg_eff = endurance.config(p, sess_override)
        n_leg_longs = sum(1 for k, c in cfg_eff.items() if k in endurance.LEG_SPORTS and int(c["sessions"]) >= 2
                          or k in ("trilha",))
        max_hard = max(1, n_avail - n_leg_longs - 1)
        # slots para todas as sessões configuradas (semanas de pouco volume deixam alguns vazios)
        wp = endurance.week_plan(p, week0, week0, tier_key, recent=recent, sessions_override=sess_override, full_slots=True,
                                 max_hard=max_hard)
        slots = []
        for sport_key, d in wp["sports"].items():
            for i, sess in enumerate(d["sessions"]):
                slots.append({"key": f"{sport_key}:{i}", "sport": sport_key, "role": sess["endurance"]["role"],
                              "minutes": sess["duration_min"]})
        ev = ((p.get("endurance") or {}).get("event") or {}).get("type")
        pref = p.get("strength_days")
        if pref:
            n_strength = int(pref)
        else:
            # híbrido: 3 dias de força se sobrarem 3 dias sem longo/qualidade de perna (leves dividem o dia);
            # evento: 2 (manutenção)
            heavy = sum(1 for x in slots if x["role"] == "qualidade" or (x["role"] == "longo" and x["sport"] in endurance.LEG_SPORTS))
            n_strength = 2 if ev or n_avail < 4 or n_avail - heavy < 3 else 3
            if combine and not ev:
                # mesmo treino: o esporte vem depois da força, então cabem mais dias de força
                # (o longão fica num dia só dele)
                longs = sum(1 for x in slots if x["role"] == "longo" and x["sport"] in endurance.LEG_SPORTS)
                n_strength = min(tier["max_strength_days"], max(2, n_avail - min(longs, 1)),
                                 3 if goal.get("hybrid") else 9)
        n_strength = max(1, min(n_strength, tier["max_strength_days"], n_avail))
        long_pref = {k: int(c["long_day"]) for k, c in endurance.config(p).items() if c.get("long_day") not in (None, "")}
        alloc = endurance.allocate(slots, avail, n_strength, long_pref, combine=combine)
        strength_days = alloc["_strength"]
        cardio_days = []
        split = _choose_split_endurance(strength_days, tier_key, goal, set(alloc["_hard_leg_days"]))
    else:
        if goal.get("hybrid"):
            n_strength = min(tier["max_strength_days"], max(2, math.ceil(n_avail * 0.6)))
        else:
            n_strength = min(tier["max_strength_days"], n_avail)
        n_strength = max(1, n_strength)
        strength_days = _spread(avail, n_strength)
        cardio_days = [d for d in avail if d not in strength_days]
        split = _choose_split(strength_days, tier_key, goal)
    months = p.get("training_months")
    rationale.append(f"Tempo de prática {('%.0f meses' % months) if months is not None else '(nível do perfil)'} → "
                     f"nível {tier['label']}: {tier['sets']['main']} séries nos principais, reps "
                     f"{(goal['reps'] or tier['reps'])['main']}, RPE ~{tier['rpe']}, ciclo de {tier['cycle_weeks']} semanas.")
    rationale.append(f"Objetivo {goal['label']}: descanso ×{goal['rest']}, volume ×{goal['volume']}, meta de cardio "
                     f"{int(cardio_target)} min/semana.")
    rationale.append(f"{n_avail} dias livres ({', '.join(WEEKDAYS[d] for d in avail)}) → {n_strength} de força "
                     f"({', '.join(DAY_TEMPLATES[s][0] for s in split)})"
                     + (f" + {len(cardio_days)} de cardio" if cardio_days else "")
                     + (f" + {sum(len(d['sessions']) for d in wp['sports'].values())} sessões de resistência" if wp else "") + ".")
    if n_avail > tier["max_strength_days"] and not goal.get("hybrid"):
        rationale.append(f"Nível {tier['label']} rende melhor com até {tier['max_strength_days']} dias de força; "
                         "os outros dias viram cardio/recuperação ativa.")

    picker = _Picker(tier, lims, equipment, seed)
    picker.strength_focus = goal_key == "forca" and tier_key != "iniciante"
    reps = goal["reps"] or tier["reps"]
    rest_mult = goal["rest"]
    if tier_key == "iniciante" and goal["reps"]:
        reps = {k: (v if _reps_bounds(v)[0] >= 8 else "8-12") for k, v in reps.items()}  # iniciante não vai a 3–5 reps

    days: list[dict] = []
    variant_count: dict[str, int] = defaultdict(int)
    warm_min = 4 if budget <= 45 else 5 if budget < 75 else 8
    for di, (wd, key) in enumerate(zip(strength_days, split)):
        title, slots = DAY_TEMPLATES[key]
        variant = variant_count[key]
        variant_count[key] += 1
        if variant:
            title += " (variação)"
        used: set[str] = set()
        main_items: list[dict] = []
        for pattern, role in slots:
            ch = picker.pick(pattern, role, used, variant + di)
            if not ch:
                continue
            eid, st = ch
            used.add(eid)
            ex = catalog.get(eid)
            item = {"exercise_id": eid, "sets": tier["sets"][role], "rest_s": int(round(tier["rest"][role] * rest_mult / 15) * 15),
                    "role": role, "pattern": pattern, "phase": "principal", "rpe_target": tier["rpe"]}
            if ex.get("timed"):
                item["duration_s"] = tier["timed_s"]
            else:
                item["reps"] = reps[role]
            if st == "cuidado":
                item["notes"] = "cuidado: carga moderada e amplitude sem dor"
            main_items.append(item)
        if main_items:
            first = main_items[0]
            if first["role"] == "main" and not first.get("duration_s"):
                first["approach_sets"] = True
                first["notes"] = ((first.get("notes") or "") + " · aproximação: 1×8 a 50% e 1×4 a 75% antes das séries válidas").strip(" ·")
        _pair_supersets(main_items)
        regions = set()
        for it in main_items:
            ex = catalog.get(it["exercise_id"])
            for m in ex["primaryMuscles"]:
                regions.add("core" if MUSCLES.get(m, {}).get("region") == "core" else MUSCLES.get(m, {}).get("region", "superior"))
        prep = _prep_items(regions, lims, di, _cardio_machine(lims, cardio_prefs, di), warm_min)
        days.append({"name": f"{WEEKDAYS[wd]} – {title}", "weekday": wd, "kind": "forca", "template": key,
                     "items": prep + main_items})

    # ---- dias de cardio
    hiit_left = 0 if mz < 4 else goal["hiit"]
    for ci, wd in enumerate(cardio_days):
        next_is_legs = any(d["weekday"] == (wd + 1) % 7 and d["template"] in HEAVY_LEGS for d in days)
        prev_is_legs = any(d["weekday"] == (wd - 1) % 7 and d["template"] in HEAVY_LEGS for d in days)
        machine = _cardio_machine(lims, cardio_prefs, ci + 1)
        if hiit_left and not next_is_legs:
            hm = _cardio_machine(lims, cardio_prefs, ci, hiit=True)
            warm_c, cool_c = (8 if budget >= 45 else 5), (5 if budget >= 40 else 3)
            work_s = 30 if tier_key == "iniciante" else 40
            fit_reps = int((budget - 3 - warm_c - cool_c) * 60 // (work_s + 90))
            items = [{"exercise_id": hm, "warmup_min": warm_c, "cooldown_min": cool_c, "phase": "principal",
                      "intervals": {"repeats": max(3, min(6 if tier_key == "iniciante" else 8, fit_reps)), "work_s": work_s,
                                    "work_zone": hard_zone + (1 if hard_zone == 4 and mz >= 5 else 0), "rest_s": 90, "rest_zone": 1}}]
            name = "Intervalado (HIIT)"
            hiit_left -= 1
        else:
            items = [{"exercise_id": machine, "duration_min": int(min(30, budget - 5) // 5 * 5), "zone": min(2, mz), "phase": "principal",
                      "notes": "zona 2: dá para conversar em frases curtas"}]
            name = "Cardio zona 2" + (" (recuperação ativa)" if prev_is_legs else "")
        mob = _prep_items({"inferior", "superior"}, lims, ci + 3, machine, 0)[1:3]
        for m in mob:
            m["phase"] = "pos"
            m["notes"] = "mobilidade pós-cardio"
        days.append({"name": f"{WEEKDAYS[wd]} – {name}", "weekday": wd, "kind": "cardio", "template": "CARDIO",
                     "items": items + mob})
    if wp:
        lims_mz = max_zone(lims) or 5
        for sport_key, d in wp["sports"].items():
            for i, sess in enumerate(d["sessions"]):
                key = f"{sport_key}:{i}"
                wd = alloc[key]
                items = [sess]
                if sport_key in endurance.LEG_SPORTS:
                    mob = _prep_items({"inferior"}, lims, i + 5, sess["exercise_id"], 0)[1:3]
                    for m in mob:
                        m["phase"] = "pos"
                        m["notes"] = "mobilidade pós-treino"
                    items += mob
                title = sess["endurance"]["title"]
                days.append({"name": f"{WEEKDAYS[wd]} – {endurance.sp.SPORTS[sport_key]['name']}: {title}", "weekday": wd,
                             "kind": "resistencia", "template": "END", "double": key in alloc["_doubles"],
                             "endurance_slot": {"sport": sport_key, "role": sess["endurance"]["role"], "index": i},
                             "items": items})
        for b in wp.get("bricks") or []:
            bike_long = next((x for x in days if (x.get("endurance_slot") or {}).get("sport") == "ciclismo"
                              and x["endurance_slot"]["role"] == "longo"), None)
            if bike_long:
                bike_long["items"] = [i for i in bike_long["items"] if i.get("phase") != "pos"] + b["items"]
                bike_long["name"] += " + transição (brick)"
                bike_long["brick"] = True
        shared = {d["weekday"] for d in days if d.get("double")}
        for d in days:
            if d["weekday"] in shared:
                d["double"] = True
        if lims_mz < 5:
            rationale.append(f"Limitação de intensidade: sessões fortes de resistência vão no máximo à zona {lims_mz}.")
        # avisos de arranjo apertado e de meta acima do que cabe
        hard_leg = {d["weekday"] for d in days if d.get("kind") == "resistencia" and d["endurance_slot"]["sport"] in endurance.LEG_SPORTS
                    and d["endurance_slot"]["role"] in ("qualidade", "longo")}
        q_leg = {d["weekday"] for d in days if d.get("kind") == "resistencia" and d["endurance_slot"]["sport"] in endurance.LEG_SPORTS
                 and d["endurance_slot"]["role"] == "qualidade"}
        tight = any((w + 1) % 7 in hard_leg or (w - 1) % 7 in q_leg for w in q_leg) or any(
            d["kind"] == "forca" and d.get("template") in HEAVY_LEGS and (d["weekday"] + 1) % 7 in hard_leg for d in days)
        if tight:
            rationale.append("Agenda apertada: algumas sessões fortes ficaram em dias seguidos (ou perna pesada na véspera). "
                             "Dá para fazer, mas segure a intensidade; liberar mais um dia resolve.")
        for k, c in endurance.config(p, sess_override).items():
            g = c.get("goal") or {}
            if g.get("type") == "volume" and g.get("weekly"):
                cap = min(endurance.weekly_cap(k, int(c["sessions"])),
                          endurance.session_max(k, endurance.sp.anchors_of(p), c.get("env"))
                          * (1 if int(c["sessions"]) <= 1 else (0.55 + 0.45 * (int(c["sessions"]) - 1)) * 0.9))
                if float(g["weekly"]) > cap * 1.02:
                    u = endurance.VOLUME[k]["unit"]
                    rationale.append(f"Meta de {endurance.sp.SPORTS[k]['name'].lower()} ({g['weekly']:g} {u}/semana) não cabe em "
                                     f"{c['sessions']} sessão(ões) — máx. ~{endurance._round_vol(k, cap):g} {u}. Aumente as sessões.")
        _end_rationale(rationale, wp, alloc, p, budget)
        leg_end = sum(1 for d in days if d.get("kind") == "resistencia" and d["endurance_slot"]["sport"] in endurance.LEG_SPORTS)
    if cardio_days and goal.get("hybrid"):
        rationale.append("Híbrido: intervalado longe da véspera de pernas, cardio sempre depois da força e em "
                         "aparelhos de baixo impacto para reduzir a interferência no ganho de força.")

    if combine and wp:
        # corrida leve depois de treino de superior (não de perna pesada): troca o dia se houver um livre
        def _wd_name(d, wd):
            d["weekday"] = wd
            d["name"] = f"{WEEKDAYS[wd]} – " + d["name"].split(" – ", 1)[1]
        for x in [d for d in days if d.get("kind") == "resistencia"]:
            slot = x["endurance_slot"]
            if slot["role"] != "leve" or slot["sport"] not in endurance.LEG_SPORTS:
                continue
            here = next((d for d in days if d["kind"] == "forca" and d["weekday"] == x["weekday"]), None)
            if not here or here.get("template") not in HEAVY_LEGS:
                continue
            free = [d for d in days if d["kind"] == "forca" and d.get("template") not in HEAVY_LEGS
                    and not any(e.get("kind") == "resistencia" and e["weekday"] == d["weekday"] for e in days)]
            if free:
                _wd_name(x, free[0]["weekday"])
        n_comb = 0
        for d in days:
            if d["kind"] != "forca":
                continue
            ends = [x for x in days if x.get("kind") == "resistencia" and x["weekday"] == d["weekday"]]
            if not ends:
                continue
            end_min = sum(float(x["items"][0].get("duration_min") or 0) for x in ends)
            d["budget"] = max(STRENGTH_MIN_COMBINED, budget - end_min - 3)  # 3 min de transição
            d["with_endurance"] = True
            for x in ends:
                x["same_session"] = True
                x["items"][0]["notes"] = ("logo depois da musculação (mesmo treino)" + (" · " + x["items"][0]["notes"]
                                          if x["items"][0].get("notes") else ""))
            n_comb += 1
        if n_comb:
            rationale.append(f"Musculação + esporte no mesmo treino em {n_comb} dia(s): a força vem primeiro e encolhe para "
                             f"o esporte caber nos {int(budget)} min (força antes protege o ganho muscular; o aquecimento "
                             "da musculação já serve para a corrida).")

    # ---- ajuste ao tempo e balanceamento de volume
    targets = weekly_targets(tier_key, goal_key, p.get("volume_factor") or 1.0)
    if wp and leg_end >= 3:
        for m in ("quadriceps", "hamstrings", "glutes", "calves"):
            if m in targets:
                targets[m] = round(targets[m] * 0.8, 1)
        rationale.append(f"{leg_end} sessões de resistência com as pernas na semana → volume de musculação das pernas "
                         "−20% para não comprometer a recuperação (efeito de interferência).")
    cardio_from_days = min(cardio_target, len(cardio_days) * min(60, budget - 5))
    if wp:
        cardio_from_days = _cardio_equiv([d for d in days if d.get("kind") == "resistencia"])
    reserve = 0.0
    if cardio_from_days < cardio_target * 0.9 and n_strength:
        reserve = round(budget * CARDIO_SHARE[goal_key] / 5) * 5
        if reserve >= 5:
            rationale.append(f"Reservados ~{int(reserve)} min de cada treino de força para cardio (objetivo {goal['label']}).")
    strength_budget = budget - reserve
    _fit_time([d for d in days if d["kind"] == "forca"], strength_budget, all_days=days, targets=targets)
    _fit_time([d for d in days if d["kind"] == "cardio"], budget)
    adj = _balance(days, targets, strength_budget)
    added = _add_complements(days, targets, strength_budget, picker, tier, reps, rest_mult)
    if added:
        adj += _balance(days, targets, strength_budget)
        rationale.append("Complementos adicionados onde sobrou tempo: " + ", ".join(added) + ".")
    if adj:
        rationale.append(f"Balanceamento de volume: {adj} ajustes de séries para aproximar cada músculo do alvo semanal "
                         "e manter puxar ≥ empurrar.")

    # ---- cardio: finalizadores até a meta
    _cardio_fill(days, cardio_target, budget, lims, cardio_prefs, mz, goal)

    acc_rest = int(round(tier["rest"]["acc"] * rest_mult / 15) * 15)
    # ---- pós-treino (alongamento) com o que sobrar de tempo (mín. 3 min)
    for d in days:
        if d["kind"] != "forca":
            continue
        d["items"] += _post_items(_muscle_load(d["items"]), lims, 5)
        for _ in range(3):  # bi-set que perde o par recupera o descanso: reajusta até caber
            _fit_time([d], budget, protect_post=True, all_days=days, targets=targets)
            _fix_supersets(d["items"], acc_rest)
            if _day_total(d) <= _b(d, budget):
                break

    days.sort(key=lambda d: d["weekday"])
    for d in days:
        d["time"] = time_breakdown(d["items"], _b(d, budget))
    week_cardio = _cardio_equiv(days)
    if week_cardio < cardio_target * 0.9:
        falta = int(cardio_target - week_cardio)
        rationale.append(f"Cardio planejado: ~{week_cardio} de {int(cardio_target)} min equivalentes — não coube no tempo das "
                         f"sessões. Complete com ~{falta} min de caminhada/bike leve em outro horário, aumente os minutos "
                         "por sessão ou libere mais um dia.")
    else:
        rationale.append(f"Cardio planejado: ~{week_cardio} min equivalentes/semana (meta {int(cardio_target)}).")
    vol = _weekly_volume(days)
    short = [MUSCLES[m]["pt"] for m, t in targets.items() if t >= 4 and vol.get(m, 0) < 0.7 * t]
    if short and n_strength:
        weekly_min = int(sum(d["time"]["total"] for d in days if d["kind"] == "forca"))
        rationale.append(f"Com ~{weekly_min} min/semana de musculação, priorizei os grandes grupos; abaixo do ideal: "
                         f"{', '.join(short)}. Mais um dia ou sessões mais longas resolvem.")
    rationale.append(f"Todo treino de força começa com aquecimento ({warm_min} min) + mobilidade/ativação dos músculos do dia "
                     "e termina com alongamento dos músculos mais trabalhados.")
    rationale.append(f"Sessões de até {int(budget)} min: tempo calculado por repetição (~{SEC_PER_REP}s), descanso entre séries e trocas de aparelho.")
    if lims:
        rationale.append("Limitações aplicadas: exercícios 'evitar' nunca entram; 'cuidado' só quando não há alternativa"
                         + (f"; cardio limitado à zona {mz}." if mz < 5 else "."))

    return {
        "name": f"Auto – {goal['label']} ({tier['label']})",
        "days_per_week": len(days),
        "days": days,
        "generator": {
            "tier": tier_key, "tier_label": tier["label"], "goal": goal_key, "goal_label": goal["label"],
            "cycle_weeks": tier["cycle_weeks"], "ramp_sets": tier["ramp_sets"], "seed": seed,
            "session_minutes": budget, "available_days": avail, "targets": targets,
            "cardio_target": cardio_target, "cardio_planned_equiv": week_cardio, "rpe": tier["rpe"],
        },
        **({"endurance_plan": {"start": week0.isoformat(), "tier": tier_key, "sessions": sess_override, "max_hard": max_hard,
                               "start_volumes": {k: v for k, v in (recent or {}).items() if k in (p.get("endurance") or {}).get("sports", {})} or None,
                               "week0": {k: {kk: v[kk] for kk in ("volume", "unit", "phase", "phase_pt")} for k, v in wp["sports"].items()}}}
           if wp else {}),
        "goals_week": {
            "sessions": len(days), "strength_sessions": n_strength, "cardio_equiv_min": cardio_target,
            "minutes_total": round(sum(d["time"]["total"] for d in days)),
            "sets_by_muscle": {m: {"planned": round(vol.get(m, 0), 1), "target": t} for m, t in targets.items()},
        },
        "rationale": rationale,
    }


def _default_days(n: int) -> list[int]:
    presets = {1: [0], 2: [0, 3], 3: [0, 2, 4], 4: [0, 1, 3, 4], 5: [0, 1, 2, 3, 4], 6: [0, 1, 2, 3, 4, 5], 7: list(range(7))}
    return presets.get(max(1, min(7, n)), [0, 2, 4])


def _day_total(d: dict) -> float:
    return sum(item_time(i)["total"] for i in d["items"]) / 60


def _surplus_ratio(it: dict, vol: dict[str, float], targets: dict[str, float] | None) -> float:
    """Quanto o(s) músculo(s) principal(is) do exercício já passam do alvo semanal (maior = pode cortar)."""
    if not targets:
        return 1.0
    ex = catalog.get(it["exercise_id"]) or {}
    ratios = [vol.get(m, 0) / targets[m] + (1.0 if m in OPTIONAL_MUSCLES else 0.0)
              for m in ex.get("primaryMuscles", []) if targets.get(m)]
    return min(ratios) if ratios else 9.0


STRENGTH_MIN_COMBINED = 40  # abaixo disso a musculação do dia vira só manutenção


def _b(d: dict, default: float) -> float:
    """Tempo disponível para a parte de força do dia (menor quando o esporte vem logo depois)."""
    return float(d["budget"]) if d.get("budget") else default


def _fit_time(days: list[dict], budget: float, protect_post: bool = False,
              all_days: list[dict] | None = None, targets: dict[str, float] | None = None) -> None:
    """Corta até caber no tempo. Corta primeiro o que é de músculo que já está sobrando na semana."""
    for d in days:
        guard = 0
        while _day_total(d) > _b(d, budget) + 0.05 and guard < 120:
            guard += 1
            items = d["items"]
            vol = _weekly_volume(all_days or days)
            by_surplus = lambda xs: sorted(xs, key=lambda i: -_surplus_ratio(i, vol, targets))  # noqa: E731
            post = [i for i in items if i.get("phase") == "pos"]
            if protect_post and len(post) > 4:
                items.remove(post[-1])
                continue
            accs = [i for i in items if i.get("role") == "acc"]
            secs = [i for i in items if i.get("role") == "sec"]
            mains = [i for i in items if i.get("role") == "main"]
            mob = [i for i in items if i.get("phase") == "preparo" and i.get("duration_s")]
            warm = next((i for i in items if i.get("phase") == "preparo" and i.get("duration_min")), None)
            fin = [i for i in items if i.get("finisher")]
            if fin:
                f = fin[-1]
                if float(f.get("duration_min") or 0) > 10:
                    f["duration_min"] = float(f["duration_min"]) - 5
                else:
                    items.remove(f)
                continue
            cand = next((i for i in by_surplus(accs) if i["sets"] > 2), None) \
                or next((i for i in by_surplus(secs) if i["sets"] > 2), None)
            if cand:
                cand["sets"] -= 1
                continue
            if len(accs) > 1:
                items.remove(by_surplus(accs)[0])
                continue
            if len(mob) > 2:
                items.remove(mob[-1])
                continue
            cand = next((i for i in by_surplus(mains) if i["sets"] > 2), None)
            if cand:
                cand["sets"] -= 1
                continue
            if warm and float(warm["duration_min"]) > 3:
                warm["duration_min"] = 3
                continue
            if accs and len(mains) + len(secs) >= 3:
                items.remove(accs[0])
                continue
            if len(mob) > 1:
                items.remove(mob[-1])
                continue
            if len(secs) > 1:
                items.remove(by_surplus(secs)[0])
                continue
            r = next((i for i in items if i.get("phase") == "principal" and (i.get("rest_s") or 0) > 60), None)
            if r:
                r["rest_s"] -= 15
                continue
            if protect_post and len(post) > 2:
                items.remove(post[-1])
                continue
            if secs and len(mains) >= 2:
                items.remove(secs[0])
                continue
            break


def _weekly_volume(days: list[dict]) -> dict[str, float]:
    vol: dict[str, float] = defaultdict(float)
    for d in days:
        for m, v in _muscle_load(d["items"]).items():
            vol[m] += v
    return vol


PUSH_MUSCLES = ("chest", "triceps")
PULL_MUSCLES = ("lats", "middle back", "biceps")


def _push_pull(vol: dict[str, float]) -> float:
    push = vol.get("chest", 0) + vol.get("triceps", 0) + 0.5 * vol.get("shoulders", 0)
    pull = vol.get("lats", 0) + vol.get("middle back", 0) + vol.get("biceps", 0) + 0.5 * vol.get("traps", 0)
    return pull / push if push else 9.0


MUSCLE_PATTERN = {
    "chest": "chest_fly", "lats": "v_pull", "middle back": "h_pull", "shoulders": "lateral", "biceps": "biceps",
    "triceps": "triceps", "quadriceps": "leg_ext", "hamstrings": "leg_curl", "glutes": "glute", "calves": "calf",
    "abdominals": "core", "adductors": "adductor", "traps": "traps",
}


def _add_complements(days, targets, budget, picker, tier, reps, rest_mult, limit: int = 8) -> list[str]:
    """Onde sobrou tempo, adiciona um exercício para o músculo mais abaixo do alvo semanal."""
    added: list[str] = []
    for _ in range(limit):
        vol = _weekly_volume(days)
        deficits = sorted(((vol.get(m, 0) / t, m) for m, t in targets.items() if t >= 3 and m in MUSCLE_PATTERN
                           and vol.get(m, 0) < 0.8 * t))
        if not deficits:
            break
        placed = False
        for _, m in deficits:
            pattern = MUSCLE_PATTERN[m]
            strength = [d for d in days if d["kind"] == "forca"]
            for d in sorted(strength, key=_day_total):
                used = {i["exercise_id"] for i in d["items"]}
                # não coloca o músculo colado em outro dia que já o treina pesado
                neighbors = [x for x in strength if x is not d and abs(x["weekday"] - d["weekday"]) in (1, 6)]
                if any(_muscle_load(x["items"]).get(m, 0) >= 4 for x in neighbors):
                    continue
                ch = picker.pick(pattern, "acc", used, len(d["items"]))
                if not ch:
                    continue
                ex = catalog.get(ch[0])
                item = {"exercise_id": ch[0], "sets": max(2, tier["sets"]["acc"] - 1), "role": "acc", "pattern": pattern,
                        "phase": "principal", "rpe_target": tier["rpe"], "complement": True,
                        "rest_s": int(round(tier["rest"]["acc"] * rest_mult / 15) * 15)}
                if ex.get("timed"):
                    item["duration_s"] = tier["timed_s"]
                else:
                    item["reps"] = reps["acc"]
                if _day_total({"items": d["items"] + [item]}) <= _b(d, budget):
                    posts = [i for i in d["items"] if i.get("phase") == "pos" or i.get("finisher")]
                    d["items"].insert(d["items"].index(posts[0]) if posts else len(d["items"]), item)
                    added.append(f"{ex['name']} ({_short(d['name'])})")
                    placed = True
                    break
            if placed:
                break
        if not placed:
            break
    return added


def _short(name: str) -> str:
    return name.split(" – ")[-1]


def _balance(days: list[dict], targets: dict[str, float], budget: float) -> int:
    """Sobe séries de músculos abaixo do alvo, corta de quem passou do máximo e mantém puxar ≥ empurrar."""
    changes = 0

    def prim(i):
        return (catalog.get(i["exercise_id"]) or {}).get("primaryMuscles", [])

    def can_add(d, it):
        extra = item_time({**it, "sets": 1, "approach_sets": False})["work"] / 60 + (it.get("rest_s") or 60) / 60
        return d["kind"] == "forca" and _day_total(d) + extra <= _b(d, budget)

    for _ in range(80):
        vol = _weekly_volume(days)
        over = [(m, vol[m] - MUSCLES[m]["mrv"]) for m in vol if m in MUSCLES and vol[m] > MUSCLES[m]["mrv"]]
        if over:
            m = max(over, key=lambda x: x[1])[0]
            cands = [(d, i) for d in days for i in d["items"] if i.get("role") and m in prim(i)]
            cands.sort(key=lambda di: ({"acc": 0, "sec": 1, "main": 2}[di[1]["role"]], -di[1]["sets"]))
            cut = next(((d, i) for d, i in cands if i["sets"] > (3 if i["role"] == "main" else 2)), None)
            if cut:
                cut[1]["sets"] -= 1
                changes += 1
                continue
            rm = next(((d, i) for d, i in cands if i["role"] == "acc"), None)
            if rm:
                rm[0]["items"].remove(rm[1])
                changes += 1
                continue
        # puxar ≥ empurrar (saúde do ombro e postura)
        if _push_pull(vol) > 1.6 and vol.get("chest", 0) > 0:
            # puxar muito acima de empurrar: tira de acessório de puxar ou soma em empurrar
            pulls = [(d, i) for d in days for i in d["items"] if i.get("role") in ("acc", "sec")
                     and set(prim(i)) & set(PULL_MUSCLES) and i["sets"] > 2]
            if pulls:
                pulls.sort(key=lambda di: -di[1]["sets"])
                pulls[0][1]["sets"] -= 1
                changes += 1
                continue
            pushes = [(d, i) for d in days for i in d["items"] if i.get("role") and set(prim(i)) & set(PUSH_MUSCLES)
                      and i["sets"] < (5 if i["role"] == "main" else 4)]
            added = next(((d, i) for d, i in sorted(pushes, key=lambda di: _day_total(di[0])) if can_add(d, i)), None)
            if added:
                added[1]["sets"] += 1
                changes += 1
                continue
        if _push_pull(vol) < 1.0 and vol.get("chest", 0) > 0:
            pulls = [(d, i) for d in days for i in d["items"] if i.get("role") and set(prim(i)) & set(PULL_MUSCLES)
                     and i["sets"] < (5 if i["role"] == "main" else 4)]
            pulls.sort(key=lambda di: (_day_total(di[0]), di[1]["sets"]))
            added = next(((d, i) for d, i in pulls if can_add(d, i)), None)
            if added:
                added[1]["sets"] += 1
                changes += 1
                continue
            pushes = [(d, i) for d in days for i in d["items"] if i.get("role") in ("acc", "sec")
                      and set(prim(i)) & set(PUSH_MUSCLES) and i["sets"] > 2]
            if pushes:
                pushes[0][1]["sets"] -= 1
                changes += 1
                continue
        deficits = sorted(((targets[m] - vol.get(m, 0)) / max(1, targets[m]), m) for m in targets)
        deficits = [x for x in deficits if x[0] > 0.1]
        if not deficits:
            break
        done = False
        for _, m in reversed(deficits):
            cands = [(d, i) for d in days for i in d["items"]
                     if i.get("phase") == "principal" and i.get("role") and m in prim(i)
                     and i["sets"] < (5 if i["role"] == "main" else 4)]
            cands.sort(key=lambda di: (_day_total(di[0]), di[1]["sets"]))
            for d, it in cands:
                ex = catalog.get(it["exercise_id"])
                if any(vol.get(x, 0) + 1 > MUSCLES[x]["mrv"] for x in ex["primaryMuscles"] if x in MUSCLES):
                    continue
                if not can_add(d, it):
                    # troca no mesmo treino: tira 1 série de um músculo que já passou do alvo
                    donor = next((i for i in sorted(d["items"], key=lambda x: -(x.get("sets") or 0))
                                  if i is not it and i.get("role") in ("acc", "sec") and i["sets"] > 2
                                  and m not in prim(i) and prim(i)
                                  and all(vol.get(x, 0) - 1 >= 1.15 * targets.get(x, 0) for x in prim(i))), None)
                    if not donor:
                        continue
                    donor["sets"] -= 1
                    if not can_add(d, it):
                        donor["sets"] += 1
                        continue
                it["sets"] += 1
                changes += 1
                done = True
                break
            if done:
                break
        if not done:
            break
    return changes


def _cardio_equiv(days: list[dict]) -> float:
    total = 0.0
    for d in days:
        for it in d["items"]:
            ex = catalog.get(it.get("exercise_id")) or {}
            if ex.get("kind") != "cardio":
                continue
            iv = it.get("intervals")
            if iv and iv.get("repeats"):
                work = iv["repeats"] * iv.get("work_s", 0) / 60
                easy = (iv["repeats"] * iv.get("rest_s", 0)) / 60 + float(it.get("warmup_min") or 0) + float(it.get("cooldown_min") or 0)
                total += 2 * work + 0.5 * easy
            else:
                z = int(it.get("zone") or 2)
                mins = float(it.get("duration_min") or 0)
                total += mins * {"leve": 0.5, "moderado": 1, "vigoroso": 2}[HR_ZONES[z]["intensity"]]
    return round(total)


def _cardio_fill(days, target, budget, lims, prefs, mz, goal) -> None:
    """Completa a meta semanal de cardio com finalizadores e estendendo dias de cardio."""
    for _ in range(30):
        missing = target - _cardio_equiv(days)
        if missing <= 0:
            return
        progressed = False
        for d in [x for x in days if x["kind"] == "cardio"]:
            z2 = next((i for i in d["items"] if i.get("duration_min") and i.get("phase") == "principal"), None)
            if not z2 and budget - _day_total(d) >= 10:  # dia de intervalado: completa com zona 2
                z2 = {"exercise_id": _cardio_machine(lims, prefs, 2), "duration_min": 5, "zone": min(2, mz),
                      "phase": "principal", "notes": "zona 2 depois do intervalado"}
                posts = [i for i in d["items"] if i.get("phase") == "pos"]
                d["items"].insert(d["items"].index(posts[0]) if posts else len(d["items"]), z2)
                progressed = True
                break
            if z2 and z2["duration_min"] < 60 and _day_total(d) + 5 <= budget:
                z2["duration_min"] += 5
                progressed = True
                break
        if progressed:
            continue
        for d in sorted([x for x in days if x["kind"] == "forca" and not x.get("with_endurance")], key=_day_total):
            slack = budget - _day_total(d) - 4  # reserva para o alongamento
            if slack < 5:
                continue
            fin = next((i for i in d["items"] if i.get("finisher")), None)
            if fin:
                if fin["duration_min"] < 25 and slack >= 5:
                    fin["duration_min"] += 5
                    progressed = True
                    break
                continue
            machine = _cardio_machine(lims, prefs, len(d["items"]))
            d["items"].append({"exercise_id": machine, "duration_min": min(15, int(slack // 5 * 5)), "zone": min(2, mz),
                               "phase": "principal", "finisher": True, "notes": "cardio pós-força (zona 2)"})
            progressed = True
            break
        if not progressed:
            return


def _end_rationale(rationale: list[str], wp: dict, alloc: dict, p: dict, budget: float) -> None:
    from .sports import SPORTS, anchors_of

    unit_pt = {"km": "km", "h": "h", "m": "m"}
    parts = []
    for sport_key, d in wp["sports"].items():
        vol = d["volume"]
        vol_txt = f"{vol:g} {unit_pt[d['unit']]}" if d["unit"] != "m" else f"{int(vol)} m"
        wtr = d.get("weeks_to_race")
        parts.append(f"{SPORTS[sport_key]['name']}: {vol_txt} na semana ({d['phase_pt']}"
                     + (f", faltam {wtr} semanas para a prova" if wtr not in (None, 0) and wtr > 0 else "") + ")")
    rationale.append("Resistência — " + "; ".join(parts) + ". Progressão de até ~8%/semana, semana leve a cada 4ª.")
    hard = sum(1 for d in wp["sports"].values() for x in d["sessions"] if x["endurance"]["hard"])
    total = sum(len(d["sessions"]) for d in wp["sports"].values())
    if total:
        rationale.append(f"Distribuição polarizada: {total - hard} de {total} sessões leves e {hard} fortes "
                         "(no máximo 3 fortes por semana, longe do longão e nunca na véspera de perna pesada).")
    if alloc.get("_doubles"):
        rationale.append("Dias com dois treinos (força + resistência): faça a musculação primeiro ou separe por 6 h+; "
                         "natação e treinos leves são os que melhor combinam.")
    over = [x for d in wp["sports"].values() for x in d["sessions"] if x["duration_min"] > budget + 5]
    if over:
        longs = [x for x in over if x["endurance"]["role"] == "longo"]
        rationale.append("⚠ Passam do seu tempo de sessão (" + str(int(budget)) + " min): "
                         + ", ".join(f"{x['endurance']['title']} ~{x['duration_min']} min" for x in over)
                         + (". O longo é o treino que mais desenvolve a resistência: fica no fim de semana e o app não corta — "
                            "reserve esse tempo (ou use 'Mover' para outro dia livre)." if longs else
                            ". Mantidas inteiras para cumprir o volume da semana — reserve esse tempo."))
    a = anchors_of(p)
    missing = [SPORTS[s]["name"] for s in wp["sports"] if s in ("corrida", "ciclismo", "natacao", "remo")
               and not a.get({"corrida": "run_vdot", "ciclismo": "ftp_w", "natacao": "css_s100", "remo": "row_2k_s"}[s])]
    if missing:
        rationale.append("Sem referência de ritmo/potência em " + ", ".join(missing) + ": os alvos saem por frequência "
                         "cardíaca e o app agenda um teste nas primeiras semanas para calibrar.")
    if wp.get("bricks"):
        rationale.append("Brick semanal (corrida logo após o pedal longo) para treinar a transição.")
