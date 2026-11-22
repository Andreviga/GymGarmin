"""Cargas: exercícios de dois lados (peso total, metade em cada lado) e equivalência entre exercícios/aparelhos.

Convenção do app: em exercício com lado direito e esquerdo (halteres, máquinas iso-laterais, polia de um braço,
remada unilateral, leg press de uma perna…) a pessoa anota o PESO TOTAL, somando os dois lados. O app entende
metade em cada lado. Ex.: supino com dois halteres de 20 kg → 40 kg; remada unilateral com halter de 25 kg nos dois
braços → 50 kg.

Equivalência: quando a pessoa nunca fez um exercício, o app estima a carga pelo 1RM estimado de um exercício
parecido que ela já faz, usando a razão de força típica entre os dois (data/load_ratios.json). A 1ª série é teste:
pelo que sobrou de repetições, as próximas são ajustadas.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from . import catalog

_SIDES_RE = re.compile(r"One-Arm|One_Arm|Single-Arm|Single_Arm|One-Legged|Single-Leg|Single_Leg|Unilateral|Alternat|"
                       r"Iso_Row|Iso-Lateral|Leverage_(Chest|Incline|Iso|High|Low)|DyRow|Kickback|Cable_Seated_Lateral|"
                       r"Cable_Y_Raise|Crossover|Cable_Rear_Delt|Cross-Body|Hip_Abduction|Hip_Adduction|Bayesian|"
                       r"ReverseLunge_Machine|StandingHipAbduction|StandingHipAdduction|Kneeling_Hip_Extension", re.I)
# um halter só, segurado com as duas mãos (não tem "dois lados")
_ONE_IMPLEMENT_RE = re.compile(r"Goblet|Pullover|Standing_Dumbbell_Triceps_Extension|Seated_Triceps_Press|Side_Bend|"
                               r"Dumbbell_Squat_To_A_Bench|Russian|Plate|Two-Arm_Kettlebell|Kettlebell_Swing|Tate", re.I)


@lru_cache(maxsize=4096)
def sides(eid: str) -> bool:
    """O exercício tem lado direito e esquerdo com carga própria (anota o total; metade em cada lado)."""
    ex = catalog.get(eid) or {}
    if not ex or ex.get("kind") == "cardio" or ex.get("timed"):
        return False
    eq = ex.get("equipment")
    if eq == "body only" or _ONE_IMPLEMENT_RE.search(eid):
        return False
    return eq == "dumbbell" or bool(_SIDES_RE.search(eid))


def step(eid: str, load: float | None = None) -> float:
    """Salto de carga que existe de verdade (para arredondar a sugestão)."""
    ex = catalog.get(eid) or {}
    eq = ex.get("equipment")
    if sides(eid):
        side = (load or 0) / 2
        per = 1.0 if side <= 10 else 2.0          # halteres: de 1 em 1 kg até 10, depois de 2 em 2
        if eq in ("machine", "cable", "other"):
            per = 2.5 if eq == "cable" else 2.5     # placa/anilha de cada lado
        return per * 2
    if eq == "barbell" or eq == "e-z curl bar":
        return 2.5
    if eq in ("machine", "cable"):
        return 5.0 if (load or 0) >= 20 else 2.5
    if eq == "kettlebells":
        return 4.0
    return 2.5


def round_load(eid: str, load: float | None) -> float | None:
    if not load or load <= 0:
        return load
    st = step(eid, load)
    return round(max(st, round(load / st) * st), 1)


def per_side(eid: str, load: float | None) -> float | None:
    return round(load / 2, 2) if load and sides(eid) else None


# ---------------------------------------------------------------- equivalência ---

@lru_cache(maxsize=1)
def ratios() -> dict:
    p = Path(__file__).resolve().parent / "data" / "load_ratios.json"
    return json.loads(p.read_text()) if p.exists() else {"exercises": {}}


def ratio_of(eid: str) -> tuple[str, float] | None:
    r = ratios()["exercises"].get(eid)
    return (r["family"], float(r["ratio"])) if r else None


def e1rm(load: float, reps: int, rpe: float | None = None) -> float:
    rir = 10 - float(rpe) if rpe and 6 <= float(rpe) <= 10 else 1.0
    return load * (1 + (reps + rir) / 30)


def load_for(e1: float, reps: int, rir: float = 2.5) -> float:
    return e1 / (1 + (reps + rir) / 30)


def estimate(eid: str, known: dict[str, dict], reps: int, rir: float = 2.5) -> dict | None:
    """Carga para quem nunca fez `eid`, pelo 1RM estimado de exercícios parecidos que a pessoa faz.
    known: {exercise_id: {"e1rm": kg, "date": "AAAA-MM-DD"}}."""
    from .gyms import similarity
    me = ratio_of(eid)
    if not me:
        return None
    fam, r_b = me
    cands = []
    for a, k in known.items():
        ra = ratio_of(a)
        if a == eid or not ra or ra[0] != fam or not k.get("e1rm"):
            continue
        cands.append((similarity(a, eid), k.get("date") or "", a, k["e1rm"] * r_b / ra[1]))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    top = cands[:2]
    w = [max(0.5, c[0]) for c in top]
    e1 = sum(c[3] * x for c, x in zip(top, w)) / sum(w)
    load = round_load(eid, load_for(e1, reps, rir))
    src = catalog.get(top[0][2]) or {}
    ex = catalog.get(eid) or {}
    note = (f"Estimado pelo seu {src.get('name', top[0][2])}: a 1ª série é teste. "
            f"Se sobrarem mais de 4 reps, suba; se não chegar em {reps}, desça. O app ajusta as próximas.")
    if ex.get("equipment") in ("machine", "cable"):
        note += " Aparelhos de marcas diferentes pesam diferente (alavanca, polia)."
    return {"load_kg": load, "note": note, "estimated": True, "from": top[0][2], "e1rm": round(e1, 1)}
