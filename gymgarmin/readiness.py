"""Prontidão diária: dados do relógio (Garmin) + forma (carga) → ajuste do treino do dia.

Fontes (todas opcionais, lidas com a mesma conexão do Garmin):
  - Training Readiness do Garmin (já combina sono, HRV, carga e recuperação)
  - status do HRV da noite, pontuação do sono, Body Battery ao acordar, FC de repouso
  - forma (TSB) calculada pelo app com todas as modalidades + musculação

Ações:
  seguir   (≥ 60)  treino como planejado
  moderar  (40–59) sessões fortes −25% de tiros/séries; musculação −1 série nos principais
  trocar   (25–39) sessões fortes viram leves (mesma modalidade, ~65% da duração); musculação −40% e RPE 7
  descanso (< 25)  sugere folga ou só mobilidade
O ajuste guarda o original para desfazer.
"""
from __future__ import annotations

import copy
from datetime import date

HRV_SCORE = {"BALANCED": 80, "UNBALANCED": 50, "LOW": 30, "POOR": 20}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(api, day: date) -> dict:
    """Lê os dados do dia no Garmin; cada fonte é opcional."""
    d = day.isoformat()
    out: dict = {"date": d}
    try:
        tr = api.get_training_readiness(d)
        first = tr[0] if isinstance(tr, list) and tr else (tr if isinstance(tr, dict) else None)
        if first and _num(first.get("score")) is not None:
            out["garmin_readiness"] = int(first["score"])
            out["garmin_level"] = first.get("level")
            out["garmin_feedback"] = first.get("feedbackShort") or first.get("feedbackLong")
    except Exception:  # noqa: BLE001
        pass
    try:
        hrv = (api.get_hrv_data(d) or {}).get("hrvSummary") or {}
        if hrv.get("status"):
            out["hrv_status"] = hrv["status"]
            out["hrv_last_night"] = hrv.get("lastNightAvg")
            out["hrv_weekly"] = hrv.get("weeklyAvg")
    except Exception:  # noqa: BLE001
        pass
    try:
        sl = (api.get_sleep_data(d) or {}).get("dailySleepDTO") or {}
        val = ((sl.get("sleepScores") or {}).get("overall") or {}).get("value")
        if val is not None:
            out["sleep_score"] = int(val)
        if sl.get("sleepTimeSeconds"):
            out["sleep_h"] = round(sl["sleepTimeSeconds"] / 3600, 1)
    except Exception:  # noqa: BLE001
        pass
    try:
        bb = api.get_body_battery(d, d) or []
        arr = (bb[0] if bb else {}).get("bodyBatteryValuesArray") or []
        vals = [v[1] for v in arr if isinstance(v, (list, tuple)) and len(v) > 1 and v[1] is not None]
        if vals:
            out["body_battery_max"] = int(max(vals))
    except Exception:  # noqa: BLE001
        pass
    try:
        rhr = api.get_rhr_day(d) or {}
        vals = (((rhr.get("allMetrics") or {}).get("metricsMap") or {}).get("WELLNESS_RESTING_HEART_RATE") or [])
        if vals and vals[0].get("value"):
            out["rhr"] = int(vals[0]["value"])
    except Exception:  # noqa: BLE001
        pass
    return out


def score(data: dict, form: float | None = None) -> dict:
    """Nota 0–100, nível, ação e motivos."""
    reasons = []
    if data.get("garmin_readiness") is not None:
        base = float(data["garmin_readiness"])
        reasons.append(f"Training Readiness do Garmin: {int(base)}")
    else:
        parts = []
        if data.get("sleep_score") is not None:
            parts.append((data["sleep_score"], 0.4))
            reasons.append(f"sono {data['sleep_score']}/100")
        if data.get("hrv_status"):
            parts.append((HRV_SCORE.get(data["hrv_status"], 50), 0.3))
            reasons.append(f"HRV {data['hrv_status'].lower()}")
        if data.get("body_battery_max") is not None:
            parts.append((data["body_battery_max"], 0.3))
            reasons.append(f"Body Battery {data['body_battery_max']}")
        if not parts:
            base = None
        else:
            w = sum(p[1] for p in parts)
            base = sum(v * p for v, p in parts) / w
    if base is None and form is None:
        return {"score": None, "level": "sem_dados", "action": "seguir",
                "reasons": ["Sem dados do relógio hoje: siga o plano pela sensação."]}
    s = base if base is not None else 70.0
    if form is not None:
        if form < -30:
            s -= 15
            reasons.append(f"forma {form:g} (muita fadiga acumulada)")
        elif form < -20:
            s -= 8
            reasons.append(f"forma {form:g}")
        elif form > 15:
            reasons.append(f"forma +{form:g} (descansado)")
    s = max(0.0, min(100.0, s))
    action = "seguir" if s >= 60 else "moderar" if s >= 40 else "trocar" if s >= 25 else "descanso"
    level = {"seguir": "alta", "moderar": "moderada", "trocar": "baixa", "descanso": "muito_baixa"}[action]
    return {"score": round(s), "level": level, "action": action, "reasons": reasons}


ACTION_TEXT = {
    "seguir": "Pronto para treinar: siga o plano.",
    "moderar": "Recuperação parcial: mantenha o treino, mas com menos tiros/séries (−25%) e sem buscar recorde.",
    "trocar": "Recuperação baixa: hoje a sessão forte vira leve e a musculação fica mais curta e leve.",
    "descanso": "Corpo pedindo descanso: folga ou só mobilidade/caminhada leve. O treino de hoje fica para depois.",
}


def _scale_repeats(steps: list[dict], frac: float) -> None:
    for st in steps:
        if st.get("kind") == "repeat":
            st["n"] = max(2, int(round(st["n"] * frac)))


def adjust_entry(entry: dict, action: str, anchors: dict | None = None) -> dict | None:
    """Ajusta um treino da agenda conforme a prontidão. Retorna o treino alterado (ou None se nada muda)."""
    from . import endurance as en

    if action == "seguir" or entry.get("status") not in ("planejado", None) or entry.get("readiness_backup"):
        return None
    e = copy.deepcopy(entry)
    e["readiness_backup"] = {"items": copy.deepcopy(entry["items"]), "day_name": entry.get("day_name")}
    notes = []
    changed = False
    new_items = []
    for it in e["items"]:
        end = it.get("endurance")
        if end:
            if action == "moderar" and end.get("hard") and end.get("type") not in ("prova",):
                _scale_repeats(end["steps"], 0.75)
                flat = []
                for st in end["steps"]:
                    flat += [x for _ in range(st["n"]) for x in st["steps"]] if st.get("kind") == "repeat" else [st]
                end["dur_s"] = int(sum(x.get("dur_s") or 0 for x in flat))
                it["duration_min"] = round(end["dur_s"] / 60)
                notes.append(f"{end['title']}: −25% dos tiros")
                changed = True
            elif action in ("trocar", "descanso") and (end.get("hard") or end.get("role") == "longo") and end.get("type") != "prova":
                sport = end["sport"]
                unit = en.VOLUME[sport]["unit"]
                vol = (end.get("dist_m") or 0) / 1000 if unit == "km" else (end.get("dist_m") or 0) if unit == "m" else end["dur_s"] / 3600
                vol = max(en.VOLUME[sport]["min"], vol * 0.65)
                easy = en.build_session(sport, "leve" if action == "trocar" else "regenerativo", en._round_vol(sport, vol),
                                        {"env": end.get("env")}, anchors or {}, "intermediario")
                easy["endurance"]["role"] = "leve"
                easy["endurance"]["note"] = f"Ajuste de prontidão: era {end['title']}"
                notes.append(f"{end['title']} → {easy['endurance']['title']} ({easy['duration_min']} min)")
                it = easy
                changed = True
        elif it.get("sets") and (it.get("phase") or "principal") == "principal" and action in ("moderar", "trocar", "descanso"):
            sets = int(it["sets"])
            cut = 1 if action == "moderar" else max(1, round(sets * 0.4))
            if it.get("role") in ("main", "sec") or action != "moderar":
                it["sets"] = max(1, sets - cut)
                if action != "moderar":
                    it["rpe_target"] = min(7, it.get("rpe_target") or 7)
                changed = True
        new_items.append(it)
    if any(i.get("sets") for i in e["items"] if not i.get("endurance")) and action != "seguir":
        notes.append("musculação: " + ("−1 série nos principais" if action == "moderar" else "−40% das séries, RPE até 7"))
    if not changed:
        return None
    e["items"] = new_items
    e.setdefault("changes", []).append("Prontidão: " + "; ".join(notes))
    if e.get("garmin_workout_id"):
        e["garmin_outdated"] = True
    return e


def undo_entry(entry: dict) -> dict:
    b = entry.get("readiness_backup")
    if not b:
        raise ValueError("Este treino não foi ajustado pela prontidão")
    e = copy.deepcopy(entry)
    e["items"] = b["items"]
    e["day_name"] = b.get("day_name") or e.get("day_name")
    e.pop("readiness_backup", None)
    e.setdefault("changes", []).append("Ajuste de prontidão desfeito")
    return e
