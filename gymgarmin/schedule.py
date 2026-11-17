"""Agenda semanal e reajuste automático quando um treino não acontece.

Fluxo:
- A semana (seg–dom) é montada a partir da planilha da agenda: cada treino cai no seu dia
  da semana (ou nos dias disponíveis, em ordem), já com a semana do ciclo aplicada
  (acúmulo / deload).
- Treino registrado na data → "feito". Data passou sem registro → "perdido".
- "Não vou treinar" (ou ajustar um perdido) dispara o REAJUSTE:
    1. MOVER: se houver um dia disponível livre na semana, o treino vai para lá — desde
       que não fique colado (±1 dia) num treino que trabalha os mesmos músculos.
       Se esse dia tinha cardio, o cardio vira finalizador.
    2. REDISTRIBUIR: sem dia livre, calcula quais músculos ficariam abaixo do alvo semanal
       e encaixa ~75% desse volume nos treinos restantes: +1 série nos exercícios que já
       trabalham o músculo (até +2 por exercício) ou adiciona o exercício perdido —
       sempre dentro do tempo da sessão (+10% de tolerância).
    3. Cardio perdido vira minutos extras nos dias de cardio ou finalizadores.
    4. O que não couber é informado, e o reajuste pode ser DESFEITO.
- Treinos já enviados ao Garmin que mudaram ficam marcados para reenviar.
- Resistência (corrida, bike, natação…): as sessões são remontadas a cada semana com o
  volume e a fase daquela semana; a prova entra no próprio dia. Longão ou sessão forte
  perdidos vão para um dia livre sem colar em outro forte; se não houver, a próxima
  sessão leve da modalidade vira a forte (ou o longão é parcialmente compensado).
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import catalog, clock, db, endurance, load
from .generator import WEEKDAYS, _cardio_equiv, _default_days, item_time, time_breakdown, weekly_targets, tier_for
from .muscles import PRIMARY_WEIGHT, muscle_pt
from .progression import apply_cycle, cycle_position


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _d(s) -> date:
    if isinstance(s, date) and not isinstance(s, datetime):
        return s
    return date.fromisoformat(str(s)[:10])


def _clean_name(name: str) -> str:
    for w in WEEKDAYS:
        if name.startswith(w + " – "):
            return name[len(w) + 3:]
    return name


def _kind(day: dict) -> str:
    if day.get("kind"):
        return day["kind"]
    items = [i for i in day.get("items", []) if (i.get("phase") or "principal") == "principal"]
    if items and all((catalog.get(i.get("exercise_id")) or {}).get("kind") == "cardio" for i in items):
        return "cardio"
    return "forca"


def schedule_plan(profile: dict) -> dict | None:
    pid = profile.get("schedule_plan_id")
    if pid:
        p = db.get_plan(int(pid))
        if p:
            return p
    plans = [p for p in db.list_plans() if p.get("active")]
    auto = [p for p in plans if p.get("generator")]
    return (auto or plans or [None])[0]


def available_days(profile: dict) -> list[int]:
    return sorted(set(profile.get("available_days") or _default_days(int(profile.get("days_per_week") or 3))))


def session_budget(profile: dict, plan: dict | None = None) -> float:
    gen = (plan or {}).get("generator") or {}
    return float(profile.get("session_minutes") or gen.get("session_minutes") or 60)


def _endurance_week(plan: dict, profile: dict, ws: date) -> dict | None:
    ep = plan.get("endurance_plan")
    if not ep or not endurance.active(profile):
        return None
    start = _d(ep.get("start")) or ws
    return endurance.week_plan(profile, ws, start, ep.get("tier") or "intermediario", sessions_override=ep.get("sessions"),
                               max_hard=ep.get("max_hard"))


def _assign_sessions(days: list[dict], wp: dict) -> dict[int, dict | None]:
    """Distribui as sessões da semana nos slots da planilha, casando pelo papel (longo/forte/leve)."""
    out: dict[int, dict | None] = {}
    for sport, d in (wp or {}).get("sports", {}).items():
        slots = [(i, day) for i, day in enumerate(days) if (day.get("endurance_slot") or {}).get("sport") == sport]
        sessions = list(d["sessions"])
        free = {i: day for i, day in slots}
        long_wd = next((day["weekday"] for i, day in slots if day["endurance_slot"]["role"] == "longo"), None)

        def take(role_slot, pred):
            for i, day in list(free.items()):
                if day["endurance_slot"]["role"] == role_slot:
                    sess = next((x for x in sessions if pred(x)), None)
                    if sess:
                        out[i] = sess
                        sessions.remove(sess)
                        free.pop(i)
        take("longo", lambda x: x["endurance"]["role"] == "longo")
        take("qualidade", lambda x: x["endurance"]["hard"])
        # sessões fortes que sobraram vão no slot leve mais longe do longão
        for sess in [x for x in sessions if x["endurance"]["hard"]]:
            if not free:
                break
            i = max(free, key=lambda k: min((free[k]["weekday"] - long_wd) % 7, (long_wd - free[k]["weekday"]) % 7)
                    if long_wd is not None else 0)
            out[i] = sess
            sessions.remove(sess)
            free.pop(i)
        for i in list(free):
            out[i] = sessions.pop(0) if sessions else None
    return out


def _race_entries(plan: dict, profile: dict, ws: date, wp: dict | None) -> list[dict]:
    """Prova (ou evento multiesporte) no próprio dia, quando cai nesta semana."""
    if not wp:
        return []
    out = []
    cfg = endurance.config(profile)
    anchors = endurance.sp.anchors_of(profile)
    ev = ((profile or {}).get("endurance") or {}).get("event") or {}
    ev_date = endurance._d(ev.get("date")) if ev.get("type") in endurance.EVENTS else None  # sem data: sem dia de prova
    if ev_date and ws <= ev_date < ws + timedelta(days=7):
        e = endurance.EVENTS[ev["type"]]
        items = []
        for s_key, dist in e["legs"]:
            c = cfg.get(s_key) or {"env": next(iter(endurance.sp.SPORTS[s_key]["envs"]))}
            vol = dist if s_key != "ciclismo" else dist / endurance.sp.speed_mps("ciclismo", 4, anchors) / 3.6
            kind = "leve"
            it = endurance.build_session(s_key, kind, vol if s_key != "natacao" else dist, c, anchors, "intermediario")
            it["endurance"].update(title=f"{endurance.sp.SPORTS[s_key]['name']} {dist:g} {'m' if s_key == 'natacao' else 'km'}",
                                   type="prova", hard=True, role="prova")
            for st in it["endurance"]["steps"]:
                st["zone"] = 4 if s_key != "ciclismo" else 3
                st["target"] = endurance.sp.target_for(s_key, st["zone"], anchors)
            items.append(it)
        out.append({"date": ev_date.isoformat(), "plan_id": plan["id"], "day_idx": None, "status": "planejado",
                    "day_name": f"EVENTO: {e['name']}", "kind": "resistencia", "race": True, "items": items, "changes": []})
        return out
    for s_key, c in cfg.items():
        g = c.get("goal") or {}
        rd = endurance._d(g.get("date")) if g.get("type") == "prova" else None
        if rd and ws <= rd < ws + timedelta(days=7):
            it = endurance.build_session(s_key, "prova", float(g.get("distance") or 5), c, anchors, "intermediario")
            out.append({"date": rd.isoformat(), "plan_id": plan["id"], "day_idx": None, "status": "planejado",
                        "day_name": it["endurance"]["title"], "kind": "resistencia", "race": True, "items": [it], "changes": []})
    return out


def build_week(plan: dict, profile: dict, ws: date, from_date: date | None = None) -> list[dict]:
    gen = plan.get("generator") or {}
    pos = cycle_position(gen.get("cycle_start") or plan.get("created_at"), ws, gen.get("cycle_weeks") or 5)
    avail = available_days(profile)
    days = plan.get("days") or []
    wp = _endurance_week(plan, profile, ws)
    sessions_by_day = _assign_sessions(days, wp) if wp else {}
    # séries extras do acúmulo só até o volume máximo recuperável de cada músculo na semana
    from .generator import _weekly_volume
    from .muscles import MUSCLES

    vol = _weekly_volume(days)
    allowance = {m: MUSCLES[m]["mrv"] - vol.get(m, 0) for m in MUSCLES}
    used: dict[int, list[dict]] = defaultdict(list)
    entries = []
    free = iter([d for d in avail])
    for idx, day in enumerate(days):
        wd = day.get("weekday")
        # dois treinos no mesmo dia só quando a planilha marca (força + resistência)
        if wd is None or (used.get(wd) and not day.get("double") and not any(x.get("double") for x in used[wd])):
            nwd = next((d for d in free if d not in used), None)
            if nwd is None and wd is None:
                break
            wd = nwd if nwd is not None else wd  # sem dia livre: mantém no dia (dois treinos)
        used[wd].append(day)
        dt = ws + timedelta(days=wd)
        if from_date and dt < from_date:
            continue
        items = apply_cycle(day.get("items", []), pos, int(gen.get("ramp_sets") or 0),
                            budget_min=session_budget(profile, plan), allowance=allowance)
        name = _clean_name(day.get("name") or f"Treino {idx + 1}")
        extra = {}
        if day.get("endurance_slot") and wp:
            sess = sessions_by_day.get(idx)
            if sess is None:
                continue  # semana com menos sessões (prova, recuperação)
            sport = day["endurance_slot"]["sport"]
            items = [sess] + [i for i in day.get("items", []) if i.get("phase") == "pos"]
            if day.get("brick"):
                items = [sess] + [b for br in (wp.get("bricks") or []) for b in br["items"]]
            name = f"{endurance.sp.SPORTS[sport]['name']}: {sess['endurance']['title']}"
            extra = {"sport": sport, "endurance_phase": wp["sports"][sport]["phase_pt"]}
        entries.append({
            "date": dt.isoformat(), "plan_id": plan["id"], "day_idx": idx, "status": "planejado",
            "day_name": name, "kind": _kind(day), "items": items, "cycle": pos, "changes": [], **extra,
        })
    for race in _race_entries(plan, profile, ws, wp):
        if not from_date or _d(race["date"]) >= from_date:
            # no dia da prova, os outros treinos daquele dia saem
            entries = [e for e in entries if e["date"] != race["date"]]
            entries.append(race)
    entries.sort(key=lambda e: (e["date"], e.get("kind") != "forca"))
    return entries


def _sessions_in_week(ws: date) -> list[dict]:
    start = ws.isoformat()
    end = (ws + timedelta(days=7)).isoformat()
    return [s for s in db.list_sessions(since=start, limit=500) if s["date"][:10] < end]


def sync_status(entries: list[dict], sessions: list[dict], today: date) -> list[dict]:
    by_sched = {s.get("schedule_id"): s for s in sessions if s.get("schedule_id")}
    by_date = defaultdict(list)
    for s in sessions:
        if not s.get("schedule_id"):
            by_date[s["date"][:10]].append(s)
    used: set = set()

    def matches(e, sess_list, substitute: bool = False):
        """Sessão sem vínculo conta para o treino do dia só se for do mesmo tipo:
        uma caminhada curta registrada pelo relógio não marca a musculação como feita.
        Com `substitute`, aceita outra modalidade de resistência de duração parecida."""
        planned_min = sum((it.get("endurance") or {}).get("dur_s", 0) / 60 or float(it.get("duration_min") or 0)
                          for it in e.get("items") or [] if it.get("endurance") or it.get("sport"))
        kind = e.get("kind")
        for sx in sess_list:
            if sx.get("id") in used:
                continue  # uma atividade não vale por dois treinos
            its = sx.get("items", [])
            strength_done = any(it.get("sets") or it.get("strength_min") for it in its)
            end_min = defaultdict(float)
            for it in its:
                sp_ = load.sport_of(it)
                if sp_ or (catalog.get(it.get("exercise_id")) or {}).get("kind") == "cardio":
                    end_min[sp_ or "cardio"] += float(it.get("duration_min") or 0)
            if substitute:
                # trocou de modalidade mas treinou de verdade (caminhada não substitui corrida/bike/natação)
                other = sum(v for k, v in end_min.items() if k not in ("caminhada", e.get("sport")))
                ok = kind == "resistencia" and other >= max(30, 0.75 * planned_min)
            else:
                ok = (kind == "forca" and strength_done) \
                    or (kind == "resistencia" and end_min.get(e.get("sport"), 0) >= max(10, 0.4 * planned_min)) \
                    or (kind == "cardio" and sum(end_min.values()) >= 10) \
                    or (kind not in ("forca", "resistencia", "cardio") and (strength_done or bool(end_min)))
            if ok:
                used.add(sx.get("id"))
                return True
        return False

    # 1º reserva as atividades dos treinos já feitos; 2º casa pelo tipo exato; 3º aceita troca de modalidade
    for e in entries:
        if e["status"] == "feito" and e["id"] not in by_sched:
            matches(e, by_date.get(e["date"], []))
    done = set()
    # mesma modalidade duas vezes no dia (ex.: remo intervalado + contínuo): casa pela duração mais próxima
    pairs = []
    for e in entries:
        if e["status"] in ("planejado", "perdido") and e.get("kind") == "resistencia" and e["id"] not in by_sched:
            planned = sum((it.get("endurance") or {}).get("dur_s", 0) / 60 for it in e.get("items") or [])
            for sx in by_date.get(e["date"], []):
                mins = sum(float(it.get("duration_min") or 0) for it in sx.get("items", []) if load.sport_of(it) == e.get("sport"))
                if mins and sx.get("id") not in used:
                    pairs.append((abs(mins - planned), e["id"], sx.get("id"), mins, planned))
    for _, eid, sid, mins, planned in sorted(pairs, key=lambda x: x[0]):
        if eid not in done and sid not in used and mins >= max(10, 0.4 * planned):
            done.add(eid)
            used.add(sid)
    for substitute in (False, True):
        for e in entries:
            if e["status"] in ("planejado", "perdido") and e["id"] not in done:
                if e["id"] in by_sched or matches(e, by_date.get(e["date"], []), substitute):
                    done.add(e["id"])
    out = []
    for e in entries:
        status = e["status"]
        if status in ("planejado", "perdido"):
            if e["id"] in done:
                status = "feito"
            elif _d(e["date"]) < today and status == "planejado":
                status = "perdido"
        if status != e["status"]:
            e["status"] = status
            e = db.save_entry(e)
        out.append(e)
    return out


def ensure_week(profile: dict, any_day: date, today: date | None = None) -> list[dict]:
    today = today or clock.today()
    ws = week_start(any_day)
    entries = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    if not entries and ws >= week_start(today):
        plan = schedule_plan(profile)
        if plan:
            entries = [db.save_entry(e) for e in build_week(plan, profile, ws, from_date=today if ws == week_start(today) else None)]
    return sync_status(entries, _sessions_in_week(ws), today)


def rebuild_week(profile: dict, today: date | None = None) -> list[dict]:
    """Recria os treinos ainda não feitos da semana atual a partir da planilha da agenda."""
    today = today or clock.today()
    ws = week_start(today)
    for e in db.list_schedule(today.isoformat(), (ws + timedelta(days=6)).isoformat()):
        if e["status"] in ("planejado", "perdido"):
            db.delete_entry(e["id"])
    plan = schedule_plan(profile)
    if plan:
        existing = {e["date"] for e in db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
                    if e["status"] == "feito"}
        for e in build_week(plan, profile, ws, from_date=today):
            if e["date"] not in existing:
                db.save_entry(e)
    return ensure_week(profile, today, today)


# ------------------------------------------------------------------ reajuste --

def _strength_items(e: dict) -> list[dict]:
    return [i for i in e.get("items", []) if (i.get("phase") or "principal") == "principal"
            and (catalog.get(i.get("exercise_id")) or {}).get("kind") == "strength"]


def _cardio_items(e: dict) -> list[dict]:
    return [i for i in e.get("items", []) if (i.get("phase") or "principal") == "principal"
            and (catalog.get(i.get("exercise_id")) or {}).get("kind") == "cardio"]


def _load(e: dict) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for it in _strength_items(e):
        ex = catalog.get(it["exercise_id"])
        for m in ex["primaryMuscles"]:
            out[m] += int(it.get("sets") or 0) * PRIMARY_WEIGHT
        for m in ex["secondaryMuscles"]:
            out[m] += int(it.get("sets") or 0) * 0.5
    return out


def _conflict(a: dict, b: dict) -> bool:
    """Treinos colados que repetem os mesmos músculos principais atrapalham a recuperação."""
    la, lb = _load(a), _load(b)
    big = lambda load: {m for m, v in load.items() if v >= 4}  # noqa: E731
    return bool(big(la) & big(lb))


def _minutes(e: dict) -> float:
    return sum(item_time(i)["total"] for i in e.get("items", [])) / 60


def _set_minutes(it: dict) -> float:
    one = item_time({**it, "sets": 1, "approach_sets": False})
    return (one["work"] + (it.get("rest_s") or 60)) / 60


def _note(e: dict, text: str) -> None:
    e.setdefault("changes", []).append(text)
    if e.get("garmin_workout_id"):
        e["garmin_outdated"] = True


def _fmt(d: str) -> str:
    dd = _d(d)
    return f"{WEEKDAYS[dd.weekday()]} {dd.day:02d}/{dd.month:02d}"


def _place_cardio(lost: float, future: list[dict], origin: str, budget: float, machine: str, changes: list[str]) -> float:
    """Encaixa minutos de cardio nos próximos dias: primeiro dias de cardio, depois finalizadores."""
    for e in [x for x in future if x.get("kind") == "cardio"] + [x for x in future if x.get("kind") == "forca"]:
        if lost < 5:
            break
        slack = budget - _minutes(e)
        add = int(min(lost, slack, 20) // 5 * 5)
        if add < 5:
            continue
        c = next((i for i in _cardio_items(e) if i.get("duration_min") and (i.get("finisher") or e.get("kind") == "cardio")), None)
        if c:
            c["duration_min"] = float(c["duration_min"]) + add
        else:
            posts = [i for i in e["items"] if i.get("phase") == "pos"]
            idx = e["items"].index(posts[0]) if posts else len(e["items"])
            e["items"].insert(idx, {"exercise_id": machine, "duration_min": add, "zone": 2, "phase": "principal",
                                    "finisher": True, "notes": "cardio compensado"})
        _note(e, f"+{add} min de cardio (compensa {origin})")
        changes.append(f"Cardio: +{add} min em {_fmt(e['date'])}.")
        lost -= add
    return lost


def handle_missed(entry_id: int, profile: dict, today: date | None = None) -> dict:
    today = today or clock.today()
    missed = db.get_entry(entry_id)
    if not missed:
        raise ValueError("Treino da agenda não encontrado")
    if missed["status"] == "feito":
        raise ValueError("Este treino já foi feito")
    ws = week_start(_d(missed["date"]))
    entries = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    snapshot = [copy.deepcopy(e) for e in entries if e["id"] != missed["id"]]
    plan = db.get_plan(missed["plan_id"]) if missed.get("plan_id") else None
    budget = session_budget(profile, plan) * 1.15  # compensação pode alongar um pouco a sessão
    changes: list[str] = []
    unrecovered: list[str] = []
    created: list[int] = []
    strategy = "redistribuir"
    moved_cardio_left = 0.0
    moved_cardio_machine = "cardio_bike_vertical"

    missed["status"] = "pulado"
    others = [e for e in entries if e["id"] != missed["id"]]
    future = [e for e in others if e["status"] == "planejado" and _d(e["date"]) >= today]
    if _d(missed["date"]) == today:
        future = [e for e in future if _d(e["date"]) > today]

    if missed.get("kind") == "resistencia":
        strategy, changes, unrecovered, created = _missed_endurance(missed, others, future, profile, ws, today)
        for e in others:
            if e.get("changes") != next((x.get("changes") for x in snapshot if x["id"] == e["id"]), None) or \
                    e["items"] != next((x["items"] for x in snapshot if x["id"] == e["id"]), None):
                db.save_entry(e)
        missed["undo"] = {"snapshot": snapshot, "created": created}
        missed["skip_result"] = {"strategy": strategy, "changes": changes, "at": clock.now().isoformat(timespec="minutes")}
        db.save_entry(missed)
        return {"strategy": strategy, "changes": changes, "unrecovered": unrecovered}

    # ---------- 1) mover para um dia livre
    if missed.get("kind") == "forca" and _strength_items(missed):
        taken = {e["date"] for e in others if e["status"] in ("planejado", "feito") and e.get("kind") == "forca"}
        first = max(today + (timedelta(days=1) if _d(missed["date"]) <= today else timedelta()), ws)
        for wd in available_days(profile):
            cand = ws + timedelta(days=wd)
            if cand < first or cand.isoformat() == missed["date"] or cand.isoformat() in taken:
                continue
            neighbors = [e for e in others if e.get("kind") == "forca" and e["status"] in ("planejado", "feito")
                         and abs((_d(e["date"]) - cand).days) == 1]
            if any(_conflict(missed, n) for n in neighbors):
                continue
            same_day = [e for e in others if e["date"] == cand.isoformat() and e["status"] in ("planejado", "feito")
                        and e.get("kind") != "cardio"]
            if len(same_day) >= 2 or any(e["status"] == "feito" for e in same_day) or \
                    any(_is_heavy_end(e) for e in same_day):
                continue  # dia cheio, já treinado ou com sessão forte/longa de resistência
            new = {k: copy.deepcopy(v) for k, v in missed.items() if k not in ("id", "undo", "garmin_workout_id", "garmin_outdated")}
            new.update(date=cand.isoformat(), status="planejado", moved_from=missed["date"], changes=[])
            cardio_day = next((e for e in others if e["date"] == cand.isoformat() and e["status"] == "planejado"
                               and e.get("kind") == "cardio"), None)
            fin_added = 0
            if cardio_day:
                slack = budget - _minutes(new)
                total_c = _cardio_equiv([{"items": _cardio_items(cardio_day)}])
                for c in _cardio_items(cardio_day):
                    moved_cardio_machine = c["exercise_id"]
                    if slack >= 10 and c.get("duration_min"):
                        mins = int(min(c["duration_min"], slack) // 5 * 5)
                        fin = {**copy.deepcopy(c), "finisher": True, "duration_min": mins,
                               "notes": "cardio do dia (virou finalizador)"}
                        posts = [i for i in new["items"] if i.get("phase") == "pos"]
                        idx = new["items"].index(posts[0]) if posts else len(new["items"])
                        new["items"].insert(idx, fin)
                        slack -= mins
                        fin_added += mins
                moved_cardio_left = max(0.0, total_c - fin_added)
                cardio_day["status"] = "substituido"
                _note(cardio_day, f"Substituído pelo treino {missed['day_name']} (movido)")
                db.save_entry(cardio_day)
            _note(new, f"Movido de {_fmt(missed['date'])}")
            saved = db.save_entry(new)
            created.append(saved["id"])
            changes.append(f"{missed['day_name']} foi movido para {_fmt(saved['date'])}"
                           + (f" ({fin_added} min do cardio do dia viraram finalizador)" if fin_added else "") + ".")
            strategy = "mover"
            break

    # ---------- 2) redistribuir volume de força
    if strategy == "redistribuir" and _strength_items(missed):
        months = profile.get("training_months")
        gen = (plan or {}).get("generator") or {}
        targets = gen.get("targets") or weekly_targets(tier_for(months if months is not None else 12),
                                                       profile.get("goal") or "hipertrofia", profile.get("volume_factor") or 1)
        projected: dict[str, float] = defaultdict(float)
        for e in others:
            if e["status"] in ("planejado", "feito"):
                for m, v in _load(e).items():
                    projected[m] += v
        missed_direct: dict[str, float] = defaultdict(float)
        for it in _strength_items(missed):
            for m in catalog.get(it["exercise_id"])["primaryMuscles"]:
                missed_direct[m] += int(it.get("sets") or 0)
        deficits = sorted(((targets.get(m, 0) - projected[m], m) for m in missed_direct if targets.get(m, 0) - projected[m] > 0.5),
                          reverse=True)
        strength_future = [e for e in future if e.get("kind") == "forca"]
        moved_ex: set[str] = set()
        for deficit, m in deficits:
            need = math.ceil(min(deficit, missed_direct[m]) * 0.75)
            placed = 0
            while need > 0:
                done = False
                for e in sorted(strength_future, key=lambda x: _minutes(x)):
                    for it in _strength_items(e):
                        if m in catalog.get(it["exercise_id"])["primaryMuscles"] and it.get("added", 0) < 2 and it["sets"] < 6:
                            if _minutes(e) + _set_minutes(it) <= budget:
                                it["sets"] += 1
                                it["added"] = it.get("added", 0) + 1
                                _note(e, f"+1 série em {catalog.get(it['exercise_id'])['name']} (compensa {missed['day_name']})")
                                need -= 1
                                placed += 1
                                done = True
                                break
                    if done:
                        break
                if not done:
                    src = next((i for i in _strength_items(missed) if i["exercise_id"] not in moved_ex
                                and m in catalog.get(i["exercise_id"])["primaryMuscles"]), None)
                    if src:
                        for e in sorted(strength_future, key=lambda x: _minutes(x)):
                            if any(i["exercise_id"] == src["exercise_id"] for i in e["items"]):
                                continue
                            sets = max(2, min(need, 3))
                            add = {**copy.deepcopy(src), "sets": sets, "added_from": missed["day_name"], "approach_sets": False,
                                   "notes": f"veio do treino {missed['day_name']}"}
                            if _minutes(e) + item_time(add)["total"] / 60 <= budget:
                                posts = [i for i in e["items"] if i.get("phase") == "pos" or i.get("finisher")]
                                idx = e["items"].index(posts[0]) if posts else len(e["items"])
                                e["items"].insert(idx, add)
                                moved_ex.add(src["exercise_id"])
                                _note(e, f"+ {catalog.get(src['exercise_id'])['name']} {sets} séries (do treino {missed['day_name']})")
                                need -= sets
                                placed += sets
                                done = True
                                break
                if not done:
                    # troca: tira 1 série de um músculo que está sobrando na semana para abrir espaço
                    for e in sorted(strength_future, key=lambda x: _minutes(x)):
                        tgt = next((it for it in _strength_items(e) if m in catalog.get(it["exercise_id"])["primaryMuscles"]
                                    and it.get("added", 0) < 2 and it["sets"] < 6), None)
                        if not tgt:
                            continue
                        donor = next((it for it in sorted(_strength_items(e), key=lambda x: -x["sets"])
                                      if it is not tgt and it.get("role") in ("acc", "sec") and it["sets"] > 2
                                      and m not in catalog.get(it["exercise_id"])["primaryMuscles"]
                                      and all(projected[x] - 1 >= targets.get(x, 0)
                                              for x in catalog.get(it["exercise_id"])["primaryMuscles"])), None)
                        if not donor:
                            continue
                        donor["sets"] -= 1
                        if _minutes(e) + _set_minutes(tgt) <= budget:
                            for x in catalog.get(donor["exercise_id"])["primaryMuscles"]:
                                projected[x] -= 1
                            tgt["sets"] += 1
                            tgt["added"] = tgt.get("added", 0) + 1
                            _note(e, f"−1 série em {catalog.get(donor['exercise_id'])['name']} (volume já sobrando) → "
                                     f"+1 em {catalog.get(tgt['exercise_id'])['name']}")
                            need -= 1
                            placed += 1
                            done = True
                            break
                        donor["sets"] += 1
                if not done:
                    break
            if placed:
                changes.append(f"{muscle_pt(m)}: {placed} séries redistribuídas na semana.")
            if need >= 2:
                unrecovered.append(f"{muscle_pt(m)} ({need} séries)")
        if not deficits:
            changes.append("O volume da semana continua dentro do alvo sem esse treino — nada precisou ser compensado.")

    # ---------- 3) cardio perdido
    if strategy == "redistribuir" and _cardio_items(missed):
        lost = _cardio_equiv([{"items": _cardio_items(missed)}])
        machine = next((i["exercise_id"] for i in _cardio_items(missed)), "cardio_bike_vertical")
        left = _place_cardio(lost, [x for x in future if x["status"] == "planejado"], missed["day_name"], budget, machine, changes)
        if left >= 10:
            unrecovered.append(f"cardio (~{int(left)} min)")
    if moved_cardio_left >= 5:
        left = _place_cardio(moved_cardio_left, [x for x in future if x["status"] == "planejado" and x["id"] not in created],
                             "cardio substituído", budget, moved_cardio_machine, changes)
        if left >= 10:
            unrecovered.append(f"cardio (~{int(left)} min)")

    for e in others:
        if e.get("changes") != next((s.get("changes") for s in snapshot if s["id"] == e["id"]), None) or \
                e["items"] != next((s["items"] for s in snapshot if s["id"] == e["id"]), None):
            db.save_entry(e)
    if unrecovered:
        changes.append("Não coube nesta semana: " + ", ".join(unrecovered) + ". Priorize na próxima.")
    missed["undo"] = {"snapshot": snapshot, "created": created}
    missed["skip_result"] = {"strategy": strategy, "changes": changes, "at": clock.now().isoformat(timespec="minutes")}
    db.save_entry(missed)
    return {"strategy": strategy, "changes": changes, "unrecovered": unrecovered}


def _end_item(e: dict) -> dict | None:
    return next((i for i in e.get("items", []) if i.get("endurance")), None)


def _is_heavy_end(e: dict) -> bool:
    it = _end_item(e)
    return bool(it and (it["endurance"].get("hard") or it["endurance"].get("role") == "longo")
                and it.get("sport") in endurance.LEG_SPORTS)


def _missed_endurance(missed: dict, others: list[dict], future: list[dict], profile: dict, ws: date, today: date):
    """Longão/forte perdido: mover para dia livre seguro; senão trocar com a próxima leve; leve: só registra."""
    it = _end_item(missed)
    changes, unrecovered, created = [], [], []
    if not it:
        return "dispensar", ["Sem sessão de resistência para reajustar."], [], []
    end = it["endurance"]
    sport = it.get("sport")
    role = end.get("role")
    if role == "leve" and not end.get("hard"):
        changes.append("Sessão leve perdida: não precisa compensar — o que constrói o condicionamento é a "
                       "consistência das próximas semanas, não recuperar cada quilômetro.")
        return "dispensar", changes, [], []
    active_entries = [e for e in others if e["status"] in ("planejado", "feito")]
    first = max(today + timedelta(days=1), ws)
    for wd in available_days(profile):
        cand = ws + timedelta(days=wd)
        if cand < first or cand.isoformat() == missed["date"]:
            continue
        same = [e for e in active_entries if e["date"] == cand.isoformat()]
        if any(e.get("kind") == "resistencia" for e in same) or len(same) >= 2 or any(e["status"] == "feito" for e in same):
            continue
        near = [e for e in active_entries if abs((_d(e["date"]) - cand).days) == 1]
        if any(_is_heavy_end(e) for e in near) and sport in endurance.LEG_SPORTS:
            continue
        new = {k: copy.deepcopy(v) for k, v in missed.items() if k not in ("id", "undo", "garmin_workout_id", "garmin_outdated")}
        new.update(date=cand.isoformat(), status="planejado", moved_from=missed["date"], changes=[])
        _note(new, f"Movido de {_fmt(missed['date'])}")
        saved = db.save_entry(new)
        created.append(saved["id"])
        changes.append(f"{missed['day_name']} foi para {_fmt(saved['date'])}" + (" (dia com musculação: faça em outro horário)" if same else "") + ".")
        return "mover", changes, unrecovered, created
    # sem dia livre: a próxima leve da modalidade vira a sessão perdida (forte) ou ganha volume (longão)
    easy = [e for e in future if e.get("kind") == "resistencia" and e.get("sport") == sport and _end_item(e)
            and not _end_item(e)["endurance"].get("hard") and _end_item(e)["endurance"].get("role") == "leve"]
    if easy:
        tgt = easy[0]
        if end.get("hard"):
            old_items = tgt["items"]
            tgt["items"] = [copy.deepcopy(it)] + [i for i in old_items if not i.get("endurance")]
            tgt["day_name"] = f"{endurance.sp.SPORTS[sport]['name']}: {end['title']}"
            _note(tgt, f"Virou a sessão forte perdida em {_fmt(missed['date'])} (a leve sai)")
            changes.append(f"{end['title']} entra no lugar da sessão leve de {_fmt(tgt['date'])}.")
            return "trocar", changes, unrecovered, created
        t_it = _end_item(tgt)
        add = 0.3
        for st in t_it["endurance"]["steps"]:
            if st.get("kind") != "repeat":
                for k in ("dist_m", "dur_s"):
                    if st.get(k):
                        st[k] = int(st[k] * (1 + add))
        for k in ("duration_min", "distance_km"):
            if t_it.get(k):
                t_it[k] = round(t_it[k] * (1 + add), 2)
        t_it["endurance"]["dur_s"] = int(t_it["endurance"]["dur_s"] * (1 + add))
        _note(tgt, f"+30% de volume (compensa parte do longão de {_fmt(missed['date'])})")
        changes.append(f"Sem dia livre: a sessão leve de {_fmt(tgt['date'])} ganhou 30% de volume. Não tente "
                       "repor o longão inteiro de uma vez.")
        unrecovered.append(f"{end['title']} (parte)")
        return "redistribuir", changes, unrecovered, created
    changes.append("Sem dia livre nem sessão leve para ajustar nesta semana: siga o plano da próxima.")
    unrecovered.append(end["title"])
    return "dispensar", changes, unrecovered, created


def undo(entry_id: int) -> None:
    e = db.get_entry(entry_id)
    if not e or not e.get("undo"):
        raise ValueError("Nada para desfazer")
    for cid in e["undo"].get("created", []):
        db.delete_entry(cid)
    for snap in e["undo"].get("snapshot", []):
        cur = db.get_entry(snap["id"])
        if cur and cur["status"] != "feito":
            db.save_entry(snap)
    e.pop("undo", None)
    e.pop("skip_result", None)
    e["status"] = "planejado"
    db.save_entry(e)


def move(entry_id: int, to: str) -> dict:
    e = db.get_entry(entry_id)
    if not e:
        raise ValueError("Treino não encontrado")
    old = e["date"]
    e["date"] = _d(to).isoformat()
    e["status"] = "planejado"
    _note(e, f"Movido manualmente de {_fmt(old)}")
    saved = db.save_entry(e)
    ws = week_start(_d(to))
    warn = [x for x in db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
            if x["id"] != entry_id and x.get("kind") == "forca" and x["status"] in ("planejado", "feito")
            and abs((_d(x["date"]) - _d(to)).days) <= 1 and _conflict(saved, x)]
    same = [x for x in db.list_schedule(saved["date"], saved["date"]) if x["status"] in ("planejado", "feito")]
    msgs = []
    if warn:
        msgs.append(f"fica colado em {warn[0]['day_name']} ({_fmt(warn[0]['date'])}), que treina os mesmos músculos")
    if len(same) > 2:
        msgs.append(f"{_fmt(saved['date'])} ficou com {len(same)} treinos — considere outro dia")
    return {"entry": saved, "warning": ("Atenção: " + "; ".join(msgs) + ".") if msgs else None}


# ------------------------------------------------------------- metas da semana --

def week_goals(entries: list[dict], profile: dict, ws: date) -> dict:
    from .analysis import cardio_summary

    sessions = _sessions_in_week(ws)
    start = datetime.combine(ws, datetime.min.time())
    end = start + timedelta(days=7)
    cardio = cardio_summary(sessions, start, end, profile)
    plan = schedule_plan(profile)
    gen = (plan or {}).get("generator") or {}
    target_cardio = float(profile.get("cardio_target_min") or gen.get("cardio_target") or 150)
    planned = [e for e in entries if e["status"] not in ("substituido",)]
    done_sets = sum(len(it.get("sets") or []) for s in sessions for it in s.get("items", []) if it.get("sets"))
    planned_sets = sum(int(i.get("sets") or 0) for e in planned for i in _strength_items(e))
    minutes_done = sum(time_breakdown(e.get("items", []))["total"] for e in entries if e["status"] == "feito")
    end_goals = {}
    plan_end = [e for e in planned if e.get("kind") == "resistencia" and e["status"] != "pulado"]
    if plan_end:
        from .sports import anchors_of

        anchors = anchors_of(profile)
        unit = {s: endurance.VOLUME[s]["unit"] for s in endurance.VOLUME}
        done = defaultdict(float)
        for sx in sessions:
            st = load.session_stress(sx, anchors)
            for sport_key, km in st["km"].items():
                if unit.get(sport_key) == "km":
                    done[sport_key] += km
                elif unit.get(sport_key) == "m":
                    done[sport_key] += km * 1000
            for sport_key, mins in st["minutes"].items():
                if unit.get(sport_key) == "h":
                    done[sport_key] += mins / 60
        plan_vol = defaultdict(float)
        for e in plan_end:
            for it in e["items"]:
                en = it.get("endurance")
                if not en:
                    continue
                u = unit.get(it.get("sport"))
                if u == "km":
                    plan_vol[it["sport"]] += (en.get("dist_m") or 0) / 1000
                elif u == "m":
                    plan_vol[it["sport"]] += en.get("dist_m") or 0
                elif u == "h":
                    plan_vol[it["sport"]] += (en.get("dur_s") or 0) / 3600
        for sport_key, v in plan_vol.items():
            r = 1 if unit[sport_key] != "m" else 0
            end_goals[sport_key] = {"done": round(done.get(sport_key, 0), r), "planned": round(v, r), "unit": unit[sport_key],
                                    "name": endurance.sp.SPORTS[sport_key]["name"]}
    return {
        "endurance": end_goals,
        "sessions": {"done": sum(1 for e in entries if e["status"] == "feito") or len(sessions),
                     "planned": sum(1 for e in planned if e["status"] != "pulado")},
        "cardio": {"done": cardio["who_equivalent"], "target": round(target_cardio)},
        "sets": {"done": done_sets, "planned": planned_sets},
        "minutes": {"done": round(minutes_done), "planned": round(sum(time_breakdown(e.get("items", []))["total"]
                                                                       for e in planned if e["status"] != "pulado"))},
    }
