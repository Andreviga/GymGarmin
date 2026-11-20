"""Foco: priorizar 1–2 músculos e/ou 1–2 aptidões por um bloco de 6–12 semanas.

Um foco de verdade mexe em várias variáveis ao mesmo tempo, não só "mais séries":

- Volume semanal: o músculo do foco sobe para a faixa alta (MAV) sem passar do máximo recuperável (MRV).
  Mais séries dão mais hipertrofia até um platô (relação dose-resposta: Schoenfeld et al. 2017; Pelland et al. 2024).
  No foco "forte", os outros músculos descem para manutenção — ~1/3 do volume já mantém a massa (Bickel et al. 2011) —
  e sobra tempo e recuperação para o que importa.
- Frequência: o volume extra é dividido em 2–3 dias da semana (mesma dose, menos fadiga por sessão).
- Ordem: o exercício do foco vem no começo do treino — o que é feito primeiro rende e progride mais
  (revisões sobre ordem dos exercícios: Simão et al. 2012; Nunes et al. 2021).
- Seleção e técnica: variações em ângulos diferentes / posição alongada e myo-reps na última série.
- Aptidões: blocos próprios (força num levantamento, explosão, fôlego, mobilidade, core, postura, pegada,
  equilíbrio, resistência muscular).
- Duração: um bloco de 6–12 semanas e depois reavaliar — manter, trocar o foco ou voltar ao equilibrado.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import catalog, clock
from .muscles import MUSCLES

MUSCLE_OPTIONS = [
    ("chest", "Peito"), ("lats", "Costas — largura"), ("middle back", "Costas — espessura"), ("shoulders", "Ombros"),
    ("biceps", "Bíceps"), ("triceps", "Tríceps"), ("forearms", "Antebraço"), ("traps", "Trapézio"), ("abdominals", "Abdômen"),
    ("glutes", "Glúteo"), ("abductors", "Glúteo médio (lateral do quadril)"), ("quadriceps", "Coxa da frente (quadríceps)"),
    ("hamstrings", "Posterior de coxa"), ("adductors", "Parte interna da coxa"), ("calves", "Panturrilha"),
]
SKILLS = {
    "forca": {"icon": "🏋️", "label": "Força máxima", "desc": "levantar mais peso num exercício (agachamento, supino, terra…)"},
    "potencia": {"icon": "⚡", "label": "Explosão e salto", "desc": "saltar mais alto, arrancar mais rápido"},
    "folego": {"icon": "🫁", "label": "Fôlego (VO₂máx)", "desc": "cansar menos em escadas, corridas e esportes"},
    "resistencia": {"icon": "🔁", "label": "Resistência muscular", "desc": "aguentar mais repetições sem queimar"},
    "mobilidade": {"icon": "🧘", "label": "Mobilidade e flexibilidade", "desc": "agachar fundo, alcançar, menos rigidez"},
    "core": {"icon": "🧱", "label": "Core e estabilidade", "desc": "tronco firme, protege a lombar"},
    "postura": {"icon": "🧍", "label": "Postura", "desc": "ombros para trás, costas mais fortes"},
    "pegada": {"icon": "✊", "label": "Pegada (grip)", "desc": "segurar mais tempo e mais peso"},
    "equilibrio": {"icon": "🦩", "label": "Equilíbrio", "desc": "firmeza numa perna só, menos risco de queda"},
}
LIFTS = {
    "agachamento": ("Agachamento", ["Barbell_Squat", "Smith_Machine_Squat", "Hack_Squat", "Leg_Press"]),
    "supino": ("Supino", ["Barbell_Bench_Press_-_Medium_Grip", "Dumbbell_Bench_Press", "Machine_Bench_Press"]),
    "terra": ("Levantamento terra", ["Barbell_Deadlift", "Romanian_Deadlift"]),
    "desenvolvimento": ("Desenvolvimento (ombro)", ["Standing_Military_Press", "Dumbbell_Shoulder_Press", "Machine_Shoulder_Military_Press"]),
    "barra": ("Barra fixa / puxada", ["Pullups", "Chin-Up", "Wide-Grip_Lat_Pulldown"]),
}
LEVELS = {"leve": {"up": 1.2, "others": 1.0, "label": "Leve: o resto do treino continua igual"},
          "forte": {"up": 1.45, "others": 0.85, "label": "Forte: o resto vai para manutenção"}}
POOLS = {
    "potencia_lower": ["Box_Jump_Multiple_Response", "Front_Box_Jump", "Freehand_Jump_Squat", "One-Arm_Kettlebell_Swings"],
    "potencia_upper": ["Medicine_Ball_Chest_Pass", "One-Arm_Medicine_Ball_Slam", "Backward_Medicine_Ball_Throw"],
    "mobilidade": ["Worlds_Greatest_Stretch", "Standing_Hip_Circles", "Cat_Stretch", "Round_The_World_Shoulder_Stretch", "Ankle_Circles"],
    "core": ["Ab_Roller", "Pallof_Press", "Dead_Bug", "Hanging_Leg_Raise", "Plank", "Side_Bridge"],
    "postura": ["Face_Pull", "Reverse_Flyes", "Cable_Rear_Delt_Fly", "Reverse_Machine_Flyes"],
    "postura_mob": ["Round_The_World_Shoulder_Stretch", "Cat_Stretch"],
    "pegada": ["Farmers_Walk", "Palms-Up_Dumbbell_Wrist_Curl_Over_A_Bench", "Palms-Down_Dumbbell_Wrist_Curl_Over_A_Bench"],
    "equilibrio": ["Kettlebell_One-Legged_Deadlift", "Split_Squat_with_Dumbbells", "Single-Leg_High_Box_Squat", "Dumbbell_Step_Ups"],
    # músculos sem padrão próprio no gerador
    "forearms": ["Palms-Up_Dumbbell_Wrist_Curl_Over_A_Bench", "Hammer_Curls", "Palms-Down_Dumbbell_Wrist_Curl_Over_A_Bench", "Farmers_Walk"],
    "abductors": ["Thigh_Abductor", "One-Legged_Cable_Kickback"],
}
# alvo semanal extra que cada aptidão pede (multiplicador)
SKILL_TARGETS = {"core": {"abdominals": 1.5}, "postura": {"middle back": 1.25, "traps": 1.15}, "pegada": {"forearms": 1.8}}
UPPER = {"chest", "lats", "middle back", "shoulders", "biceps", "triceps", "traps"}
LOWER = {"quadriceps", "hamstrings", "glutes", "calves", "adductors", "abductors"}


def _ex(eid: str) -> dict:
    return catalog.get(eid) or {}


def normalize(f: dict | None) -> dict | None:
    if not f:
        return None
    muscles = [m for m in (f.get("muscles") or []) if m in dict(MUSCLE_OPTIONS)][:2]
    skills = [s for s in (f.get("skills") or []) if s in SKILLS][:2]
    if not muscles and not skills:
        return None
    weeks = int(f.get("weeks") or 8)
    out = {"muscles": muscles, "skills": skills, "level": f.get("level") if f.get("level") in LEVELS else "forte",
           "lift": f.get("lift") if f.get("lift") in LIFTS else "agachamento", "weeks": max(4, min(16, weeks)),
           "start": f.get("start") or clock.today().isoformat()}
    out["label"] = label(out)
    return out


def active(profile: dict | None, today: date | None = None) -> dict | None:
    f = normalize((profile or {}).get("focus"))
    if not f:
        return None
    today = today or clock.today()
    try:
        start = date.fromisoformat(f["start"])
    except ValueError:
        start = today
    week = max(1, (today - start).days // 7 + 1)
    return {**f, "week": week, "ended": week > f["weeks"], "end": (start + timedelta(weeks=f["weeks"])).isoformat()}


def label(f: dict) -> str:
    parts = [dict(MUSCLE_OPTIONS)[m] for m in f.get("muscles", [])] + [SKILLS[s]["label"] for s in f.get("skills", [])]
    if "forca" in f.get("skills", []):
        parts[parts.index("Força máxima")] = f"Força no {LIFTS[f['lift']][0].lower()}"
    return " + ".join(parts)


# ------------------------------------------------------------------ volume ----

def adjust_targets(targets: dict[str, float], f: dict) -> list[str]:
    """Sobe o alvo semanal do foco (até o MRV) e, no foco forte, leva o resto para manutenção."""
    lv = LEVELS[f["level"]]
    notes = []
    for m in f["muscles"]:
        info = MUSCLES[m]
        base = targets.get(m) or info["mev"]
        new = min(info["mrv"] * 0.9, max(info["mav"][0], base * lv["up"]))
        targets[m] = round(new, 1)
        notes.append(f"{dict(MUSCLE_OPTIONS)[m]}: {base:g} → {targets[m]:g} séries/semana")
    for s in f["skills"]:
        for m, k in SKILL_TARGETS.get(s, {}).items():
            if m in targets and m not in f["muscles"]:
                targets[m] = round(min(MUSCLES[m]["mrv"] * 0.8, targets[m] * k), 1)
    if lv["others"] < 1:
        keep = set(f["muscles"]) | {m for s in f["skills"] for m in SKILL_TARGETS.get(s, {})}
        for m in targets:
            if m not in keep:
                targets[m] = round(max(MUSCLES[m]["mev"], targets[m] * lv["others"]), 1)
    out = []
    if notes:
        out.append("Foco (" + ("forte" if f["level"] == "forte" else "leve") + "): " + "; ".join(notes)
                   + (". Os outros músculos ficam em manutenção (~1/3 do volume já mantém a massa) para sobrar tempo e recuperação."
                      if lv["others"] < 1 else "."))
    return out


# ------------------------------------------------------------------ montagem ----

def _principal(d: dict) -> list[dict]:
    return [i for i in d["items"] if (i.get("phase") or "principal") == "principal" and not i.get("finisher")]


def _prim(it: dict) -> set[str]:
    return set(_ex(it.get("exercise_id")).get("primaryMuscles") or [])


def _first_principal_idx(d: dict) -> int:
    for k, i in enumerate(d["items"]):
        if (i.get("phase") or "principal") == "principal":
            return k
    return len(d["items"])


def _end_idx(d: dict) -> int:
    for k, i in enumerate(d["items"]):
        if i.get("phase") == "pos" or i.get("finisher"):
            return k
    return len(d["items"])


def _item(eid: str, sets: int, rest: int, notes: str, reps: str | None = None, dur: int | None = None,
          role: str = "acc", phase: str = "principal") -> dict:
    ex = _ex(eid)
    it = {"exercise_id": eid, "sets": sets, "rest_s": rest, "role": role, "phase": phase, "focus": True, "notes": notes}
    if dur or ex.get("timed"):
        it["duration_s"] = dur or 30
    else:
        it["reps"] = reps or "8-12"
    return it


def _pick(pool: list[str], ok, used: set[str], rot: int = 0) -> str | None:
    cands = [e for e in pool if catalog.get(e) and e not in used and ok(e)]
    cands.sort(key=lambda e: ok(e) != "ok")
    return cands[rot % len(cands)] if cands else None


def _is_lower_day(d: dict) -> bool:
    load = [m for i in _principal(d) for m in _prim(i)]
    return sum(m in LOWER for m in load) >= max(1, sum(m in UPPER for m in load))


def apply(days: list[dict], f: dict, ctx: dict) -> list[str]:
    """Aplica frequência, ordem, técnica e os blocos de aptidão nos dias de força já montados."""
    from .generator import MUSCLE_PATTERN, _muscle_load
    ok = lambda e: ctx["ok"](e)  # noqa: E731
    tier, reps, tier_key = ctx["tier"], ctx["reps"], ctx["tier_key"]
    acc_rest = ctx["acc_rest"]
    strength = [d for d in days if d["kind"] == "forca"]
    notes: list[str] = []
    if not strength:
        return ["Foco: sem dias de musculação na semana — libere pelo menos 2 dias para o foco funcionar."]
    min_freq = min(len(strength), 3 if len(strength) >= 4 and tier_key in ("avancado", "elite") else 2)

    # 1) frequência: o músculo do foco aparece em 2–3 dias
    for m in f["muscles"]:
        have = [d for d in strength if any(m in _prim(i) for i in _principal(d))]
        others = [d for d in strength if d not in have]
        others.sort(key=lambda d: (any(abs(x["weekday"] - d["weekday"]) in (1, 6) and _muscle_load(x["items"]).get(m, 0) >= 4
                                       for x in have), len(d["items"])))
        added = 0
        for d in others[:max(0, min_freq - len(have))]:
            used = {i["exercise_id"] for i in d["items"]}
            pat = MUSCLE_PATTERN.get(m)
            ch = ctx["picker"].pick(pat, "acc", used, len(d["items"])) if pat else None
            eid = ch[0] if ch else _pick(POOLS.get(m, []), ok, used, len(d["items"]))
            if not eid:
                continue
            d["items"].insert(_end_idx(d), _item(eid, tier["sets"]["acc"], acc_rest, "foco: mais um dia para este músculo", reps["acc"]))
            added += 1
        if have or added:
            notes.append(f"{dict(MUSCLE_OPTIONS)[m]} em {len(have) + added}× por semana (o volume extra dividido em dias diferentes).")

    # 2) ordem: o foco vem cedo; 3) técnica na última série
    focus_m = set(f["muscles"])
    for d in strength:
        pr = _principal(d)
        hits = [i for i in pr if _prim(i) & focus_m]
        # bi-set com um exercício do foco: o par anda junto
        for i in list(hits):
            if i.get("superset"):
                k = d["items"].index(i)
                mate = d["items"][k + 1] if i["superset"] == "A" and k + 1 < len(d["items"]) else d["items"][k - 1] if i["superset"] == "B" and k > 0 else None
                if mate is not None and mate not in hits:
                    hits.append(mate)
        hits.sort(key=lambda i: d["items"].index(i))
        if not hits:
            continue
        for i in hits:
            i["focus"] = True
            if "foco" not in (i.get("notes") or ""):
                i["notes"] = ((i.get("notes") or "") + " · foco: faça descansado(a), no começo").strip(" ·")
        start = _first_principal_idx(d)
        for i in reversed([x for x in hits if _ex(x["exercise_id"]).get("mechanic") == "compound" and not x.get("superset")]):
            d["items"].remove(i)
            d["items"].insert(start, i)
        iso = [x for x in hits if _ex(x["exercise_id"]).get("mechanic") != "compound" or x.get("superset")]
        first = d["items"][_first_principal_idx(d)] if d["items"] else None
        pos = start + (0 if (first is not None and first in hits) or not pr else 1)
        for i in reversed(iso):
            d["items"].remove(i)
            d["items"].insert(min(pos, _end_idx(d)), i)
        last = [x for x in hits if x.get("role") == "acc" and not x.get("duration_s")]
        if last and tier_key != "iniciante":
            last[-1]["notes"] += " · última série: myo-reps (na falha técnica, 15 s de pausa e mini-séries de 3–5 reps, até 3×)"
    if f["muscles"]:
        notes.append("Exercícios do foco no começo do treino (o que vem primeiro rende e progride mais).")

    # 4) aptidões
    lower_days = [d for d in strength if _is_lower_day(d)] or strength
    upper_days = [d for d in strength if not _is_lower_day(d)] or strength
    for s in f["skills"]:
        if s == "forca":
            name, pool = LIFTS[f["lift"]]
            lift = next((e for e in pool if catalog.get(e) and ok(e)), None)
            if not lift:
                notes.append(f"Força no {name.lower()}: suas limitações não liberam esse exercício — escolha outro levantamento.")
                continue
            hit = [d for d in strength if any(i["exercise_id"] in pool for i in _principal(d))]
            if not hit:
                d0 = (lower_days if f["lift"] in ("agachamento", "terra") else upper_days)[0]
                d0["items"].insert(_first_principal_idx(d0), _item(lift, tier["sets"]["main"], 180, "", "3-5", role="main"))
                hit = [d0]
            heavy = hit[0]
            it = next(i for i in _principal(heavy) if i["exercise_id"] in pool)
            # especificidade: para ficar forte no levantamento, treina-se o próprio levantamento
            it.update(exercise_id=lift, reps="3-5", sets=max(int(it.get("sets") or 3), 4), rest_s=max(int(it.get("rest_s") or 0), 180),
                      focus=True, role="main", notes="foco força: aqueça em rampa, 1 série pesada (RPE 8–9) e as outras ~10% mais leves")
            it.pop("duration_s", None)
            heavy["items"].remove(it)
            heavy["items"].insert(_first_principal_idx(heavy), it)
            if len(strength) >= 2:
                spaced = lambda d: abs(d["weekday"] - heavy["weekday"]) not in (0, 1, 6)  # noqa: E731
                want_lower = f["lift"] in ("agachamento", "terra")
                fits = lambda d: _is_lower_day(d) == want_lower  # noqa: E731
                cand = [d for d in hit[1:] if spaced(d)] or [d for d in strength if d is not heavy and spaced(d)] or \
                       [d for d in strength if d is not heavy]
                cand.sort(key=lambda d: (not fits(d), d not in hit))
                d2 = cand[0]
                tech = {"exercise_id": lift, "reps": "3", "sets": 3, "rest_s": 150, "role": "main", "focus": True,
                        "notes": "foco força (técnica): ~75–80%, barra rápida, longe da falha"}
                it2 = next((i for i in _principal(d2) if i["exercise_id"] in pool), None)
                if it2:
                    it2.update(tech)
                    d2["items"].remove(it2)
                else:
                    it2 = {**tech, "phase": "principal"}
                d2["items"].insert(_first_principal_idx(d2), it2)
            notes.append(f"Força no {name.lower()}: 2× por semana — um dia pesado (3–5 reps, descanso 3 min) e um de técnica/velocidade.")
        elif s == "potencia":
            n = 0
            for k, d in enumerate(lower_days[:2]):
                eid = _pick(POOLS["potencia_lower"], ok, {i["exercise_id"] for i in d["items"]}, k)
                if eid:
                    d["items"].insert(_first_principal_idx(d), _item(eid, 3, 90, "explosão: máxima intenção, pouso suave; pare se perder altura ou qualidade", "3-5"))
                    n += 1
            if upper_days:
                d = upper_days[0]
                eid = _pick(POOLS["potencia_upper"], ok, {i["exercise_id"] for i in d["items"]})
                if eid:
                    d["items"].insert(_first_principal_idx(d), _item(eid, 3, 90, "explosão: arremesse o mais rápido possível", "5"))
                    n += 1
            notes.append(f"Explosão: {n} bloco(s) de saltos/arremessos logo após o aquecimento, com o corpo descansado (poucas reps, descanso completo)."
                         if n else "Explosão: nenhum salto/arremesso é seguro com suas limitações ou equipamento — o foco fica na força das pernas.")
        elif s == "resistencia":
            for d in strength:
                for i in _principal(d):
                    if i.get("role") == "acc" and not i.get("duration_s") and not i.get("focus"):
                        i["reps"] = "15-20"
                        i["rest_s"] = max(30, int(round((i.get("rest_s") or 60) * 0.6 / 15) * 15))
            notes.append("Resistência muscular: acessórios com 15–20 repetições e descanso curto (30–45 s).")
        elif s == "folego":
            cardio_day = next((d for d in days if d["kind"] == "cardio" and not any(i.get("intervals") for i in d["items"])), None)
            mz = ctx.get("max_zone", 5)
            if cardio_day:
                mach = cardio_day["items"][0]["exercise_id"]
                cardio_day["items"][0] = {"exercise_id": mach, "warmup_min": 8, "cooldown_min": 5, "phase": "principal", "focus": True,
                                          "intervals": {"repeats": 4, "work_s": 240, "work_zone": min(4, mz), "rest_s": 180, "rest_zone": 1},
                                          "notes": "4×4 min forte (respiração ofegante, frases curtas) com 3 min leve entre eles"}
                cardio_day["name"] = cardio_day["name"].split(" – ")[0] + " – Intervalado 4×4 (fôlego)"
                notes.append("Fôlego: um dia de cardio virou intervalado 4×4 min — um dos métodos que mais sobem o VO₂máx.")
            elif ctx.get("endurance_on"):
                notes.append("Fôlego: as sessões de resistência já trazem treinos de qualidade; mantenha uma forte por semana.")
            else:
                d = min(strength, key=lambda x: _muscle_load(x["items"]).get("quadriceps", 0))
                mach = ctx.get("cardio_machine") or "cardio_bike_vertical"
                d["items"].insert(_end_idx(d), {"exercise_id": mach, "warmup_min": 2, "cooldown_min": 2, "phase": "principal", "finisher": True,
                                                "focus": True, "intervals": {"repeats": 6, "work_s": 60, "work_zone": min(4, mz), "rest_s": 60, "rest_zone": 1},
                                                "notes": "fôlego: 6×1 min forte / 1 min leve no fim do treino"})
                notes.append("Fôlego: finalizador intervalado (6×1 min forte) no treino com menos perna.")
        elif s == "mobilidade":
            for k, d in enumerate(strength):
                used = {i["exercise_id"] for i in d["items"]}
                for j in range(2):
                    eid = _pick(POOLS["mobilidade"], ok, used, k + j)
                    if eid:
                        used.add(eid)
                        d["items"].insert(_first_principal_idx(d), _item(eid, 1, 10, "mobilidade: amplitude máxima sem dor, respiração solta", dur=45, phase="preparo"))
            notes.append("Mobilidade: +2 exercícios de mobilidade no aquecimento de todo treino (quadril, coluna, ombros, tornozelo).")
        elif s == "core":
            for k, d in enumerate(strength):
                if any("abdominals" in _prim(i) for i in _principal(d)):
                    continue
                eid = _pick(POOLS["core"], ok, {i["exercise_id"] for i in d["items"]}, k)
                if eid:
                    d["items"].insert(_end_idx(d), _item(eid, 3, 45, "core: tronco travado, sem prender a respiração", "8-12"))
            notes.append("Core: um exercício de estabilidade em todo treino (anti-extensão, anti-rotação, flexão).")
        elif s == "postura":
            for k, d in enumerate(upper_days[:3]):
                used = {i["exercise_id"] for i in d["items"]}
                if not any(i["exercise_id"] in POOLS["postura"] for i in d["items"]):
                    eid = _pick(POOLS["postura"], ok, used, k)
                    if eid:
                        d["items"].insert(_end_idx(d), _item(eid, 3, 45, "postura: puxe com as escápulas para trás e para baixo", "12-15"))
                mob = _pick(POOLS["postura_mob"], ok, used, k)
                if mob:
                    d["items"].insert(_first_principal_idx(d), _item(mob, 1, 10, "postura: abra o peito e mobilize a coluna torácica", dur=45, phase="preparo"))
            notes.append("Postura: costas altas e deltoide posterior nos treinos de superior + mobilidade torácica no aquecimento.")
        elif s == "pegada":
            for k, d in enumerate(strength[:2]):
                eid = _pick(POOLS["pegada"], ok, {i["exercise_id"] for i in d["items"]}, k)
                if eid:
                    d["items"].insert(_end_idx(d), _item(eid, 3, 60, "pegada: aperte forte, ombros firmes", "12-15", dur=40 if eid == "Farmers_Walk" else None))
            notes.append("Pegada: caminhada do fazendeiro / punho 2× por semana, no fim do treino (para não atrapalhar os puxadas).")
        elif s == "equilibrio":
            for k, d in enumerate(lower_days[:2]):
                eid = _pick(POOLS["equilibrio"], ok, {i["exercise_id"] for i in d["items"]}, k)
                if eid:
                    d["items"].insert(_end_idx(d), _item(eid, 3, 60, "equilíbrio: unilateral, joelho alinhado; no fim, 30 s em um pé de cada lado (olhos fechados se ficar fácil)", "8-10"))
            notes.append("Equilíbrio: exercício unilateral de perna 2× por semana + 30 s em um pé no fim.")
    return [f"Foco: {label(f)} — semana {f.get('week', 1)} de {f['weeks']}."] + notes


# ------------------------------------------------------------------ progresso ----

def progress(f: dict, sessions: list[dict], targets: dict | None, today: date | None = None) -> dict:
    """Séries por semana dos músculos do foco (contra o alvo) e evolução do 1RM estimado nos exercícios do foco."""
    from .history import e1rm
    today = today or clock.today()
    start = date.fromisoformat(f["start"])
    first_week = start - timedelta(days=start.weekday()) - timedelta(weeks=2)  # 2 semanas antes: referência
    weeks: dict[str, dict] = {}
    lifts: dict[str, list[tuple[str, float]]] = {}
    for s in sessions:
        try:
            dt = date.fromisoformat(str(s.get("date"))[:10])
        except ValueError:
            continue
        if dt < first_week or dt > today:
            continue
        wk = (dt - timedelta(days=dt.weekday())).isoformat()
        w = weeks.setdefault(wk, {m: 0 for m in f["muscles"]})
        for it in s.get("items") or []:
            prim = set(_ex(it.get("exercise_id")).get("primaryMuscles") or [])
            hit = prim & set(f["muscles"])
            sets = [x for x in it.get("sets") or [] if (x.get("reps") or x.get("duration_s"))]
            for m in hit:
                w[m] += len(sets)
            if hit or (it.get("exercise_id") in LIFTS[f["lift"]][1] and "forca" in f["skills"]):
                best = max((e1rm(x.get("load_kg") or 0, x.get("reps") or 0, x.get("rpe")) or 0 for x in sets), default=0)
                if best:
                    lifts.setdefault(it["exercise_id"], []).append((dt.isoformat(), best))
    lift_out = []
    for eid, pts in lifts.items():
        pts.sort()
        if len(pts) >= 2:
            a, b = pts[0][1], pts[-1][1]
            lift_out.append({"exercise_id": eid, "name": _ex(eid).get("name", eid), "first": round(a, 1), "last": round(b, 1),
                             "change_pct": round(100 * (b - a) / a, 1), "n": len(pts)})
    lift_out.sort(key=lambda x: -x["n"])
    return {"focus": f, "label": label(f), "weeks": [{"week": k, "sets": v} for k, v in sorted(weeks.items())],
            "targets": {m: (targets or {}).get(m) for m in f["muscles"]}, "lifts": lift_out[:4],
            "names": {m: dict(MUSCLE_OPTIONS)[m] for m in f["muscles"]}}


def options() -> dict:
    return {"muscles": [{"id": k, "name": v} for k, v in MUSCLE_OPTIONS],
            "skills": [{"id": k, **v} for k, v in SKILLS.items()],
            "lifts": [{"id": k, "name": v[0]} for k, v in LIFTS.items()],
            "levels": [{"id": k, "name": v["label"]} for k, v in LEVELS.items()]}
