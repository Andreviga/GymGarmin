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
import math
from datetime import date, datetime, timedelta

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


def _who() -> dict[int, dict]:
    """Para distinguir duas contas com o mesmo nome: quem é o dono do app e desde quando a conta existe."""
    return {int(a["user_id"]): {"owner": bool(a.get("is_admin")), "since": str(a.get("created_at") or "")[:10] or None}
            for a in db.list_accounts()}


def _names() -> dict[int, str]:
    out = {}
    for a in db.list_accounts():
        name = (a.get("name") or "").strip()
        if not name or name == "Eu":  # dono que não preencheu o nome: "Eu está na frente" confundiria quem lê
            name = (str(a.get("email") or "").split("@")[0].replace(".", " ").strip() or "Sem nome").title()
        out[int(a["user_id"])] = name
    return out


def first_name(name: str) -> str:
    return (name or "").split()[0] if (name or "").strip() else "Parceiro"


def is_partner(uid: int, other: int) -> bool:
    return str(other) in _state(uid)["links"] and str(uid) in _state(other)["links"]


def partner_ids(uid: int) -> list[int]:
    names = _names()
    return [int(k) for k in _state(uid)["links"] if int(k) in names and is_partner(uid, int(k))]


def overview(uid: int) -> dict:
    """Quem eu posso convidar, quem me convidou e com quem já treino."""
    st, names, who = _state(uid), _names(), _who()
    people = []
    for oid, name in sorted(names.items(), key=lambda kv: kv[1].lower()):
        if oid == uid:
            continue
        k = str(oid)
        status = "parceiro" if (k in st["links"] and is_partner(uid, oid)) else "convidou" if k in st["received"] else "convidado" if k in st["sent"] else None
        people.append({"id": oid, "name": name, "first": first_name(name), "status": status, **who.get(oid, {})})
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


def _cardio_minutes(s: dict) -> float:
    return sum(float(it.get("duration_min") or 0) for it in s.get("items") or []
               if not isinstance(it.get("sets"), list) and not it.get("strength_min"))


def _n_sets(s: dict) -> int:
    return sum(len(it["sets"]) for it in s.get("items") or [] if isinstance(it.get("sets"), list))


def watch_covers_only_cardio(s: dict) -> bool:
    """Treino com musculação + cardio em que o relógio gravou só o cardio (ex.: só a esteira): a duração e as calorias
    "do relógio" são da corrida, não do treino inteiro."""
    summ = s.get("garmin_summary") or {}
    cardio = _cardio_minutes(s)
    if not (_n_sets(s) and cardio > 0 and (s.get("garmin_activity_id") or s.get("ext_id"))):
        return False
    measured = float(s.get("duration_min") or summ.get("duration_min") or 0)
    return bool(summ.get("partial")) or 0 < measured <= cardio * 1.1 + 2


def session_time(s: dict) -> tuple[float, bool]:
    """(minutos, estimado?). Medido pelo relógio ou informado; sem isso, itens de tempo + ~2,6 min por série (estimado)."""
    summ = s.get("garmin_summary") or {}
    if not watch_covers_only_cardio(s):
        if s.get("duration_min"):
            return float(s["duration_min"]), False
        if summ.get("duration_min"):
            return float(summ["duration_min"]), False
    mins = sum(float(it.get("duration_min") or it.get("strength_min") or 0) for it in s.get("items") or [])
    sets = _n_sets(s)
    return round(mins + sets * SET_MIN, 1), bool(sets)


def session_minutes(s: dict) -> float:
    return session_time(s)[0]


def _bucket_kg(weight_kg: float | None) -> float:
    """Peso em faixas de 5 kg: a estimativa não muda quase nada e o parceiro não consegue descobrir o peso exato."""
    return float(round(float(weight_kg or DEFAULT_KG) / 5) * 5)


def session_kcal(s: dict, weight_kg: float | None, minutes: float | None = None) -> tuple[float, bool]:
    """(calorias, estimado?). Do relógio quando há; senão estimativa por MET × peso × tempo (arredondada à dezena).
    `minutes` troca o tempo estimado pelo tempo medido do treino feito junto com o parceiro."""
    summ = s.get("garmin_summary") or {}
    kg = _bucket_kg(weight_kg)
    if watch_covers_only_cardio(s):  # caloria medida só da corrida + estimativa da musculação
        cardio_kcal = sum(float(it.get("kcal") or 0) for it in s.get("items") or [] if not isinstance(it.get("sets"), list)) \
            or float(summ.get("kcal") or 0)
        strength_min = max(0.0, (minutes if minutes is not None else session_time(s)[0]) - _cardio_minutes(s))
        return float(round((cardio_kcal + MET_STRENGTH * kg * strength_min / 60) / 10) * 10), True
    if summ.get("kcal"):
        return float(summ["kcal"]), False
    measured = sum(float(it.get("kcal") or 0) for it in s.get("items") or [])
    if measured > 0:
        return measured, False
    total, counted = 0.0, 0.0
    for it in s.get("items") or []:
        if isinstance(it.get("sets"), list) or it.get("strength_min"):
            continue
        mins = float(it.get("duration_min") or 0)
        zone = 1 if load.is_light(it) else int(it.get("zone") or 2)
        total += MET_BY_ZONE.get(zone, 6.0) * kg * mins / 60
        counted += mins
    whole = minutes if minutes is not None else session_minutes(s)
    strength_min = max(0.0, whole - counted) if any(isinstance(it.get("sets"), list) or it.get("strength_min") for it in s.get("items") or []) else 0.0
    total += MET_STRENGTH * kg * strength_min / 60
    return float(round(total / 10) * 10), True


def unnamed_sets(s: dict) -> int:
    """Séries gravadas pelo relógio sem exercício identificado (ainda não nomeadas): não dá para puxar."""
    return sum(len(it["sets"]) for it in s.get("items") or []
               if isinstance(it.get("sets"), list) and it["sets"] and not _is_strength(it))


def _session_view(s: dict, weight_kg: float | None, detail: bool = True, minutes: float | None = None) -> dict:
    kcal, est = session_kcal(s, weight_kg, minutes)
    mins, mins_est = (minutes, False) if minutes is not None else session_time(s)
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
            "minutes": round(mins), "minutes_estimated": mins_est, "kcal": round(kcal), "kcal_estimated": est,
            "sets": sum(i["n"] for i in items if i["kind"] == "forca"), "items": items, "unnamed_sets": unnamed_sets(s),
            "has_sets": any(i["kind"] == "forca" for i in items), "with_partner": s.get("with_partner"),
            "watch": bool(s.get("garmin_activity_id") or s.get("ext_id")), "source": s.get("source") or "app"}


def strength_ids(s: dict) -> set[str]:
    return {it["exercise_id"] for it in s.get("items") or [] if _is_strength(it)}


def same_workout(a: set, b: set) -> bool:
    """Mesmo treino: pelo menos 60% dos exercícios do menor em comum (um pode ter pulado ou trocado um aparelho)."""
    if not a or not b:
        return False
    return len(a & b) >= max(1, round(0.6 * min(len(a), len(b))))


def same_copy(a: set, b: set) -> bool:
    """Cópia do mesmo treino (mais rígido): pelo menos 70% dos exercícios do MAIOR em comum, e no mínimo 2
    (um abdominal em comum não faz o treino de pernas ser o mesmo que o de abdômen)."""
    if not a or not b:
        return False
    if len(a) == 1 and len(b) == 1:
        return a == b
    common = len(a & b)
    return common >= 2 and common >= math.ceil(0.7 * max(len(a), len(b)))


def linked_overlap(a: set, b: set) -> bool:
    """Treino marcado como feito com a pessoa: é o par de um treino dela se a maioria dos exercícios do menor bate."""
    common = len(a & b)
    # e também uma parte de verdade do maior: o abdominal do fim do treino de pernas não faz dele o "abdômen" da noite
    return bool(a and b) and common >= max(1, math.ceil(0.5 * min(len(a), len(b)))) and common >= math.ceil(len(a | b) / 3)


def _when(s: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s.get("date"))[:19]) if len(str(s.get("date") or "")) > 10 else None
    except ValueError:
        return None


def _near(a: dict, b: dict, hours: float = 3) -> bool:
    ta, tb = _when(a), _when(b)
    return bool(ta and tb) and abs((ta - tb).total_seconds()) <= hours * 3600


def pair(mine: list[dict], theirs: list[dict], uid: int, pid: int) -> dict[int, int | None]:
    """Para cada treino MEU do dia (com séries): a qual treino do parceiro ele corresponde.
    {id do meu treino: id do treino dele, ou None se é treino com ele sem par (ex.: treinou junto pelo planejado
    e ele ainda não registrou)}. Treino meu que não aparece no resultado não tem relação com o parceiro.
    Com vínculo (um puxou do outro / treinou junto): basta a maioria dos exercícios do menor. Sem vínculo: só se é
    o mesmo treino de verdade (mesmos exercícios) e em horário próximo; cada um fazer a mesma divisão em horários
    diferentes não é "treinaram juntos"."""
    out: dict[int, int | None] = {}
    th = [(t, strength_ids(t)) for t in theirs if strength_ids(t)]
    for m in mine:
        ids = strength_ids(m)
        if not ids:
            continue
        best, best_n = None, 0
        for t, tids in th:
            n = len(ids & tids)
            linked = m.get("with_partner") == pid or t.get("with_partner") == uid
            ok = linked_overlap(ids, tids) if linked else (same_copy(ids, tids) and _near(m, t))
            if ok and n > best_n:
                best, best_n = t, n
        if best is not None:
            out[m["id"]] = best["id"]
        elif m.get("with_partner") == pid:
            out[m["id"]] = None
    return out


def planned_registered(e: dict, mine: list[dict], pid: int) -> list[int]:
    """Treinos MEUS do dia que já são esse treino planejado da pessoa (mesmos exercícios)."""
    want = {it.get("exercise_id") for it in e.get("items") or []
            if (it.get("phase") or "principal") == "principal" and (catalog.get(it.get("exercise_id")) or {}).get("kind") == "strength"}
    return [m["id"] for m in mine if strength_ids(m) and (same_copy(strength_ids(m), want)
            or (m.get("with_partner") == pid and linked_overlap(strength_ids(m), want)))]


def duplicates(mine: list[dict], links: dict[int, int | None]) -> list[list[int]]:
    """Registros meus que são o MESMO treino (ligados ao mesmo treino da pessoa, ou cópias um do outro): devem virar um."""
    groups: list[list[int]] = []
    by_t: dict[int, list[int]] = {}
    for mid, tid in links.items():
        if tid is not None:
            by_t.setdefault(tid, []).append(mid)
    groups += [g for g in by_t.values() if len(g) > 1]
    loose = [m for m in mine if m.get("id") in links and links[m["id"]] is None]
    used: set = set()
    for i, a in enumerate(loose):
        if a["id"] in used:
            continue
        g = [a["id"]] + [b["id"] for b in loose[i + 1:] if b["id"] not in used and same_copy(strength_ids(a), strength_ids(b))]
        if len(g) > 1:
            used.update(g)
            groups.append(g)
    return groups


def registered_view(s: dict) -> dict:
    """O que ficou salvo de um treino meu, para mostrar "✓ Você registrou"."""
    st = [it for it in s.get("items") or [] if _is_strength(it)]
    sets = [x for it in st for x in it["sets"] if x.get("done", True)]
    t, est = session_time(s)
    return {"id": s.get("id"), "title": s.get("day_name") or s.get("notes") or "Treino", "time": str(s.get("date"))[11:16],
            "date": str(s.get("date"))[:10], "exercises": len(st), "sets": len(sets), "minutes": round(t), "minutes_estimated": est,
            "no_load": sum(1 for x in sets if x.get("load_kg") in (None, "") and not x.get("duration_s")),
            "with_partner": s.get("with_partner")}


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
    ov_me, ov_p = _shared_time(mine, theirs, uid, pid)  # treino feito junto: o mesmo tempo que o placar mostra
    links = pair(mine, theirs, uid, pid)
    done = []
    for s in sorted(theirs, key=lambda s: str(s.get("date"))):
        v = _session_view(s, w_p, minutes=ov_p.get(s.get("id")))
        v["mine_ids"] = [mid for mid, tid in links.items() if tid == s.get("id")]   # o que EU já registrei desse treino
        done.append(v)
    mine_v = []
    for s in sorted(mine, key=lambda s: str(s.get("date"))):
        v = _session_view(s, w_me, detail=True, minutes=ov_me.get(s.get("id")))
        v["linked"] = s.get("id") in links
        v["registered"] = registered_view(s)
        mine_v.append(v)
    return {"date": iso, "next": nxt, "partner": {"id": pid, "name": _names().get(pid, ""), "first": first_name(_names().get(pid, ""))},
            "done": done, "planned": [{**_entry_view(e), "mine_ids": planned_registered(e, mine, pid)} for e in planned], "mine": mine_v,
            "registered": [registered_view(s) for s in sorted(mine, key=lambda s: str(s.get("date"))) if s.get("id") in links],
            "duplicates": duplicates(mine, links)}


def plan_items_from_session(s: dict) -> tuple[list[dict], dict[str, list[dict]]]:
    """Sessão feita pelo parceiro → itens no formato de planilha (para o app montar o MEU rascunho com as minhas cargas)
    + as séries dele por exercício (repetições de cada série e a carga que ELE usou, só como referência)."""
    done: dict[str, list[dict]] = {}
    for it in s.get("items") or []:  # o mesmo exercício em dois blocos (alternou aparelhos) vira um item só, com todas as séries
        if _is_strength(it):
            sets = [x for x in it["sets"] if x.get("done", True)]
            if sets:
                done.setdefault(it["exercise_id"], []).extend(
                    {"reps": x.get("reps"), "load_kg": x.get("load_kg"), "duration_s": x.get("duration_s")} for x in sets)
    items = []
    for ex_id, sets in done.items():
        reps = [int(x["reps"]) for x in sets if x.get("reps")]
        items.append({"exercise_id": ex_id, "sets": len(sets), "reps": str(max(set(reps), key=reps.count)) if reps else "10",
                      "rest_s": 90, "phase": "principal", **({"duration_s": sets[0]["duration_s"]} if sets[0].get("duration_s") and not reps else {})})
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


def _week_sessions(uid: int, start: date, end: date) -> list[dict]:
    return [s for s in db.list_sessions(uid, since=start.isoformat(), limit=400)
            if start.isoformat() <= str(s.get("date"))[:10] <= end.isoformat()
            and str(s.get("source")) != "passos"]  # a caminhada estimada pelos passos (festival) não é treino para o placar


def _shared_time(mine: list[dict], theirs: list[dict], uid: int, pid: int) -> tuple[dict, dict]:
    """Treino feito junto (um puxou o do outro): se só um dos dois tem o tempo medido, vale para os dois.
    Devolve {id da sessão: minutos} para cada lado."""
    ov_me, ov_p = {}, {}
    for a in mine:
        for b in theirs:
            if str(a.get("date"))[:10] != str(b.get("date"))[:10] or not (_n_sets(a) and _n_sets(b)):
                continue
            if a.get("with_partner") != pid and b.get("with_partner") != uid:
                continue
            (ta, ea), (tb, eb) = session_time(a), session_time(b)
            if ea and not eb and a.get("id") not in ov_me:
                ov_me[a.get("id")] = tb
            elif eb and not ea and b.get("id") not in ov_p:
                ov_p[b.get("id")] = ta
    return ov_me, ov_p


def _totals(uid: int, sessions: list[dict], shared: dict | None = None) -> dict:
    kg = db.get_profile(uid).get("weight_kg")
    days: dict[str, dict] = {}
    tot = {"sessions": 0, "minutes": 0.0, "minutes_estimated": False, "kcal": 0.0, "kcal_estimated": False, "sets": 0, "km": 0.0}
    for s in sessions:
        iso = str(s.get("date"))[:10]
        v = _session_view(s, kg, detail=False, minutes=(shared or {}).get(s.get("id")))
        row = days.setdefault(iso, {"date": iso, "sessions": 0, "minutes": 0, "minutes_estimated": False, "kcal": 0, "kcal_estimated": False, "titles": []})
        row["sessions"] += 1
        row["minutes"] += v["minutes"]
        row["minutes_estimated"] = row["minutes_estimated"] or v["minutes_estimated"]
        row["kcal"] += v["kcal"]
        row["kcal_estimated"] = row["kcal_estimated"] or v["kcal_estimated"]
        row["titles"].append(v["title"])
        tot["sessions"] += 1
        tot["minutes"] += v["minutes"]
        tot["minutes_estimated"] = tot["minutes_estimated"] or v["minutes_estimated"]
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
    mine, theirs = _week_sessions(uid, start, end), _week_sessions(pid, start, end)
    ov_me, ov_p = _shared_time(mine, theirs, uid, pid)
    me, other = _totals(uid, mine, ov_me), _totals(pid, theirs, ov_p)
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
            "minutes_estimated": me["minutes_estimated"] or other["minutes_estimated"],
            "together": [x["date"] for x in days if x["me"] and x["partner"]]}
