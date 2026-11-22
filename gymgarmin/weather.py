"""Calor e umidade no dia do treino ao ar livre (previsão horária do Open-Meteo, gratuita, sem chave).

WBGT aproximado à sombra (fórmula do Bureau of Meteorology australiano): 0,567·T + 0,393·e + 3,94, com e = pressão de
vapor (hPa) pela umidade relativa. Faixas de conduta (consenso de calor, Racinais et al., BJSM 2015; ACSM):
- < 18 °C: normal;
- 18–23: corra pelo esforço/FC, não pelo ritmo (o ritmo cai ~0,3–0,4% por °C);
- 23–28: tiros 20–30% mais curtos ou em horário mais fresco; beba pela sede + o que perde no suor;
- 28–32: só rodagem leve ou dentro (esteira/bike);
- ≥ 32: ao ar livre, não.
"""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WX_URL = "https://api.open-meteo.com/v1/forecast"
REFRESH_H = 3
SLOT_HOURS = {"cedo": 6, "manha": 8, "meio_dia": 12, "fim_tarde": 17, "noite": 20}


def wbgt(temp_c: float, rh: float) -> float:
    e = (rh / 100.0) * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return round(0.567 * temp_c + 0.393 * e + 3.94, 1)


def band(w: float) -> dict:
    if w >= 32:
        return {"level": "perigo", "text": "Calor perigoso: ao ar livre, não. Treine dentro (esteira, bike) ou em outro horário."}
    if w >= 28:
        return {"level": "alto", "text": "Muito quente: só rodagem leve ou treino dentro; deixe os tiros para um horário fresco."}
    if w >= 23:
        return {"level": "moderado", "text": "Quente: corra pelo esforço; nos tiros, encurte 20–30% ou mude para um horário mais fresco. Beba durante."}
    if w >= 18:
        return {"level": "leve", "text": "Morno: corra pelo esforço ou pela FC, não pelo ritmo (o ritmo cai um pouco no calor)."}
    return {"level": "ok", "text": "Temperatura boa para treinar."}


def _get(url: str, params: dict, timeout: float = 6.0) -> dict:
    q = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(q, headers={"User-Agent": "hax-fitness-app"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 — URL fixa do Open-Meteo
        return json.loads(r.read().decode("utf-8"))


def geocode(city: str, kv_get, kv_set, fetch=_get) -> dict | None:
    key = "geo:" + city.strip().lower()
    raw = kv_get(key)
    if raw:
        return json.loads(raw) or None
    try:
        res = fetch(GEO_URL, {"name": city.strip(), "count": 1, "language": "pt", "countryCode": "BR"})
        r = (res.get("results") or [None])[0]
        if not r:
            res = fetch(GEO_URL, {"name": city.strip(), "count": 1, "language": "pt"})
            r = (res.get("results") or [None])[0]
    except Exception:  # noqa: BLE001 — sem rede: sem aviso de calor hoje
        return None
    out = {"lat": r["latitude"], "lon": r["longitude"], "name": r.get("name"), "admin": r.get("admin1"),
           "tz": r.get("timezone") or "America/Sao_Paulo"} if r else {}
    kv_set(key, json.dumps(out))
    return out or None


def forecast(city: str, day: date, kv_get, kv_set, now: datetime, fetch=_get) -> dict | None:
    g = geocode(city, kv_get, kv_set, fetch)
    if not g:
        return None
    key = f"wx:{g['lat']:.2f},{g['lon']:.2f}:{day.isoformat()}"
    raw = kv_get(key)
    if raw:
        c = json.loads(raw)
        if (now - datetime.fromisoformat(c["at"])) < timedelta(hours=REFRESH_H):
            return c
    try:
        res = fetch(WX_URL, {"latitude": g["lat"], "longitude": g["lon"], "hourly": "temperature_2m,relative_humidity_2m",
                             "timezone": g["tz"], "start_date": day.isoformat(), "end_date": day.isoformat()})
    except Exception:  # noqa: BLE001
        return json.loads(raw) if raw else None
    h = res.get("hourly") or {}
    hours = []
    for t, tc, rh in zip(h.get("time") or [], h.get("temperature_2m") or [], h.get("relative_humidity_2m") or []):
        if tc is None or rh is None:
            continue
        hours.append({"hour": int(str(t)[11:13]), "temp": round(float(tc), 1), "rh": int(rh), "wbgt": wbgt(float(tc), float(rh))})
    out = {"at": now.isoformat(timespec="minutes"), "city": g.get("name") or city, "hours": hours}
    kv_set(key, json.dumps(out))
    return out


def advice(fc: dict | None, slot: str | None = None) -> dict | None:
    """O que dizer hoje: no seu horário de sempre, o pior horário do dia e o melhor horário (5h–21h)."""
    if not fc or not fc.get("hours"):
        return None
    day_h = [h for h in fc["hours"] if 5 <= h["hour"] <= 21]
    if not day_h:
        return None
    best = min(day_h, key=lambda h: (h["wbgt"], abs(h["hour"] - 7)))
    worst = max(day_h, key=lambda h: h["wbgt"])
    at = None
    if slot in SLOT_HOURS:
        at = min(day_h, key=lambda h: abs(h["hour"] - SLOT_HOURS[slot]))
    ref = at or worst
    b = band(ref["wbgt"])
    return {"city": fc.get("city"), "at": at, "best": best, "worst": worst, "level": b["level"], "text": b["text"],
            "show": worst["wbgt"] >= 18}
