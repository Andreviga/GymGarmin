"""Progressão de carga ("dupla progressão") e ciclo de treino (mesociclo + deload).

Dupla progressão: trabalhe dentro de uma faixa de repetições (ex.: 8-12).
- Fez o TOPO da faixa em todas as séries com RPE ≤ 8,5  → sobe a carga.
- Ficou ABAIXO do mínimo da faixa com RPE ≥ 9,5          → reduz ~5%.
- Ficou abaixo do mínimo                                   → mantém e consolida.
- Senão                                                    → mantém e busca +1 rep por série.

Mesociclo: semanas de acúmulo (séries sobem nos exercícios principais conforme o nível) e
a última semana do ciclo é DELOAD (≈60% das séries, RPE 6, carga −10%) para recuperar.
"""

from __future__ import annotations

import copy
import math
from datetime import date

from . import catalog
from .garmin_export import parse_reps

LOWER = {"quadriceps", "hamstrings", "glutes"}


def _bounds(reps) -> tuple[int, int]:
    if reps is None or reps == "":
        return 8, 12
    nums = [int(n) for n in str(reps).replace("–", "-").split("-") if n.strip().isdigit()]
    if not nums:
        hi, _ = parse_reps(reps)
        return hi, hi
    return min(nums), max(nums)


def increment(ex: dict) -> float:
    eq = ex.get("equipment")
    lower = bool(set(ex.get("primaryMuscles", [])) & LOWER) and ex.get("mechanic") == "compound"
    if eq == "dumbbell":
        return 2.0
    if eq in ("kettlebells",):
        return 4.0
    if lower and eq in ("barbell", "machine"):
        return 5.0
    if ex.get("mechanic") == "isolation":
        return 1.0 if eq == "cable" else 2.5
    return 2.5


def _round_load(x: float, step: float = 0.5) -> float:
    return round(round(x / step) * step, 1)


def suggest(plan_item: dict, last: dict | None, deload: bool = False) -> dict:
    """Sugere a carga da próxima sessão para um exercício."""
    ex = catalog.get(plan_item.get("exercise_id")) or {}
    lo, hi = _bounds(plan_item.get("reps"))
    sets = [s for s in ((last or {}).get("sets") or []) if s.get("load_kg")]
    if not sets:
        load = plan_item.get("load_kg")
        if load and deload:
            return {"load_kg": _round_load(load * 0.9), "note": "Deload: −10% de carga, RPE ~6"}
        return {"load_kg": load, "note": "Sem histórico: escolha uma carga que deixe 2–3 reps \"no tanque\" (RPE 7–8)."}
    loads = [s["load_kg"] for s in sets]
    work_load = max(set(loads), key=loads.count)
    work_sets = [s for s in sets if s["load_kg"] == work_load]
    reps = [s.get("reps") or 0 for s in work_sets]
    rpes = [s["rpe"] for s in work_sets if s.get("rpe")]
    avg_rpe = sum(rpes) / len(rpes) if rpes else None
    inc = increment(ex)
    if deload:
        return {"load_kg": _round_load(work_load * 0.9), "note": "Deload: −10% de carga, menos séries, RPE ~6"}
    if all(r >= hi for r in reps) and (avg_rpe is None or avg_rpe <= 8.5):
        return {"load_kg": _round_load(work_load + inc), "note": f"Bateu {hi} reps em todas as séries → +{inc:g} kg"}
    if any(r < lo for r in reps) and avg_rpe and avg_rpe >= 9.5:
        return {"load_kg": _round_load(work_load * 0.95), "note": "Muito pesado na última vez → −5%"}
    if any(r < lo for r in reps):
        return {"load_kg": work_load, "note": f"Mantenha {work_load:g} kg até completar {lo}+ reps em todas as séries"}
    return {"load_kg": work_load, "note": f"Mantenha {work_load:g} kg e busque +1 rep por série (alvo {hi})"}


# ------------------------------------------------------------------ ciclo ---

def cycle_position(cycle_start: str | date | None, week_start: date, cycle_weeks: int) -> dict:
    if not cycle_start:
        cycle_start = week_start
    if isinstance(cycle_start, str):
        cycle_start = date.fromisoformat(cycle_start[:10])
    weeks = max(0, (week_start - cycle_start).days // 7)
    n = max(2, int(cycle_weeks or 5))
    w = weeks % n + 1
    return {"week": w, "length": n, "cycle": weeks // n + 1, "deload": w == n}


def apply_cycle(items: list[dict], pos: dict, ramp_sets: int) -> list[dict]:
    """Aplica a semana do ciclo ao treino (cópia): acúmulo ou deload."""
    out = copy.deepcopy(items)
    for it in out:
        if it.get("phase") not in (None, "principal") or not it.get("sets"):
            continue
        if pos["deload"]:
            it["sets"] = max(1, math.ceil(it["sets"] * 0.6))
            it["rpe_target"] = 6
            it["deload"] = True
        elif ramp_sets and it.get("role") in ("main", "sec") and pos["week"] >= 3:
            extra = min(ramp_sets, (pos["week"] - 1) // 2)
            if extra:
                it["sets"] = it["sets"] + extra
                it["ramp"] = extra
    return out
