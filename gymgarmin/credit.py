"""Crédito do que foi treinado e saldo da semana por músculo.

Contas puras (sem gravar nada), usadas pela agenda (schedule.py):

- Volume em séries FRACIONADAS: músculo primário do exercício conta 1,0 e secundário 0,5. Entre as formas
  testadas de contar séries indiretas (0; 0,5; 1), a fracionada foi a que melhor explicou hipertrofia nas
  meta-regressões de Pelland et al. (preprint SportRxiv 2024; 67 estudos).
- Só séries de TRABALHO contam: aquecimento e série não feita ficam fora (ver analysis._hard_sets).
- COBERTURA de um treino da agenda por uma sessão registrada: quanto do que estava planejado, músculo a
  músculo, a sessão realmente entregou. É o que decide se um treino livre "foi" o treino do dia.
- SALDO da semana (segunda a domingo) por músculo: feito de verdade + o que ainda está na agenda, contra a
  referência da semana. A referência é o que a planilha entrega (limitada ao alvo do plano), então semana
  de deload não vira "falta".

Limiares (COVER_*, FLOOR, MIN_GAP) são decisões de produto/engenharia, não números da literatura.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from . import analysis, catalog
from .muscles import MUSCLES, PRIMARY_WEIGHT, SECONDARY_WEIGHT, muscle_pt

COVER_CLOSE = 0.70   # sessão cobre ≥ 70% do planejado → fecha o treino
COVER_ASK = 0.25     # entre 25% e 70% → o app pergunta; abaixo disso é treino extra
FLOOR = 0.65         # abaixo de 65% da referência da semana o músculo fica "bem abaixo do plano"
MIN_GAP = 2.0        # diferenças menores que 2 séries não viram aviso nem reforço
MIN_REF = 3.0        # músculos com referência menor que isso não entram no saldo


# ------------------------------------------------------------------ sessões registradas ----

def session_load(session: dict) -> dict[str, float]:
    """Séries fracionadas por músculo de uma sessão registrada (só séries de trabalho)."""
    return {m: v["sets"] for m, v in analysis.session_muscle_load(session).items() if v["sets"]}


def session_direct(session: dict) -> dict[str, float]:
    return {m: v["direct"] for m, v in analysis.session_muscle_load(session).items() if v["direct"]}


def has_sets(session: dict) -> bool:
    return any(analysis._hard_sets(it) for it in session.get("items", [])
               if (catalog.get(it.get("exercise_id")) or {}).get("kind") == "strength")


def work_sets(session: dict) -> int:
    """Quantas séries de trabalho de musculação a sessão tem (aquecimento e série não feita ficam fora)."""
    return sum(len(analysis._hard_sets(it)) for it in session.get("items", [])
               if (catalog.get(it.get("exercise_id")) or {}).get("kind") == "strength")


def is_free_workout(session: dict) -> bool:
    """Treino registrado pelo app, com séries, sem vínculo com a agenda (o botão "Treino livre")."""
    return bool(session.get("client_id")) and not session.get("schedule_id") and has_sets(session)


# ------------------------------------------------------------------ treinos da agenda ----

def strength_items(e: dict) -> list[dict]:
    return [i for i in e.get("items", []) if (i.get("phase") or "principal") == "principal"
            and (catalog.get(i.get("exercise_id")) or {}).get("kind") == "strength"]


def _sets(it: dict, base: bool) -> int:
    n = int(it.get("sets") or 0)
    return max(0, n - int(it.get("added") or 0)) if base else n


def entry_load(e: dict, base: bool = False) -> dict[str, float]:
    """Séries fracionadas planejadas por músculo. `base` = como foi planejado, sem compensação de treino pulado."""
    out: dict[str, float] = defaultdict(float)
    for it in strength_items(e):
        if base and it.get("added_from"):
            continue
        ex = catalog.get(it["exercise_id"])
        n = _sets(it, base)
        for m in ex["primaryMuscles"]:
            out[m] += n * PRIMARY_WEIGHT
        for m in ex["secondaryMuscles"]:
            out[m] += n * SECONDARY_WEIGHT
    return dict(out)


def entry_direct(e: dict, base: bool = False) -> dict[str, float]:
    """Séries DIRETAS por músculo (só músculos primários)."""
    out: dict[str, float] = defaultdict(float)
    for it in strength_items(e):
        if base and it.get("added_from"):
            continue
        for m in catalog.get(it["exercise_id"])["primaryMuscles"]:
            out[m] += _sets(it, base)
    return dict(out)


def coverage(session: dict, entry: dict) -> float | None:
    """Fração do treino planejado (músculo a músculo) que a sessão entregou. None = treino sem musculação."""
    planned = entry_load(entry, base=True)
    total = sum(planned.values())
    if total <= 0:
        return None
    done = session_load(session)
    return round(sum(min(done.get(m, 0.0), v) for m, v in planned.items()) / total, 2)


# ------------------------------------------------------------------ saldo da semana ----

def _moved_away(e: dict) -> bool:
    """Treino pulado que foi MOVIDO para outro dia: a cópia nova é que conta no plano da semana."""
    return e.get("status") == "pulado" and (e.get("skip_result") or {}).get("strategy") == "mover"


def week_balance(entries: list[dict], sessions: list[dict], targets: dict | None, today: date,
                 exclude_id: int | None = None) -> dict[str, dict]:
    """Por músculo: feito (real), agenda (o que ainda vem), ref, piso, projetado, falta e sobra.

    `exclude_id` tira um treino da agenda (para responder "e se eu pular este?").
    """
    strength = [e for e in entries if e.get("kind") == "forca" and e.get("status") != "substituido" and not _moved_away(e)]
    done: dict[str, float] = defaultdict(float)
    direct_done: dict[str, float] = defaultdict(float)
    dates_with_sets = set()
    for s in sessions:
        if has_sets(s):
            dates_with_sets.add(str(s.get("date"))[:10])
        for m, v in analysis.session_muscle_load(s).items():
            done[m] += v["sets"]
            direct_done[m] += v["direct"]
    for e in strength:
        # feito só pelo relógio (sem séries registradas): vale o que estava planejado
        if e.get("status") == "feito" and e["date"] not in dates_with_sets:
            for m, v in entry_load(e, base=True).items():
                done[m] += v
            for m, v in entry_direct(e, base=True).items():
                direct_done[m] += v
    scheduled: dict[str, float] = defaultdict(float)
    plan: dict[str, float] = defaultdict(float)
    for e in strength:
        for m, v in entry_load(e, base=True).items():
            plan[m] += v
        if e.get("status") == "planejado" and e["date"] >= today.isoformat() and e.get("id") != exclude_id:
            for m, v in entry_load(e).items():
                scheduled[m] += v
    out: dict[str, dict] = {}
    for m in MUSCLES:
        t = (targets or {}).get(m)
        ref = min(float(t), plan.get(m, 0.0)) if t else plan.get(m, 0.0)
        proj = done.get(m, 0.0) + scheduled.get(m, 0.0)
        out[m] = {"key": m, "pt": muscle_pt(m), "done": round(done.get(m, 0.0), 1), "direct_done": round(direct_done.get(m, 0.0), 1),
                  "scheduled": round(scheduled.get(m, 0.0), 1), "ref": round(ref, 1), "floor": round(FLOOR * ref, 1),
                  "projected": round(proj, 1), "missing": round(max(0.0, ref - proj), 1), "surplus": round(max(0.0, proj - ref), 1)}
        b = out[m]
        b["state"] = ("—" if ref < MIN_REF else "falta" if b["missing"] >= MIN_GAP
                      else "sobrando" if b["surplus"] >= 3 and proj > 1.2 * ref else "em_dia")
    return out


def balance_list(bal: dict[str, dict]) -> list[dict]:
    """Saldo para a tela: só músculos com referência relevante, os que mais faltam primeiro."""
    rows = [b for b in bal.values() if b["ref"] >= MIN_REF]
    rows.sort(key=lambda b: (-b["missing"], -b["ref"]))
    return rows
