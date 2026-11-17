"""Histórico de evolução de cargas por exercício.

Trabalha só com as sessões registradas (app ou Garmin). Para cada exercício de
força, cada sessão vira um ponto com: carga máxima, melhor série, 1RM estimado
(Epley), volume (carga × reps) e reps totais. Daí saem os recordes (PRs), a
tendência das últimas semanas e o aviso de estagnação.

Exercícios sem carga (peso corporal) acompanham reps máximas; isométricos
(prancha etc.) acompanham o tempo máximo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import catalog

# Acima disso a fórmula de Epley superestima bastante; o ponto continua no
# gráfico, mas marcado como "estimativa fraca".
E1RM_MAX_REPS = 12


def e1rm(load: float, reps: int) -> float | None:
    """1RM estimado (Epley). Uma repetição é a própria carga."""
    if not load or not reps or reps <= 0:
        return None
    if reps == 1:
        return round(load, 1)
    return round(load * (1 + reps / 30), 1)


def _day(iso: str) -> date:
    return datetime.fromisoformat(str(iso)[:19]).date()


def _work_sets(item: dict) -> list[dict]:
    """Séries efetivas: feitas (ou sem marcação, como as do Garmin) e sem aquecimento."""
    out = []
    for s in item.get("sets") or []:
        if not s.get("done", True) or s.get("warmup"):
            continue
        reps = int(s.get("reps") or 0)
        dur = int(s.get("duration_s") or 0)
        if reps <= 0 and dur <= 0:
            continue
        out.append({"reps": reps, "load_kg": float(s.get("load_kg") or 0), "duration_s": dur,
                    "rpe": s.get("rpe")})
    return out


def _metric(sets: list[dict]) -> str:
    if any(s["load_kg"] > 0 and s["reps"] > 0 for s in sets):
        return "carga"
    if any(s["reps"] > 0 for s in sets):
        return "reps"
    return "tempo"


def session_point(session: dict, item: dict) -> dict | None:
    sets = _work_sets(item)
    if not sets:
        return None
    metric = _metric(sets)
    loaded = [s for s in sets if s["load_kg"] > 0 and s["reps"] > 0]
    best = None
    if loaded:
        best = max(loaded, key=lambda s: (e1rm(s["load_kg"], s["reps"]) or 0, s["load_kg"]))
    rpes = [float(s["rpe"]) for s in sets if s.get("rpe") not in (None, "")]
    point = {
        "session_id": session.get("id"),
        "date": str(session.get("date"))[:10],
        "source": session.get("source") or "app",
        "sets": len(sets),
        "reps_total": sum(s["reps"] for s in sets),
        "top_load": max((s["load_kg"] for s in loaded), default=0.0),
        "volume": round(sum(s["load_kg"] * s["reps"] for s in sets), 1),
        "max_reps": max((s["reps"] for s in sets), default=0),
        "max_duration_s": max((s["duration_s"] for s in sets), default=0),
        "best_set": {"load_kg": best["load_kg"], "reps": best["reps"]} if best else None,
        "e1rm": e1rm(best["load_kg"], best["reps"]) if best else None,
        "e1rm_weak": bool(best and best["reps"] > E1RM_MAX_REPS),
        "avg_rpe": round(sum(rpes) / len(rpes), 1) if rpes else None,
        "metric": metric,
        "detail": [{k: s[k] for k in ("reps", "load_kg", "duration_s")} for s in sets],
    }
    point["value"] = _value(point, metric)
    return point


def _value(p: dict, metric: str) -> float:
    """Número principal do gráfico para cada tipo de exercício."""
    if metric == "carga":
        return p["e1rm"] or p["top_load"]
    if metric == "reps":
        return p["max_reps"]
    return p["max_duration_s"]


def _points_by_exercise(sessions: list[dict]) -> dict[str, list[dict]]:
    by_ex: dict[str, list[dict]] = {}
    for s in sorted(sessions, key=lambda x: str(x.get("date"))):
        for it in s.get("items") or []:
            eid = it.get("exercise_id")
            if not eid or not it.get("sets"):
                continue
            p = session_point(s, it)
            if p:
                by_ex.setdefault(eid, []).append(p)
    # Um mesmo exercício duas vezes no dia (ex.: duas sessões) vira um ponto só
    for eid, pts in by_ex.items():
        merged: dict[str, dict] = {}
        for p in pts:
            m = merged.get(p["date"])
            merged[p["date"]] = p if m is None else _merge(m, p)
        by_ex[eid] = list(merged.values())
    return by_ex


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    out["sets"] = a["sets"] + b["sets"]
    out["reps_total"] = a["reps_total"] + b["reps_total"]
    out["volume"] = round(a["volume"] + b["volume"], 1)
    out["detail"] = a["detail"] + b["detail"]
    for k in ("top_load", "max_reps", "max_duration_s"):
        out[k] = max(a[k], b[k])
    if (b["e1rm"] or 0) > (a["e1rm"] or 0):
        out.update(e1rm=b["e1rm"], best_set=b["best_set"], e1rm_weak=b["e1rm_weak"])
    if a["metric"] != b["metric"]:
        out["metric"] = "carga" if "carga" in (a["metric"], b["metric"]) else "reps"
    out["value"] = _value(out, out["metric"])
    return out


PR_KINDS = (("e1rm", "1RM estimado"), ("top_load", "carga máxima"), ("volume", "volume"),
            ("max_reps", "reps"), ("max_duration_s", "tempo"))


def _mark_prs(points: list[dict]) -> None:
    """Marca em cada ponto quais recordes ele bateu (o primeiro treino não conta)."""
    best: dict[str, float] = {}
    metric = points[-1]["metric"] if points else "carga"
    relevant = {"carga": ("e1rm", "top_load", "volume"), "reps": ("max_reps", "volume"),
                "tempo": ("max_duration_s",)}[metric]
    for i, p in enumerate(points):
        p["prs"] = []
        for key, _label in PR_KINDS:
            if key not in relevant:
                continue
            v = p.get(key) or 0
            if v <= 0:
                continue
            if i > 0 and v > best.get(key, 0) + 1e-9:
                p["prs"].append(key)
            best[key] = max(best.get(key, 0), v)


def _slope_per_week(points: list[dict]) -> float | None:
    """Inclinação (regressão linear) do valor principal, em unidades por semana."""
    if len(points) < 3:
        return None
    xs = [(_day(p["date"]) - _day(points[0]["date"])).days / 7 for p in points]
    ys = [p["value"] for p in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def summary(points: list[dict], today: date | None = None) -> dict:
    today = today or date.today()
    if not points:
        return {"sessions": 0}
    metric = points[-1]["metric"]
    first, last = points[0], points[-1]
    recent = [p for p in points if _day(p["date"]) >= today - timedelta(weeks=8)]
    slope = _slope_per_week(recent)
    base = (sum(p["value"] for p in recent) / len(recent)) if recent else last["value"]
    pct_week = round(100 * slope / base, 2) if slope is not None and base else None

    def best_of(key):
        return max((p.get(key) or 0 for p in points), default=0)

    def change_since(days):
        ref = [p for p in points if _day(p["date"]) <= today - timedelta(days=days)]
        if not ref or not ref[-1]["value"]:
            return None
        return round(100 * (last["value"] - ref[-1]["value"]) / ref[-1]["value"], 1)

    last_pr = next((p["date"] for p in reversed(points) if p.get("prs")), None)
    since_pr = sum(1 for _ in _after(points, last_pr)) if last_pr else len(points) - 1

    tail = points[-4:]
    tail_slope = _slope_per_week(tail)
    tail_pct = 100 * tail_slope / (sum(p["value"] for p in tail) / len(tail)) if tail_slope is not None else None

    if len(points) < 3:
        status, msg = "inicio", "Poucos registros ainda: a tendência aparece a partir de 3 treinos."
    elif pct_week is not None and pct_week <= -1:
        status, msg = "caindo", "Queda nas últimas semanas: confira sono, alimentação e se não é hora de deload."
    elif since_pr >= 4 and (tail_pct is None or tail_pct < 0.5):
        status, msg = "estagnado", (f"{since_pr} treinos sem recorde. Experimente mudar a faixa de reps, "
                                     "trocar por uma variação ou fazer uma semana de deload.")
    elif pct_week is not None and pct_week >= 0.5:
        status, msg = "progredindo", f"Subindo cerca de {str(round(pct_week, 1)).replace('.', ',')}% por semana nas últimas 8 semanas."
    else:
        status, msg = "estavel", "Estável nas últimas semanas."

    return {
        "sessions": len(points),
        "metric": metric,
        "first_date": first["date"],
        "last_date": last["date"],
        "first_value": first["value"],
        "last_value": last["value"],
        "best": {"e1rm": best_of("e1rm"), "top_load": best_of("top_load"), "volume": best_of("volume"),
                 "max_reps": best_of("max_reps"), "max_duration_s": best_of("max_duration_s")},
        "change_total_pct": round(100 * (last["value"] - first["value"]) / first["value"], 1)
        if first["value"] else None,
        "change_30d_pct": change_since(30),
        "change_90d_pct": change_since(90),
        "slope_per_week": round(slope, 2) if slope is not None else None,
        "pct_per_week": pct_week,
        "last_pr_date": last_pr,
        "sessions_since_pr": since_pr,
        "status": status,
        "message": msg,
    }


def _after(points: list[dict], iso: str | None):
    return (p for p in points if iso and p["date"] > iso)


def _ex_info(eid: str) -> dict:
    ex = catalog.get(eid) or {}
    return {"exercise_id": eid, "name": ex.get("name") or eid,
            "primary_pt": ex.get("primary_pt") or [], "images": (ex.get("images") or [])[:1]}


def exercise_history(sessions: list[dict], exercise_id: str, today: date | None = None) -> dict:
    points = _points_by_exercise(sessions).get(exercise_id, [])
    _mark_prs(points)
    return {**_ex_info(exercise_id), "points": points, "summary": summary(points, today)}


def overview(sessions: list[dict], today: date | None = None) -> dict:
    """Lista de exercícios com histórico, do mais recente para o mais antigo, e os recordes recentes."""
    today = today or date.today()
    rows, recent_prs = [], []
    for eid, pts in _points_by_exercise(sessions).items():
        _mark_prs(pts)
        sm = summary(pts, today)
        spark = [p["value"] for p in pts[-12:]]
        rows.append({**_ex_info(eid), "summary": sm, "spark": spark})
        for p in pts:
            if p["prs"] and _day(p["date"]) >= today - timedelta(days=30):
                recent_prs.append({"exercise_id": eid, "name": rows[-1]["name"], "date": p["date"],
                                   "prs": p["prs"], "best_set": p["best_set"], "value": p["value"],
                                   "max_reps": p["max_reps"], "max_duration_s": p["max_duration_s"],
                                   "metric": p["metric"]})
    rows.sort(key=lambda r: r["summary"]["last_date"], reverse=True)
    recent_prs.sort(key=lambda r: r["date"], reverse=True)
    return {"exercises": rows, "recent_prs": recent_prs[:20]}


def session_prs(sessions: list[dict], session: dict) -> list[dict]:
    """Recordes batidos por uma sessão recém-salva (para comemorar no fim do treino)."""
    day = str(session.get("date"))[:10]
    out = []
    ids = {it.get("exercise_id") for it in session.get("items") or []}
    by_ex = _points_by_exercise(sessions)
    for eid in ids:
        pts = by_ex.get(eid) or []
        _mark_prs(pts)
        for p in pts:
            if p["date"] == day and p["prs"]:
                out.append({"exercise_id": eid, "name": _ex_info(eid)["name"], "prs": p["prs"],
                            "best_set": p["best_set"], "e1rm": p["e1rm"], "max_reps": p["max_reps"],
                            "max_duration_s": p["max_duration_s"], "metric": p["metric"]})
    return out
