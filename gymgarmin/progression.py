"""Progressão de carga ("dupla progressão") e ciclo de treino (mesociclo + deload).

Dupla progressão: trabalhe dentro de uma faixa de repetições (ex.: 8-12).
- Fez o TOPO da faixa (todas as séries, ou faltou só 1 rep no total) com RPE ≤ 9 → sobe a carga
  (RPE 9,5 → sobe meio incremento; RPE 10 em tudo → repete a carga).
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
    # topo da faixa: todas as séries no topo, ou faltou no máximo 1 rep no total (a última série cansa)
    hit_top = all(r >= hi for r in reps) or (sum(reps) >= hi * len(reps) - 1 and min(reps) >= hi - 1)
    if hit_top and (avg_rpe is None or avg_rpe <= 9.5):
        step = inc if (avg_rpe is None or avg_rpe <= 9) else max(1.0, inc / 2)  # RPE 9,5: sobe menos
        new = _round_load(work_load + step)
        jump = (new - work_load) / work_load if work_load else 0
        note = f"Bateu o topo ({hi} reps) → +{new - work_load:g} kg"
        if jump > 0.12:
            note += (f" (salto de {jump:.0%}: é normal as reps caírem para perto de {lo} — reconstrua até {hi})")
        return {"load_kg": new, "note": note}
    if hit_top:
        return {"load_kg": work_load, "note": f"Topo da faixa, mas no limite (RPE 10): repita {work_load:g} kg com mais controle"}
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


def apply_cycle(items: list[dict], pos: dict, ramp_sets: int, budget_min: float | None = None,
                allowance: dict[str, float] | None = None) -> list[dict]:
    """Aplica a semana do ciclo ao treino (cópia): acúmulo ou deload.

    Deload: ~55% das séries (cada exercício), RPE 6.
    Acúmulo: +1 série por vez nos principais (depois nos secundários) a partir da semana 3, só enquanto
    couber no tempo da sessão (`budget_min`) e no volume máximo recuperável de cada músculo (`allowance`
    = séries que ainda cabem por músculo na semana; é consumida aqui).
    """
    from .generator import time_breakdown

    out = copy.deepcopy(items)
    work = [it for it in out if it.get("phase") in (None, "principal") and it.get("sets")]
    if pos["deload"]:
        for it in work:
            it["sets"] = max(1, round(it["sets"] * 0.55))
            it["rpe_target"] = 6
            it["deload"] = True
        return out
    extra_max = min(ramp_sets, (pos["week"] - 1) // 2) if ramp_sets and pos["week"] >= 3 else 0
    if not extra_max:
        return out
    order = [it for it in work if it.get("role") == "main"] + [it for it in work if it.get("role") == "sec"]
    for _ in range(extra_max):
        for it in order:
            ex = catalog.get(it.get("exercise_id")) or {}
            prim, sec = ex.get("primaryMuscles", []), ex.get("secondaryMuscles", [])
            if allowance is not None and (any(allowance.get(m, 0) < 1 for m in prim) or any(allowance.get(m, 0) < 0.5 for m in sec)):
                continue
            it["sets"] += 1
            if budget_min is not None and time_breakdown(out)["total"] > budget_min:
                it["sets"] -= 1
                continue
            it["ramp"] = it.get("ramp", 0) + 1
            if allowance is not None:
                for m in prim:
                    allowance[m] = allowance.get(m, 0) - 1
                for m in sec:
                    allowance[m] = allowance.get(m, 0) - 0.5
    return out
