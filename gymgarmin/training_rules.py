"""Regras aplicadas na hora de treinar (app e relógio), valem também para planilhas já geradas.

Descanso entre séries (evidência):
- Hipertrofia: há um pequeno ganho em descansar mais de 60 s, e acima de ~90 s não aparece diferença
  apreciável (Singer, Schoenfeld et al., 2024, revisão sistemática com meta-análise bayesiana). O motivo
  é o volume: descanso curto demais derruba as repetições das séries seguintes; depois de ~90 s isso estabiliza.
- Força com multiarticulares pesados (≤ 6 reps): 2–3 min preservam a carga das séries seguintes
  (Grgic et al., 2017; Schoenfeld et al., 2016).
- Descanso escolhido pela pessoa mantém o volume tão bem quanto descanso fixo longo — por isso o app
  deixa terminar antes ("estou pronto") e o tempo abaixo é um teto razoável, não uma obrigação.

Séries de aquecimento do próprio exercício (aproximação): melhoram a força/velocidade da 1ª série
válida; poucas repetições só com carga leve não bastam — vale chegar perto da carga de trabalho
(Ribeiro et al., 2020: 6 × 40% + 6 × 80% da carga de treino no supino; 80% no agachamento).
O 1º multiarticular do dia ganha 2–3 séries em rampa; o 1º exercício de um músculo ainda não
trabalhado ganha 1 série; isoladores e músculos já aquecidos não precisam.
"""
from __future__ import annotations

import re

from . import catalog

# descanso base (objetivo hipertrofia) por nível e papel do exercício, em segundos
REST = {
    "iniciante": {"main": 90, "sec": 75, "acc": 60},
    "intermediario": {"main": 120, "sec": 90, "acc": 60},
    "avancado": {"main": 150, "sec": 105, "acc": 60},
    "elite": {"main": 150, "sec": 120, "acc": 60},
}
REST_PREF = {"curto": 0.75, "padrao": 1.0, "longo": 1.25}
WARM_REST_S = 45        # entre séries de aquecimento
WARM_LAST_REST_S = 75   # da última de aquecimento até a 1ª válida
APPROACH_NOTE = re.compile(r"\s*·?\s*aproximação:[^·]*?antes das séries válidas")


def rest_for(role: str, tier_key: str, goal_mult: float, pref: str | None) -> int:
    base = REST.get(tier_key, REST["intermediario"]).get(role, 75) * goal_mult * REST_PREF.get(pref or "padrao", 1.0)
    floor = 30 if role == "acc" else 45
    return int(max(floor, round(base / 15) * 15))


def _reps_low(reps) -> int:
    nums = [int(n) for n in str(reps or "").replace("–", "-").split("-") if n.strip().isdigit()]
    return min(nums) if nums else 10


def warmup_plan(it: dict, ex: dict, first_compound: bool, seen: set[str]) -> list[dict]:
    """Séries de aquecimento do próprio exercício: [{'pct': 50, 'reps': 8}, ...]."""
    if ex.get("kind") == "cardio" or it.get("duration_s") or ex.get("timed"):
        return []
    role = it.get("role")
    compound = ex.get("mechanic") == "compound" or role in ("main", "sec")
    if not compound:
        return []
    heavy = _reps_low(it.get("reps")) <= 6
    muscles = set(ex.get("primaryMuscles") or [])
    if first_compound:
        ramp = [(50, 8), (70, 4), (85, 2)] if heavy else [(50, 8), (75, 4)]
    elif not (muscles & seen):
        ramp = [(60, 6)]
    elif heavy and role == "main":
        ramp = [(75, 3)]
    else:
        ramp = []
    return [{"pct": p, "reps": r} for p, r in ramp]


def apply(items: list[dict], profile: dict | None, warmups: bool = True) -> list[dict]:
    """Devolve cópias dos itens com descanso pela evidência e séries de aquecimento (quando ligadas)."""
    from .generator import GOALS, tier_for  # import tardio: o gerador também usa este módulo

    prof = profile or {}
    tier = tier_for(prof.get("training_months"))
    goal_mult = (GOALS.get(prof.get("goal") or "hipertrofia") or GOALS["hipertrofia"])["rest"]
    pref = prof.get("rest_pref") or "padrao"
    warm_on = (prof.get("warmup_sets") or "auto") != "off"
    out, seen, first_done = [], set(), False
    for raw in items:
        it = dict(raw)
        ex = catalog.get(it.get("exercise_id")) or {}
        if (it.get("phase") or "principal") == "principal" and ex.get("kind") != "cardio" and not it.get("endurance"):
            role = it.get("role")
            if role in ("main", "sec", "acc") and it.get("superset") != "A" and not it.get("rest_custom"):
                it["rest_s"] = rest_for(role, tier, goal_mult, pref)
            if warmups and "warmup_sets" not in it:
                it["warmup_sets"] = warmup_plan(it, ex, not first_done, seen) if warm_on else []
            if warm_on and it.get("notes"):
                it["notes"] = APPROACH_NOTE.sub("", it["notes"]).strip(" ·") or None
            if ex.get("mechanic") == "compound" or it.get("role") in ("main", "sec"):
                first_done = first_done or not (it.get("duration_s") or ex.get("timed"))
            seen |= set(ex.get("primaryMuscles") or [])
        out.append(it)
    return out


def warm_load(work_kg: float | None, pct: int, equipment: str | None) -> float | None:
    if not work_kg:
        return None
    step = 2.0 if equipment == "dumbbell" else 2.5
    v = round(work_kg * pct / 100 / step) * step
    return max(step, v)
