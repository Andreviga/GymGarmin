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

from . import catalog, clock, credit, db, endurance, generator, load
from .generator import WEEKDAYS, _cardio_equiv, _default_days, _reps_bounds, item_time, time_breakdown, weekly_targets, tier_for
from .muscles import PRIMARY_WEIGHT, SECONDARY_WEIGHT, muscle_pt
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
    # o que foi realmente feito (registros/Garmin) limita o volume: falhou semanas ou ficou doente → plano não salta
    sessions = db.list_sessions(since=(ws - timedelta(weeks=8)).isoformat(), limit=800)
    done = endurance.done_volumes([s for s in sessions if s["date"][:10] < ws.isoformat()], profile, ws)
    return endurance.week_plan(profile, ws, start, ep.get("tier") or "intermediario", recent=ep.get("start_volumes"),
                               sessions_override=ep.get("sessions"), max_hard=ep.get("max_hard"), done=done)


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
    taper = _race_taper(wp, profile, ws)
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
        base_items = generator.rotate_accessories(day.get("items", []), pos.get("cycle"), profile, gen.get("tier"))
        items = apply_cycle(base_items, pos, int(gen.get("ramp_sets") or 0),
                            budget_min=session_budget(profile, plan), allowance=allowance)
        if taper and _kind(day) == "forca":
            items = _taper_strength(items, taper, day, wd)
        elif _kind(day) == "forca" and _hybrid_station_day(profile, wp, idx, days):
            from . import hybrid_race
            items = items + hybrid_race.station_block(pos.get("week") or 0, profile,
                                                      ((profile.get("endurance") or {}).get("event") or {}))
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
    if gen.get("same_session"):
        fit_same_session(entries, days, session_budget(profile, plan))
    for race in _race_entries(plan, profile, ws, wp):
        if not from_date or _d(race["date"]) >= from_date:
            # no dia da prova, os outros treinos daquele dia saem
            entries = [e for e in entries if e["date"] != race["date"]]
            entries.append(race)
    entries.sort(key=lambda e: (e["date"], e.get("kind") != "forca"))
    return entries


def fit_same_session(entries: list[dict], days: list[dict], budget: float) -> None:
    """Musculação + esporte no mesmo treino: a corrida da semana muda de duração (progressão, semana
    leve, teste), então a força daquele dia é recortada para os dois caberem nos minutos do dia."""
    from .generator import STRENGTH_MIN_COMBINED, _fit_time, time_breakdown

    by_date: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_date[e["date"]].append(e)
    for es in by_date.values():
        strength = [e for e in es if e.get("kind") == "forca"]
        ends = [e for e in es if e.get("sport") and (days[e["day_idx"]] if 0 <= e.get("day_idx", -1) < len(days) else {}).get("same_session")]
        if not strength or not ends:
            continue
        end_min = sum(time_breakdown(e["items"])["total"] for e in ends)
        target = max(STRENGTH_MIN_COMBINED, budget - end_min - 3)
        for e in strength:
            if time_breakdown(e["items"])["total"] > target + 0.5:
                d = {"items": [dict(i) for i in e["items"]], "kind": "forca"}
                _fit_time([d], target, protect_post=True)
                e["items"] = d["items"]
                e.setdefault("changes", []).append(f"ajustado para caber com o esporte do dia ({int(budget)} min no total)")


def _hybrid_station_day(profile: dict, wp: dict | None, idx: int, days: list[dict]) -> bool:
    """Prova híbrida: as estações entram no 1º treino de força da semana (e no 2º no pico)."""
    ev = ((profile or {}).get("endurance") or {}).get("event") or {}
    if ev.get("type") != "hibrida" or not wp:
        return False
    phase = ((wp.get("sports") or {}).get("corrida") or {}).get("phase")
    if phase not in ("base", "construcao", "pico"):
        return False
    strength_idx = [i for i, d in enumerate(days) if _kind(d) == "forca"]
    return idx in strength_idx[:2 if phase == "pico" else 1]


def _race_taper(wp: dict | None, profile: dict, ws: date) -> dict | None:
    """Semana de polimento ou de prova de uma modalidade de resistência: a musculação acompanha."""
    cfg = endurance.config(profile)
    for sport, d in ((wp or {}).get("sports") or {}).items():
        if d.get("phase") in ("polimento", "prova"):
            race_wd = None
            if d["phase"] == "prova":
                rd = _d(((cfg.get(sport) or {}).get("goal") or {}).get("date"))
                race_wd = (rd - ws).days if rd and 0 <= (rd - ws).days <= 6 else 6
            return {"phase": d["phase"], "sport": sport, "race_wd": race_wd}
    return None


def _taper_strength(items: list[dict], taper: dict, day: dict, wd: int) -> list[dict]:
    """Polimento: mantém a carga e corta ~1/3 das séries (a força não cai em 2–3 semanas, a fadiga sim).
    Semana da prova: volume de deload e, nos 2 dias antes da prova, nada pesado de perna."""
    out = copy.deepcopy(items)
    race_week = taper["phase"] == "prova"
    near_race = race_week and taper.get("race_wd") is not None and 0 <= taper["race_wd"] - wd <= 2
    keep = []
    for it in out:
        if it.get("phase") not in (None, "principal") or not it.get("sets"):
            keep.append(it)
            continue
        # 2 dias antes da prova: nenhum exercício de perna, em qualquer treino (perna, corpo inteiro ou acessório)
        if near_race and taper["sport"] in ("corrida", "ciclismo", "multi", "trilha", "caminhada"):
            prim = (catalog.get(it.get("exercise_id")) or {}).get("primaryMuscles") or []
            if any(m in ("quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors") for m in prim):
                continue  # pernas descansadas para a prova
        f = 0.5 if race_week else 0.7  # polimento: volume −30%; semana da prova −50% (Bosquet 2007: cortar 41–60%)
        it["sets"] = max(1, round(it["sets"] * f))
        it["taper"] = True
        it["notes"] = ((it.get("notes") or "") + (" · semana da prova: menos séries, mesma carga, sem ir à falha" if race_week
                                                   else " · polimento: menos séries, mesma carga")).strip(" ·")
        keep.append(it)
    return keep


def _sessions_in_week(ws: date) -> list[dict]:
    start = ws.isoformat()
    end = (ws + timedelta(days=7)).isoformat()
    return [s for s in db.list_sessions(since=start, limit=500) if s["date"][:10] < end]


def _targets(plan: dict | None, profile: dict) -> tuple[str, dict]:
    """Nível e alvo semanal de séries por músculo (do plano; sem plano, do perfil)."""
    gen = (plan or {}).get("generator") or {}
    months = profile.get("training_months")
    tier = gen.get("tier") or tier_for(months if months is not None else 12)
    return tier, gen.get("targets") or weekly_targets(tier, profile.get("goal") or "hipertrofia", profile.get("volume_factor") or 1)


def _watch_strength(sx: dict) -> bool:
    """A sessão traz musculação medida pelo relógio (minutos de força, sem séries detalhadas)?"""
    return any(it.get("strength_min") for it in sx.get("items", []))


def _strength_match(sx: dict, e: dict) -> bool:
    """Sessão sem vínculo fecha a musculação do dia? Sessão registrada no app: só pelo CONTEÚDO (precisa cobrir
    a maior parte do que estava planejado: bíceps, tríceps ou alongamento não "fazem" um treino de perna).
    Atividade do relógio ou importada (sem séries confiáveis): vale pelo tipo, como sempre."""
    if sx.get("extra") or sx.get("match_pending"):
        return False
    if sx.get("client_id") and not _watch_strength(sx):
        if not credit.has_sets(sx):
            return False
        cov = credit.coverage(sx, e)
        return cov is None or cov >= credit.COVER_CLOSE
    return any(it.get("sets") or it.get("strength_min") for it in sx.get("items", []))


def sync_status(entries: list[dict], sessions: list[dict], today: date) -> list[dict]:
    by_sched: dict = {}
    for s in sessions:
        if s.get("schedule_id"):
            by_sched.setdefault(s["schedule_id"], s)
    # uma atividade não vale por dois treinos do mesmo tipo; mas musculação + corrida na mesma sessão
    # fecha a musculação E a corrida do dia (mesmo quando a sessão já está ligada a um dos dois)
    used: set = set()

    def cls_of(e: dict | None) -> tuple:
        k = (e or {}).get("kind")
        return ("forca",) if k == "forca" else ("end",) if k in ("resistencia", "cardio") else ("forca", "end")

    by_entry = {e["id"]: e for e in entries}
    by_date = defaultdict(list)
    for s in sessions:
        if s.get("schedule_id"):  # já ligada a um treino: não vale de novo para outro treino do MESMO tipo
            used.update((s.get("id"), c) for c in cls_of(by_entry.get(s["schedule_id"])))
        by_date[s["date"][:10]].append(s)

    def linked(e: dict) -> dict | None:
        """Sessão aberta pela agenda: fecha o treino, a não ser que a pessoa tenha feito menos de 25% dele."""
        sx = by_sched.get(e["id"])
        if sx and e.get("kind") == "forca" and sx.get("client_id") and not _watch_strength(sx):
            cov = credit.coverage(sx, e)
            if sx.get("extra") or sx.get("match_pending") or (cov is not None and cov < credit.COVER_ASK):
                return None
        return sx

    def matches(e, sess_list, substitute: bool = False):
        """Sessão sem vínculo conta para o treino do dia só se for do mesmo tipo:
        uma caminhada curta registrada pelo relógio não marca a musculação como feita.
        Com `substitute`, aceita outra modalidade de resistência de duração parecida."""
        planned_min = sum((it.get("endurance") or {}).get("dur_s", 0) / 60 or float(it.get("duration_min") or 0)
                          for it in e.get("items") or [] if it.get("endurance") or it.get("sport"))
        kind = e.get("kind")
        for sx in sess_list:
            if any((sx.get("id"), c) in used for c in cls_of(e)):
                continue
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
                ok = (kind == "forca" and _strength_match(sx, e)) \
                    or (kind == "resistencia" and end_min.get(e.get("sport"), 0) >= max(10, 0.4 * planned_min)) \
                    or (kind == "cardio" and sum(end_min.values()) >= 10) \
                    or (kind not in ("forca", "resistencia", "cardio") and (strength_done or bool(end_min)))
            if ok:
                used.update((sx.get("id"), c) for c in cls_of(e))
                return sx
        return None

    # 1º reserva as atividades dos treinos já feitos; 2º casa pelo tipo exato; 3º aceita troca de modalidade
    for e in entries:
        if e["status"] == "feito" and e["id"] not in by_sched:
            sid = (e.get("done_by") or {}).get("session_id")
            if sid is not None:
                used.update((sid, c) for c in cls_of(e))
            else:
                matches(e, by_date.get(e["date"], []))
    done: dict = {}
    # mesma modalidade duas vezes no dia (ex.: remo intervalado + contínuo): casa pela duração mais próxima
    pairs = []
    for e in entries:
        if e["status"] in ("planejado", "perdido") and e.get("kind") == "resistencia" and e["id"] not in by_sched:
            planned = sum((it.get("endurance") or {}).get("dur_s", 0) / 60 for it in e.get("items") or [])
            for sx in by_date.get(e["date"], []):
                mins = sum(float(it.get("duration_min") or 0) for it in sx.get("items", []) if load.sport_of(it) == e.get("sport"))
                if mins and (sx.get("id"), "end") not in used:
                    pairs.append((abs(mins - planned), e["id"], sx, mins, planned))
    for _, eid, sx, mins, planned in sorted(pairs, key=lambda x: x[0]):
        if eid not in done and (sx.get("id"), "end") not in used and mins >= max(10, 0.4 * planned):
            done[eid] = (sx, "data")
            used.add((sx.get("id"), "end"))
    for substitute in (False, True):
        for e in entries:
            if e["status"] in ("planejado", "perdido") and e["id"] not in done:
                sx = linked(e)
                how = "vinculo"
                if not sx and e["id"] not in by_sched:
                    sx, how = matches(e, by_date.get(e["date"], []), substitute), "data"
                if sx:
                    done[e["id"]] = (sx, how)
    out = []
    for e in entries:
        status = e["status"]
        if status in ("planejado", "perdido"):
            if e["id"] in done:
                sx, how = done[e["id"]]
                cov = credit.coverage(sx, e) if e.get("kind") == "forca" and credit.has_sets(sx) else None
                e["done_by"] = {"session_id": sx.get("id"), "how": how, "coverage": cov, "prev": status}
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
    orphans = []
    week = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    for e in [x for x in week if x["date"] >= today.isoformat()]:
        if e["status"] in ("planejado", "perdido"):
            if e.get("garmin_workout_id"):
                orphans.append(e["garmin_workout_id"])  # já está no relógio: apagar lá no próximo envio
            if e.get("moved_from"):  # cópia de um treino movido: o original passa a ser só "pulado"
                for o in week:
                    u = o.get("undo") or {}
                    if o["status"] == "pulado" and e["id"] in (u.get("created") or []):
                        u["created"] = [c for c in u["created"] if c != e["id"]]
                        if o.get("skip_result"):
                            o["skip_result"]["strategy"] = "seguir"
                        db.save_entry(o)
            db.delete_entry(e["id"])
    if orphans:
        import json
        old = json.loads(db.kv_get("garmin_orphans") or "[]")
        db.kv_set("garmin_orphans", json.dumps(sorted(set(old) | set(orphans), key=str)))
    plan = schedule_plan(profile)
    if plan:
        now = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
        existing = {e["date"] for e in now if e["status"] == "feito"}
        skipped = {(e["date"], e.get("kind")) for e in now if e["status"] == "pulado"}  # treino pulado não volta duplicado
        for e in build_week(plan, profile, ws, from_date=today):
            if e["date"] not in existing and (e["date"], e.get("kind")) not in skipped:
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


# ------------------------------------------------------- pular / treino perdido --
# Princípio (estudo de 20/11/2026): falta isolada não gera dívida. Uma semana mais leve não faz perder o que
# foi construído (pausas de 1 semana em treinados não mudaram a hipertrofia: Coleman et al. 2024), a frequência
# não pesa com o volume igualado (Schoenfeld, Grgic e Krieger 2019) e nenhum app de referência espalha pedaços
# do treino perdido em treino de outro grupo. Então o padrão é SEGUIR: pular sem mexer nos outros dias.
# O reforço existe, mas é pequeno e nunca piora o treino que o recebe. Todos os números abaixo são
# heurísticas de engenharia (conservadoras), não valores da literatura.
SESSION_CAP = {"iniciante": 8, "intermediario": 10, "avancado": 10, "elite": 10}  # séries fracionadas por músculo
#   por treino até onde o APP acrescenta (o ganho extra por sessão fica difícil de detectar por volta de 11
#   séries fracionadas: Remmert et al. 2025, preprint; paramos antes). Não limita o que a pessoa escolhe fazer.
COMP_PER_EXERCISE = 1        # no máximo +1 série por exercício
COMP_EXERCISE_MAX_SETS = 5   # e o exercício não passa de 5 séries
COMP_PER_MUSCLE = 3          # no máximo +3 séries por músculo
COMP_PER_SESSION = 4         # no máximo +4 séries por treino
DAY_TOL = 1.10               # o dia (musculação + corrida) pode passar 10% do tempo do perfil
NEIGHBOR_DIRECT = 4          # 4+ séries diretas do mesmo músculo no dia vizinho bloqueiam o reforço
REINFORCE_MIN_DIRECT = 2     # o treino que recebe o reforço já precisa treinar o músculo (2+ séries diretas)
#   (repetir peito pesado com 24 h derrubou as repetições; com 48 h não: Miranda et al. 2018)
LEG_DAY_DIRECT = 6           # treino com 6+ séries diretas de perna é "dia de perna"
LOWER = {"quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"}


def _num(v: float) -> str:
    return f"{round(float(v), 1):g}".replace(".", ",")


def _is_leg_item(it: dict) -> bool:
    ex = catalog.get(it.get("exercise_id")) or {}
    prim = set(ex.get("primaryMuscles") or [])
    return bool(prim & LOWER) or ("lower back" in prim and bool(set(ex.get("secondaryMuscles") or []) & LOWER))


def _is_leg_day(e: dict) -> bool:
    return sum(v for m, v in credit.entry_direct(e).items() if m in LOWER) >= LEG_DAY_DIRECT


def _active(e: dict) -> bool:
    return e.get("status") in ("planejado", "feito")


def _day_minutes(day_iso: str, entries: list[dict]) -> float:
    """Tempo total do dia: musculação + corrida (o que conta para a pessoa é o dia, não cada treino)."""
    return sum(_minutes(e) for e in entries if e["date"] == day_iso and _active(e))


def _heavy_end_on(day_iso: str, entries: list[dict]) -> bool:
    return any(_is_heavy_end(e) for e in entries if e["date"] == day_iso and _active(e))


def _direct_on(day_iso: str, entries: list[dict], sessions: list[dict]) -> dict[str, float]:
    """Séries diretas por músculo num dia: o que está planejado + o que foi feito (registrado no app; treino
    fechado pelo relógio vale pelo plano, porque a detecção de exercícios do relógio não é confiável)."""
    out: dict[str, float] = defaultdict(float)
    by_plan = False
    for e in entries:
        if e["date"] != day_iso or e.get("kind") != "forca":
            continue
        plan_credit = credit.credited_by_plan(e, sessions)
        by_plan = by_plan or plan_credit
        if e["status"] == "planejado" or plan_credit:
            for m, v in credit.entry_direct(e).items():
                out[m] += v
    for s in sessions:
        if str(s.get("date"))[:10] == day_iso and credit.has_sets(s) and (s.get("client_id") or not by_plan):
            for m, v in credit.session_direct(s).items():
                out[m] += v
    return out


def _skip_ctx(missed: dict, profile: dict, today: date) -> dict:
    ws = week_start(_d(missed["date"]))
    entries = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    others = [e for e in entries if e["id"] != missed["id"]]
    plan = db.get_plan(missed["plan_id"]) if missed.get("plan_id") else None
    gen = (plan or {}).get("generator") or {}
    months = profile.get("training_months")
    tier = gen.get("tier") or tier_for(months if months is not None else 12)
    targets = gen.get("targets") or weekly_targets(tier, profile.get("goal") or "hipertrofia", profile.get("volume_factor") or 1)
    sessions = _sessions_in_week(ws)
    future = [e for e in others if e["status"] == "planejado" and _d(e["date"]) >= today]
    if _d(missed["date"]) == today:
        future = [e for e in future if _d(e["date"]) > today]
    return {
        "ws": ws, "today": today, "entries": entries, "others": others, "future": future, "plan": plan, "tier": tier,
        "sessions": sessions, "minutes": session_budget(profile, plan), "profile": profile,
        "balance": credit.week_balance(entries, sessions, targets, today, exclude_id=missed["id"]),
        "deload": bool((missed.get("cycle") or {}).get("deload")) or any(i.get("taper") for i in missed.get("items") or []),
        "other_misses": any(e.get("kind") == "forca" and e["status"] in ("pulado", "perdido") and not credit._moved_away(e)
                            for e in others),
    }


def _affected(missed: dict, ctx: dict) -> list[dict]:
    """Músculos que o treino pulado treinava de verdade (2+ séries diretas), com o saldo da semana sem ele."""
    direct = credit.entry_direct(missed, base=True)
    rows = [ctx["balance"][m] for m, v in direct.items() if v >= 2 and ctx["balance"][m]["ref"] >= credit.MIN_REF]
    return sorted(rows, key=lambda b: b["projected"] - b["ref"])


def _reinforce_ops(missed: dict, ctx: dict, asked: bool) -> tuple[list[dict], dict[int, dict]]:
    """Plano de reforço: +1 série em exercícios que JÁ existem em treinos que JÁ treinam o músculo.

    Nunca cria exercício, nunca cruza região, nunca passa do teto por treino, nunca cai colado em outro dia
    forte do músculo, nunca põe perna no dia/véspera de longão ou corrida forte, e respeita o tempo do DIA.
    Devolve (operações, cópias dos treinos já com o reforço).
    """
    if ctx["deload"]:
        return [], {}
    bal, cap, limit = ctx["balance"], SESSION_CAP.get(ctx["tier"], 10), ctx["minutes"] * DAY_TOL
    work = {e["id"]: copy.deepcopy(e) for e in ctx["future"] if e.get("kind") == "forca"}
    view = [work.get(e["id"], e) for e in ctx["others"]]  # a semana como ficaria (sem o treino pulado)
    projected = {m: b["projected"] for m, b in bal.items()}
    missed_direct = credit.entry_direct(missed, base=True)
    comp = {eid: sum(int(i.get("added") or 0) for i in credit.strength_items(e)) for eid, e in work.items()}
    ops: list[dict] = []

    def need_of(m: str) -> int:
        if asked:  # a pessoa pediu: até metade do que o treino pulado dava, sem passar da referência
            room = min(bal[m]["ref"] - projected[m], 0.5 * missed_direct[m])
            return min(COMP_PER_MUSCLE, math.ceil(room)) if room >= 1 else 0
        gap = bal[m]["floor"] - projected[m]  # automático: só se o músculo fica bem abaixo do plano
        return min(COMP_PER_MUSCLE, math.ceil(gap)) if gap >= credit.MIN_GAP else 0

    for b in _affected(missed, ctx):
        m = b["key"]
        need = need_of(m)
        while need > 0:
            best = None
            for e in sorted(work.values(), key=lambda x: (credit.entry_load(x).get(m, 0), x["date"])):
                load_e, day = credit.entry_load(e), _d(e["date"])
                if comp[e["id"]] >= COMP_PER_SESSION or credit.entry_direct(e).get(m, 0) < REINFORCE_MIN_DIRECT:
                    continue
                if any(_direct_on((day + timedelta(days=k)).isoformat(), view, ctx["sessions"]).get(m, 0) >= NEIGHBOR_DIRECT
                       for k in (-1, 1)):
                    continue
                run_block = _heavy_end_on(e["date"], view) or _heavy_end_on((day + timedelta(days=1)).isoformat(), view)
                cands = []
                for idx, it in enumerate(e["items"]):
                    ex = catalog.get(it.get("exercise_id")) or {}
                    if (it.get("phase") or "principal") != "principal" or ex.get("kind") != "strength" or m not in ex["primaryMuscles"]:
                        continue
                    if int(it.get("added") or 0) >= COMP_PER_EXERCISE or int(it.get("sets") or 0) >= COMP_EXERCISE_MAX_SETS:
                        continue
                    if it.get("superset") or it.get("duration_s") or it.get("added_from"):
                        continue
                    if it.get("role") == "main" and _reps_bounds(it.get("reps"))[1] <= 6:
                        continue  # exercício principal pesado não recebe série de compensação
                    if _is_leg_item(it) and run_block:
                        continue
                    if any(load_e.get(x, 0) + PRIMARY_WEIGHT > cap for x in ex["primaryMuscles"]) \
                            or any(load_e.get(x, 0) + SECONDARY_WEIGHT > cap for x in ex["secondaryMuscles"]):
                        continue
                    if _day_minutes(e["date"], view) + _set_minutes(it) > limit:
                        continue
                    cands.append(({"sec": 0, "acc": 1}.get(it.get("role"), 2), len(ex["primaryMuscles"]) > 1, idx))
                if cands:
                    best = (e, min(cands)[2])
                    break
            if not best:
                break
            e, idx = best
            it = e["items"][idx]
            it["sets"] = int(it["sets"]) + 1
            it["added"] = int(it.get("added") or 0) + 1
            comp[e["id"]] += 1
            ex = catalog.get(it["exercise_id"])
            for x in ex["primaryMuscles"]:
                projected[x] = projected.get(x, 0) + PRIMARY_WEIGHT
            for x in ex["secondaryMuscles"]:
                projected[x] = projected.get(x, 0) + SECONDARY_WEIGHT
            ops.append({"entry_id": e["id"], "exercise_id": it["exercise_id"], "index": idx, "sets": 1, "muscle": m})
            need -= 1
    return ops, work


def _ops_lines(ops: list[dict], ctx: dict, work: dict[int, dict]) -> list[dict]:
    """Operações agrupadas por dia, com texto pronto para a tela."""
    by: dict[int, list[dict]] = defaultdict(list)
    for o in ops:
        by[o["entry_id"]].append(o)
    out = []
    for eid, lst in by.items():
        before = next(e for e in ctx["entries"] if e["id"] == eid)
        names = [catalog.get(o["exercise_id"])["name"] for o in lst]
        mins = (round(_minutes(before)), round(_minutes(work[eid])))
        out.append({"entry_id": eid, "date": before["date"], "day_name": before["day_name"],
                    "lines": [f"+1 série em {n}" for n in names],
                    "text": f"{_fmt(before['date'])} · {before['day_name']}: " + ", ".join(f"+1 série em {n}" for n in names)
                            + (f" ({mins[0]} → {mins[1]} min)" if mins[1] != mins[0] else "") + "."})
    return sorted(out, key=lambda d: d["date"])


def _move_days(missed: dict, ctx: dict) -> list[dict]:
    """Dias possíveis para fazer o treino pulado, cada um com o que já existe nele, bloqueios e avisos."""
    today, ws, others = ctx["today"], ctx["ws"], ctx["others"]
    first = max(today + (timedelta(days=1) if _d(missed["date"]) <= today else timedelta()), ws)
    avail = set(available_days(ctx["profile"]))
    mine = {m for m, v in credit.entry_direct(missed, base=True).items() if v >= NEIGHBOR_DIRECT}
    leg, limit, out = _is_leg_day(missed), ctx["minutes"] * DAY_TOL, []
    for k in range(7):
        cand = ws + timedelta(days=k)
        iso = cand.isoformat()
        if cand < first or iso == missed["date"]:
            continue
        same = [e for e in others if e["date"] == iso and _active(e)]
        label = " + ".join(e["day_name"] for e in same) or "livre"
        # dia de cardio: o cardio vira finalizador só até onde cabe no tempo; o resto sai da semana
        cardio_days = [e for e in same if e.get("kind") == "cardio" and e["status"] == "planejado"]
        c_total = round(sum(float(c.get("duration_min") or 0) for e in cardio_days for c in _cardio_items(e)))
        fin = _finisher_fit(missed, c_total, limit)
        total = _day_minutes(iso, others) - sum(_minutes(e) for e in cardio_days) + _minutes(missed) + fin
        races = [e for e in others if e.get("race") and 0 < (_d(e["date"]) - cand).days <= 2]
        reason, warns = None, []
        if any(e.get("race") for e in same):
            reason = "dia da prova"
        elif any(e["status"] == "feito" for e in same):
            reason = "dia já treinado"
        elif any(e.get("kind") == "forca" for e in same):
            reason = "já tem musculação neste dia"
        elif len([e for e in same if e.get("kind") != "cardio"]) >= 2:
            reason = "dia cheio"
        elif any(_is_heavy_end(e) for e in same):
            reason = "dia de longão ou corrida forte"
        elif leg and _heavy_end_on((cand + timedelta(days=1)).isoformat(), others):
            reason = "véspera de longão ou corrida forte (treino de perna)"
        if not reason:
            clash = sorted({muscle_pt(m) for d in (-1, 1) for m, v in
                            _direct_on((cand + timedelta(days=d)).isoformat(), others, ctx["sessions"]).items()
                            if m in mine and v >= NEIGHBOR_DIRECT})
            if clash:
                warns.append(f"{', '.join(clash)} em dias seguidos")
            if total > limit:
                warns.append(f"o dia fica com ~{round(total)} min")
            if cardio_days:
                warns.append(f"{fin} min do cardio do dia passam para o fim do treino" + (f" (os outros {c_total - fin} min saem)" if fin < c_total else "")
                             if fin else f"o cardio do dia ({c_total} min) sai da semana: não cabe junto com o treino")
            if races:
                warns.append("treino de perna a menos de 3 dias da prova" if leg else "prova em menos de 3 dias: vá leve")
            if cand.weekday() not in avail:
                warns.append("não é um dia de treino do seu perfil")
            if cand == today:
                warns.append("é hoje")
        # distância até outro treino forte dos mesmos músculos (quanto maior, melhor a recuperação)
        dist = min([abs((_d(e["date"]) - cand).days) for e in others if e.get("kind") == "forca" and _active(e)
                    and any(credit.entry_direct(e).get(m, 0) >= NEIGHBOR_DIRECT for m in mine)] or [7])
        out.append({"date": iso, "label": f"{_fmt(iso)} · {label}" + (f" · ~{round(_day_minutes(iso, others))} min" if same else ""),
                    "ok": not reason, "reason": reason, "warnings": warns, "minutes": round(total),
                    "recommendable": not reason and not warns and not same, "score": dist})
    return out


def _finisher_fit(missed: dict, cardio_min: float, limit: float) -> int:
    """Quantos minutos do cardio do dia cabem como finalizador depois do treino movido (múltiplos de 5; 0 se a folga for < 10)."""
    slack = limit - _minutes(missed)
    return int(min(cardio_min, slack) // 5 * 5) if slack >= 10 and cardio_min else 0


def _apply_move(missed: dict, to_iso: str, ctx: dict, undo_info: dict) -> list[str]:
    """Cria o treino no novo dia. Dia com cardio: o cardio vira finalizador (até onde cabe) e o dia de cardio é substituído."""
    others, budget = ctx["others"], ctx["minutes"] * DAY_TOL
    new = {k: copy.deepcopy(v) for k, v in missed.items()
           if k not in ("id", "undo", "skip_result", "garmin_workout_id", "garmin_outdated", "garmin_fmt", "done_by", "partial")}
    new.update(date=to_iso, status="planejado", moved_from=missed["date"], changes=[])
    lines, fin_added, cardio_total = [], 0, 0
    for cardio_day in [e for e in others if e["date"] == to_iso and e["status"] == "planejado" and e.get("kind") == "cardio"]:
        slack = budget - _minutes(new)
        cardio_total += round(sum(float(c.get("duration_min") or 0) for c in _cardio_items(cardio_day)))
        for c in _cardio_items(cardio_day):
            if slack >= 10 and c.get("duration_min"):
                mins = int(min(c["duration_min"], slack) // 5 * 5)
                fin = {**copy.deepcopy(c), "finisher": True, "duration_min": mins, "notes": "cardio do dia (virou finalizador)"}
                posts = [i for i in new["items"] if i.get("phase") == "pos"]
                new["items"].insert(new["items"].index(posts[0]) if posts else len(new["items"]), fin)
                slack -= mins
                fin_added += mins
        note = f"Substituído pelo treino {missed['day_name']} (movido)"
        undo_info["status_changes"].append({"entry_id": cardio_day["id"], "from": cardio_day["status"], "to": "substituido"})
        undo_info["notes"].append({"entry_id": cardio_day["id"], "text": note})
        cardio_day["status"] = "substituido"
        _note(cardio_day, note)
        db.save_entry(cardio_day)
    _note(new, f"Movido de {_fmt(missed['date'])}")
    saved = db.save_entry(new)
    undo_info["created"].append(saved["id"])
    lines.append(f"{missed['day_name']} foi movido para {_fmt(saved['date'])}"
                 + (f" ({fin_added} min do cardio do dia passaram para o fim do treino)" if fin_added else "") + ".")
    if cardio_total > fin_added:
        lines.append(f"{cardio_total - fin_added} min de cardio desse dia saíram da semana (não cabiam junto com o treino).")
    return lines


def _calm(ctx: dict) -> str:
    """A frase que explica por que não precisa correr atrás (muda se já houve outra falta na semana)."""
    if ctx["deload"]:
        return "Semana leve: nada a repor. Volte ao normal na próxima."
    if ctx["other_misses"]:
        return ("Com mais de uma falta, a semana fica mais leve para esses músculos. Uma semana assim não faz perder o que "
                "foi construído; retome o plano normal na próxima, sem dobrar treino.")
    return "Uma falta isolada não muda o resultado de forma perceptível: o que conta é a sequência das próximas semanas."


def _skip_options(missed: dict, ctx: dict) -> dict:
    """As opções de "Não vou treinar", com a consequência de cada uma e UMA recomendação com o motivo."""
    affected = _affected(missed, ctx)
    closing = "; ".join(f"{b['pt']}: {_num(b['projected'])} de {_num(b['ref'])} séries na semana" for b in affected[:3])
    seguir = {"id": "seguir", "title": "Só pular", "available": True,
              "text": "Os outros dias ficam como estão." + (f" {closing}." if closing else ""), "note": _calm(ctx)}
    opts, days, auto_ops = [seguir], [], []
    if missed.get("kind") == "forca" and credit.strength_items(missed):
        auto_ops, _ = _reinforce_ops(missed, ctx, asked=False)
        ops, work = _reinforce_ops(missed, ctx, asked=True)
        ref = {"id": "reforcar", "title": "Reforçar um pouco o próximo treino parecido", "available": bool(ops)}
        if ops:
            lines = _ops_lines(ops, ctx, work)
            ref.update(text=" ".join(d["text"] for d in lines) + " Os outros treinos não mudam.", days=lines)
        elif ctx["deload"]:
            ref["reason"] = "Semana leve: não precisa repor."
        elif not any(credit.entry_direct(e).get(b["key"], 0) >= REINFORCE_MIN_DIRECT for e in ctx["future"] if e.get("kind") == "forca"
                     for b in affected):
            ref["reason"] = "Não há outro treino desses músculos daqui até o fim da semana."
        elif not any(b["ref"] - b["projected"] >= 1 for b in affected):
            ref["reason"] = "A semana já fica no volume planejado sem este treino."
        else:
            ref["reason"] = "Os próximos treinos desses músculos já estão no limite (séries por treino, dias seguidos ou tempo do dia)."
        opts.append(ref)
        days = _move_days(missed, ctx)
        okd = [d for d in days if d["ok"]]
        mv = {"id": "mover", "title": "Fazer em outro dia", "available": bool(okd)}
        best = max((d for d in days if d["recommendable"]), key=lambda d: (d["score"], -_d(d["date"]).toordinal()), default=None)
        if best:
            mv.update(text=f"Dia livre: {_fmt(best['date'])}. O treino inteiro vai para lá, sem mexer nos outros.", to_date=best["date"])
        elif okd:
            mv["text"] = "Seus outros dias de treino já estão ocupados; dá para escolher outro dia, com os avisos de cada um."
        else:
            mv["reason"] = "Não sobrou dia possível nesta semana."
        opts.append(mv)
    elif missed.get("kind") == "cardio" and _cardio_items(missed):
        opts.append({"id": "reforcar", "title": "Repor o cardio nos outros dias", "available": True,
                     "text": "Os minutos de cardio viram finalizadores (até 20 min por treino), dentro do tempo do dia."})
    # ---- uma recomendação só
    rec, why = "seguir", "Uma falta isolada não precisa ser reposta."
    mover = next((o for o in opts if o["id"] == "mover"), None)
    if ctx["deload"]:
        why = "Semana leve: não precisa repor."
    elif ctx["other_misses"]:
        why = "Já houve outra falta nesta semana: o melhor é seguir com os treinos que restam, sem empilhar."
    elif mover and mover.get("to_date"):
        rec, why = "mover", "Existe um dia livre: o treino inteiro cabe lá sem atrapalhar os outros."
    elif len(auto_ops) >= 2:
        rec, why = "reforcar", "Sem este treino, um músculo fica bem abaixo do planejado na semana."
    for o in opts:
        o["recommended"] = o["id"] == rec
    run = next((e for e in ctx["others"] if e["date"] == missed["date"] and e.get("kind") == "resistencia"
                and e["status"] == "planejado"), None) if missed.get("kind") != "resistencia" else None
    return {"options": opts, "recommended": rec, "why": why, "move_days": days,
            "muscles": [{k: b[k] for k in ("key", "pt", "done", "scheduled", "projected", "ref")} for b in affected],
            "run": {"id": run["id"], "name": run["day_name"]} if run else None}


def handle_missed(entry_id: int, profile: dict, today: date | None = None, strategy: str | None = None,
                  to_date: str | None = None, dry_run: bool = False, skip_endurance: bool = False) -> dict:
    """"Não vou treinar" / ajustar treino perdido.

    dry_run → só as opções com a prévia (nada é gravado). Sem `strategy` vale "seguir" (não mexe em nenhum
    outro dia). "mover" leva o treino inteiro para `to_date` (ou para o melhor dia livre). "reforcar" soma
    poucas séries em treinos que já treinam os mesmos músculos.
    """
    today = today or clock.today()
    missed = db.get_entry(entry_id)
    if not missed:
        raise ValueError("Treino da agenda não encontrado")
    if missed["status"] == "feito":
        raise ValueError("Este treino já foi feito")
    if missed["status"] not in ("planejado", "perdido"):
        raise ValueError("Este treino já foi ajustado")
    if missed.get("kind") == "resistencia":
        return _skip_endurance(missed, profile, today, dry_run, strategy)
    ctx = _skip_ctx(missed, profile, today)
    preview = _skip_options(missed, ctx)
    head = {"entry": {"id": missed["id"], "day_name": missed["day_name"], "date": missed["date"], "status": missed["status"]}}
    if dry_run:
        asked = next(({"session_id": s["id"], "coverage": (s.get("match_pending") or {}).get("coverage"), "work_sets": credit.work_sets(s)}
                      for s in ctx["sessions"] if isinstance(s.get("match_pending"), dict) and s["match_pending"].get("entry_id") == missed["id"]), None)
        return {"dry_run": True, **head, **preview, "pending": asked}
    strategy = strategy or "seguir"
    opt = next((o for o in preview["options"] if o["id"] == strategy), None)
    if not opt:
        raise ValueError("Opção inválida para este treino")
    undo_info = {"v": 2, "ops": [], "created": [], "status_changes": [], "notes": [], "linked": [], "snapshots": []}
    changes: list[str] = []
    days: list[dict] = []
    if strategy == "mover":
        target = to_date or opt.get("to_date")
        day = next((d for d in preview["move_days"] if d["date"] == target), None) if target else None
        if not day or not day["ok"]:
            raise ValueError("Não dá para mover para esse dia" + (f": {day['reason']}" if day and day.get("reason") else ""))
        changes += _apply_move(missed, target, ctx, undo_info)
        if day["warnings"]:
            changes.append("Atenção: " + "; ".join(day["warnings"]) + ".")
    elif strategy == "reforcar" and missed.get("kind") == "cardio":
        before = {e["id"]: copy.deepcopy(e) for e in ctx["future"]}
        lost = _cardio_equiv([{"items": _cardio_items(missed)}])
        machine = next((i["exercise_id"] for i in _cardio_items(missed)), "cardio_bike_vertical")
        left = _place_cardio(lost, ctx["future"], missed["day_name"], ctx["minutes"] * DAY_TOL, machine, changes)
        for e in ctx["future"]:
            old = before[e["id"]]
            if e["items"] == old["items"]:
                continue
            # marca o que este ajuste colocou (item novo ou minutos a mais) para o desfazer tirar só isso
            if len(e["items"]) > len(old["items"]):
                for it in e["items"]:
                    if it not in old["items"]:
                        it["comp_v"], it["comp_from"] = 2, missed["id"]
            else:
                for a, b in zip(old["items"], e["items"]):
                    delta = float(b.get("duration_min") or 0) - float(a.get("duration_min") or 0)
                    if delta > 0:
                        b["comp_min"] = {**(b.get("comp_min") or {}), str(missed["id"]): delta}
            for note in (e.get("changes") or [])[len(old.get("changes") or []):]:
                undo_info["notes"].append({"entry_id": e["id"], "text": note})
            undo_info["ops"].append({"entry_id": e["id"], "kind": "cardio"})
            db.save_entry(e)
        if left >= 10:
            changes.append(f"~{int(left)} min de cardio não couberam e ficam dispensados.")
    elif strategy == "reforcar":
        ops, work = _reinforce_ops(missed, ctx, asked=True)
        if not ops:
            raise ValueError(opt.get("reason") or "Não há onde reforçar nesta semana")
        days = _ops_lines(ops, ctx, work)
        for o in ops:
            e = next(x for x in ctx["future"] if x["id"] == o["entry_id"])
            it = e["items"][o["index"]]
            it["sets"] = int(it["sets"]) + 1
            it["added"] = int(it.get("added") or 0) + 1
            it["comp_v"], it["comp_from"] = 2, missed["id"]
            note = f"+1 série em {catalog.get(it['exercise_id'])['name']} (reforço: {missed['day_name']} não feito)"
            _note(e, note)
            undo_info["notes"].append({"entry_id": e["id"], "text": note})
            undo_info["ops"].append({"entry_id": e["id"], "exercise_id": it["exercise_id"], "sets": 1})
        for eid in {o["entry_id"] for o in ops}:
            db.save_entry(next(x for x in ctx["future"] if x["id"] == eid))
        changes += [d["text"] for d in days]
    else:
        changes.append("Nada mudou nos outros dias.")
    if strategy != "mover" and missed.get("kind") == "forca":
        def gained(m: str) -> float:  # o que o reforço devolveu ao músculo (séries fracionadas)
            exs = [catalog.get(o["exercise_id"]) for o in undo_info["ops"]]
            return sum(PRIMARY_WEIGHT if m in x["primaryMuscles"] else SECONDARY_WEIGHT if m in x["secondaryMuscles"] else 0 for x in exs)
        changes += [f"{b['pt']}: {_num(b['projected'] + gained(b['key']))} de {_num(b['ref'])} séries na semana."
                    for b in _affected(missed, ctx)[:3]]
        changes.append(_calm(ctx))
    missed["status"] = "pulado"
    if skip_endurance and preview.get("run"):
        try:  # "também não vou fazer" = só pular: a corrida não é remarcada nem troca de lugar com outra
            handle_missed(preview["run"]["id"], profile, today, strategy="seguir")
            undo_info["linked"].append(preview["run"]["id"])
            changes.append(f"{preview['run']['name']}: também pulada; as outras sessões ficam como estão.")
        except ValueError:
            pass
    missed["undo"] = undo_info
    missed["skip_result"] = {"strategy": strategy, "changes": changes, "at": clock.now().isoformat(timespec="seconds")}
    db.save_entry(missed)
    title = {"mover": "Treino remarcado", "reforcar": "Semana reajustada"}.get(strategy, "Treino pulado")
    return {"strategy": strategy, "title": title, "changes": changes, "days": days, "unrecovered": [], "can_undo": True,
            "muscles": preview["muscles"], **head}


def _skip_endurance(missed: dict, profile: dict, today: date, dry_run: bool, strategy: str | None = None) -> dict:
    """Corrida/bike/natação. Leve → só pula. Longão ou forte → "reajustar" (dia seguro, ou a próxima leve
    assume o papel dela) ou "seguir" (só pula). Sem `strategy` vale a recomendada."""
    it = _end_item(missed)
    end = (it or {}).get("endurance") or {}
    easy = not it or (end.get("role") == "leve" and not end.get("hard"))
    head = {"id": missed["id"], "day_name": missed["day_name"], "date": missed["date"], "status": missed["status"]}
    opts = [{"id": "seguir", "title": "Só pular", "available": True, "recommended": easy,
             "text": "Sessão leve: não precisa compensar." if easy else "As outras sessões ficam como estão."}]
    if not easy:
        opts.insert(0, {"id": "reajustar", "title": "Remarcar esta sessão", "available": True, "recommended": True,
                        "text": "Procuro um dia seguro para ela (sem colar em outra sessão forte nem em treino de perna). "
                                "Se não houver, a próxima sessão leve assume o papel dela."})
    rec = "seguir" if easy else "reajustar"
    if dry_run:
        return {"dry_run": True, "entry": head, "options": opts, "recommended": rec, "move_days": [], "muscles": [], "run": None,
                "why": "Sessão leve perdida não muda o condicionamento." if easy else
                       "Longão e sessão forte são as que mais contam na semana: vale remarcar se houver dia seguro."}
    strategy = strategy or rec
    if strategy not in [o["id"] for o in opts]:
        raise ValueError("Opção inválida para esta sessão")
    ws = week_start(_d(missed["date"]))
    entries = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    snapshot = [copy.deepcopy(e) for e in entries if e["id"] != missed["id"]]
    missed["status"] = "pulado"
    others = [e for e in entries if e["id"] != missed["id"]]
    future = [e for e in others if e["status"] == "planejado" and _d(e["date"]) >= today]
    if _d(missed["date"]) == today:
        future = [e for e in future if _d(e["date"]) > today]
    if strategy == "seguir":
        result, unrecovered, created = "seguir" if not easy else "dispensar", [], []
        changes = ["Sessão leve perdida: não precisa compensar — o que constrói o condicionamento é a consistência das "
                   "próximas semanas, não recuperar cada quilômetro."] if easy else \
                  ["Nada mudou nos outros dias. Uma sessão perdida não se repõe em dobro: siga o plano."]
    else:
        result, changes, unrecovered, created = _missed_endurance(missed, others, future, profile, ws, today)
    changed, notes = [], []
    for e in others:
        snap = next((x for x in snapshot if x["id"] == e["id"]), None)
        if snap and (e.get("changes") != snap.get("changes") or e["items"] != snap["items"] or e.get("day_name") != snap.get("day_name")):
            changed.append({"id": e["id"], "items": snap["items"], "day_name": snap.get("day_name")})
            notes += [{"entry_id": e["id"], "text": n} for n in (e.get("changes") or [])[len(snap.get("changes") or []):]]
            db.save_entry(e)
    missed["undo"] = {**_empty_undo(created), "snapshots": changed, "notes": notes}
    missed["skip_result"] = {"strategy": result, "changes": changes, "at": clock.now().isoformat(timespec="seconds")}
    db.save_entry(missed)
    return {"strategy": result, "title": "Sessão pulada" if result in ("dispensar", "seguir") else "Semana reajustada", "changes": changes,
            "days": [], "unrecovered": unrecovered, "can_undo": True, "muscles": [], "entry": {**head, "status": "pulado"}}


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
        if sport in endurance.LEG_SPORTS and any(e.get("kind") == "forca" and _is_leg_day(e) and 0 <= (cand - _d(e["date"])).days <= 1
                                                 for e in active_entries):
            continue  # longão/forte no dia do treino de perna ou no dia seguinte: pernas ainda cansadas
        new = {k: copy.deepcopy(v) for k, v in missed.items()
               if k not in ("id", "undo", "skip_result", "garmin_workout_id", "garmin_outdated", "garmin_fmt", "done_by", "partial")}
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


def _empty_undo(created: list | None = None) -> dict:
    return {"v": 2, "ops": [], "created": list(created or []), "status_changes": [], "notes": [], "linked": [], "snapshots": []}


def _revert_ops(e: dict) -> None:
    """Tira só o que o ajuste do treino `e` mudou nos outros treinos da semana.

    O reforço é achado pelo DONO (comp_from), não pelo lugar onde foi posto: continua valendo se o exercício foi
    trocado ou se o treino reforçado foi movido. Treino já feito não é mexido."""
    u = e.get("undo") or {}
    ws = week_start(_d(e["date"]))
    week = {x["id"]: x for x in db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())}
    dirty: set = set()

    def ok(x: dict | None) -> bool:
        return bool(x) and x["status"] != "feito" and x["id"] != e["id"]

    for x in week.values():
        if not ok(x):
            continue
        keep = []
        for it in x.get("items", []):
            if it.get("comp_v") == 2 and it.get("comp_from") == e["id"]:
                dirty.add(x["id"])
                if not it.get("added"):
                    continue  # item inteiro criado pelo ajuste (finalizador de cardio)
                it["sets"] = max(1, int(it.get("sets") or 0) - int(it["added"]))
                for k in ("added", "comp_v", "comp_from"):
                    it.pop(k, None)
            extra = it.get("comp_min") or {}
            if str(e["id"]) in extra:
                left = max(5.0, float(it.get("duration_min") or 0) - float(extra.pop(str(e["id"]))))
                it["duration_min"] = int(left) if left == int(left) else left
                if not extra:
                    it.pop("comp_min", None)
                dirty.add(x["id"])
            keep.append(it)
        x["items"] = keep
    for snap in u.get("snapshots", []):  # sessão de resistência que tinha assumido o papel da perdida
        x = week.get(snap.get("id"))
        if ok(x):
            for k in ("items", "day_name"):
                if k in snap:
                    x[k] = copy.deepcopy(snap[k])
            dirty.add(x["id"])
    for sc in u.get("status_changes", []):
        x = week.get(sc["entry_id"])
        if ok(x) and x["status"] == sc["to"]:
            x["status"] = sc["from"]
            dirty.add(x["id"])
    for n in u.get("notes", []):
        x = week.get(n["entry_id"])
        if ok(x) and n["text"] in (x.get("changes") or []):
            x["changes"].remove(n["text"])
            dirty.add(x["id"])
    for eid in dirty:
        x = week[eid]
        if x.get("garmin_workout_id"):
            x["garmin_outdated"] = True
        db.save_entry(x)


def _is_copy_of(c: dict, e: dict) -> bool:
    """`c` é mesmo a cópia criada ao mover `e`? (a semana pode ter sido refeita e o id reaproveitado)"""
    return c.get("moved_from") == e.get("date") and c.get("day_name") == e.get("day_name")


def _undo_check(e: dict) -> None:
    """Confere, ANTES de gravar qualquer coisa, se o ajuste ainda pode ser desfeito (inclusive em cadeia)."""
    u = e.get("undo") or {}
    for cid in u.get("created", []):
        c = db.get_entry(cid)
        if not c or not _is_copy_of(c, e):
            continue
        if c["status"] == "feito":
            raise ValueError("O treino movido já foi feito; não há o que desfazer.")
        if c["status"] == "pulado" and c.get("undo"):
            _undo_check(c)
    if u.get("v") == 2:
        for snap in u.get("snapshots", []):
            x = db.get_entry(snap.get("id") or 0)
            if x and x["status"] == "feito":
                raise ValueError("A sessão que assumiu o lugar desta já foi feita; não há o que desfazer.")


def _drop_created(e: dict) -> None:
    """Apaga a cópia criada ao mover `e`. Se a cópia também foi pulada/movida, desfaz antes o ajuste dela."""
    for cid in (e.get("undo") or {}).get("created", []):
        c = db.get_entry(cid)
        if not c or c["status"] == "feito" or not _is_copy_of(c, e):
            continue
        if c["status"] == "pulado" and c.get("undo"):
            undo(cid)
        db.delete_entry(cid)


def _noop_skip(x: dict) -> bool:
    """O pulo deste treino não mexeu em nenhum outro treino (só pulou)?"""
    u = x.get("undo") or {}
    if u.get("v") == 2:
        return not (u.get("ops") or u.get("created") or u.get("snapshots") or u.get("status_changes") or u.get("linked"))
    return (x.get("skip_result") or {}).get("strategy") == "dispensar" and not u.get("created")


def undo(entry_id: int) -> None:
    """Desfaz o ajuste de um treino pulado: só o que ESSE ajuste mudou (não o retrato da semana inteira)."""
    e = db.get_entry(entry_id)
    if not e or not e.get("undo"):
        raise ValueError("Nada para desfazer")
    u = e["undo"]
    _undo_check(e)
    if u.get("v") == 2:
        _drop_created(e)
        _revert_ops(e)
        for lid in u.get("linked", []):
            le = db.get_entry(lid)
            if le and le["status"] == "pulado" and le.get("undo"):
                try:
                    undo(lid)
                except ValueError:
                    pass  # a corrida pulada junto não pode mais voltar (já foi feita em outro dia): fica como está
    else:  # ajuste antigo (retrato da semana inteira): só é seguro se nada mais novo mexeu na semana
        ws = week_start(_d(e["date"]))
        at = (e.get("skip_result") or {}).get("at") or ""
        newer = [x for x in db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
                 if x["id"] != e["id"] and x["status"] == "pulado" and ((x.get("skip_result") or {}).get("at") or "") > at]
        block = next((x for x in newer if not _noop_skip(x)), None)
        if block:  # um ajuste mais novo mexeu em outros treinos: o retrato antigo passaria por cima dele
            raise ValueError(f"Desfaça primeiro o ajuste de {block['day_name']} ({_fmt(block['date'])}).")
        later = {x["id"] for x in newer}  # pulados depois, sem mexer em nada: ficam como estão
        _drop_created(e)
        for snap in ([] if _noop_skip(e) else u.get("snapshot", [])):  # pulo que só pulou não tem o que restaurar
            cur = db.get_entry(snap["id"])
            if not cur or cur["status"] == "feito" or cur["id"] in later:
                continue
            if snap.get("status") == "pulado" and cur["status"] != "pulado":
                # no retrato esse treino estava pulado, mas a pessoa já desfez: volta só o conteúdo, não o pulo
                snap = {**snap, **{k: cur.get(k) for k in ("status",)}}
                snap.pop("undo", None)
                snap.pop("skip_result", None)
            db.save_entry(snap)
    e = db.get_entry(entry_id) or e
    e.pop("undo", None)
    e.pop("skip_result", None)
    e["status"] = "planejado"
    db.save_entry(e)


def move(entry_id: int, to: str, today: date | None = None) -> dict:
    """Mover manualmente: valida o básico e devolve TODOS os avisos (a escolha é da pessoa)."""
    e = db.get_entry(entry_id)
    if not e:
        raise ValueError("Treino não encontrado")
    if e["status"] not in ("planejado", "perdido"):
        raise ValueError("Só dá para mover um treino que ainda não foi feito nem pulado")
    dest = _d(to)
    if week_start(dest) != week_start(_d(e["date"])):
        raise ValueError("Só dá para mover dentro da mesma semana")
    if today and dest < today:
        raise ValueError("Escolha um dia de hoje em diante")
    old = e["date"]
    e["date"] = dest.isoformat()
    e["status"] = "planejado"
    _note(e, f"Movido manualmente de {_fmt(old)}")
    saved = db.save_entry(e)
    ws = week_start(dest)
    week = [x for x in db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat()) if x["id"] != entry_id]
    msgs = []
    if saved.get("kind") == "forca":
        mine = {m for m, v in credit.entry_direct(saved).items() if v >= NEIGHBOR_DIRECT}
        for x in sorted(week, key=lambda x: x["date"]):
            if x.get("kind") != "forca" or not _active(x) or abs((_d(x["date"]) - dest).days) != 1:
                continue
            both = sorted(muscle_pt(m) for m, v in credit.entry_direct(x).items() if m in mine and v >= NEIGHBOR_DIRECT)
            if both:
                msgs.append(f"fica colado em {x['day_name']} ({_fmt(x['date'])}), que treina os mesmos músculos ({', '.join(both)})")
        if _is_leg_day(saved) and (_heavy_end_on(dest.isoformat(), week) or _heavy_end_on((dest + timedelta(days=1)).isoformat(), week)):
            msgs.append("treino de perna no dia ou na véspera de longão/corrida forte")
    same = [x for x in week if x["date"] == saved["date"] and _active(x)]
    if len(same) + 1 > 2:
        msgs.append(f"{_fmt(saved['date'])} ficou com {len(same) + 1} treinos — considere outro dia")
    elif same:
        plan = db.get_plan(saved["plan_id"]) if saved.get("plan_id") else None
        total = _day_minutes(saved["date"], week) + _minutes(saved)
        if total > session_budget(db.get_profile(), plan) * DAY_TOL + 10:
            msgs.append(f"o dia fica com ~{round(total)} min")
    return {"entry": saved, "warning": ("Atenção: " + "; ".join(msgs) + ".") if msgs else None, "warnings": msgs}


# -------------------------------------------- reajuste antigo (regra anterior a 20/11/2026) --

_LEGACY_NOTES = ("(compensa ", "(do treino ", "(volume já sobrando)")


def _legacy_items(e: dict) -> list[dict]:
    return [i for i in credit.strength_items(e) if (i.get("added") or i.get("added_from")) and i.get("comp_v") != 2]


def _legacy_notes(e: dict) -> list[str]:
    return [c for c in e.get("changes") or [] if any(k in c for k in _LEGACY_NOTES)]


def _has_legacy(e: dict) -> bool:
    return bool(_legacy_items(e) or _legacy_notes(e))


def legacy_compensation(entries: list[dict], today: date) -> dict | None:
    """O que a regra antiga espalhou pela semana ao pular um treino (para oferecer a limpeza)."""
    days = []
    for e in sorted(entries, key=lambda x: x["date"]):
        if e["status"] != "planejado" or _d(e["date"]) < today or e.get("legacy_kept") or not _has_legacy(e):
            continue
        lines = []
        for it in _legacy_items(e):
            name = catalog.get(it["exercise_id"])["name"]
            lines.append(f"{name} ({it.get('sets')} séries) veio do treino {it['added_from']}" if it.get("added_from")
                         else f"{name}: +{it['added']} série{'s' if int(it['added']) > 1 else ''}")
        lines += [n for n in _legacy_notes(e) if "cardio" in n or "volume já sobrando" in n]
        if lines:
            days.append({"entry_id": e["id"], "date": e["date"], "label": f"{_fmt(e['date'])} · {e['day_name']}", "lines": lines})
    return {"days": days} if days else None


def remove_compensation(ws: date, today: date, keep: bool = False) -> dict:
    """Volta ao plano original os treinos mexidos pela regra antiga (ou marca "manter como está").

    O plano original está guardado no retrato do primeiro pulo antigo da semana: é de lá que os treinos são
    restaurados (inclusive a série que a regra antiga tirou de outro exercício e o cardio que ela somou).
    Sem retrato, tira só o que está marcado."""
    entries = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    old_skips = sorted((e for e in entries if e["status"] == "pulado" and e.get("undo") and e["undo"].get("v") != 2),
                       key=lambda e: (e.get("skip_result") or {}).get("at") or "")
    original: dict = {}
    for sk in old_skips:
        for snap in sk["undo"].get("snapshot", []):
            original.setdefault(snap["id"], snap)
    n_sets = lambda x: sum(int(i.get("sets") or 0) for i in credit.strength_items(x))  # noqa: E731
    removed, changed = 0, []
    for e in entries:
        if e["status"] != "planejado" or _d(e["date"]) < today or not _has_legacy(e):
            continue
        if keep:
            e["legacy_kept"] = True
            db.save_entry(e)
            continue
        before = n_sets(e)
        src = original.get(e["id"])
        src = src if src and not _has_legacy(src) else None
        had_cardio = any("cardio" in n for n in _legacy_notes(e))
        orig_by_ex: dict = {}
        for it in (src or {}).get("items", []):
            orig_by_ex.setdefault(it.get("exercise_id"), []).append(it)
        items = []
        for it in e["items"]:
            if it.get("comp_v") != 2 and it.get("added_from"):
                continue  # exercício transplantado do treino pulado
            if it.get("comp_v") != 2 and it.get("added"):
                it["sets"] = max(1, int(it.get("sets") or 0) - int(it["added"]))
                it.pop("added", None)
            o = (orig_by_ex.get(it.get("exercise_id")) or [None]).pop(0) if src else None
            if o is not None and it.get("comp_v") != 2:
                # o mesmo exercício existia antes do ajuste: volta o que a regra antiga mexia (séries do "doador", minutos de cardio)
                if o.get("sets") is not None and it.get("sets") is not None:
                    it["sets"] = o["sets"]
                if had_cardio and o.get("duration_min") is not None and it.get("duration_min") is not None:
                    it["duration_min"] = o["duration_min"]
            elif o is None and src and had_cardio and it.get("notes") == "cardio compensado":
                continue  # finalizador de cardio criado pela regra antiga
            items.append(it)
        e["items"] = items
        e["changes"] = [c for c in e.get("changes") or [] if c not in _legacy_notes(e)]
        backup = e.get("readiness_backup")
        if backup and backup.get("items"):  # "desfazer ajuste de prontidão" não pode devolver o reforço antigo
            kept = []
            for it in backup["items"]:
                if it.get("comp_v") != 2 and it.get("added_from"):
                    continue
                if it.get("comp_v") != 2 and it.get("added"):
                    it["sets"] = max(1, int(it.get("sets") or 0) - int(it["added"]))
                    it.pop("added", None)
                kept.append(it)
            backup["items"] = kept
        removed += max(0, before - n_sets(e))
        _note(e, "Ajuste antigo removido: voltou ao plano original")
        db.save_entry(e)
        changed.append(e["id"])
    if not keep:
        for sk in old_skips:
            if sk.get("kind") == "forca":  # o desfazer deste treino não pode trazer o reforço antigo de volta
                sk["undo"] = _empty_undo(sk["undo"].get("created"))
            else:  # corrida/cardio: o desfazer continua valendo, só não mexe mais na musculação
                sk["undo"]["snapshot"] = [x for x in sk["undo"].get("snapshot", []) if x.get("kind") != "forca"]
            db.save_entry(sk)
    return {"removed_sets": removed, "entries": changed}


# ---------------------------------------------------------------- crédito da sessão --

def _close(e: dict, session: dict, how: str, cov: float | None) -> dict:
    e["done_by"] = {"session_id": session.get("id"), "how": how, "coverage": cov, "prev": e["status"]}
    e["status"] = "feito"
    if cov is not None and cov < credit.COVER_CLOSE:
        e["partial"] = True
    else:
        e.pop("partial", None)
    return db.save_entry(e)


def _unskip(e: dict, today: date) -> dict:
    """O treino pulado acabou sendo feito: o ajuste que o pulo fez na semana deixa de valer."""
    u = e.get("undo") or {}
    if u:
        _drop_created(e)
        if u.get("v") == 2:
            _revert_ops(e)
        else:
            remove_compensation(week_start(_d(e["date"])), today)
    e = db.get_entry(e["id"]) or e
    e.pop("undo", None)
    e.pop("skip_result", None)
    return e


def _entry_brief(e: dict, cov: float | None = None) -> dict:
    return {"entry_id": e["id"], "day_name": e["day_name"], "date": e["date"], "label": _fmt(e["date"]),
            "coverage": cov, "status": e["status"], "partial": bool(e.get("partial"))}


def _light_week(entries: list[dict]) -> bool:
    """Semana de deload ou de polimento para prova: é para ser mais leve, não há o que repor."""
    return any(e.get("kind") == "forca" and ((e.get("cycle") or {}).get("deload") or any(i.get("taper") for i in e.get("items") or []))
               for e in entries)


def balance(profile: dict, day: date, today: date | None = None) -> list[dict]:
    """O que falta na semana, por músculo (feito de verdade + agenda, contra a referência da semana)."""
    today = today or clock.today()
    ws = week_start(day)
    entries = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    if not any(e.get("kind") == "forca" for e in entries) or _light_week(entries):
        return []
    targets = _targets(schedule_plan(profile), profile)[1]
    return credit.balance_list(credit.week_balance(entries, _sessions_in_week(ws), targets, today))


def credit_session(session: dict, profile: dict, today: date | None = None) -> dict:
    """Depois de salvar uma sessão: que treino da agenda ela fecha (pelo CONTEÚDO, não só pela data) e o saldo da semana.

    - Aberta pela agenda (schedule_id): fecha o treino; se a pessoa fez menos de 25% dele, o treino continua
      na agenda e o app pergunta.
    - Treino livre: cobre ≥ 70% do treino de hoje → fecha o de hoje; senão, cobre ≥ 70% de um treino pulado ou
      perdido da semana → fecha esse (e desfaz o ajuste do pulo); cobre ≥ 70% de um treino futuro, ou entre 25%
      e 70% do de hoje → o app PERGUNTA; abaixo disso é treino extra e o planejado continua na agenda.
    """
    today = today or clock.today()
    day = _d(str(session["date"])[:10])
    ws = week_start(day)
    # a semana pode ainda não existir na agenda (primeiro treino da semana salvo antes de abrir a tela Hoje)
    entries = ensure_week(profile, day, today) if ws >= week_start(today) else \
        db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
    load_ = credit.session_load(session)
    out: dict = {"session_id": session.get("id"), "work_sets": credit.work_sets(session), "closed": None, "question": None,
                 "extra": False, "kept_open": None,
                 "added": [{"key": m, "pt": muscle_pt(m), "sets": round(v, 1)}
                           for m, v in sorted(load_.items(), key=lambda x: -x[1]) if v >= 1][:6]}
    flags: dict = {}
    sid = session.get("schedule_id")
    mp = session.get("match_pending")
    # uma sessão fecha UM treino de musculação: se ela já fechou um (pelo relógio, por reenvio…), é esse
    mine = next((e for e in entries if e.get("kind") == "forca" and e["status"] == "feito" and session.get("id") is not None
                 and (e.get("done_by") or {}).get("session_id") == session["id"]), None)
    if mine and not sid and session.get("client_id") and credit.has_sets(session) and (mine.get("done_by") or {}).get("how") == "data" \
            and (credit.coverage(session, mine) or 0.0) < credit.COVER_ASK:
        # o relógio tinha fechado o treino "pelo tipo", mas as séries registradas no app mostram outro treino
        reopen_for_session(session, today)
        entries = db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat())
        mine = None
    if mine and not sid:
        cov = credit.coverage(session, mine) if credit.has_sets(session) else (mine.get("done_by") or {}).get("coverage")
        if cov is not None and cov < credit.COVER_CLOSE and not mine.get("partial"):
            mine["partial"] = True
            mine = db.save_entry(mine)
        out["closed"] = _entry_brief(mine, cov)
        if session.get("client_id"):
            db.save_session({**{k: v for k, v in session.items() if k not in ("extra", "match_pending")}, "schedule_id": mine["id"]},
                            session_id=session["id"])
    elif session.get("extra") and not sid:    # decisão já tomada (reenvio do mesmo treino): não reabre
        out["extra"] = True
    elif isinstance(mp, dict) and not sid:
        e = db.get_entry(int(mp.get("entry_id") or 0))
        if e and e["status"] in ("planejado", "perdido"):
            out["question"] = {"kind": mp.get("kind"), **_entry_brief(e, mp.get("coverage"))}
    elif sid:
        e = db.get_entry(int(sid))
        if e and e["status"] == "feito":
            out["closed"] = _entry_brief(e, (e.get("done_by") or {}).get("coverage"))
        elif e:
            cov = credit.coverage(session, e) if e.get("kind") == "forca" else None
            # aberto pela agenda no app e quase nada feito (menos de 25%, ou só o cardio): o treino continua na agenda
            thin = cov is not None and cov < credit.COVER_ASK and session.get("client_id") and not _watch_strength(session)
            if thin:
                out["kept_open"] = _entry_brief(e, cov)
            else:
                if e["status"] == "pulado":
                    e = _unskip(e, today)
                out["closed"] = _entry_brief(_close(e, session, "vinculo", cov), cov)
    elif credit.is_free_workout(session):
        iso = day.isoformat()
        linked = {s.get("schedule_id") for s in _sessions_in_week(ws) if s.get("schedule_id")}
        strength = [e for e in entries if e.get("kind") == "forca" and credit.strength_items(e) and not credit._moved_away(e)]
        scored = sorted(((credit.coverage(session, e) or 0.0, e) for e in strength
                         if e["status"] in ("planejado", "perdido", "pulado")), key=lambda x: (-x[0], x[1]["date"]))
        c_today, e_today = next(((c, e) for c, e in scored if e["date"] == iso and e["status"] != "pulado"), (0.0, None))
        rest = [(c, e) for c, e in scored if e is not e_today and c >= credit.COVER_CLOSE]
        missed_hit = next(((c, e) for c, e in rest if e["status"] in ("pulado", "perdido")), None)
        future_hit = next(((c, e) for c, e in rest if e["status"] == "planejado"), None)
        # treino do dia que a regra antiga já tinha marcado como feito por causa desta sessão (sem registro de quem fechou)
        old = next((e for e in strength if e["date"] == iso and e["status"] == "feito" and not e.get("done_by")
                    and e["id"] not in linked), None)
        c_old = (credit.coverage(session, old) or 0.0) if old else 0.0
        if e_today and c_today >= credit.COVER_CLOSE:
            flags["schedule_id"] = e_today["id"]
            out["closed"] = _entry_brief(_close(e_today, session, "livre", c_today), c_today)
        elif old and c_old >= credit.COVER_ASK:
            flags["schedule_id"] = old["id"]
            out["closed"] = _entry_brief(_close(old, session, "livre", c_old), c_old)
        elif missed_hit:
            c, e = missed_hit
            was_skipped = e["status"] == "pulado"
            if was_skipped:
                e = _unskip(e, today)
            flags["schedule_id"] = e["id"]
            out["closed"] = {**_entry_brief(_close(e, session, "livre", c), c), "was_skipped": was_skipped, "other_day": e["date"] != iso}
        elif future_hit:
            flags["match_pending"] = {"entry_id": future_hit[1]["id"], "kind": "futuro", "coverage": future_hit[0]}
            out["question"] = {"kind": "futuro", **_entry_brief(future_hit[1], future_hit[0])}
        elif e_today and c_today >= credit.COVER_ASK:
            flags["match_pending"] = {"entry_id": e_today["id"], "kind": "hoje", "coverage": c_today}
            out["question"] = {"kind": "hoje", **_entry_brief(e_today, c_today)}
        else:
            flags["extra"] = True
            out["extra"] = True
            if e_today:
                out["kept_open"] = _entry_brief(e_today, c_today)
        if session.get("id"):
            keep = {k: v for k, v in session.items() if k not in ("extra", "match_pending")}
            db.save_session({**keep, **flags}, session_id=session["id"])
    out["balance"] = balance(profile, day, today)
    return out


def free_workout_state(profile: dict, today: date | None = None) -> list[dict]:
    """Treinos livres da semana: decide os que ainda não têm decisão (salvos antes do crédito por conteúdo)
    e devolve as perguntas em aberto ("esse treino livre foi o treino X?"), uma por sessão."""
    today = today or clock.today()
    # a semana virou e a pergunta ficou sem resposta: o treino que a pessoa fez não pode ficar "perdido" para sempre
    for s in _sessions_in_week(week_start(today) - timedelta(days=7)):
        mp = s.get("match_pending")
        if isinstance(mp, dict):
            e = db.get_entry(int(mp.get("entry_id") or 0))
            if e and e["status"] in ("planejado", "perdido"):
                match_session(s["id"], profile, e["id"], today=today, how="semana_virou")
            else:
                db.save_session({**{k: v for k, v in s.items() if k != "match_pending"}, "extra": True}, session_id=s["id"])
    out = []
    for s in sorted(_sessions_in_week(week_start(today)), key=lambda x: x["date"]):
        if not credit.is_free_workout(s) or s.get("extra"):
            continue
        mp = s.get("match_pending")
        if not isinstance(mp, dict):
            q = credit_session(s, profile, today).get("question")
            if q:
                out.append({"session_id": s["id"], "session_date": s["date"], "work_sets": credit.work_sets(s), **q})
            continue
        e = db.get_entry(int(mp.get("entry_id") or 0))
        if not e:  # a semana foi refeita e o treino da pergunta não existe mais: avalia a sessão de novo
            fresh = db.save_session({k: v for k, v in s.items() if k != "match_pending"}, session_id=s["id"])
            q = credit_session(fresh, profile, today).get("question")
            if q:
                out.append({"session_id": s["id"], "session_date": s["date"], "work_sets": credit.work_sets(s), **q})
            continue
        if e["status"] not in ("planejado", "perdido"):
            # o treino em dúvida já foi resolvido de outro jeito (feito ou pulado): a sessão fica como extra
            db.save_session({**{k: v for k, v in s.items() if k != "match_pending"}, "extra": True}, session_id=s["id"])
            continue
        out.append({"session_id": s["id"], "session_date": s["date"], "work_sets": credit.work_sets(s), "kind": mp.get("kind"),
                    **_entry_brief(e, mp.get("coverage"))})
    return out


def match_session(session_id: int, profile: dict, schedule_id: int | None = None, today: date | None = None,
                  how: str = "decisao") -> dict:
    """Resposta da pessoa: "foi o treino X" (schedule_id) ou "é um extra" (sem schedule_id). Troca a decisão anterior."""
    today = today or clock.today()
    s = db.get_session(session_id)
    if not s:
        raise ValueError("Treino não encontrado")
    day = _d(str(s["date"])[:10])
    target = None
    if schedule_id:
        target = db.get_entry(int(schedule_id))
        if not target or week_start(_d(target["date"])) != week_start(day):
            raise ValueError("Treino da agenda não encontrado nesta semana")
        if target["status"] == "feito" and (target.get("done_by") or {}).get("session_id") != session_id:
            raise ValueError("Esse treino já foi feito")
    reopen_for_session(s, today)
    s = {k: v for k, v in s.items() if k not in ("match_pending", "extra", "schedule_id")}
    closed = None
    if target:
        target = db.get_entry(target["id"]) or target
        cov = credit.coverage(s, target) if target.get("kind") == "forca" else None
        if target["status"] == "pulado":
            target = _unskip(target, today)
        closed = _entry_brief(_close(target, s, how, cov), cov)
        s["schedule_id"] = target["id"]
    else:
        s["extra"] = True
    db.save_session(s, session_id=session_id)
    return {"closed": closed, "extra": bool(s.get("extra")), "balance": balance(profile, day, today)}


def reopen_for_session(session: dict, today: date | None = None, min_cover: float | None = None) -> list[int]:
    """Sessão apagada (ou editada até não cobrir mais o treino): o treino que ela tinha fechado volta para a agenda."""
    today = today or clock.today()
    ws = week_start(_d(str(session["date"])[:10]))
    others = {s.get("schedule_id") for s in _sessions_in_week(ws) if s.get("id") != session.get("id")}
    out = []
    for e in db.list_schedule(ws.isoformat(), (ws + timedelta(days=6)).isoformat()):
        by = e.get("done_by") or {}
        mine = by.get("session_id") == session.get("id") if by else (session.get("schedule_id") == e["id"] and e["id"] not in others)
        if e["status"] != "feito" or not mine or session.get("id") is None:
            continue
        if min_cover is not None:
            cov = credit.coverage(session, e) if e.get("kind") == "forca" else None
            if by.get("how") == "decisao" or cov is None or cov >= min_cover:
                continue
        if by.get("prev") == "pulado":  # estava pulado antes de ser feito: volta a pulado (dá para desfazer pelo cartão)
            e["status"] = "pulado"
            e["undo"] = _empty_undo()
            e["skip_result"] = {"strategy": "seguir", "changes": ["Treino pulado."], "at": clock.now().isoformat(timespec="seconds")}
        else:
            e["status"] = "planejado" if _d(e["date"]) >= today else "perdido"
        e.pop("done_by", None)
        e.pop("partial", None)
        db.save_entry(e)
        out.append(e["id"])
    return out


# ------------------------------------------------------------- metas da semana --

def week_goals(entries: list[dict], profile: dict, ws: date, today: date | None = None) -> dict:
    from .analysis import cardio_summary

    sessions = _sessions_in_week(ws)
    start = datetime.combine(ws, datetime.min.time())
    end = start + timedelta(days=7)
    cardio = cardio_summary(sessions, start, end, profile)
    plan = schedule_plan(profile)
    gen = (plan or {}).get("generator") or {}
    target_cardio = float(profile.get("cardio_target_min") or gen.get("cardio_target") or 150)
    # o que conta como "planejado": fora o que foi substituído, pulado ou movido para outro dia (a cópia é que vale)
    planned = [e for e in entries if e["status"] not in ("substituido", "pulado")]
    done_sets = sum(credit.logged_sets(s) for s in sessions)  # séries de trabalho (aquecimento não conta)
    planned_sets = sum(int(i.get("sets") or 0) for e in planned for i in _strength_items(e))
    extras = sum(1 for s in sessions if s.get("extra"))
    has_strength = any(e.get("kind") == "forca" for e in entries)
    light = _light_week(entries)
    muscles = credit.balance_list(credit.week_balance(entries, sessions, _targets(plan, profile)[1], today or clock.today())) \
        if has_strength and not light else []
    minutes_done = sum(time_breakdown(e.get("items", []))["total"] for e in entries if e["status"] == "feito")
    end_goals = {}
    plan_end = [e for e in planned if e.get("kind") == "resistencia"]
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
        "sessions": {"done": sum(1 for e in entries if e["status"] == "feito") if entries else len(sessions),
                     "planned": len(planned), "extra": extras},
        "cardio": {"done": cardio["who_equivalent"], "target": round(target_cardio)},
        "sets": {"done": done_sets, "planned": planned_sets},
        "minutes": {"done": round(minutes_done), "planned": round(sum(time_breakdown(e.get("items", []))["total"] for e in planned))},
        "muscles": muscles,          # saldo por músculo (vazio em semana leve: deload/polimento não se repõe)
        "light_week": light,
    }
