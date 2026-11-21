"""Treinar com o companheiro: duas contas ligadas por convite + aceite.

Parceiros veem um do outro SÓ o treino: o que está planejado para o dia, o que foi feito (exercícios,
séries, tempo, calorias) e o placar da semana. Nunca saúde, sono, passos, peso ou login do relógio.

Para que serve:
- "treinar junto": abrir o treino do parceiro (o planejado de hoje ou o que ele já fez) como o MEU treino,
  com as MINHAS cargas (histórico e sugestão são de quem está treinando);
- quem treinou só com o relógio (que marca apenas o tempo) puxa o treino que o parceiro registrou e fica
  com exercícios e séries no lugar de "65 min de atividade";
- placar da semana: treinos, tempo de treino e calorias de cada um.

Tudo aqui é leitura do banco + contas simples; quem grava a sessão é o fluxo normal de salvar treino.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import catalog, clock, db, load

KEY = "partners"
MET_STRENGTH = 5.0      # musculação "geral": estimativa de calorias quando o relógio não informou
MET_BY_ZONE = {1: 3.5, 2: 6.0, 3: 8.0, 4: 10.0, 5: 11.5}
SET_MIN = 2.6           # minutos por série (execução + descanso) quando a sessão não tem duração
DEFAULT_KG = 70.0


# ------------------------------------------------------------------ vínculo
def _state(uid: int) -> dict:
    try:
        d = json.loads(db.kv_get(KEY, user_id=uid) or "{}")
    except ValueError:
        d = {}
    return {"links": dict(d.get("links") or {}), "sent": dict(d.get("sent") or {}), "received": dict(d.get("received") or {})}


def _save(uid: int, st: dict) -> None:
    db.kv_set(KEY, json.dumps(st), user_id=uid)


def _names() -> dict[int, str]:
    return {int(a["user_id"]): (a.get("name") or "").strip() or "Sem nome" for a in db.list_accounts()}


def first_name(name: str) -> str:
    return (name or "").split()[0] if (name or "").strip() else "Parceiro"


def is_partner(uid: int, other: int) -> bool:
    return str(other) in _state(uid)["links"] and str(uid) in _state(other)["links"]


def partner_ids(uid: int) -> list[int]:
    names = _names()
    return [int(k) for k in _state(uid)["links"] if int(k) in names and is_partner(uid, int(k))]


def overview(uid: int) -> dict:
    """Quem eu posso convidar, quem me convidou e com quem já treino."""
    st, names = _state(uid), _names()
    people = []
    for oid, name in sorted(names.items(), key=lambda kv: kv[1].lower()):
        if oid == uid:
            continue
        k = str(oid)
        status = "parceiro" if (k in st["links"] and is_partner(uid, oid)) else "convidou" if k in st["received"] else "convidado" if k in st["sent"] else None
        people.append({"id": oid, "name": name, "first": first_name(name), "status": status})
    return {"people": people, "partners": [p for p in people if p["status"] == "parceiro"],
            "requests": [p for p in people if p["status"] == "convidou"]}


def invite(uid: int, other: int) -> str:
    """Convida; se a outra pessoa já tinha me convidado, vira parceria na hora. Devolve o estado final."""
    if other == uid or other not in _names():
        raise ValueError("Pessoa não encontrada")
    mine, theirs = _state(uid), _state(other)
    if str(other) in mine["links"] and str(uid) in theirs["links"]:
        return "parceiro"
    if str(other) in mine["received"]:
        return accept(uid, other)
    now = clock.now().isoformat(timespec="minutes")
    mine["sent"][str(other)] = now
    theirs["received"][str(uid)] = now
    _save(uid, mine)
    _save(other, theirs)
    return "convidado"


def accept(uid: int, other: int) -> str:
    mine, theirs = _state(uid), _state(other)
    if str(other) not in mine["received"]:
        raise ValueError("Não há convite dessa pessoa")
    now = clock.now().isoformat(timespec="minutes")
    for st, a in ((mine, other), (theirs, uid)):
        st["links"][str(a)] = {"since": now}
        st["sent"].pop(str(a), None)
        st["received"].pop(str(a), None)
    _save(uid, mine)
    _save(other, theirs)
    return "parceiro"


def remove(uid: int, other: int) -> None:
    """Desfaz parceria, recusa ou cancela convite (os dois lados)."""
    for a, b in ((uid, other), (other, uid)):
        st = _state(a)
        for part in ("links", "sent", "received"):
            st[part].pop(str(b), None)
        _save(a, st)


# ------------------------------------------------------------------ o treino do outro
def _ex_name(eid: str | None, it: dict | None = None) -> str:
    ex = catalog.get(eid) or {}
    return ex.get("name") or (it or {}).get("_name") or str(eid or "")


def _is_strength(it: dict) -> bool:
    return isinstance(it.get("sets"), list) and bool(it["sets"]) and (catalog.get(it.get("exercise_id")) or {}).get("kind") == "strength"


def session_minutes(s: dict) -> float:
    """Tempo de treino de uma sessão: o medido; sem ele, a soma dos itens ou ~2,6 min por série."""
    if s.get("duration_min"):
        return float(s["duration_min"])
    summ = s.get("garmin_summary") or {}
    if summ.get("duration_min"):
        return float(summ["duration_min"])
    mins = sum(float(it.get("duration_min") or it.get("strength_min") or 0) for it in s.get("items") or [])
    sets = sum(len(it["sets"]) for it in s.get("items") or [] if isinstance(it.get("sets"), list))
    return round(mins + sets * SET_MIN, 1)


def session_kcal(s: dict, weight_kg: float | None) -> tuple[float, bool]:
    """(calorias, estimado?). Do relógio quando há; senão estimativa por MET × peso × tempo."""
    summ = s.get("garmin_summary") or {}
    if summ.get("kcal"):
        return float(summ["kcal"]), False
    measured = sum(float(it.get("kcal") or 0) for it in s.get("items") or [])
    if measured > 0:
        return measured, False
    kg = float(weight_kg or DEFAULT_KG)
    total, counted = 0.0, 0.0
    for it in s.get("items") or []:
        if isinstance(it.get("sets"), list) or it.get("strength_min"):
            continue
        mins = float(it.get("duration_min") or 0)
        zone = 1 if load.is_light(it) else int(it.get("zone") or 2)
        total += MET_BY_ZONE.get(zone, 6.0) * kg * mins / 60
        counted += mins
    strength_min = max(0.0, session_minutes(s) - counted) if any(isinstance(it.get("sets"), list) or it.get("strength_min") for it in s.get("items") or []) else 0.0
    total += MET_STRENGTH * kg * strength_min / 60
    return round(total), True


def _session_view(s: dict, weight_kg: float | None, detail: bool = True) -> dict:
    kcal, est = session_kcal(s, weight_kg)
    items = []
    for it in s.get("items") or []:
        if _is_strength(it):
            sets = [x for x in it["sets"] if x.get("done", True)]
            row = {"exercise_id": it["exercise_id"], "name": _ex_name(it["exercise_id"]), "kind": "forca", "n": len(sets)}
            if detail:
                row["sets"] = [{"reps": x.get("reps"), "load_kg": x.get("load_kg"), "duration_s": x.get("duration_s")} for x in sets]
            items.append(row)
        elif it.get("duration_min") or it.get("strength_min"):
            items.append({"exercise_id": it.get("exercise_id"), "name": _ex_name(it.get("exercise_id"), it), "kind": "cardio",
                          "minutes": round(float(it.get("duration_min") or it.get("strength_min") or 0)),
                          "km": it.get("distance_km")})
    return {"id": s.get("id"), "date": str(s.get("date"))[:16], "title": s.get("day_name") or s.get("notes") or "Treino",
            "minutes": round(session_minutes(s)), "kcal": round(kcal), "kcal_estimated": est,
            "sets": sum(i["n"] for i in items if i["kind"] == "forca"), "items": items,
            "has_sets": any(i["kind"] == "forca" for i in items), "with_partner": s.get("with_partner"),
            "watch": bool(s.get("garmin_activity_id") or s.get("ext_id")), "source": s.get("source") or "app"}


def _entry_view(e: dict) -> dict:
    items = []
    for it in e.get("items") or []:
        if (it.get("phase") or "principal") != "principal":
            continue
        ex = catalog.get(it.get("exercise_id")) or {}
        if ex.get("kind") == "strength":
            items.append({"exercise_id": it["exercise_id"], "name": ex.get("name"), "kind": "forca", "n": int(it.get("sets") or 0), "reps": it.get("reps")})
        elif ex:
            items.append({"exercise_id": it["exercise_id"], "name": ex.get("name"), "kind": "cardio", "minutes": it.get("duration_min")})
    return {"id": e["id"], "date": e["date"], "title": e.get("day_name") or "Treino", "status": e.get("status"), "kind": e.get("kind"),
            "items": items, "has_sets": any(i["kind"] == "forca" for i in items)}


def day(uid: int, pid: int, d: date) -> dict:
    """O dia do parceiro (planejado e feito) e as MINHAS sessões do dia (para juntar com o treino só-de-tempo do relógio)."""
    iso = d.isoformat()
    nxt = (d + timedelta(days=1)).isoformat()
    w_p, w_me = db.get_profile(pid).get("weight_kg"), db.get_profile(uid).get("weight_kg")
    theirs = [s for s in db.list_sessions(pid, since=iso, limit=60) if str(s.get("date"))[:10] == iso and str(s.get("source")) != "passos"]
    mine = [s for s in db.list_sessions(uid, since=iso, limit=60) if str(s.get("date"))[:10] == iso and str(s.get("source")) != "passos"]
    planned = [e for e in db.list_schedule(iso, iso, user_id=pid) if e.get("status") in ("planejado", "perdido")]
    return {"date": iso, "next": nxt, "partner": {"id": pid, "name": _names().get(pid, ""), "first": first_name(_names().get(pid, ""))},
            "done": [_session_view(s, w_p) for s in sorted(theirs, key=lambda s: str(s.get("date")))],
            "planned": [_entry_view(e) for e in planned],
            "mine": [_session_view(s, w_me, detail=False) for s in sorted(mine, key=lambda s: str(s.get("date")))]}


def plan_items_from_session(s: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    """Sessão feita pelo parceiro → itens no formato de planilha (para o app montar o MEU rascunho com as minhas cargas)
    + as séries dele por exercício (repetições de cada série e a carga que ELE usou, só como referência)."""
    items, done = [], {}
    for it in s.get("items") or []:
        if _is_strength(it):
            sets = [x for x in it["sets"] if x.get("done", True)]
            if not sets:
                continue
            reps = [int(x["reps"]) for x in sets if x.get("reps")]
            items.append({"exercise_id": it["exercise_id"], "sets": len(sets), "reps": str(max(set(reps), key=reps.count)) if reps else "10",
                          "rest_s": 90, "phase": "principal", **({"duration_s": sets[0]["duration_s"]} if sets[0].get("duration_s") and not reps else {})})
            done.setdefault(it["exercise_id"], []).extend({"reps": x.get("reps"), "load_kg": x.get("load_kg"), "duration_s": x.get("duration_s")} for x in sets)
    return items, done


def plan_items_from_entry(e: dict) -> list[dict]:
    """Treino planejado do parceiro → itens de planilha sem as cargas dele (as minhas vêm do meu histórico)."""
    out = []
    for it in e.get("items") or []:
        ex = catalog.get(it.get("exercise_id")) or {}
        if ex.get("kind") != "strength":
            continue  # corrida/cardio do outro têm ritmo e zonas dele: cada um faz o seu
        out.append({k: v for k, v in it.items() if k not in ("load_kg", "added", "added_from", "comp_v", "comp_from", "swap", "garmin_workout_id")})
    return out


# ------------------------------------------------------------------ placar
def _week(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def _totals(uid: int, start: date, end: date) -> dict:
    kg = db.get_profile(uid).get("weight_kg")
    days: dict[str, dict] = {}
    tot = {"sessions": 0, "minutes": 0.0, "kcal": 0.0, "kcal_estimated": False, "sets": 0, "km": 0.0}
    for s in db.list_sessions(uid, since=start.isoformat(), limit=400):
        iso = str(s.get("date"))[:10]
        if not (start.isoformat() <= iso <= end.isoformat()) or str(s.get("source")) == "passos":
            continue  # a caminhada estimada pelos passos (festival) não é treino para o placar
        v = _session_view(s, kg, detail=False)
        row = days.setdefault(iso, {"date": iso, "sessions": 0, "minutes": 0, "kcal": 0, "kcal_estimated": False, "titles": []})
        row["sessions"] += 1
        row["minutes"] += v["minutes"]
        row["kcal"] += v["kcal"]
        row["kcal_estimated"] = row["kcal_estimated"] or v["kcal_estimated"]
        row["titles"].append(v["title"])
        tot["sessions"] += 1
        tot["minutes"] += v["minutes"]
        tot["kcal"] += v["kcal"]
        tot["kcal_estimated"] = tot["kcal_estimated"] or v["kcal_estimated"]
        tot["sets"] += v["sets"]
        tot["km"] += sum(float(i.get("km") or 0) for i in v["items"])
    return {**{k: (round(v, 1) if k == "km" else round(v) if isinstance(v, float) else v) for k, v in tot.items()},
            "days": [days[k] for k in sorted(days)]}


def duel(uid: int, pid: int, d: date, today: date | None = None) -> dict:
    """Placar da semana (seg–dom) entre mim e o parceiro: treinos, tempo e calorias; e o dia a dia frente a frente."""
    today = today or clock.today()
    start, end = _week(d)
    names = _names()
    me, other = _totals(uid, start, end), _totals(pid, start, end)
    metrics = []
    for key, label, unit in (("sessions", "Treinos", ""), ("minutes", "Tempo de treino", "min"), ("kcal", "Calorias", "kcal")):
        a, b = me[key], other[key]
        metrics.append({"key": key, "label": label, "unit": unit, "me": a, "partner": b,
                        "lead": "me" if a > b else "partner" if b > a else "tie"})
    wins_me = sum(1 for m in metrics if m["lead"] == "me")
    wins_p = sum(1 for m in metrics if m["lead"] == "partner")
    by_me = {r["date"]: r for r in me["days"]}
    by_p = {r["date"]: r for r in other["days"]}
    days = []
    for i in range(7):
        iso = (start + timedelta(days=i)).isoformat()
        a, b = by_me.get(iso), by_p.get(iso)
        days.append({"date": iso, "future": iso > today.isoformat(), "me": a, "partner": b,
                     "lead": None if not (a or b) else "me" if (a or {}).get("kcal", 0) > (b or {}).get("kcal", 0)
                     else "partner" if (b or {}).get("kcal", 0) > (a or {}).get("kcal", 0) else "tie"})
    return {"week_start": start.isoformat(), "week_end": end.isoformat(), "prev": (start - timedelta(days=7)).isoformat(),
            "next": (start + timedelta(days=7)).isoformat() if end < today else None,
            "me": {"id": uid, "name": names.get(uid, ""), "first": first_name(names.get(uid, "")), **{k: v for k, v in me.items() if k != "days"}},
            "partner": {"id": pid, "name": names.get(pid, ""), "first": first_name(names.get(pid, "")), **{k: v for k, v in other.items() if k != "days"}},
            "metrics": metrics, "score": {"me": wins_me, "partner": wins_p,
                                          "lead": "me" if wins_me > wins_p else "partner" if wins_p > wins_me else "tie"},
            "days": days, "kcal_estimated": me["kcal_estimated"] or other["kcal_estimated"],
            "together": [x["date"] for x in days if x["me"] and x["partner"]]}
