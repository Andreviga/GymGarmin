"""Treino registrado duas vezes: no app (Treinar) e no relógio (importado do Garmin).

Os dois viram uma sessão só: as séries/cargas digitadas no app (mais precisas) e, do
relógio, o vínculo com a atividade, FC, calorias, tempo e — na resistência — distância,
potência e TSS medidos. Assim a carga (CTL/ATL), o histórico e a agenda não contam em dobro.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import load

GARMIN_ITEM_KEYS = ("distance_km", "avg_hr", "max_hr", "kcal", "tss", "np", "avg_power", "elev_m", "pace_s_km",
                    "pace_s_100", "split_s_500", "speed_kmh", "pool_m", "te_aerobic", "te_anaerobic")


def kinds(session: dict) -> set[str]:
    out = set()
    for it in session.get("items") or []:
        if it.get("sets") or it.get("strength_min"):
            out.add("forca")
        sp = load.sport_of(it)
        if sp:
            out.add(f"end:{sp}")
    return out


def _time(s: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s.get("date"))[:19])
    except ValueError:
        return None


def _saved_at(s: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s.get("created_at"))[:19]) if s.get("created_at") else None
    except ValueError:
        return None


def _watch(s: dict) -> bool:
    """Veio de um relógio (Garmin, Apple Saúde, Health Connect)?"""
    return bool(s.get("garmin_activity_id") or s.get("ext_id"))


UNKNOWN_ACTIVITY = "atividade_outra"   # o relógio gravou tempo/FC/calorias mas não disse o que era
SET_MIN = 2.6                          # minutos por série (com o descanso), para estimar a duração de um treino anotado
MIN_OVERLAP = 0.5                      # fração do treino mais curto que precisa coincidir no horário


def is_unknown_watch(s: dict) -> bool:
    """Atividade do relógio sem tipo ("outro treino" do Amazfit/Zepp, Xiaomi, Samsung): só tempo, FC e calorias."""
    items = s.get("items") or []
    return _watch(s) and bool(items) and all(i.get("exercise_id") == UNKNOWN_ACTIVITY and not i.get("sets") for i in items)


def _span(s: dict) -> tuple[datetime, datetime] | None:
    """Início e fim do treino, quando a sessão tem hora (não só o dia) e dá para saber quanto durou."""
    t = _time(s)
    if not t or len(str(s.get("date") or "")) <= 10:
        return None
    items = s.get("items") or []
    mins = float(s.get("duration_min") or (s.get("garmin_summary") or {}).get("duration_min") or 0)
    if not mins:
        mins = sum(float(i.get("duration_min") or i.get("strength_min") or 0) for i in items) + \
            SET_MIN * sum(len(i["sets"]) for i in items if isinstance(i.get("sets"), list))
    return (t, t + timedelta(minutes=mins)) if mins > 0 else None


def overlap(a: dict, b: dict) -> float:
    """Quanto do treino mais curto acontece ao mesmo tempo que o outro (0 a 1)."""
    sa, sb = _span(a), _span(b)
    if not sa or not sb:
        return 0.0
    both = (min(sa[1], sb[1]) - max(sa[0], sb[0])).total_seconds()
    short = min((sa[1] - sa[0]).total_seconds(), (sb[1] - sb[0]).total_seconds())
    return max(0.0, both) / short if short > 0 else 0.0


def _same_time_twin(new: dict, sessions: list[dict], day: str) -> dict | None:
    """Musculação anotada no app ↔ atividade sem tipo do relógio no mesmo horário: é o mesmo treino."""
    from_watch = _watch(new)
    if from_watch and not is_unknown_watch(new):
        return None
    if not from_watch and "forca" not in kinds(new):
        return None
    best, best_o = None, 0.0
    for s in sessions:
        if str(s.get("date") or "")[:10] != day or s.get("id") == new.get("id") or _watch(s) == from_watch:
            continue
        if from_watch and not any(i.get("sets") for i in s.get("items") or []):
            continue      # do lado do app tem de ser treino com séries
        if not from_watch and not is_unknown_watch(s):
            continue
        o = overlap(new, s)
        if o >= MIN_OVERLAP and o > best_o:
            best, best_o = s, o
    return best


def find_twin(new: dict, sessions: list[dict]) -> dict | None:
    """Sessão do mesmo dia e do mesmo tipo vinda da outra fonte (app ↔ relógio)."""
    day = str(new.get("date") or "")[:10]
    k_new = kinds(new)
    if not day:
        return None
    if not k_new:
        return _same_time_twin(new, sessions, day)
    from_garmin = _watch(new)
    best, best_gap = None, None
    for s in sessions:
        if str(s.get("date") or "")[:10] != day or s.get("id") == new.get("id"):
            continue
        if _watch(s) == from_garmin:
            continue  # já está ligada a uma atividade (ou as duas são do app)
        if s.get("merged"):
            continue  # atividade do relógio que já foi juntada com um treino anotado: tem dono, não recebe outro por cima
        if not (kinds(s) & k_new):
            continue
        t1, t2 = _time(s), _time(new)
        gap = abs((t1 - t2).total_seconds()) if t1 and t2 else 0
        # treino livre que o app já avaliou ("extra" ou pergunta em aberto): a atividade do relógio é o MESMO treino
        # quando começou antes de a pessoa salvar o treino no app (inclusive treino anotado horas depois); se o relógio
        # começou bem depois de o treino do app estar salvo, é outro treino (o do dia, feito só com o relógio)
        if s.get("extra") or s.get("match_pending"):
            saved = _saved_at(s) or t1
            if t2 and saved and t2 > saved + timedelta(minutes=30):
                continue
        if best is None or gap < best_gap:
            best, best_gap = s, gap
    return best or _same_time_twin(new, sessions, day)


def is_time_only(s: dict) -> bool:
    """Atividade do relógio que só tem tempo/FC/calorias (musculação sem séries, "outro exercício", cardio genérico)."""
    items = s.get("items") or []
    return _watch(s) and bool(items) and all(
        (i.get("strength_min") or str(i.get("exercise_id") or "").startswith("atividade_")) and not i.get("sets") for i in items)


def only_time(s: dict) -> bool:
    """Do relógio, sem séries: musculação só com o tempo ou atividade sem tipo."""
    items = s.get("items") or []
    return _watch(s) and bool(items) and all(
        (i.get("strength_min") or i.get("exercise_id") == UNKNOWN_ACTIVITY) and not i.get("sets") for i in items)


def is_mergeable_watch(s: dict) -> bool:
    """Atividade do relógio que pode SER a musculação que a pessoa está anotando: musculação sem séries ou atividade
    sem tipo. Yoga, futebol, dança, corrida etc. são outra coisa e nunca viram o treino de força."""
    items = s.get("items") or []
    return _watch(s) and not s.get("merged") and bool(items) and all(
        (i.get("strength_min") or i.get("exercise_id") == UNKNOWN_ACTIVITY) and not i.get("sets") for i in items)


def unmerge_watch(s: dict, strength_id: str) -> dict | None:
    """Treino juntado (séries do app + relógio) que a pessoa apagou: o que o relógio mediu continua valendo.
    Devolve a atividade só-de-tempo para ser regravada, ou None se não havia medida de tempo."""
    summ = s.get("garmin_summary") or {}
    mins = float(s.get("duration_min") or summ.get("duration_min") or 0)
    if not (s.get("merged") and _watch(s) and mins > 0 and any(i.get("sets") for i in s.get("items") or [])):
        return None
    item = {"exercise_id": strength_id, "_name": "Musculação (relógio)", "strength_min": round(mins, 1), "duration_min": round(mins, 1)}
    for k in ("avg_hr", "max_hr", "kcal"):
        if summ.get(k):
            item[k] = summ[k]
    out = {k: s[k] for k in ("date", "source", "garmin_activity_id", "ext_id", "garmin_summary") if s.get(k)}
    return {**out, "duration_min": round(mins, 1), "notes": "Musculação (relógio)", "items": [item]}


def merge_into_watch(app_s: dict, watch_s: dict) -> dict:
    """A pessoa disse que o treino que está salvando É aquela atividade do relógio: ficam os exercícios e séries do app
    e, do relógio, o vínculo, o horário, a duração, a FC e as calorias. O item genérico ("65 min de atividade") sai,
    para o treino não contar duas vezes na carga, no tempo e nas calorias."""
    out = {k: v for k, v in app_s.items() if k not in ("id", "created_at", "merge_into")}
    for k in ("garmin_activity_id", "ext_id", "source"):
        if watch_s.get(k):
            out[k] = watch_s[k]
    summ = dict(watch_s.get("garmin_summary") or {})
    for it in watch_s.get("items") or []:  # as medidas ficavam no item genérico: sobem para o resumo
        for src, dst in (("avg_hr", "avg_hr"), ("max_hr", "max_hr"), ("kcal", "kcal"), ("duration_min", "duration_min")):
            if it.get(src) and not summ.get(dst):
                summ[dst] = it[src]
    out["garmin_summary"] = summ
    out["merged"] = True
    out["date"] = watch_s.get("date") or out.get("date")           # o relógio sabe a hora em que o treino aconteceu
    out["duration_min"] = watch_s.get("duration_min") or summ.get("duration_min") or out.get("duration_min")
    return out


def merge(app_s: dict, garmin_s: dict) -> dict:
    """Une as duas: itens do app + medidas do relógio."""
    out = {k: v for k, v in app_s.items() if k not in ("id", "created_at")}
    if garmin_s.get("garmin_activity_id"):
        out["garmin_activity_id"] = garmin_s.get("garmin_activity_id")
    if garmin_s.get("ext_id"):
        out["ext_id"] = garmin_s["ext_id"]
    out["garmin_summary"] = garmin_s.get("garmin_summary") or {}
    out["merged"] = True
    # o relógio gravou só a corrida/bike de um treino que também tem musculação: o tempo e as calorias dele valem
    # para o item de cardio (abaixo), não para o treino inteiro
    only_cardio = "forca" in kinds(app_s) and "forca" not in kinds(garmin_s) and not is_unknown_watch(garmin_s)
    if only_cardio:
        out["garmin_summary"] = {"partial": True}
    elif garmin_s.get("duration_min") and not out.get("duration_min"):
        out["duration_min"] = garmin_s["duration_min"]
    if is_unknown_watch(garmin_s) or (only_time(garmin_s) and any(i.get("sets") for i in out.get("items") or [])):
        # relógio que só marca tempo: as medidas estavam no item genérico (que não entra); sobem para o resumo, e o
        # horário e a duração medidos valem mais que os anotados (ex.: o tempo copiado do treino do parceiro)
        summ = dict(out["garmin_summary"])
        for gi in garmin_s.get("items") or []:
            for k in ("avg_hr", "max_hr", "kcal", "duration_min"):
                if gi.get(k) and not summ.get(k):
                    summ[k] = gi[k]
        out["garmin_summary"] = summ
        if garmin_s.get("source") and not out.get("source"):
            out["source"] = garmin_s["source"]
        out["date"] = garmin_s.get("date") or out.get("date")
        out["duration_min"] = garmin_s.get("duration_min") or summ.get("duration_min") or out.get("duration_min")
    items = [dict(i) for i in out.get("items") or []]
    g_items = garmin_s.get("items") or []
    for gi in g_items:
        sp = load.sport_of(gi)
        if not sp:
            continue
        target = next((i for i in items if load.sport_of(i) == sp), None)
        if target is None:
            items.append(gi)
            continue
        for k in GARMIN_ITEM_KEYS:
            if gi.get(k) is not None and target.get(k) in (None, "", 0):
                target[k] = gi[k]
        if not target.get("duration_min") and gi.get("duration_min"):
            target["duration_min"] = gi["duration_min"]
    if not any(i.get("sets") for i in items):
        items += [gi for gi in g_items if gi.get("strength_min") and gi not in items]
    out["items"] = items
    return out
