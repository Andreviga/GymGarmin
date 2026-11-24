"""Academia da pessoa: quais aparelhos provavelmente existem lá e qual o substituto mais parecido quando falta um.

Nenhuma academia publica a lista de aparelhos de cada unidade. Por isso cada academia tem um perfil provável
(kit: musculação de alto nível, boutique, rede premium, completa, básica) e o app aprende com a pessoa:
- gym_missing: aparelhos que ela disse que não tem → nunca mais entram no treino;
- gym_has: aparelhos que ela confirmou → entram mesmo se forem raros para aquele tipo de academia.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import catalog

DATA = Path(__file__).resolve().parent / "data"
AVAIL_LEVEL = {"comum": 1, "média": 2, "rara": 3}

# substituto mais parecido, em ordem: mesmo movimento, mesmo músculo alongado, mesmo tipo de apoio/carga
CLOSEST: dict[str, list[str]] = {
    "Pendulum_Squat": ["VSquat_Machine", "Hack_Squat", "Reverse_Hack_Squat", "LeverSquat_Machine", "Belt_Squat", "Leg_Press"],
    "VSquat_Machine": ["Pendulum_Squat", "Hack_Squat", "LeverSquat_Machine", "Belt_Squat", "Leg_Press"],
    "Belt_Squat": ["LeverSquat_Machine", "Hack_Squat", "Pendulum_Squat", "Leg_Press", "Goblet_Squat"],
    "LeverSquat_Machine": ["Hack_Squat", "Belt_Squat", "Pendulum_Squat", "Smith_Machine_Squat", "Leg_Press"],
    "Reverse_Hack_Squat": ["Hack_Squat", "Pendulum_Squat", "Leg_Press"],
    "Hack_Squat": ["Pendulum_Squat", "VSquat_Machine", "LeverSquat_Machine", "Leg_Press", "Smith_Machine_Squat", "Barbell_Squat"],
    "Leg_Press": ["Horizontal_Leg_Press", "Hack_Squat", "VerticalLegPress_Machine", "Smith_Machine_Squat", "Goblet_Squat"],
    "Horizontal_Leg_Press": ["Leg_Press", "Hack_Squat", "Smith_Machine_Squat", "Goblet_Squat"],
    "VerticalLegPress_Machine": ["Leg_Press", "Horizontal_Leg_Press", "Hack_Squat"],
    "Single-Leg_Leg_Press": ["Horizontal_Leg_Press_Unilateral", "Split_Squat_with_Dumbbells", "Dumbbell_Rear_Lunge"],
    "ReclinedLegExtension_Machine": ["Leg_Extensions", "SissySquatBench"],
    "SissySquatBench": ["Leg_Extensions", "ReclinedLegExtension_Machine"],
    "Leg_Extensions": ["ReclinedLegExtension_Machine", "SissySquatBench", "Split_Squat_with_Dumbbells"],
    "KneelingLegCurl_Machine": ["Seated_Leg_Curl", "Lying_Leg_Curls", "NordicBench"],
    "Seated_Leg_Curl": ["KneelingLegCurl_Machine", "Lying_Leg_Curls", "NordicBench", "Romanian_Deadlift"],
    "Lying_Leg_Curls": ["Seated_Leg_Curl", "KneelingLegCurl_Machine", "NordicBench", "Romanian_Deadlift"],
    "NordicBench": ["Seated_Leg_Curl", "Lying_Leg_Curls", "Glute_Ham_Raise"],
    "Hip_Thrust_Machine": ["StandingHipThrust_Machine", "Smith_Machine_Hip_Thrust", "Barbell_Hip_Thrust", "Glute_Kickback_Machine"],
    "StandingHipThrust_Machine": ["Hip_Thrust_Machine", "Smith_Machine_Hip_Thrust", "Barbell_Hip_Thrust", "Pull_Through"],
    "Glute_Kickback_Machine": ["Kneeling_Hip_Extension_Machine", "One-Legged_Cable_Kickback", "Hip_Thrust_Machine"],
    "Kneeling_Hip_Extension_Machine": ["Glute_Kickback_Machine", "One-Legged_Cable_Kickback"],
    "BackExtensionMachine": ["Hyperextensions_Back_Extensions", "Pull_Through", "Romanian_Deadlift"],
    "Hack_Machine_Calf_Raise": ["Standing_Calf_Raises", "Calf_Press_On_The_Leg_Press_Machine", "Seated_Calf_Raise"],
    "Standing_Calf_Raises": ["Hack_Machine_Calf_Raise", "Calf_Press_On_The_Leg_Press_Machine", "Smith_Machine_Calf_Raise"],
    "Seated_Calf_Raise": ["Standing_Calf_Raises", "Calf_Press_On_The_Leg_Press_Machine"],
    "Thigh_Adductor": ["StandingHipAdduction_Machine", "Cable_Hip_Adduction"],
    "Thigh_Abductor": ["StandingHipAbduction_Machine", "Cable_Standing_Hip_Abduction"],
    "Leverage_Chest_Press": ["Machine_Bench_Press", "Dumbbell_Bench_Press", "Barbell_Bench_Press_-_Medium_Grip"],
    "Machine_Bench_Press": ["Leverage_Chest_Press", "Dumbbell_Bench_Press", "Barbell_Bench_Press_-_Medium_Grip"],
    "Leverage_Incline_Chest_Press": ["Incline_Dumbbell_Press", "LandmineStation", "Machine_Bench_Press"],
    "InclineFlyMachine": ["Low_Cable_Crossover", "Incline_Cable_Flye", "Butterfly", "Incline_Dumbbell_Flyes"],
    "Butterfly": ["Cable_Crossover", "InclineFlyMachine", "Low_Cable_Crossover", "Dumbbell_Flyes"],
    "Machine_Shoulder_Military_Press": ["VikingPress_Machine", "Seated_Dumbbell_Press", "Dumbbell_Shoulder_Press"],
    "VikingPress_Machine": ["Machine_Shoulder_Military_Press", "Seated_Dumbbell_Press", "LandmineStation"],
    "Machine_Lateral_Raise": ["Cable_Seated_Lateral_Raise", "Cable_Y_Raise", "Side_Lateral_Raise"],
    "Reverse_Machine_Flyes": ["Cable_Rear_Delt_Fly", "RearDeltRow_Machine", "Face_Pull", "Dumbbell_Lying_Rear_Lateral_Raise"],
    "RearDeltRow_Machine": ["Reverse_Machine_Flyes", "Cable_Rear_Delt_Fly", "Face_Pull"],
    "Machine_Lat_Pulldown": ["Wide-Grip_Lat_Pulldown", "Close-Grip_Front_Lat_Pulldown", "Assisted_Pull-Up_Machine", "Pullups"],
    "Machine_Pullover": ["Straight-Arm_Pulldown", "Rope_Straight-Arm_Pulldown", "Straight-Arm_Dumbbell_Pullover"],
    "DyRow_Machine": ["Leverage_Low_Row", "Leverage_Iso_Row", "Seated_Cable_Rows", "One-Arm_Dumbbell_Row"],
    "SealRowBench": ["Leverage_Iso_Row", "Lying_T-Bar_Row", "Dumbbell_Incline_Row", "Leverage_Low_Row"],
    "Leverage_Low_Row": ["DyRow_Machine", "Seated_Cable_Rows", "Leverage_Iso_Row", "One-Arm_Dumbbell_Row"],
    "Leverage_Iso_Row": ["SealRowBench", "Leverage_Low_Row", "Dumbbell_Incline_Row", "Seated_Cable_Rows"],
    "Leverage_High_Row": ["Machine_Lat_Pulldown", "Wide-Grip_Lat_Pulldown", "Leverage_Iso_Row"],
    "T-Bar_Row_with_Handle": ["Lying_T-Bar_Row", "SealRowBench", "Leverage_Iso_Row", "Bent_Over_Barbell_Row"],
    "Machine_Preacher_Curls": ["Preacher_Curl", "Bayesian_Cable_Curl", "Incline_Dumbbell_Curl"],
    "Bayesian_Cable_Curl": ["Incline_Dumbbell_Curl", "Machine_Preacher_Curls", "Preacher_Curl"],
    "OverheadTricepsMachine": ["Cable_Rope_Overhead_Triceps_Extension", "Standing_Dumbbell_Triceps_Extension", "EZ-Bar_Skullcrusher"],
    "Machine_Triceps_Extension": ["Triceps_Pushdown_-_Rope_Attachment", "Cable_Rope_Overhead_Triceps_Extension", "Assisted_Dip_Machine"],
    "Ab_Crunch_Machine": ["Cable_Crunch", "AbCoaster_Machine", "Reverse_Crunch"],
    "AbCoaster_Machine": ["Hanging_Leg_Raise", "Captains_Chair_Knee_Raise", "Reverse_Crunch"],
    "RotaryTorso_Machine": ["Pallof_Press", "Side_Bridge"],
    "TibialisMachine": ["Standing_Calf_Raises"],
    "LandmineStation": ["Incline_Dumbbell_Press", "Leverage_Incline_Chest_Press"],
    # peso livre e polia: quando o banco/rack/polia está ocupado ou quebrado
    "Barbell_Bench_Press_-_Medium_Grip": ["Dumbbell_Bench_Press", "Smith_Machine_Bench_Press", "Machine_Bench_Press", "Leverage_Chest_Press"],
    "Dumbbell_Bench_Press": ["Barbell_Bench_Press_-_Medium_Grip", "Machine_Bench_Press", "Leverage_Chest_Press", "Smith_Machine_Bench_Press"],
    "Incline_Dumbbell_Press": ["Barbell_Incline_Bench_Press_-_Medium_Grip", "Smith_Machine_Incline_Bench_Press", "Leverage_Incline_Chest_Press"],
    "Barbell_Incline_Bench_Press_-_Medium_Grip": ["Incline_Dumbbell_Press", "Smith_Machine_Incline_Bench_Press", "Leverage_Incline_Chest_Press"],
    "Barbell_Squat": ["Smith_Machine_Squat", "Hack_Squat", "Front_Barbell_Squat", "Leg_Press", "Goblet_Squat"],
    "Smith_Machine_Squat": ["Hack_Squat", "Barbell_Squat", "Leg_Press", "Goblet_Squat"],
    "Romanian_Deadlift": ["Stiff-Legged_Dumbbell_Deadlift", "Pull_Through", "Hyperextensions_Back_Extensions", "Seated_Leg_Curl"],
    "Barbell_Deadlift": ["Romanian_Deadlift", "Stiff-Legged_Dumbbell_Deadlift", "Pull_Through"],
    "Barbell_Hip_Thrust": ["Hip_Thrust_Machine", "Smith_Machine_Hip_Thrust", "StandingHipThrust_Machine", "Glute_Kickback_Machine"],
    "Smith_Machine_Hip_Thrust": ["Hip_Thrust_Machine", "Barbell_Hip_Thrust", "StandingHipThrust_Machine"],
    "Seated_Cable_Rows": ["Leverage_Low_Row", "Wide-Grip_Seated_Cable_Row", "One-Arm_Dumbbell_Row", "Leverage_Iso_Row"],
    "Bent_Over_Barbell_Row": ["T-Bar_Row_with_Handle", "One-Arm_Dumbbell_Row", "Leverage_Iso_Row", "Seated_Cable_Rows"],
    "One-Arm_Dumbbell_Row": ["Seated_Cable_Rows", "Leverage_Low_Row", "Dumbbell_Incline_Row"],
    "Wide-Grip_Lat_Pulldown": ["Machine_Lat_Pulldown", "Close-Grip_Front_Lat_Pulldown", "Assisted_Pull-Up_Machine", "Pullups"],
    "Close-Grip_Front_Lat_Pulldown": ["Wide-Grip_Lat_Pulldown", "Machine_Lat_Pulldown", "Assisted_Pull-Up_Machine"],
    "Pullups": ["Assisted_Pull-Up_Machine", "Wide-Grip_Lat_Pulldown", "Machine_Lat_Pulldown"],
    "Seated_Dumbbell_Press": ["Machine_Shoulder_Military_Press", "Dumbbell_Shoulder_Press", "Leverage_Shoulder_Press", "Barbell_Shoulder_Press"],
    "Dumbbell_Shoulder_Press": ["Seated_Dumbbell_Press", "Machine_Shoulder_Military_Press", "Leverage_Shoulder_Press"],
    "Side_Lateral_Raise": ["Cable_Seated_Lateral_Raise", "Machine_Lateral_Raise", "Cable_Y_Raise", "Seated_Side_Lateral_Raise"],
    "Cable_Seated_Lateral_Raise": ["Machine_Lateral_Raise", "Side_Lateral_Raise", "Cable_Y_Raise"],
    "Cable_Y_Raise": ["Cable_Seated_Lateral_Raise", "Machine_Lateral_Raise", "Side_Lateral_Raise"],
    "Face_Pull": ["Cable_Rear_Delt_Fly", "Reverse_Machine_Flyes", "Dumbbell_Lying_Rear_Lateral_Raise"],
    "Cable_Rear_Delt_Fly": ["Reverse_Machine_Flyes", "Face_Pull", "Dumbbell_Lying_Rear_Lateral_Raise"],
    "Cable_Crossover": ["Butterfly", "Low_Cable_Crossover", "Dumbbell_Flyes", "InclineFlyMachine"],
    "Low_Cable_Crossover": ["InclineFlyMachine", "Incline_Cable_Flye", "Butterfly", "Incline_Dumbbell_Flyes"],
    "Barbell_Curl": ["EZ-Bar_Curl", "Dumbbell_Bicep_Curl", "Standing_Biceps_Cable_Curl", "Machine_Preacher_Curls"],
    "EZ-Bar_Curl": ["Barbell_Curl", "Dumbbell_Bicep_Curl", "Standing_Biceps_Cable_Curl"],
    "Dumbbell_Bicep_Curl": ["Barbell_Curl", "Standing_Biceps_Cable_Curl", "Incline_Dumbbell_Curl", "Machine_Preacher_Curls"],
    "Incline_Dumbbell_Curl": ["Bayesian_Cable_Curl", "Dumbbell_Bicep_Curl", "Preacher_Curl"],
    "Preacher_Curl": ["Machine_Preacher_Curls", "Bayesian_Cable_Curl", "EZ-Bar_Curl"],
    "Hammer_Curls": ["Cable_Hammer_Curls_-_Rope_Attachment", "Dumbbell_Bicep_Curl"],
    "Cable_Hammer_Curls_-_Rope_Attachment": ["Hammer_Curls", "Dumbbell_Bicep_Curl"],
    "Triceps_Pushdown_-_Rope_Attachment": ["Triceps_Pushdown", "Triceps_Pushdown_-_V-Bar_Attachment", "Machine_Triceps_Extension", "Cable_Triceps_Kickback"],
    "Triceps_Pushdown": ["Triceps_Pushdown_-_Rope_Attachment", "Triceps_Pushdown_-_V-Bar_Attachment", "Machine_Triceps_Extension"],
    "Cable_Rope_Overhead_Triceps_Extension": ["OverheadTricepsMachine", "Standing_Dumbbell_Triceps_Extension", "EZ-Bar_Skullcrusher"],
    "EZ-Bar_Skullcrusher": ["Cable_Rope_Overhead_Triceps_Extension", "Standing_Dumbbell_Triceps_Extension", "OverheadTricepsMachine"],
    "Cable_Crunch": ["Ab_Crunch_Machine", "Reverse_Crunch", "Hanging_Leg_Raise", "Dead_Bug"],
    "Hanging_Leg_Raise": ["Captains_Chair_Knee_Raise", "Reverse_Crunch", "AbCoaster_Machine"],
    "Hyperextensions_Back_Extensions": ["BackExtensionMachine", "Pull_Through", "Romanian_Deadlift"],
    "Split_Squat_with_Dumbbells": ["Dumbbell_Rear_Lunge", "Single-Leg_Leg_Press", "Dumbbell_Lunges"],
    "Dumbbell_Rear_Lunge": ["Split_Squat_with_Dumbbells", "Dumbbell_Lunges", "Single-Leg_Leg_Press"],
}


@lru_cache(maxsize=1)
def data() -> dict:
    return json.loads((DATA / "gyms_sp.json").read_text())


@lru_cache(maxsize=1)
def machines() -> dict[str, dict]:
    return {m["id"]: m for m in json.loads((DATA / "machines.json").read_text())}


def all_gyms() -> list[dict]:
    d = data()
    return [{**g, "kit_label": d["kits"][g["kit"]]["label"]} for g in d["gyms"] + d["generic"]]


def gym_of(p: dict) -> dict | None:
    gid = ((p or {}).get("gym") or {}).get("id") if isinstance((p or {}).get("gym"), dict) else None
    return next((g for g in all_gyms() if g["id"] == gid), None)


def kit_of(p: dict) -> dict:
    """Perfil de aparelhos da academia. Sem academia escolhida, vem do tipo (premium = tudo, como antes)."""
    kits = data()["kits"]
    g = gym_of(p)
    if g:
        return {"key": g["kit"], **kits[g["kit"]]}
    t = (p or {}).get("gym_type")
    key = {"premium": "musculacao", "basica": "basica"}.get(t, "completa")
    return {"key": key, **kits[key]}


def kit_allows(kit: dict, eid: str) -> bool:
    if eid in (kit.get("exclude") or []):
        return False
    m = machines().get(eid)
    if not m:
        return True   # exercícios do catálogo geral (barra, halter, polia, máquinas comuns): toda academia tem
    return AVAIL_LEVEL.get(m.get("availability_br") or "comum", 1) <= kit["level"]


def status(p: dict, eid: str) -> str:
    """'nao_tem' (a pessoa disse), 'tem' (confirmou), 'provavel' ou 'improvavel' (pelo tipo de academia)."""
    p = p or {}
    if eid in (p.get("gym_missing") or []):
        return "nao_tem"
    if eid in (p.get("gym_has") or []):
        return "tem"
    return "provavel" if kit_allows(kit_of(p), eid) else "improvavel"


HOME_EQUIP = {"body only": None, "dumbbell": "halteres", "kettlebells": "kettlebell", "bands": "elastico"}


def home_allows(kit: set[str], eid: str) -> bool:
    """Em casa: só peso do corpo e o que a pessoa tem (halteres, kettlebell, elástico, barra fixa)."""
    ex = catalog.get(eid) or {}
    if eid in STATIONS["barra_fixa"]:
        return "barra" in kit
    eq = ex.get("equipment")
    if eq not in HOME_EQUIP:
        return False
    need = HOME_EQUIP[eq]
    return need is None or need in kit


def checker(p: dict, home: bool = False):
    """Função eid → pode entrar no treino nesta academia? home=True: em casa, só o que a pessoa tem (na troca; o
    gerador monta o treino de academia e converte para casa depois)."""
    p = p or {}
    g = p.get("gym")
    if home and isinstance(g, dict) and g.get("id") == "casa":
        hk = set(p.get("home_kit") or [])
        return lambda eid: home_allows(hk, eid)
    missing, has, kit = set(p.get("gym_missing") or []), set(p.get("gym_has") or []), kit_of(p)
    return lambda eid: eid not in missing and (eid in has or kit_allows(kit, eid))


def similarity(src: str, dst: str) -> float:
    from .alternative import pattern_of
    from .generator import LENGTHENED
    a, b = catalog.get(src) or {}, catalog.get(dst) or {}
    s = 0.0
    near = CLOSEST.get(src) or []
    if dst in near:
        s += 12 - near.index(dst)
    if pattern_of(src) and pattern_of(src) == pattern_of(dst):
        s += 4
    pa, pb = a.get("primaryMuscles") or [], b.get("primaryMuscles") or []
    if pa[:1] and pa[:1] == pb[:1]:
        s += 3
    s += 0.5 * len(set(pa) & set(pb))
    ea, eb = a.get("equipment"), b.get("equipment")
    if ea and ea == eb:
        s += 2
    elif {ea, eb} <= {"machine", "cable", "other"}:
        s += 1
    if (src in LENGTHENED or (machines().get(src) or {}).get("lengthened")) == (dst in LENGTHENED or (machines().get(dst) or {}).get("lengthened")):
        s += 1
    if a.get("mechanic") and a.get("mechanic") == b.get("mechanic"):
        s += 1
    if dst in _curated():
        s += 3      # exercício que o app já usa e explica (não um nome obscuro do catálogo)
    return s


@lru_cache(maxsize=1)
def _curated() -> frozenset:
    from .generator import PATTERNS, PREMIUM_EXTRA
    return frozenset([e for ids in PATTERNS.values() for e in ids] + [e for ids in PREMIUM_EXTRA.values() for e in ids]
                     + list(machines()) + [e for ids in CLOSEST.values() for e in ids])


def substitutes(eid: str, p: dict, pool: list[str], *, my_ids: set[str] | None = None, lims: list[dict] | None = None,
                exclude: set[str] | None = None) -> list[str]:
    """Do mais parecido para o menos, só o que dá para fazer na academia da pessoa (e sem 'evitar' pelas limitações)."""
    from .limitations import check_exercise
    ok = checker(p, home=True)
    my_ids, exclude = my_ids or set(), exclude or set()
    cands = []
    for c in dict.fromkeys(list(CLOSEST.get(eid) or []) + list(pool)):
        ex = catalog.get(c)
        if c == eid or c in exclude or not ex or not (ok(c) or c in my_ids):
            continue
        st = check_exercise(ex, lims or [])["status"] if lims else "ok"
        if st == "evitar":
            continue
        cands.append((c, st))
    has = set((p or {}).get("gym_has") or [])
    cands.sort(key=lambda x: (x[1] != "ok", -(similarity(eid, x[0]) + (2 if x[0] in my_ids or x[0] in has else 0))))
    return [c for c, _ in cands]


def day_profile(p: dict, gym_id: str, home_kit: list[str] | None = None) -> dict:
    """Perfil "de hoje": mesma pessoa, outra academia (os 'não tem/tem' são da academia de sempre: não valem lá)."""
    g = next((x for x in all_gyms() if x["id"] == gym_id), None)
    if not g:
        raise KeyError(gym_id)
    kit = data()["kits"][g["kit"]]
    out = {**(p or {}), "gym": {"id": g["id"], "name": g["name"] if g["id"] != "casa" else "Em casa"}, "gym_type": kit["gym_type"],
           "gym_missing": [], "gym_has": [], "equipment": []}
    if g["id"] == "casa":
        out["home_kit"] = list(home_kit or [])
    return out


def adapt_day(items: list[dict], p: dict, day_p: dict, pool_for) -> tuple[list[dict], list[str]]:
    """Troca no treino do dia o que a academia de hoje (provavelmente) não tem pelo mais parecido que ela tem; numa
    academia mais equipada que a de sempre, oferece o aparelho moderno do movimento. Devolve (itens, mudanças)."""
    import copy
    from . import generator as G
    from .alternative import pattern_of
    from .limitations import check_exercise

    items = copy.deepcopy(items)
    changes: list[str] = []
    if G.home_kit(day_p) is not None:
        d = {"kind": "forca", "items": items, "weekday": 0, "budget": float(p.get("session_minutes") or 60)}
        before = [i.get("exercise_id") for i in items if (i.get("phase") or "principal") == "principal"]
        G._to_home([d], day_p, G.home_kit(day_p), [])
        after = [i.get("exercise_id") for i in d["items"] if (i.get("phase") or "principal") == "principal"]
        for a, b in zip(before, after):
            if a != b:
                changes.append(f"{(catalog.get(a) or {}).get('name', a)} → {(catalog.get(b) or {}).get('name', b)}")
        return d["items"], changes
    ok = checker(day_p)
    lims = (p or {}).get("limitations") or []
    upgrade = kit_of(day_p)["level"] > kit_of(p)["level"] and kit_of(day_p)["gym_type"] == "premium"
    used = {i.get("exercise_id") for i in items}
    for it in items:
        eid = it.get("exercise_id")
        ex = catalog.get(eid) or {}
        if (it.get("phase") or "principal") != "principal" or ex.get("kind") == "cardio" or it.get("endurance"):
            continue
        new = None
        if not ok(eid):
            subs = substitutes(eid, day_p, pool_for(eid), lims=lims, exclude=used)
            new = subs[0] if subs else None
        elif upgrade and it.get("role") != "main":
            pat = pattern_of(eid, it)
            best = [e for e in (G.PREMIUM_PREF.get(pat) or [])[:2] if ok(e) and e not in used and catalog.get(e)
                    and check_exercise(catalog.get(e), lims)["status"] == "ok"]
            if best and eid not in (G.PREMIUM_PREF.get(pat) or [])[:3]:
                new = best[0]
        if new and new != eid:
            it["gym_from"] = eid
            it["exercise_id"] = new
            if catalog.get(new).get("timed") and not it.get("duration_s"):
                it.pop("reps", None)
                it["duration_s"] = 40
            used.add(new)
            changes.append(f"{ex.get('name', eid)} → {catalog.get(new)['name']}")
    return items, changes


# ------------------------------------------------- troca no meio do treino (aparelho ocupado) ----
# Aparelhos/estações onde vários exercícios são feitos: se a estação está ocupada, nenhum deles serve agora.
STATIONS: dict[str, set[str]] = {
    "pulley_alto": {"Wide-Grip_Lat_Pulldown", "Close-Grip_Front_Lat_Pulldown", "V-Bar_Pulldown", "Underhand_Cable_Pulldowns",
                    "Full_Range-Of-Motion_Lat_Pulldown", "Wide-Grip_Pulldown_Behind_The_Neck", "One_Arm_Lat_Pulldown",
                    "Kneeling_High_Pulley_Row", "Kneeling_Single-Arm_High_Pulley_Row"},
    "remada_baixa": {"Seated_Cable_Rows", "Wide-Grip_Seated_Cable_Row", "Seated_One-arm_Cable_Pulley_Rows", "Elevated_Cable_Rows",
                     "Low_Pulley_Row_To_Neck"},
    "barra_fixa": {"Pullups", "Chin-Up", "Mixed_Grip_Chin", "V-Bar_Pullup", "Wide-Grip_Rear_Pull-Up", "Band_Assisted_Pull-Up",
                   "Scapular_Pull-Up", "One_Arm_Chin-Up", "Side_To_Side_Chins"},
    "graviton": {"Assisted_Pull-Up_Machine", "Assisted_Dip_Machine"},
    "paralelas": {"Parallel_Bar_Dip", "Dips_-_Chest_Version", "Dips_-_Triceps_Version"},
    "peck_deck": {"Butterfly", "Reverse_Machine_Flyes"},
    "leg_press_45": {"Leg_Press", "Narrow_Stance_Leg_Press", "Single-Leg_Leg_Press", "Calf_Press_On_The_Leg_Press_Machine"},
    "leg_press_h": {"Horizontal_Leg_Press", "Horizontal_Leg_Press_Unilateral"},
    "hack": {"Hack_Squat", "Narrow_Stance_Hack_Squats", "Reverse_Hack_Squat", "Hack_Machine_Calf_Raise"},
    "extensora": {"Leg_Extensions", "Single-Leg_Leg_Extension"},
    "abdutora": {"Thigh_Abductor", "Thigh_Adductor"},
    "supino_reto": {"Barbell_Bench_Press_-_Medium_Grip", "Wide-Grip_Barbell_Bench_Press", "Close-Grip_Barbell_Bench_Press",
                    "Bench_Press_-_Powerlifting"},
    "supino_inclinado": {"Barbell_Incline_Bench_Press_-_Medium_Grip"},
}
SOFT_STATIONS = {"polia"}     # academia costuma ter várias polias: dá para usar outra livre
# não oferecer como troca: risco sem ganho (atrás da nuca, guilhotina) ou precisa de equipamento especial
NEVER_SUB = {"Wide-Grip_Pulldown_Behind_The_Neck", "Barbell_Guillotine_Bench_Press", "Standing_Barbell_Press_Behind_Neck",
             "Push_Press_-_Behind_the_Neck", "Rocky_Pull-Ups_Pulldowns", "Bradford_Rocky_Presses", "Bench_Press_with_Chains",
             "Bench_Press_-_With_Bands", "Reverse_Band_Bench_Press", "Neck_Press", "Front_Raise_And_Pullover",
             "Gironda_Sternum_Chins", "Smith_Machine_Pistol_Squat"}
# movimentos diferentes que treinam o mesmo músculo: servem quando não há troca do mesmo movimento
COMPAT = {("v_pull", "h_pull"), ("h_pull", "v_pull"), ("v_pull", "pullover"), ("squat", "single_leg"), ("single_leg", "squat"),
          ("h_push", "incline_push"), ("incline_push", "h_push"), ("leg_ext", "squat"), ("leg_ext", "single_leg"),
          ("hinge", "glute"), ("glute", "hinge"), ("rear_delt", "h_pull"), ("chest_fly", "fly"), ("fly", "chest_fly"),
          ("h_push", "fly"), ("incline_push", "fly"), ("lateral", "raise"), ("raise", "lateral")}


def station_of(eid: str) -> str | None:
    for st, ids in STATIONS.items():
        if eid in ids:
            return st
    if "Smith" in eid:
        return "smith"
    eq = (catalog.get(eid) or {}).get("equipment")
    if eq == "barbell" and motion(eid) == "squat":
        return "rack"      # agachamentos com barra: o rack ocupado serve para todos eles
    if eq == "cable":
        return "polia"
    if eq == "machine":
        return eid
    return None


def motion(eid: str) -> str | None:
    """Movimento de verdade (o catálogo chama crucifixo e pullover de 'empurrar' só por serem de peito)."""
    import re
    from .alternative import pattern_of
    ex = catalog.get(eid) or {}
    txt = f"{eid} {ex.get('name', '')}"
    if re.search(r"pullover", txt, re.I):
        return "pullover"
    if re.search(r"fly|flye|butterfly|crossover|crucifixo|pec.?deck|iron.?cross", txt, re.I) and not re.search(r"rear|reverse|invers|posterior", txt, re.I):
        return "fly"
    if re.search(r"front.?raise|eleva[cç][aã]o frontal|front.?cable.?raise", txt, re.I):
        return "raise"
    if re.search(r"rota[cç][aã]o|rotation", txt, re.I):
        return "rotator"
    if re.search(r"jump|salto|plyo", txt, re.I):
        return "plyo"
    pat = pattern_of(eid)
    if pat == "lateral" and re.search(r"rear|reverse|invertid|posterior", txt, re.I):
        return "rear_delt"
    if pat == "h_push" and re.search(r"inclin", txt, re.I):
        return "incline_push"
    return pat


def _readable(eid: str) -> bool:
    ex = catalog.get(eid) or {}
    return eid in _curated() or bool(ex.get("popular")) or ex.get("name") != ex.get("name_en")


def _near_dup(a: str, b: str) -> bool:
    """Praticamente o mesmo exercício (fazer os dois no mesmo treino não acrescenta nada)."""
    if a == b or a in (CLOSEST.get(b) or [])[:2] or b in (CLOSEST.get(a) or [])[:2]:
        return True
    ea, eb = catalog.get(a) or {}, catalog.get(b) or {}
    return (motion(a) == motion(b) and ea.get("equipment") == eb.get("equipment")
            and (ea.get("primaryMuscles") or [None])[:1] == (eb.get("primaryMuscles") or [None])[:1])


def sensible(src: str, dst: str) -> str | None:
    """'mesmo' (mesmo movimento), 'outro' (mesmo músculo de outro jeito) ou None (não faz sentido no lugar dele)."""
    if dst in NEVER_SUB:
        return None
    a, b = catalog.get(src) or {}, catalog.get(dst) or {}
    if a.get("kind") == "strength" and b.get("kind") != "strength":
        return None
    if dst in (CLOSEST.get(src) or []):
        return "mesmo"
    if not _readable(dst):
        return None
    ma, mb = motion(src), motion(dst)
    if ma and ma == mb:
        return "mesmo"
    if (ma, mb) in COMPAT:
        return "outro"
    return None


def session_options(eid: str, p: dict, pool: list[str], *, my_ids: set[str] | None = None, lims: list[dict] | None = None,
                    in_session: list[str] | None = None, done: list[str] | None = None, busy: bool = True,
                    limit: int = 10) -> list[dict]:
    """Trocas que fazem sentido AGORA, neste treino: nada que já está no treino (feito ou por fazer), nada da mesma
    estação quando o aparelho está ocupado, nada de outro movimento sem relação; o que repete quase igual algo já
    feito vai para o fim, avisado. Devolve [{id, fit: mesmo|outro, note}]."""
    in_session, done = list(in_session or []), list(done or [])
    cands = substitutes(eid, p, pool, my_ids=my_ids, lims=lims, exclude=set(in_session) | set(done))
    st = station_of(eid)
    out = []
    for k, c in enumerate(cands):
        fit = sensible(eid, c)
        if not fit:
            continue
        note, rank = None, 0
        cs = station_of(c)
        if busy and st and cs == st:
            if st not in SOFT_STATIONS:
                continue          # mesmo aparelho que está ocupado
            note, rank = "em outra polia livre", 1
        dup = next((d for d in done if _near_dup(c, d)), None) or next((d for d in in_session if _near_dup(c, d)), None)
        if dup:
            nm = (catalog.get(dup) or {}).get("name", dup)
            note, rank = (f"parecido com {nm}, que você já fez" if dup in done else f"parecido com {nm}, que ainda vem no treino"), 3
        if fit == "outro":
            rank += 2
            note = note or "mesmo músculo, outro movimento"
        ea, eb = (catalog.get(eid) or {}).get("equipment"), (catalog.get(c) or {}).get("equipment")
        if eb in ("body only", None) and ea not in ("body only", None) and c not in (CLOSEST.get(eid) or []):
            rank += 4
            note = note or "sem carga: só se o resto estiver ocupado"
        out.append((rank, k, {"id": c, "fit": fit, "note": note}))
    out.sort(key=lambda x: (x[0], x[1]))
    return [o for _, _, o in out[:limit]]
