"""Treino registrado duas vezes: no app (Treinar) e no relógio (importado do Garmin).

Os dois viram uma sessão só: as séries/cargas digitadas no app (mais precisas) e, do
relógio, o vínculo com a atividade, FC, calorias, tempo e — na resistência — distância,
potência e TSS medidos. Assim a carga (CTL/ATL), o histórico e a agenda não contam em dobro.
"""
from __future__ import annotations

from datetime import datetime

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


def _watch(s: dict) -> bool:
    """Veio de um relógio (Garmin, Apple Saúde, Health Connect)?"""
    return bool(s.get("garmin_activity_id") or s.get("ext_id"))


def find_twin(new: dict, sessions: list[dict]) -> dict | None:
    """Sessão do mesmo dia e do mesmo tipo vinda da outra fonte (app ↔ relógio)."""
    day = str(new.get("date") or "")[:10]
    k_new = kinds(new)
    if not day or not k_new:
        return None
    from_garmin = _watch(new)
    best, best_gap = None, None
    for s in sessions:
        if str(s.get("date") or "")[:10] != day or s.get("id") == new.get("id"):
            continue
        if _watch(s) == from_garmin:
            continue  # já está ligada a uma atividade (ou as duas são do app)
        if not (kinds(s) & k_new):
            continue
        t1, t2 = _time(s), _time(new)
        gap = abs((t1 - t2).total_seconds()) if t1 and t2 else 0
        # treino livre que o app já decidiu que NÃO era o treino do dia ("extra" / pergunta em aberto): só é a
        # mesma atividade do relógio se for no mesmo horário; horas depois é outro treino (o do dia, feito com o relógio)
        if (s.get("extra") or s.get("match_pending")) and gap > 2 * 3600:
            continue
        if best is None or gap < best_gap:
            best, best_gap = s, gap
    return best


def merge(app_s: dict, garmin_s: dict) -> dict:
    """Une as duas: itens do app + medidas do relógio."""
    out = {k: v for k, v in app_s.items() if k not in ("id", "created_at")}
    if garmin_s.get("garmin_activity_id"):
        out["garmin_activity_id"] = garmin_s.get("garmin_activity_id")
    if garmin_s.get("ext_id"):
        out["ext_id"] = garmin_s["ext_id"]
    out["garmin_summary"] = garmin_s.get("garmin_summary") or {}
    out["merged"] = True
    if garmin_s.get("duration_min") and not out.get("duration_min"):
        out["duration_min"] = garmin_s["duration_min"]
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
