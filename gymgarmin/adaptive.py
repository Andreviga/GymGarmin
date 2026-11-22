"""O treino que aprende com o histórico: volume por músculo, semana leve pelos dados e nota de esforço.

Volume (Scarpelli 2022; Barsuhn 2025; Pelland 2026):
- a força estimada (e1RM: carga × (1 + (reps + sobra)/30)) de cada exercício principal é comparada entre o começo e o
  fim das últimas ~6 semanas;
- músculo progredindo (> +0,5%/semana): o volume fica;
- parado (variação < 1% em 3+ semanas) e se recuperando bem: +1 série por semana (no máximo 2 por ajuste);
- caindo (−3% ou mais) e recuperando mal: −1 série nos acessórios desse músculo.

Semana leve pelos dados (Bell 2022/2023; Coleman 2024): 2 ou mais destes sinais nos últimos 7–10 dias —
força caindo em 2+ exercícios principais, esforço médio dos treinos ≥ 8,5/10, média de sono < 6 h, VFC da semana abaixo
do normal da pessoa. A semana leve corta ~40% das séries e mantém a carga (não é semana parada: semana parada custa força).

Nota de esforço (Foster 1998): carga da sessão = esforço 0–10 × minutos; monotonia = média ÷ desvio da carga diária.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import catalog
from .muscles import MUSCLES

WINDOW_DAYS = 42
MIN_POINTS = 4
MIN_SPAN_DAYS = 18
PROGRESS_PER_WEEK = 0.005
FALL = -0.03
COOLDOWN_DAYS = 14


def _date(s) -> date | None:
    try:
        return datetime.fromisoformat(str(s.get("date"))[:19]).date()
    except (TypeError, ValueError):
        return None


def best_e1rm(item: dict) -> float | None:
    best = None
    for x in item.get("sets") or []:
        if not isinstance(x, dict) or x.get("done") is False:
            continue
        load, reps = x.get("load_kg"), x.get("reps")
        try:
            load, reps = float(load), int(reps)
        except (TypeError, ValueError):
            continue
        if load <= 0 or reps <= 0 or reps > 20:
            continue
        rpe = x.get("rpe")
        rir = 10 - float(rpe) if rpe and 6 <= float(rpe) <= 10 else 0
        v = load * (1 + (reps + rir) / 30)
        best = v if best is None or v > best else best
    return best


def series(sessions: list[dict], exercise_id: str, today: date, days: int = WINDOW_DAYS) -> list[tuple[date, float]]:
    lo = today - timedelta(days=days)
    out = []
    for s in sessions:
        d = _date(s)
        if not d or d < lo or d > today:
            continue
        vals = [best_e1rm(it) for it in s.get("items") or [] if it.get("exercise_id") == exercise_id]
        vals = [v for v in vals if v]
        if vals:
            out.append((d, max(vals)))
    out.sort()
    return out


def trend(points: list[tuple[date, float]]) -> dict | None:
    """Variação entre o começo e o fim (média dos 2 primeiros × 2 últimos) e por semana."""
    if len(points) < MIN_POINTS or (points[-1][0] - points[0][0]).days < MIN_SPAN_DAYS:
        return None
    first = sum(v for _, v in points[:2]) / 2
    last = sum(v for _, v in points[-2:]) / 2
    if first <= 0:
        return None
    change = last / first - 1
    weeks = max(1.0, (points[-1][0] - points[0][0]).days / 7)
    rate = change / weeks
    status = "caindo" if change <= FALL else "progredindo" if rate > PROGRESS_PER_WEEK else "parado"
    return {"change": round(change, 4), "per_week": round(rate, 4), "status": status, "n": len(points)}


def plan_lifts(plan: dict) -> dict[str, dict]:
    """Exercícios principais/secundários do plano → músculos principais."""
    out = {}
    for d in plan.get("days") or []:
        for it in d.get("items") or []:
            if (it.get("phase") or "principal") != "principal" or it.get("role") not in ("main", "sec"):
                continue
            ex = catalog.get(it.get("exercise_id")) or {}
            if ex.get("kind") == "strength":
                out[ex["id"]] = {"muscles": ex.get("primaryMuscles") or [], "name": ex.get("name")}
    return out


def muscle_status(plan: dict, sessions: list[dict], today: date) -> dict[str, dict]:
    lifts = plan_lifts(plan)
    by_m: dict[str, list[dict]] = defaultdict(list)
    for eid, info in lifts.items():
        t = trend(series(sessions, eid, today))
        if not t:
            continue
        for m in info["muscles"]:
            by_m[m].append({"exercise_id": eid, "name": info["name"], **t})
    out = {}
    for m, rows in by_m.items():
        st = "caindo" if any(r["status"] == "caindo" for r in rows) else \
            "progredindo" if any(r["status"] == "progredindo" for r in rows) else "parado"
        out[m] = {"status": st, "lifts": rows, "pt": MUSCLES.get(m, {}).get("pt", m)}
    return out


# ------------------------------------------------------------- esforço e recuperação ---

def session_minutes(s: dict) -> float:
    m = s.get("duration_min") or (s.get("garmin_summary") or {}).get("duration_min")
    if m:
        return float(m)
    sets = sum(len(it["sets"]) for it in s.get("items") or [] if isinstance(it.get("sets"), list))
    cardio = sum(float(it.get("duration_min") or 0) for it in s.get("items") or [] if not it.get("sets"))
    return sets * 2.6 + cardio


def srpe_week(sessions: list[dict], today: date) -> dict:
    """Carga da semana pela nota de esforço (Foster): soma, média da nota e monotonia (só com nota em 2+ treinos)."""
    lo = today - timedelta(days=6)
    daily = defaultdict(float)
    notes = []
    for s in sessions:
        d = _date(s)
        if not d or d < lo or d > today or s.get("srpe") is None:
            continue
        try:
            r = float(s["srpe"])
        except (TypeError, ValueError):
            continue
        notes.append(r)
        daily[d] += r * session_minutes(s)
    if len(notes) < 2:
        return {"n": len(notes)}
    loads = [daily.get(lo + timedelta(days=i), 0.0) for i in range(7)]
    mean = sum(loads) / 7
    sd = math.sqrt(sum((x - mean) ** 2 for x in loads) / 7)
    return {"n": len(notes), "avg": round(sum(notes) / len(notes), 1), "load": round(sum(loads)),
            "monotony": round(mean / sd, 2) if sd > 0 else None}


def recovery_signals(health_base: dict | None, srpe: dict) -> list[str]:
    hb = health_base or {}
    out = []
    if srpe.get("avg") is not None and srpe["avg"] >= 8.5:
        out.append(f"treinos da semana com esforço médio {srpe['avg']:g}/10")
    if hb.get("sleep_avg7") is not None and hb["sleep_avg7"] < 6:
        out.append(f"média de sono de {hb['sleep_avg7']:.1f} h nos últimos 7 dias".replace(".", ","))
    if hb.get("hrv_band") == "baixa":
        out.append("variabilidade cardíaca da semana abaixo do seu normal")
    return out


# --------------------------------------------------------------------- propostas ---

def proposals(plan: dict | None, sessions: list[dict], today: date, health_base: dict | None = None,
              state: dict | None = None) -> dict:
    """O que os dados sugerem agora: semana leve, +1 série onde parou, −1 onde está caindo."""
    if not plan:
        return {"items": []}
    st = state or {}
    ms = muscle_status(plan, sessions, today)
    sr = srpe_week(sessions, today)
    rec = recovery_signals(health_base, sr)
    falling = sorted({r["name"] for m in ms.values() for r in m["lifts"] if r["status"] == "caindo"})
    signals = list(rec) + ([f"força caindo em {', '.join(falling[:3])}"] if len(falling) >= 2 else [])
    out = []
    snooze = st.get("snooze") or {}
    def ok(kind):  # noqa: E306
        until = snooze.get(kind)
        return not until or until < today.isoformat()
    if len(signals) >= 2 and ok("deload"):
        out.append({"kind": "deload", "title": "Seus dados pedem uma semana mais leve",
                    "why": signals, "action": "Aplicar nesta semana: −40% das séries, mesma carga, esforço até 7"})
    last = st.get("last_volume")
    recent_change = last and (today - date.fromisoformat(last)).days < COOLDOWN_DAYS
    if not recent_change and ok("volume") and not any(p["kind"] == "deload" for p in out):
        good = len(rec) == 0
        up = [m for m, v in ms.items() if v["status"] == "parado" and good][:2]
        down = [m for m, v in ms.items() if v["status"] == "caindo" and not good][:2]
        if up:
            out.append({"kind": "volume_up", "muscles": up, "title": "+1 série por semana para " + ", ".join(ms[m]["pt"] for m in up),
                        "why": [f"{ms[m]['pt']}: força parada há 3+ semanas ({_pct(ms[m])}) e você está se recuperando bem" for m in up],
                        "action": "Aplicar a partir da próxima semana"})
        if down:
            out.append({"kind": "volume_down", "muscles": down, "title": "−1 série nos acessórios de " + ", ".join(ms[m]["pt"] for m in down),
                        "why": [f"{ms[m]['pt']}: força caindo ({_pct(ms[m])})" for m in down] + rec,
                        "action": "Aplicar a partir da próxima semana"})
    return {"items": out, "muscles": ms, "srpe": sr}


def _pct(m: dict) -> str:
    ch = max((r["change"] for r in m["lifts"]), key=abs, default=0)
    return f"{ch * 100:+.1f}%".replace(".", ",")


def apply_volume(plan: dict, muscles: list[str], delta: int) -> list[str]:
    """Muda as séries do plano para os músculos: +1 no 1º secundário/acessório de cada um (−1 nos acessórios)."""
    changed = []
    for m in muscles:
        done = False
        for d in plan.get("days") or []:
            if done:
                break
            for it in d.get("items") or []:
                ex = catalog.get(it.get("exercise_id")) or {}
                if (it.get("phase") or "principal") != "principal" or m not in (ex.get("primaryMuscles") or []):
                    continue
                if delta > 0 and it.get("role") in ("sec", "acc") and int(it.get("sets") or 0) < 6:
                    it["sets"] = int(it["sets"]) + 1
                elif delta < 0 and it.get("role") == "acc" and int(it.get("sets") or 0) > 2:
                    it["sets"] = int(it["sets"]) - 1
                else:
                    continue
                changed.append(f"{ex.get('name')}: {it['sets'] - delta} → {it['sets']} séries")
                done = True
                break
    return changed


def load_state(raw: str | None) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}
