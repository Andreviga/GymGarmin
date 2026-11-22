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
        return 2.5
    return 2.5


def stack_step(ex: dict) -> float:
    """Salto que costuma existir no aparelho (pilha de placas de polia/máquina: 5 kg)."""
    return 5.0 if ex.get("equipment") in ("cable", "machine") else increment(ex)


def _round_load(x: float, step: float = 0.5) -> float:
    return round(round(x / step) * step, 1)


def _is_deload(perf: dict, prev: dict | None = None) -> bool:
    """Treino de deload (marcado pelo app ao salvar a semana leve do ciclo)."""
    return bool(perf.get("deload"))


def pick_last(perfs: list[dict]) -> dict | None:
    """Das últimas vezes que o exercício foi feito (mais recente primeiro), escolhe a base da sugestão:
    a última que NÃO foi deload (senão cada deload puxaria a carga 10% para baixo para sempre).
    Devolve {..., "recent": [até 3 treinos normais], "last_any_date": data do treino mais recente}."""
    perfs = [p for p in perfs if p and p.get("sets")]
    if not perfs:
        return None
    # treino registrado sem carga (séries marcadas sem digitar o peso) não serve de base quando há outros com carga
    loaded = [p for p in perfs if any(s.get("load_kg") for s in p["sets"])]
    perfs = loaded or perfs
    normal = [p for i, p in enumerate(perfs) if not _is_deload(p, perfs[i + 1] if i + 1 < len(perfs) else None)]
    base = normal[0] if normal else perfs[0]
    return {**base, "recent": normal[:3], "last_any_date": perfs[0].get("date")}


def _work(perf: dict) -> tuple[float, list[int], float | None]:
    sets = [x for x in perf.get("sets") or [] if x.get("load_kg")]
    loads = [x["load_kg"] for x in sets]
    wl = max(set(loads), key=loads.count)
    ws = [x for x in sets if x["load_kg"] == wl]
    rpes = [float(x["rpe"]) for x in ws if x.get("rpe")]
    return wl, [int(x.get("reps") or 0) for x in ws], (sum(rpes) / len(rpes) if rpes else None)


def _e1rm(load: float, reps: int, rpe: float | None) -> float:
    rir = 10 - rpe if rpe and 6 <= rpe <= 10 else 0
    return load * (1 + (reps + rir) / 30)


def suggest(plan_item: dict, last: dict | None, deload: bool = False, today: date | None = None) -> dict:
    """Sugere a carga da próxima sessão para um exercício (dupla progressão com ajustes)."""
    ex = catalog.get(plan_item.get("exercise_id")) or {}
    lo, hi = _bounds(plan_item.get("reps"))
    sets = [s for s in ((last or {}).get("sets") or []) if s.get("load_kg")]
    if not sets:
        load = plan_item.get("load_kg")
        if load and deload:
            return {"load_kg": _round_load(load * 0.9), "note": "Deload: −10% de carga, RPE ~6"}
        return {"load_kg": load, "note": "Sem histórico: escolha uma carga que deixe 2–3 reps \"no tanque\" (RPE 7–8)."}
    work_load, reps, avg_rpe = _work(last)
    inc = increment(ex)
    from . import loads
    if loads.sides(ex.get("id") or ""):
        inc = max(inc, loads.step(ex["id"], work_load))
    if hi <= 6 and ex.get("equipment") == "barbell":
        inc = min(inc, 2.5)  # faixa de força (3–5 reps): saltos de 5 kg derrubam as reps — anilhas de 1,25 kg por lado
    if deload:
        return {"load_kg": _round_load(work_load * 0.9), "note": "Deload: −10% de carga, menos séries, RPE ~6"}
    # voltando de pausa: a força cai depois de ~2 semanas parado
    try:
        gap = ((today or date.today()) - date.fromisoformat(str((last or {}).get("last_any_date") or last.get("date"))[:10])).days
    except (TypeError, ValueError):
        gap = 0
    if gap >= 14:
        cut = 0.95 if gap < 28 else 0.9 if gap < 56 else 0.8
        new = _round_load(max(inc, work_load - math.ceil(work_load * (1 - cut) / inc) * inc))
        return {"load_kg": new, "note": f"{gap // 7} semanas sem este exercício → −{round((1 - cut) * 100)}% para voltar com segurança"}
    recent = (last or {}).get("recent") or [last]
    prev = next((p for p in recent[1:] if any(s.get("load_kg") for s in (p or {}).get("sets") or [])), None)
    best = max(range(len(reps)), key=lambda i: reps[i])
    e1 = _e1rm(work_load, reps[best], avg_rpe)
    mid = (lo + hi) / 2
    # salto grande (halter leve: 8 → 10 kg = 25%): depois de subir, as reps caem — consolide em vez de descer
    big_jump = work_load > 0 and inc / work_load > 0.12
    top = hi
    # topo da faixa: todas as séries no topo, ou faltou no máximo 1 rep no total (a última série cansa)
    hit_top = all(r >= top for r in reps) or (sum(reps) >= top * len(reps) - 1 and min(reps) >= top - 1)
    just_jumped = False
    if prev:
        pl0, preps0, _ = _work(prev)
        just_jumped = pl0 < work_load and work_load - pl0 <= max(inc, stack_step(ex)) + 1e-6
    if hit_top and (avg_rpe is None or avg_rpe <= 9.5):
        step = inc if (avg_rpe is None or avg_rpe <= 9) else max(1.0, inc / 2)  # RPE 9,5: sobe menos
        if (avg_rpe is not None and avg_rpe <= 7) or min(reps) >= hi + 3:
            # leve demais (sobrou muito): pula direto para a carga do meio da faixa com 2 reps no tanque
            target = e1 / (1 + (mid + 2) / 30)
            step = max(step, min(target, work_load * 1.15) - work_load)
        if prev:
            pl, preps, prpe = _work(prev)
            p_top = all(r >= top for r in preps) or (sum(preps) >= top * len(preps) - 1 and min(preps) >= top - 1)
            if abs(pl - work_load) < 1e-6 and p_top:
                # bateu o topo duas vezes com a mesma carga: o aparelho não tem o ajuste pequeno → salto do aparelho
                step = max(step, stack_step(ex))
        # sobe em múltiplos do incremento (o meio incremento do RPE 9,5 fica como está)
        new = _round_load(work_load + (step if step < inc else max(1, round(step / inc)) * inc))
        jump = (new - work_load) / work_load if work_load else 0
        note = f"Bateu o topo ({top} reps) → +{new - work_load:g} kg"
        if prev and abs(_work(prev)[0] - work_load) < 1e-6 and step >= stack_step(ex) > inc:
            note += f" (o aparelho não tem ajuste de {inc:g} kg? suba para {new:g})"
        if jump > 0.12:
            note += f" (salto de {jump:.0%}: é normal as reps caírem para perto de {lo} — reconstrua até {hi})"
        return {"load_kg": new, "note": note}
    if hit_top:
        return {"load_kg": work_load, "note": f"Topo da faixa, mas no limite (RPE 10): repita {work_load:g} kg com mais controle"}
    if just_jumped and min(reps) >= lo - 3 and big_jump:
        # acabou de subir um salto grande: reps baixas são esperadas — consolide antes de pensar em descer
        return {"load_kg": work_load, "note": f"Carga nova: mantenha {work_load:g} kg e reconstrua as reps até {lo}–{hi}"}
    # descer um degrau só ajuda se a carga de baixo ainda for desafiadora (halter: 10 → 8 kg pode ficar leve demais)
    below = work_load - inc
    reps_below = 30 * (e1 / below - 1) - 2 if below > 0 else 99  # reps com 2 no tanque na carga de baixo
    if any(r < lo for r in reps) and big_jump and reps_below > hi and min(reps) >= lo - 4:
        return {"load_kg": work_load, "note": f"Fique nos {work_load:g} kg (o de {below:g} kg ficaria leve demais) e reconstrua as reps até {lo}"}
    if min(reps) <= lo - 2:
        # bem abaixo da faixa: pesado demais (com ou sem RPE) → carga para chegar ao mínimo com 1 rep no tanque
        target = max(work_load * 0.85, min(work_load * 0.95, e1 / (1 + (lo + 1) / 30)))
        new = _round_load(max(inc, work_load - max(1, math.ceil((work_load - target) / inc)) * inc))
        return {"load_kg": new, "note": f"Ficou bem abaixo de {lo} reps → −{work_load - new:g} kg"}
    if any(r < lo for r in reps) and avg_rpe and avg_rpe >= 9.5:
        new = _round_load(max(inc, work_load - max(1, math.ceil(0.05 * work_load / inc)) * inc))
        return {"load_kg": new, "note": f"Muito pesado na última vez → −{work_load - new:g} kg"}
    if any(r < lo for r in reps):
        if prev:
            pl, preps, _ = _work(prev)
            if abs(pl - work_load) < 1e-6 and any(r < lo for r in preps):
                new = _round_load(max(inc, work_load - inc))
                return {"load_kg": new, "note": f"Duas vezes abaixo de {lo} reps com {work_load:g} kg → −{work_load - new:g} kg"}
        return {"load_kg": work_load, "note": f"Mantenha {work_load:g} kg até completar {lo}+ reps em todas as séries"}
    return {"load_kg": work_load, "note": f"Mantenha {work_load:g} kg e busque +1 rep por série (alvo {top})"}


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
