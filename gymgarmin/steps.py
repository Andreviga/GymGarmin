"""Passos do relógio: quanto vem dos treinos (corrida, caminhada) e quanto é do dia a dia.

O total de passos do dia mistura duas coisas: a corrida ou caminhada registrada como treino e o
movimento do dia a dia. Uma corrida de 40 min já soma uns 6,5 mil passos: não é "dia mais ativo",
é o treino. O app separa as duas partes, acompanha o dia a dia e, quando o dia a dia foge muito do
costume sem treino que explique (festival, show, viagem, dia inteiro em pé), PERGUNTA o que foi.
Confirmado, o dia vira uma caminhada leve estimada pelos passos: entra na carga de treino, na
leitura do dia seguinte e deixa de distorcer a média do dia a dia.

Os limites abaixo são escolhas de produto (conservadoras), não valores de estudo.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, timedelta

from . import load

RUN_SPM = 165          # passos por minuto correndo: usado só quando o relógio não mandou os passos da atividade
WALK_SPM = 110
HIKE_SPM = 100
MACHINE_SPM = {"cardio_eliptico": 90, "cardio_escada": 80,  # o relógio no pulso conta parte do movimento
               # outras atividades sem os passos medidos (ex.: vindas do celular): estimativa conservadora
               "atividade_coletivo": 95, "atividade_raquete": 70, "atividade_danca": 80, "atividade_hiit": 50, "atividade_luta": 35,
               "atividade_outra": 40}
EVENT_SPM = 85         # festival/passeio: anda devagar e para muito (vira o tempo estimado "em pé e andando")
BASE_DAYS = 28         # janela do "costume" (mediana: um dia fora da curva não puxa a referência)
MIN_BASE_DAYS = 7
SPIKE_FACTOR = 2.0     # dia a dia ≥ 2× o costume…
SPIKE_EXTRA = 5000     # …e pelo menos 5 mil passos acima dele…
SPIKE_MIN = 9000       # …e nunca menos que 9 mil: só então vale perguntar
ASK_DAYS = 10          # só pergunta sobre dias recentes (depois disso a pessoa não lembra)
MIN_EVENT_STEPS = 1500  # abaixo disso não vale criar uma caminhada estimada
MIN_VALID_STEPS = 500   # dia fechado com menos que isso = relógio fora do pulso: não entra no costume nem nas médias
NORMAL_MARGIN = 1.25    # depois de um "dia normal"/"trabalho", só pergunta de novo acima desse dia com 25% de folga
NORMAL_DAYS = 60
MAX_EVENT_MIN = 360
SOURCE = "passos"      # sessões criadas a partir dos passos (caminhada estimada do evento)

# `tss_h`: carga por hora da caminhada estimada (andar devagar ≈ 16/h; caminhada de verdade ≈ 25/h; corrida leve ≈ 45/h)
TAGS = {
    "festival": {"label": "Festival, show ou festa", "short": "Festival ou festa", "session": True, "zone": 1, "tss_h": 16, "spm": EVENT_SPM,
                 "name": "Festival, show ou festa"},
    "passeio": {"label": "Passeio, viagem ou turismo", "short": "Passeio ou viagem", "session": True, "zone": 1, "tss_h": 16, "spm": EVENT_SPM,
                "name": "Passeio ou viagem"},
    "caminhada": {"label": "Caminhada ou trilha que não registrei", "short": "Caminhada sem registro", "session": True, "zone": 2, "tss_h": 25, "spm": WALK_SPM,
                  "name": "Caminhada não registrada"},
    "trabalho": {"label": "Trabalho ou dia inteiro em pé", "short": "Trabalho em pé", "session": False, "name": "Dia inteiro em pé"},
    "nada": {"label": "Nada de especial, foi um dia normal", "short": "Dia normal", "session": False, "name": "Dia normal"},
}


def item_steps(it: dict) -> float:
    """Passos de um item de treino: os medidos pelo relógio, ou uma estimativa pela duração."""
    if it.get("steps"):
        return float(it["steps"])
    mins = float(it.get("duration_min") or 0)
    if mins <= 0:
        return 0.0
    sp = load.sport_of(it)
    if sp == "corrida":
        return mins * float(it.get("cadence") or RUN_SPM)
    if sp == "caminhada":
        return mins * float(it.get("cadence") or WALK_SPM)
    if sp == "trilha":
        return mins * HIKE_SPM
    return mins * MACHINE_SPM.get(it.get("exercise_id"), 0)


def _what(it: dict) -> str | None:
    sp = load.sport_of(it)
    if sp in ("corrida", "caminhada", "trilha"):
        return sp
    eid = str(it.get("exercise_id") or "")
    if eid.startswith("atividade_"):
        return "atividade"
    return {"cardio_eliptico": "eliptico", "cardio_escada": "escada"}.get(eid)


def is_event(session: dict) -> bool:
    return session.get("source") == SOURCE


def split(days: dict[str, dict], sessions: list[dict]) -> list[dict]:
    """Por dia (do mais antigo ao mais novo): total do relógio, passos dos treinos e passos do dia a dia.

    `estimated` = algum treino do dia veio sem os passos medidos (conta pela duração × cadência típica)."""
    by_day: dict[str, dict[str, float]] = {}
    events: dict[str, dict] = {}
    guessed: set[str] = set()
    for s in sessions:
        d = str(s.get("date"))[:10]
        for it in s.get("items") or []:
            n = item_steps(it)
            w = _what(it)
            if n <= 0 or not w:
                continue
            if is_event(s):
                w = "evento"
                events[d] = {"session_id": s.get("id"), "name": s.get("day_name"), "steps": round(n), "tag": it.get("event")}
            elif not it.get("steps"):
                guessed.add(d)
            by_day.setdefault(d, {})
            by_day[d][w] = by_day[d].get(w, 0) + n
    out = []
    for d in sorted(days):
        total = (days[d] or {}).get("steps")
        if total is None:
            continue
        total = float(total)
        parts = dict(by_day.get(d) or {})
        raw = sum(parts.values())
        if raw > total > 0:  # estimativa passou do total do relógio: reduz as partes na mesma proporção
            parts = {k: v * total / raw for k, v in parts.items()}
        workout = min(total, raw)
        out.append({"date": d, "total": round(total), "workout": round(workout), "daily": round(total - workout),
                    "parts": {k: round(v) for k, v in parts.items()}, "event": events.get(d),
                    "estimated": d in guessed, "goal": days[d].get("step_goal"), "valid": total >= MIN_VALID_STEPS})
    return out


def _is_event_tag(tags: dict, day: str) -> bool:
    return bool(TAGS.get((tags.get(day) or {}).get("tag"), {}).get("session"))


def baseline(rows: list[dict], before: str | None = None, tags: dict | None = None) -> float | None:
    """O costume do dia a dia: mediana dos últimos 28 dias com dado.

    Dias de evento confirmado (festival, passeio, caminhada) ficam fora; "dia normal" e "trabalho" ficam dentro:
    se a pessoa diz que aquilo é rotina, a régua sobe e o app pergunta menos."""
    tags = tags or {}
    vals = [r["daily"] for r in rows if (before is None or r["date"] < before) and r.get("valid", True)
            and not r.get("event") and not _is_event_tag(tags, r["date"])]
    vals = vals[-BASE_DAYS:]
    return float(statistics.median(vals)) if len(vals) >= MIN_BASE_DAYS else None


def normal_ceiling(rows: list[dict], tags: dict, day: str) -> float:
    """O maior dia a dia que a pessoa já disse ser normal (ou trabalho) nos 60 dias antes de `day`: abaixo dele
    (com folga) o app não pergunta de novo — é assim que a resposta "dia normal" passa a valer."""
    lo = (date.fromisoformat(day) - timedelta(days=NORMAL_DAYS)).isoformat()
    vals = [r["daily"] for r in rows if lo <= r["date"] < day and (tags.get(r["date"]) or {}).get("tag") in ("nada", "trabalho")]
    return max(vals, default=0.0)


def threshold(base: float) -> float:
    return max(SPIKE_FACTOR * base, base + SPIKE_EXTRA, SPIKE_MIN)


def is_spike(daily: float, base: float | None) -> bool:
    return base is not None and daily >= threshold(base)


def _late_night(rec: dict | None) -> bool:
    """Dormiu entre 0h45 e 7h? (a noite que começa depois de um dia de festa aparece no registro do dia seguinte)"""
    start = ((rec or {}).get("sleep") or {}).get("start")
    return bool(start) and "00:45" <= str(start)[:5] < "07:00"


def hint(next_day: dict | None, d: date) -> str:
    """Palpite do que foi (só define qual opção aparece primeiro): noite longa ou sexta/sábado → evento."""
    return "festival" if _late_night(next_day) or d.weekday() in (4, 5) else "passeio"


def summary(days: dict[str, dict], sessions: list[dict], tags: dict, today: date, ask: bool = True) -> dict:
    """Tudo o que as telas precisam: série de 28 dias, médias, costume e os dias que pedem uma pergunta.

    `ask=False` (ex.: a importação de treinos do relógio ainda não terminou) segura as perguntas: uma corrida que
    ainda não chegou pareceria "dia a dia" e viraria pergunta à toa."""
    rows = split(days, sessions)
    closed = [r for r in rows if r["date"] < today.isoformat()]  # hoje ainda está em andamento: fora do costume e das médias
    base = baseline(closed, tags=tags)
    pending = []
    for r in closed if ask else []:
        d = date.fromisoformat(r["date"])
        if r["date"] in tags or r.get("event") or (today - d).days > ASK_DAYS or not r.get("valid", True):
            continue
        b = baseline(rows, before=r["date"], tags=tags)
        if b is None:
            continue  # poucos dias válidos antes desse: ainda não há "costume" para comparar
        if is_spike(r["daily"], b) and r["daily"] > NORMAL_MARGIN * normal_ceiling(rows, tags, r["date"]):
            nxt = days.get((d + timedelta(days=1)).isoformat())
            pending.append({**r, "baseline": round(b), "extra": round(r["daily"] - b), "hint": hint(nxt, d)})
    week = [r for r in closed if 1 <= (today - date.fromisoformat(r["date"])).days <= 7]
    prev = [r for r in closed if 8 <= (today - date.fromisoformat(r["date"])).days <= 35]

    def avg(rs, k, skip_events=False):
        vals = [r[k] for r in rs if r.get("valid", True) and not (skip_events and (r.get("event") or _is_event_tag(tags, r["date"])))]
        return round(statistics.mean(vals)) if vals else None

    recent = None  # evento ontem ou anteontem: contexto para o treino de hoje
    for r in rows:
        age = (today - date.fromisoformat(r["date"])).days
        t = tags.get(r["date"]) or {}
        if 1 <= age <= 2 and t.get("tag") in ("festival", "passeio", "caminhada", "trabalho"):
            recent = {"date": r["date"], "tag": t["tag"], "label": TAGS[t["tag"]]["name"], "steps": r["total"], "days_ago": age,
                      "late_night": _late_night(days.get((date.fromisoformat(r["date"]) + timedelta(days=1)).isoformat()))}
    cut = (today - timedelta(days=56)).isoformat()
    return {"has_data": bool(rows), "baseline": round(base) if base is not None else None,
            "threshold": round(threshold(base)) if base is not None else None,
            "avg7": {"total": avg(week, "total"), "workout": avg(week, "workout"), "daily": avg(week, "daily", True)},
            "avg_prev": {"daily": avg(prev, "daily", True), "total": avg(prev, "total")},
            "estimated": any(r["estimated"] for r in rows[-28:]),
            "series": rows[-28:], "pending": pending[-2:][::-1], "recent_event": recent,
            "tags": {d: {**t, "label": (TAGS.get(t.get("tag")) or {}).get("label")} for d, t in tags.items() if d >= cut},
            "options": [{"id": k, "label": v["label"], "short": v["short"]} for k, v in TAGS.items()]}


def event_session(day: str, extra_steps: float, tag: str) -> dict | None:
    """A caminhada estimada de um dia de evento: entra na carga e explica os passos "a mais" do dia."""
    cfg = TAGS.get(tag)
    if not cfg or not cfg["session"] or extra_steps < MIN_EVENT_STEPS:
        return None
    mins = min(MAX_EVENT_MIN, round(extra_steps / cfg["spm"]))
    return {"date": f"{day}T18:00:00", "source": SOURCE, "ext_id": f"{SOURCE}:{day}", "day_name": cfg["name"],
            "notes": f"Estimado pelos passos do relógio: {fmt(extra_steps)} passos além do seu costume.",
            "duration_min": mins,
            "items": [{"exercise_id": "caminhada_rua", "sport": "caminhada", "duration_min": mins, "zone": cfg["zone"],
                       "steps": round(extra_steps), "tss": round(mins / 60 * cfg["tss_h"], 1), "estimated": True, "event": tag}]}


def fmt(n: float) -> str:
    return f"{round(n):,}".replace(",", ".")


def activity_text(s: dict) -> str | None:
    """Frase para o painel de saúde: dia a dia separado dos treinos."""
    a = s.get("avg7") or {}
    if a.get("total") is None:
        return None
    if not a.get("workout"):
        return f"{fmt(a['total'])} passos/dia"
    daily = a["daily"] if a.get("daily") is not None else a["total"] - a["workout"]
    return f"{fmt(daily)} passos/dia no dia a dia; com os treinos (corrida, caminhada), {fmt(a['total'])}"


def load_tags(raw: str | None) -> dict:
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}
