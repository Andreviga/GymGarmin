"""Área do coach: triagem dos atletas, alertas, check-in diário e áreas de tensão.

Princípios (ver a pesquisa no README da área):
- Comparar cada atleta com ele mesmo (linha de base de 28 dias), não com tabelas genéricas.
- Alertas em dois níveis (atenção / agir) e poucos: o coach precisa saber em 5 segundos quem olhar.
- Dor por região na escala 0–10 (modelo de monitoramento da dor, Silbernagel): até 2 = seguir,
  3–5 = treinar com modificação (cuidado), ≥6 ou piorando = evitar o movimento que dói.
- O coach só vê quem o autorizou; nunca vê nem muda senha, login do Garmin ou código pessoal.
"""

from __future__ import annotations

import json
import statistics as st
from datetime import date, timedelta

from . import catalog, clock, db, limitations

# Regiões do corpo do check-in = limitações do catálogo que mapeiam exercícios
REGIONS = {k: LIM["label"].split(" (")[0] for k, LIM in limitations.LIMITATIONS.items()
           if k in ("cervical", "ombro", "cotovelo", "punho", "lombar", "quadril", "joelho", "tornozelo")}

# Limiares padrão (o coach ajusta em Coach → Alertas). Comentário = de onde vem o número.
DEFAULTS = {
    "compliance_warn": 70,       # % do planejado feito em 7 dias (TrueCoach/Everfit/CoachRx: 60–70% = atenção)
    "compliance_act": 50,        # % (CoachRx: < 50% ou 2 faltas seguidas = precisa de atenção)
    "inactive_warn": 5,          # dias sem treinar (com plano ativo)
    "inactive_act": 9,
    "readiness_warn": 50,        # prontidão do dia (0–100)
    "readiness_act": 35,
    "sleep_warn": 7.0,           # média de 7 noites (h): < 7 h = sono curto (consenso BJSM 2021)
    "sleep_act": 6.0,
    "rhr_warn": 5,               # bpm acima da linha de base de 28 dias (média de 3 dias) — heurística
    "rhr_act": 8,
    "load_jump_warn": 25,        # pico de carga: % sobre a semana anterior (preferido ao ACWR, ver Impellizzeri 2020)
    "load_jump_act": 50,
    "wellness_warn": 4,          # item do check-in (1 ótimo … 5 péssimo); 2+ itens ≥ 4 = atenção, 5 em cansaço/dor = agir
    "pain_warn": 3,              # dor 0–10 (Silbernagel): 3–5 aceitável se passa até a manhã seguinte
    "pain_act": 6,               # > 5, ou piorando: reduzir/trocar o exercício da região
    "pain_avoid": 8,             # ≥ 8: evitar a região e procurar um profissional
}

WELLNESS = {"sleep_q": "Sono", "fatigue": "Cansaço", "soreness": "Dor muscular", "stress": "Estresse", "mood": "Humor"}


def settings(coach_id: int) -> dict:
    raw = db.kv_get("coach_settings", user_id=coach_id)
    cur = json.loads(raw) if raw else {}
    return {**DEFAULTS, **{k: v for k, v in cur.items() if k in DEFAULTS}}


def save_settings(coach_id: int, body: dict) -> dict:
    cur = settings(coach_id)
    for k, v in (body or {}).items():
        if k in DEFAULTS and v not in (None, ""):
            cur[k] = float(v)
    db.kv_set("coach_settings", json.dumps(cur), user_id=coach_id)
    return cur


# ------------------------------------------------------------------ check-in
def save_checkin(day: date, body: dict) -> dict:
    """Check-in do atleta (10 segundos): bem-estar 1–5 e dor 0–10 por região."""
    rec = {"date": day.isoformat(), "at": clock.now().isoformat(timespec="minutes")}
    for k in WELLNESS:
        v = body.get(k)
        if v not in (None, ""):
            rec[k] = max(1, min(5, int(v)))
    pain = {}
    for k, v in (body.get("pain") or {}).items():
        if k in REGIONS and v not in (None, ""):
            p = max(0, min(10, int(v)))
            if p:
                pain[k] = p
    rec["pain"] = pain
    if body.get("note"):
        rec["note"] = str(body["note"])[:280]
    db.kv_set(f"checkin:{day.isoformat()}", json.dumps(rec, ensure_ascii=False))
    return rec


def checkins(today: date, days: int = 28) -> list[dict]:
    out = []
    for i in range(days):
        raw = db.kv_get(f"checkin:{(today - timedelta(days=i)).isoformat()}")
        if raw:
            out.append(json.loads(raw))
    return out  # do mais recente para o mais antigo


def pain_suggestions(recent: list[dict], lims: list[dict], s: dict) -> list[dict]:
    """Dor relatada → sugestão de área de tensão, pelo modelo de monitoramento da dor (Silbernagel):
    até 5/10 e sem piorar = segue (sem progredir a região); > 5 ou piorando = cuidado (trocar/reduzir);
    ≥ 8 = evitar e procurar um profissional."""
    s = {**DEFAULTS, **(s or {})}
    cur = {l["key"]: l.get("severity") for l in lims or []}
    out = []
    last = recent[0]["pain"] if recent and recent[0].get("pain") is not None else {}
    prev = next((c.get("pain") or {} for c in recent[1:8] if c.get("pain") is not None), {})
    for k, p in last.items():
        worse = prev.get(k) is not None and p > prev[k]
        want = ("evitar" if p >= s["pain_avoid"] else
                "cuidado" if p >= s["pain_act"] or (worse and p >= s["pain_warn"]) else None)
        if want and cur.get(k) != want and not (cur.get(k) == "evitar" and want == "cuidado"):
            out.append({"region": k, "label": REGIONS[k], "pain": p, "severity": want, "worse": worse,
                        "refer": p >= s["pain_avoid"]})
    return out


# ------------------------------------------------------------- planilha × tensão
def _patterns_of(eid: str) -> list[str]:
    from .generator import PATTERNS

    return [p for p, ids in PATTERNS.items() if eid in ids]


def adapt_plan(plan: dict, lims: list[dict], profile: dict) -> tuple[dict, list[str]]:
    """Troca na planilha os exercícios que as áreas de tensão pedem para evitar (e, em 'cuidado', os de
    risco quando existe opção tranquila no mesmo padrão de movimento)."""
    from .generator import TIERS, _Picker, tier_for

    tier = TIERS[tier_for(profile.get("training_months") if profile.get("training_months") is not None else 12)]
    picker = _Picker(tier, lims, set(profile.get("equipment") or []) or None, 0)
    changes = []
    for day in plan.get("days") or []:
        used = {i.get("exercise_id") for i in day.get("items") or []}
        for it in day.get("items") or []:
            if (it.get("phase") or "principal") != "principal" or not it.get("sets"):
                continue
            ex = catalog.get(it.get("exercise_id"))
            if not ex or ex.get("kind") != "strength":
                continue
            chk = limitations.check_exercise(ex, lims)
            if chk["status"] == "ok":
                continue
            pats = [it["pattern"]] if it.get("pattern") else _patterns_of(ex["id"])
            new = None
            for pat in pats:
                for eid, stt in picker.candidates(pat):
                    if eid in used:
                        continue
                    if stt == "ok" or (chk["status"] == "evitar" and stt == "cuidado"):
                        new = eid
                        break
                if new:
                    break
            if not new:
                prim = set(ex.get("primaryMuscles") or [])
                for cand in catalog.all_exercises():
                    if cand.get("kind") != "strength" or cand["id"] in used or not prim & set(cand.get("primaryMuscles") or []):
                        continue
                    if limitations.check_exercise(cand, lims)["status"] == "ok" and cand.get("popular"):
                        new = cand["id"]
                        break
            if new:
                old_name = ex.get("name")
                used.discard(it["exercise_id"])
                used.add(new)
                it["exercise_id"] = new
                it.pop("notes", None)
                it["notes"] = "trocado pela área de tensão: " + "; ".join(chk["reasons"][:1])
                changes.append(f"{day.get('name', '').split(' – ')[-1]}: {old_name} → {catalog.get(new).get('name')}")
            elif chk["status"] == "evitar":
                it["notes"] = "⚠ sem alternativa segura no catálogo: faça com carga leve e amplitude sem dor, ou pule"
    return plan, changes


# ------------------------------------------------------------------ triagem
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def flags(snap: dict, s: dict) -> list[dict]:
    """Regras de alerta (atenção = amarelo, agir = vermelho) a partir do retrato do atleta."""
    out = []

    def add(level, code, text):
        out.append({"level": level, "code": code, "text": text})

    if snap.get("missed_row", 0) >= 2:
        add("agir", "faltas", f"{snap['missed_row']} treinos seguidos não feitos")
    c7 = snap.get("compliance_7d")
    if c7 is not None and snap.get("planned_7d", 0) >= 3:
        if c7 < s["compliance_act"]:
            add("agir", "adesao", f"Fez {c7:.0f}% do planejado na semana")
        elif c7 < s["compliance_warn"]:
            add("atencao", "adesao", f"Fez {c7:.0f}% do planejado na semana")
    di = snap.get("days_inactive")
    if di is not None and snap.get("has_plan"):
        if di >= s["inactive_act"]:
            add("agir", "parado", f"{di} dias sem treinar")
        elif di >= s["inactive_warn"]:
            add("atencao", "parado", f"{di} dias sem treinar")
    rd = snap.get("readiness")
    if rd is not None:
        if rd < s["readiness_act"]:
            add("agir", "prontidao", f"Prontidão {rd:.0f} hoje")
        elif rd < s["readiness_warn"]:
            add("atencao", "prontidao", f"Prontidão {rd:.0f} hoje")
    sl = snap.get("sleep_7d")
    if sl is not None:
        if sl < s["sleep_act"]:
            add("agir", "sono", f"Dormindo {sl:.1f} h/noite (7 dias)".replace(".", ","))
        elif sl < s["sleep_warn"] or snap.get("short_nights", 0) >= 2:
            extra = f", {snap['short_nights']} noites < 6 h" if snap.get("short_nights", 0) >= 2 else ""
            add("atencao", "sono", f"Dormindo {sl:.1f} h/noite (7 dias){extra}".replace(".", ","))
    if snap.get("hrv_low"):
        both = (snap.get("rhr_delta") or 0) >= s["rhr_warn"]
        add("agir" if both else "atencao", "vfc",
            "VFC abaixo da faixa normal" + (" e FC de repouso alta: possível fadiga/doença" if both else ""))
    rh = snap.get("rhr_delta")
    if rh is not None:
        if rh >= s["rhr_act"]:
            add("agir", "fc_repouso", f"FC de repouso +{rh:.0f} bpm acima do normal")
        elif rh >= s["rhr_warn"]:
            add("atencao", "fc_repouso", f"FC de repouso +{rh:.0f} bpm acima do normal")
    for a in snap.get("health_alerts") or []:
        if "doen" in a.lower() or "infec" in a.lower():
            add("agir", "doenca", a)
    lj = snap.get("load_change")
    if lj is not None and (snap.get("load_prev") or 0) >= 100:  # semana anterior com carga de verdade
        if lj >= s["load_jump_act"]:
            add("agir", "carga", f"Pico de carga: +{lj:.0f}% sobre a semana anterior")
        elif lj >= s["load_jump_warn"]:
            add("atencao", "carga", f"Pico de carga: +{lj:.0f}% sobre a semana anterior")
    if snap.get("strain_high"):
        add("atencao", "monotonia", "Semana pesada e repetitiva para ele (esforço acima do habitual): "
            "alterne dias leves e fortes")
    if (snap.get("form") or 0) < -30:
        add("agir", "fadiga", f"Fadiga muito acima do condicionamento (forma {snap['form']:.0f})")
    w = snap.get("wellness") or {}
    bad = [WELLNESS[k] for k, v in w.items() if k in WELLNESS and v >= s["wellness_warn"]]
    z = snap.get("wellness_z")
    severe = any(v >= 5 for k, v in w.items() if k in ("fatigue", "soreness"))
    if severe or (z is not None and z >= 2):
        add("agir", "bem_estar", "Check-in bem pior que o normal" + (f": {', '.join(bad).lower()}" if bad else ""))
    elif len(bad) >= 2 or (z is not None and z >= 1):
        add("atencao", "bem_estar", "Check-in pior que o normal" + (f": {', '.join(bad).lower()}" if bad else ""))
    prev_pain = snap.get("pain_prev") or {}
    for k, p in (snap.get("pain") or {}).items():
        worse = k in prev_pain and p > prev_pain[k]
        name = REGIONS.get(k, k).lower()
        if p >= s["pain_avoid"]:
            add("agir", f"dor_{k}", f"Dor {p}/10 em {name}: evitar a região e procurar um profissional")
        elif p >= s["pain_act"] or (worse and p >= s["pain_warn"]):
            add("agir", f"dor_{k}", f"Dor {p}/10 em {name}" + (" (piorando)" if worse else "") + ": trocar/reduzir os exercícios da região")
        elif p >= s["pain_warn"]:
            add("atencao", f"dor_{k}", f"Dor {p}/10 em {name}: pode treinar se passar até amanhã, sem aumentar a carga da região")
    if snap.get("falling", 0) >= 2:
        add("atencao", "forca", f"{snap['falling']} exercícios com carga caindo")
    muted = set(snap.get("muted") or [])
    return [f for f in out if f["code"] not in muted]


def priority(fl: list[dict]) -> int:
    return sum(3 if f["level"] == "agir" else 1 for f in fl)


def _strain(week: list[float]) -> float | None:
    sd = st.pstdev(week)
    total = sum(week)
    return total * ((total / 7) / sd) if sd > 0 and total > 0 else None


def load_numbers(series: list[dict]) -> dict:
    """Pico de carga semana a semana e esforço (strain de Foster = carga × monotonia) comparado com
    as semanas anteriores do próprio atleta (média + 1 DP), em vez de um corte fixo."""
    tss = [p["tss"] for p in series]
    if len(tss) < 14:
        return {}
    this, prev = sum(tss[-7:]), sum(tss[-14:-7])
    weeks = [tss[len(tss) - 7 * (i + 1): len(tss) - 7 * i] for i in range(len(tss) // 7)]
    strains = [_strain(w) for w in weeks]
    out = {"load_week": round(this), "load_prev": round(prev),
           "load_change": round(100 * (this - prev) / prev) if prev > 0 else None}
    past = [x for x in strains[1:] if x is not None]
    if strains and strains[0] is not None and len(past) >= 3:
        out["strain_high"] = strains[0] > st.mean(past) + st.pstdev(past)
    return out


def wellness_z(cks: list[dict]) -> float | None:
    """z do check-in de hoje contra os últimos 28 dias (positivo = pior que o normal dele)."""
    def total(c):
        vals = [c[k] for k in WELLNESS if k in c]
        return sum(vals) / len(vals) if vals else None
    base = [t for t in (total(c) for c in cks[1:]) if t is not None]
    now = total(cks[0]) if cks else None
    if now is None or len(base) < 7:
        return None
    sd = st.pstdev(base) or 0.35  # piso: escala de 1 a 5, variação mínima significativa
    return round((now - st.mean(base)) / sd, 2)


def hrv_low(hd: dict, today: date) -> bool:
    """Média de 7 dias da VFC abaixo da faixa normal (média − 0,5 DP dos 28 dias anteriores; Plews/Buchheit)."""
    vals = {}
    for i in range(35):
        v = ((hd.get((today - timedelta(days=i)).isoformat()) or {}).get("hrv") or {}).get("last_night")
        if v:
            vals[i] = v
    recent = [vals[i] for i in range(7) if i in vals]
    base = [vals[i] for i in range(7, 35) if i in vals]
    if len(recent) < 3 or len(base) < 10:
        return False
    return st.mean(recent) < st.mean(base) - 0.5 * st.pstdev(base)
