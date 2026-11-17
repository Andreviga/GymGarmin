"""Recalibração das âncoras (VDOT, FTP, CSS, 2 km de remo, FC) por teste, atividades e métricas do Garmin."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import clock, sports as sp

ANCHOR_LABEL = {"run_vdot": "VDOT (corrida)", "ftp_w": "FTP (ciclismo)", "css_s100": "CSS (natação)",
                "row_2k_s": "2 km (remo)", "lthr": "FC de limiar", "hrmax": "FC máxima"}


def fmt_anchor(key: str, v) -> str:
    if v in (None, ""):
        return "—"
    if key == "run_vdot":
        return f"{v:g}"
    if key == "ftp_w":
        return f"{int(v)} W"
    if key == "css_s100":
        return sp.fmt_pace(v, "/100m")
    if key == "row_2k_s":
        return f"{sp.fmt_duration(v)} ({sp.fmt_pace(v / 4, '/500m')})"
    return f"{int(v)} bpm"


def from_test(kind: str, data: dict) -> tuple[str, float]:
    """Resultado de teste → (âncora, valor)."""
    if kind == "corrida":
        dist = float(data.get("distance_km") or 5)
        t = sp.parse_duration(data.get("time"))
        v = sp.vdot(dist, t) if t else None
        if not v:
            raise ValueError("Informe distância e tempo (ex.: 5 km em 24:30)")
        return "run_vdot", v
    if kind == "ciclismo":
        if data.get("ftp"):
            return "ftp_w", float(data["ftp"])
        p20 = float(data.get("power_20min") or 0)
        if p20 <= 0:
            raise ValueError("Informe a potência média dos 20 min (ou o FTP)")
        return "ftp_w", sp.ftp_from_20min(p20)
    if kind == "natacao":
        css = sp.css_from_tests(sp.parse_duration(data.get("t400")), sp.parse_duration(data.get("t200")))
        if not css:
            raise ValueError("Informe os tempos de 400 m e 200 m (o de 400 maior que o de 200)")
        return "css_s100", css
    if kind == "remo":
        t = sp.parse_duration(data.get("t2k"))
        if not t:
            raise ValueError("Informe o tempo dos 2 km")
        return "row_2k_s", t
    if kind in ("lthr", "hrmax"):
        return kind, float(data.get("value"))
    raise ValueError("Teste desconhecido")


def suggestions(sessions: list[dict], anchors: dict, garmin_metrics: dict | None = None,
                today: date | None = None) -> list[dict]:
    """Sugestões de atualização: melhores esforços recentes e métricas do Garmin."""
    today = today or clock.today()
    out = []
    since = today - timedelta(weeks=8)
    best_vdot, best_src = None, None
    for s in sessions:
        try:
            d = datetime.fromisoformat(str(s.get("date"))[:19]).date()
        except ValueError:
            continue
        if d < since:
            continue
        for it in s.get("items") or []:
            if it.get("sport") == "corrida" and it.get("distance_km") and it.get("duration_min") \
                    and it["distance_km"] >= 3 and it["duration_min"] >= 12 and it.get("exercise_id") != "corrida_trilha":
                v = sp.vdot(float(it["distance_km"]), float(it["duration_min"]) * 60)
                if v and (best_vdot is None or v > best_vdot):
                    best_vdot = v
                    best_src = f"{it['distance_km']:g} km em {sp.fmt_duration(it['duration_min'] * 60)} ({d.strftime('%d/%m')})"
    cur = anchors.get("run_vdot")
    gm = garmin_metrics or {}
    rp = gm.get("race_predictions") or {}
    # Um treino comum (rodagem leve, esteira) só subestima o condicionamento: a previsão de 5 km do
    # Garmin vale mais, a não ser que um treino tenha mostrado mais que ela.
    pred = sp.vdot(5, float(rp["time5K"])) if rp.get("time5K") else None
    if pred and (not best_vdot or pred >= best_vdot):
        if not cur or abs(pred - cur) >= 1.5:
            out.append({"key": "run_vdot", "value": pred, "source": f"previsão de 5 km do Garmin ({sp.fmt_duration(rp['time5K'])})",
                        "current": cur})
    elif best_vdot and (not cur or best_vdot >= cur + 1):
        out.append({"key": "run_vdot", "value": best_vdot, "source": f"melhor corrida recente: {best_src}",
                    "current": cur})
    if gm.get("ftp") and (not anchors.get("ftp_w") or abs(gm["ftp"] - anchors["ftp_w"]) >= 5):
        out.append({"key": "ftp_w", "value": float(gm["ftp"]), "source": "FTP estimado pelo Garmin", "current": anchors.get("ftp_w")})
    if gm.get("lthr") and (not anchors.get("lthr") or abs(gm["lthr"] - anchors["lthr"]) >= 3):
        out.append({"key": "lthr", "value": float(gm["lthr"]), "source": "limiar de lactato do Garmin", "current": anchors.get("lthr")})
    for x in out:
        x["label"] = ANCHOR_LABEL[x["key"]]
        x["value_txt"] = fmt_anchor(x["key"], x["value"])
        x["current_txt"] = fmt_anchor(x["key"], x.get("current"))
    return out


def garmin_metrics(api, today: date) -> dict:
    """Métricas de desempenho do Garmin (opcionais)."""
    out: dict = {"date": today.isoformat()}
    try:
        rp = api.get_race_predictions() or {}
        rp = rp[0] if isinstance(rp, list) and rp else rp
        out["race_predictions"] = {k: rp.get(k) for k in ("time5K", "time10K", "timeHalfMarathon", "timeMarathon") if rp.get(k)}
    except Exception:  # noqa: BLE001
        pass
    try:
        ftp = api.get_cycling_ftp() or {}
        ftp = ftp[0] if isinstance(ftp, list) and ftp else ftp
        val = ftp.get("functionalThresholdPower") or ftp.get("ftp")
        if val:
            out["ftp"] = float(val)
    except Exception:  # noqa: BLE001
        pass
    try:
        lt = api.get_lactate_threshold(latest=True) or {}
        hr = ((lt.get("speed_and_heart_rate") or {}).get("heartRate")) or lt.get("heartRate")
        if hr:
            out["lthr"] = float(hr)
    except Exception:  # noqa: BLE001
        pass
    try:
        mm = api.get_max_metrics(today.isoformat()) or []
        mm = mm[0] if isinstance(mm, list) and mm else mm
        vo2 = ((mm or {}).get("generic") or {}).get("vo2MaxPreciseValue") or ((mm or {}).get("generic") or {}).get("vo2MaxValue")
        if vo2:
            out["vo2max"] = float(vo2)
    except Exception:  # noqa: BLE001
        pass
    return out
