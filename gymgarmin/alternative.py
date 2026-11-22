"""Outra opção de treino para hoje (por motivo), exercícios extras e o próximo exercício que faz sentido.

Tudo parte do mesmo "vocabulário" do gerador: padrões de movimento (agachamento, empurrar na horizontal, puxar na
vertical…), papel no treino (principal, secundário, acessório), séries/repetições do nível e do objetivo da pessoa,
limitações, equipamento e tempo do dia.

Motivos e o que cada um muda (base científica resumida em REASONS[...]["base"]):
- ocupado   academia cheia: troca barra/máquina por halteres, polia e peso do corpo; mesma estação em sequência
- tempo     pouco tempo: mantém os principais, acessórios em bi-set, menos séries e descanso mais curto
- cansado   dormiu mal / cansado: ~1/3 menos séries, para 2–3 repetições antes da falha, máquinas com apoio
- dor       dor numa região: tira o que machuca, troca por variações sem dor, inclui um exercício de proteção
- dolorido  músculo ainda dolorido do último treino: treina outros grupos (que estão recuperados e faltando)
- variar    quero variar: troca secundários e acessórios por outros do mesmo padrão (os principais medem o progresso)
- fora      fora da academia (casa/hotel): só o que dá para fazer com o que a pessoa tem
- falta     treinar o que está faltando na semana

Para o treino livre e para "exercício extra", next_exercises() escolhe o próximo padrão pelo molde de treino mais
parecido com o que já foi feito (a ordem do molde: compostos antes de isolados), pelo que falta na semana, pelo que
já foi feito hoje (não passa de ~8 séries por músculo na sessão) e pela recuperação de cada músculo.
"""
from __future__ import annotations

import copy
import re
from collections import defaultdict

from . import catalog, gyms, life_stage, personal, training_rules
from . import generator as G
from .limitations import LIMITATIONS, check_exercise
from .muscles import MUSCLES, PRIMARY_WEIGHT, SECONDARY_WEIGHT

# ------------------------------------------------------------------ motivos ---
REASONS = [
    {"id": "ocupado", "icon": "👥", "label": "Academia cheia ou aparelho ocupado",
     "hint": "Troca barra e máquinas por halteres, polia e peso do corpo.",
     "base": "Máquinas, polia e pesos livres dão ganho parecido de força e massa quando o volume e o esforço são iguais "
             "(Haugen et al., 2023). Um par de halteres e um banco fazem quase tudo sem esperar rack."},
    {"id": "tempo", "icon": "⏱️", "label": "Tenho pouco tempo", "asks": "minutes",
     "hint": "Mantém os principais, junta acessórios em bi-set e corta o que rende menos.",
     "base": "Bi-sets de músculos que não competem entre si mantêm o volume com menos tempo (Robbins et al., 2010), e "
             "2–3 séries perto da falha por exercício já mantêm e fazem progredir (dose mínima: Iversen et al., 2021)."},
    {"id": "cansado", "icon": "😴", "label": "Estou cansado ou dormi mal",
     "hint": "Cerca de 1/3 menos séries, parando antes da falha, com máquinas de apoio.",
     "base": "Com recuperação ruim, reduzir volume e parar 2–3 repetições antes da falha mantém o estímulo com bem menos "
             "fadiga (autorregulação; Helms et al., 2016). Um terço do volume já mantém o que foi ganho (Bickel et al., 2011)."},
    {"id": "dor", "icon": "🩹", "label": "Estou com dor em uma região", "asks": "region",
     "hint": "Tira o que pode machucar e treina o resto em volta da dor.",
     "base": "Treinar em volta da dor (sem dor durante e depois) mantém o condicionamento e não atrasa a recuperação. "
             "Dor forte, que piora, com inchaço ou formigamento: pare e procure avaliação."},
    {"id": "dolorido", "icon": "🦵", "label": "Ainda dolorido do último treino", "asks": "muscles",
     "hint": "Treina outros músculos, que já estão recuperados.",
     "base": "A dor muscular tardia (24–72 h) indica que o músculo ainda está se recuperando: treiná-lo pesado rende "
             "menos. Treinar outros grupos mantém a semana sem atrapalhar a recuperação."},
    {"id": "variar", "icon": "🔀", "label": "Quero variar",
     "hint": "Troca secundários e acessórios por outros do mesmo movimento.",
     "base": "Variar os acessórios mantém o estímulo e a motivação; manter os exercícios principais é o que permite medir "
             "a progressão de carga (Kassiano et al., 2022)."},
    {"id": "fora", "icon": "🏠", "label": "Estou fora da academia (casa, hotel)", "asks": "kit",
     "hint": "Só com o que você tem: peso do corpo, halteres, elástico, barra fixa.",
     "base": "Cargas leves dão ganho de massa parecido com cargas altas quando a série vai até perto da falha "
             "(Schoenfeld et al., 2017); flexão de braço perto da falha rende como supino (Kikuchi e Nakazato, 2017)."},
    {"id": "doente", "icon": "🤧", "label": "Estou doente ou me recuperando", "asks": "sick",
     "hint": "Resfriado sem febre: treino bem mais leve. Febre ou dor no corpo: descanso.",
     "base": "Regra do pescoço: sintomas só acima do pescoço (nariz, garganta) e sem febre permitem treino leve, a 50–70% do "
             "volume; febre, dor no corpo ou sintomas no peito/estômago pedem descanso até 24–48 h sem febre. Depois de gripe "
             "ou COVID, volte aos poucos em 1–2 semanas (Elliott et al., BJSM 2020)."},
    {"id": "falta", "icon": "📊", "label": "Treinar o que está faltando na semana",
     "hint": "Monta o treino com os músculos que mais ficaram para trás.",
     "base": "O que importa para cada músculo é o volume da semana (Schoenfeld et al., 2017; Pelland et al., 2024): "
             "repor onde faltou equilibra o resultado."},
]
REASON_IDS = {r["id"] for r in REASONS}
REGIONS = [{"id": k, "label": v["label"].split(" (")[0]} for k, v in LIMITATIONS.items()
           if k in ("ombro", "joelho", "lombar", "cotovelo", "punho", "quadril", "tornozelo", "cervical")]
KITS = [{"id": "corpo", "label": "Só o peso do corpo"}, {"id": "halteres", "label": "Halteres"},
        {"id": "elastico", "label": "Elásticos"}, {"id": "barra", "label": "Barra fixa"},
        {"id": "kettlebell", "label": "Kettlebell"}, {"id": "banco", "label": "Banco ou cadeira firme"}]
MUSCLE_GROUPS = {
    "pernas": {"label": "Pernas", "muscles": ["quadriceps", "hamstrings", "glutes", "adductors", "abductors", "calves"]},
    "peito": {"label": "Peito", "muscles": ["chest"]},
    "costas": {"label": "Costas", "muscles": ["lats", "middle back", "traps", "lower back"]},
    "ombros": {"label": "Ombros", "muscles": ["shoulders"]},
    "bracos": {"label": "Braços", "muscles": ["biceps", "triceps", "forearms"]},
    "abdomen": {"label": "Abdômen", "muscles": ["abdominals"]},
}

# ------------------------------------------------------------ fora da academia ---
# por padrão de movimento, do melhor para o pior; cada opção diz o que precisa (vazio = só o corpo)
HOME: dict[str, list[tuple[str, set[str], str | None]]] = {
    "squat": [("Goblet_Squat", {"halteres|kettlebell"}, "segure um halter (ou kettlebell) junto ao peito"),
              ("Dumbbell_Squat", {"halteres"}, None),
              ("Bodyweight_Squat", set(), "3 s na descida; até 1–2 repetições do limite")],
    "hinge": [("Stiff-Legged_Dumbbell_Deadlift", {"halteres"}, None),
              ("Kettlebell_One-Legged_Deadlift", {"kettlebell"}, None),
              ("Band_Good_Morning", {"elastico"}, None),
              ("Single_Leg_Glute_Bridge", set(), "uma perna por vez, 2 s parado em cima")],
    "single_leg": [("Split_Squat_with_Dumbbells", {"halteres"}, None),
                   ("Dumbbell_Lunges", {"halteres"}, None),
                   ("Bodyweight_Walking_Lunge", set(), "passos longos e lentos; até perto do limite")],
    "leg_curl": [("Natural_Glute_Ham_Raise", set(), "flexão nórdica: pés presos sob um sofá, desça o mais devagar que conseguir"),
                 ("Seated_Band_Hamstring_Curl", {"elastico"}, None),
                 ("Stiff-Legged_Dumbbell_Deadlift", {"halteres"}, None),
                 ("Single_Leg_Glute_Bridge", set(), "calcanhar longe do quadril para puxar mais o posterior")],
    "leg_ext": [("Split_Squat_with_Dumbbells", {"halteres"}, None),
                ("Bodyweight_Walking_Lunge", set(), "tronco reto, joelho da frente avançando sobre o pé")],
    "glute": [("Single_Leg_Glute_Bridge", set(), "2 s parado em cima, apertando o glúteo"),
              ("Hip_Extension_with_Bands", {"elastico"}, None),
              ("Glute_Kickback", set(), None)],
    "calf": [("Standing_Dumbbell_Calf_Raise", {"halteres"}, "num degrau, amplitude toda"),
             ("Calf_Raises_-_With_Bands", {"elastico"}, None),
             ("Single_Leg_Calf_Raise_Step", set(), "num degrau, uma perna por vez, até perto do limite")],
    "adductor": [("Band_Hip_Adductions", {"elastico"}, None)],
    "h_push": [("Dumbbell_Bench_Press", {"halteres", "banco"}, None),
               ("Dumbbell_Floor_Press", {"halteres"}, "deitado no chão, cotovelo encosta e sobe"),
               ("Bench_Press_-_With_Bands", {"elastico"}, None),
               ("Pushups", set(), "até 1–2 repetições do limite; fácil demais: pés elevados ou mais lento")],
    "incline_push": [("Incline_Dumbbell_Press", {"halteres", "banco"}, None),
                     ("Decline_Push-Up", set(), "pés num sofá ou cadeira: puxa mais o peitoral de cima"),
                     ("Bench_Press_-_With_Bands", {"elastico"}, "puxe o elástico de baixo para cima")],
    "chest_fly": [("Dumbbell_Flyes", {"halteres", "banco"}, None),
                  ("Cross_Over_-_With_Bands", {"elastico"}, None),
                  ("Push-Up_Wide", set(), None)],
    "v_push": [("Dumbbell_Shoulder_Press", {"halteres"}, None),
               ("Arnold_Dumbbell_Press", {"halteres"}, None),
               ("Shoulder_Press_-_With_Bands", {"elastico"}, None),
               ("Decline_Push-Up", set(), "mãos perto, quadril alto (flexão em V) para puxar mais o ombro")],
    "lateral": [("Side_Lateral_Raise", {"halteres"}, None),
                ("Lateral_Raise_-_With_Bands", {"elastico"}, None)],
    "rear_delt": [("Reverse_Flyes", {"halteres"}, None),
                  ("Band_Pull_Apart", {"elastico"}, None),
                  ("Back_Flyes_-_With_Bands", {"elastico"}, None)],
    "v_pull": [("Pullups", {"barra"}, None),
               ("Chin-Up", {"barra"}, None),
               ("Band_Assisted_Pull-Up", {"barra", "elastico"}, None),
               ("One-Arm_Dumbbell_Row", {"halteres"}, "sem barra: remada puxando o cotovelo para trás e para baixo"),
               ("Inverted_Row", set(), "deitado sob uma mesa firme, puxe o peito até a borda")],
    "h_pull": [("One-Arm_Dumbbell_Row", {"halteres"}, None),
               ("Inverted_Row", set(), "deitado sob uma mesa firme, puxe o peito até a borda")],
    "traps": [("Dumbbell_Shrug", {"halteres"}, None)],
    "biceps": [("Dumbbell_Bicep_Curl", {"halteres"}, None), ("Hammer_Curls", {"halteres"}, None),
               ("Chin-Up", {"barra"}, "pegada supinada puxa bastante o bíceps")],
    "triceps": [("Standing_Dumbbell_Triceps_Extension", {"halteres"}, None),
                ("Tricep_Dumbbell_Kickback", {"halteres"}, None),
                ("Push-Ups_-_Close_Triceps_Position", set(), None),
                ("Bench_Dips", {"banco"}, "numa cadeira firme ou sofá")],
    "core": [("Plank", set(), None), ("Dead_Bug", set(), None), ("Reverse_Crunch", set(), None), ("Side_Bridge", set(), None)],
}

# exercícios que carregam a coluna em pé (pesados para quem está cansado ou com dor lombar)
AXIAL = {"Barbell_Squat", "Front_Barbell_Squat", "Barbell_Deadlift", "Bent_Over_Barbell_Row", "Standing_Military_Press",
         "T-Bar_Row_with_Handle", "Barbell_Walking_Lunge", "Smith_Machine_Squat", "Romanian_Deadlift", "Good_Morning"}
COMPOUND_PATTERNS = {"squat", "hinge", "single_leg", "h_push", "incline_push", "v_push", "v_pull", "h_pull"}
# padrão "de base" de cada músculo (o composto, para quando o músculo inteiro está faltando)
MUSCLE_MAIN = {"chest": "h_push", "lats": "v_pull", "middle back": "h_pull", "shoulders": "v_push", "quadriceps": "squat",
               "hamstrings": "hinge", "glutes": "glute", "biceps": "biceps", "triceps": "triceps", "calves": "calf",
               "abdominals": "core", "adductors": "adductor", "traps": "traps"}
NEEDS_BAR = {"Pullups", "Chin-Up", "Band_Assisted_Pull-Up", "Scapular_Pull-Up", "Hanging_Leg_Raise", "Wide-Grip_Rear_Pull-Up",
             "Close-Grip_Front_Lat_Pulldown", "Dips_-_Triceps_Version", "Dips_-_Chest_Version", "Knee_Hip_Raise_On_Parallel_Bars"}
NEEDS_BENCH = {"Dumbbell_Bench_Press", "Incline_Dumbbell_Press", "Dumbbell_Flyes", "Incline_Dumbbell_Curl", "Bench_Dips"}
TEMPLATE_WORDS = {"Superior": "de membros superiores", "Inferior": "de pernas", "Full body": "de corpo inteiro",
                  "Empurrar": "de empurrar (peito, ombro, tríceps)", "Puxar": "de puxar (costas, bíceps)", "Pernas": "de pernas"}
SESSION_MUSCLE_CAP = 8.0   # séries por músculo numa sessão: acima disso o retorno cai muito (Remmert et al., 2023)
SESSION_SETS_CAP = 26      # séries de trabalho numa sessão


# =================================================================== ajudas ===

def _pattern_index() -> dict[str, str]:
    idx = {}
    for pat, ids in G.PATTERNS.items():
        for e in ids:
            idx.setdefault(e, pat)
    for pat, opts in HOME.items():
        for e, _, _ in opts:
            idx.setdefault(e, pat)
    return idx


_PIDX: dict[str, str] | None = None


def pattern_of(eid: str, it: dict | None = None) -> str | None:
    global _PIDX
    if it and it.get("pattern") in G.PATTERNS:
        return it["pattern"]
    if _PIDX is None:
        _PIDX = _pattern_index()
    if eid in _PIDX:
        return _PIDX[eid]
    ex = catalog.get(eid) or {}
    if ex.get("pattern") in G.PATTERNS:
        return ex["pattern"]
    prim = (ex.get("primaryMuscles") or [None])[0]
    if prim and ex.get("mechanic") == "compound" and prim in MUSCLE_MAIN:
        return MUSCLE_MAIN[prim]
    return G.MUSCLE_PATTERN.get(prim) if prim else None


def is_strength(it: dict) -> bool:
    ex = catalog.get(it.get("exercise_id")) or {}
    return (it.get("phase") or "principal") == "principal" and ex.get("kind") == "strength" and bool(it.get("sets"))


def _name(eid: str) -> str:
    return (catalog.get(eid) or {}).get("name") or eid


def _muscles_pt(ms) -> str:
    return ", ".join(MUSCLES.get(m, {}).get("pt", m) for m in ms)


def _region_of(items: list[dict]) -> str | None:
    up = lo = 0
    for it in items:
        ex = catalog.get(it.get("exercise_id")) or {}
        if ex.get("kind") != "strength":
            continue
        prim = set(ex.get("primaryMuscles") or [])
        n = max(1, int(it.get("sets") or 1))
        up += n if prim & G._UPPER_M else 0
        lo += n if prim & G._LOWER_M else 0
    if not up and not lo:
        return None
    return "superior" if up > 2 * lo else "inferior" if lo > 2 * up else "misto"


def _pattern_region(pat: str) -> str:
    ms = set(G.PATTERN_MUSCLES.get(pat, []))
    return "superior" if ms & G._UPPER_M else "inferior" if ms & G._LOWER_M else "neutro"


def session_load(items: list[dict], done_only: bool = False) -> dict[str, float]:
    load: dict[str, float] = defaultdict(float)
    for it in items:
        ex = catalog.get(it.get("exercise_id")) or {}
        if ex.get("kind") != "strength" or (it.get("phase") or "principal") != "principal":
            continue
        sets = it.get("sets")
        n = len([x for x in sets if x.get("done")]) if isinstance(sets, list) and done_only else \
            len(sets) if isinstance(sets, list) else int(sets or 0)
        for m in ex.get("primaryMuscles") or []:
            load[m] += n * PRIMARY_WEIGHT
        for m in ex.get("secondaryMuscles") or []:
            load[m] += n * SECONDARY_WEIGHT
    return load


class Ctx:
    """Tudo o que a escolha precisa saber da pessoa e da semana."""

    def __init__(self, profile: dict, *, sessions: list[dict] | None = None, recovery: dict | None = None,
                 balance: list[dict] | None = None, my_ids: set[str] | None = None, budget: float | None = None,
                 history_ids: set[str] | None = None, seed: int = 0):
        p = profile or {}
        self.profile = p
        self.pers = personal.derive(p)
        self.lims = G.with_personal_lims(p.get("limitations") or [], self.pers)
        months = p.get("training_months")
        self.tier_key = G.tier_for(months if months is not None else {"iniciante": 3, "intermediario": 12, "avancado": 36}.get(p.get("level"), 12))
        self.tier = G.TIERS[self.tier_key]
        self.goal_key = p.get("goal") if p.get("goal") in G.GOALS else "hipertrofia"
        self.goal = G.GOALS[self.goal_key]
        self.reps = self.goal["reps"] or self.tier["reps"]
        if self.tier_key == "iniciante" and self.goal["reps"]:
            self.reps = {k: (v if G._reps_bounds(v)[0] >= 8 else "8-12") for k, v in self.reps.items()}
        self.equipment = set(p.get("equipment") or []) or None
        self.gym = G.gym_type(p)
        self.gym_ok = gyms.checker(p)                      # aparelhos que a academia tem (e o que a pessoa disse)
        self.gym_has = set(p.get("gym_has") or [])
        self.life = life_stage.stage(p, self.tier_key)
        self.budget = float(budget or p.get("session_minutes") or 60)
        self.sessions = sessions or []
        self.recovery = recovery or {}
        self.balance = balance or []
        self.my_ids = my_ids or set()
        self.history_ids = history_ids if history_ids is not None else {
            it.get("exercise_id") for s in self.sessions for it in s.get("items") or [] if it.get("sets")}
        self.seed = int(seed or 0)
        self.kit: set[str] | None = None   # fora da academia: só o que a pessoa tem

    def set_kit(self, kit) -> None:
        self.kit = set(kit or [])
        eq = {"body only"}
        eq |= {"dumbbell"} if "halteres" in self.kit else set()
        eq |= {"kettlebells"} if "kettlebell" in self.kit else set()
        eq |= {"bands"} if "elastico" in self.kit else set()
        self.equipment = eq

    def missing(self) -> dict[str, float]:
        return {b["key"]: float(b["missing"]) for b in self.balance if b.get("missing", 0) > 0}

    def sore(self, threshold: int = 70) -> set[str]:
        return {m for m, r in self.recovery.items() if r.get("ready_pct", 100) < threshold and r.get("hours_since") is not None}

    def rest(self, role: str) -> int:
        return training_rules.rest_for(role, self.tier_key, self.goal["rest"], self.profile.get("rest_pref"))


def _status(eid: str, lims: list[dict]) -> str | None:
    ex = catalog.get(eid)
    if not ex:
        return None
    st = check_exercise(ex, lims)["status"]
    return None if st == "evitar" else st


def _allowed_rare(eid: str, ctx: Ctx) -> bool:
    ex = catalog.get(eid) or {}
    if eid in ctx.my_ids or eid in ctx.gym_has:
        return True
    if not ctx.gym_ok(eid):
        return False
    if ctx.gym == "premium" and ex.get("rotation") is not False:
        return True
    return not (ex.get("rotation") is False or ex.get("availability_br") == "rara")


def candidates(pattern: str, ctx: Ctx, *, lims: list[dict] | None = None, avoid: set[str] | None = None,
               eq_ok=None, rank=None) -> list[tuple[str, str]]:
    """Exercícios do padrão que a pessoa pode fazer: [(id, 'ok'|'cuidado')], do melhor para o pior."""
    lims = ctx.lims if lims is None else lims
    avoid = avoid or set()
    pool = list(G.PATTERNS.get(pattern, []))
    if ctx.gym == "premium" and ctx.kit is None:
        pool += [e for e in G.PREMIUM_EXTRA.get(pattern, []) if e not in pool]
    elif ctx.gym == "basica":
        pool = [e for e in pool if e not in G.MEDIUM_MACHINES] or pool
    pool += [m for m in ctx.my_ids | ctx.gym_has if pattern_of(m) == pattern and m not in pool]
    out = []
    for eid in pool:
        if eid in avoid or eid in ctx.pers["avoid"] or not _allowed_rare(eid, ctx):
            continue
        ex = catalog.get(eid)
        if not ex:
            continue
        if ctx.equipment and ex.get("equipment") not in ctx.equipment and ex.get("equipment") not in (None, "body only", "other"):
            continue
        if ctx.kit is not None and (ex.get("equipment") in ("other", "machine", "cable", "barbell", "e-z curl bar")
                                    or (eid in NEEDS_BAR and "barra" not in ctx.kit) or (eid in NEEDS_BENCH and "banco" not in ctx.kit)):
            continue
        if eq_ok and not eq_ok(ex):
            continue
        st = _status(eid, lims)
        if st:
            out.append((eid, st))
    base = {e: i for i, (e, _) in enumerate(out)}
    prem = {e: i for i, e in enumerate(G.PREMIUM_PREF.get(pattern, []))} if ctx.gym == "premium" and ctx.kit is None else {}

    def key(c):
        eid, st = c
        ex = catalog.get(eid)
        extra = rank(ex) if rank else 0
        return (st != "ok", extra, eid not in ctx.my_ids, prem.get(eid, len(prem)), eid not in ctx.history_ids, base[eid])
    out.sort(key=key)
    return out


def _choose(cands: list[tuple[str, str]], ctx: Ctx, k: int = 0) -> tuple[str, str] | None:
    """O melhor; com "gerar outra" (seed), gira entre os de mesma qualidade."""
    if not cands:
        return None
    top = [c for c in cands if c[1] == cands[0][1]][:3]
    return top[(ctx.seed + k) % len(top)]


def _new_item(old: dict | None, eid: str, ctx: Ctx, role: str | None = None, pattern: str | None = None) -> dict:
    """Item novo no lugar do antigo: mantém séries, papel, descanso; ajusta repetições/tempo ao exercício."""
    ex = catalog.get(eid) or {}
    it = {k: v for k, v in (old or {}).items() if k in ("sets", "reps", "duration_s", "rest_s", "role", "phase", "rpe_target",
                                                        "rir", "superset", "approach_sets", "deload", "focus")}
    it["exercise_id"] = eid
    it["phase"] = "principal"
    it["role"] = role or it.get("role") or ("sec" if ex.get("mechanic") == "compound" else "acc")
    it["pattern"] = pattern or pattern_of(eid) or (old or {}).get("pattern")
    it.setdefault("sets", ctx.tier["sets"][it["role"]])
    it.setdefault("rest_s", ctx.rest(it["role"]))
    it.setdefault("rpe_target", ctx.tier["rpe"])
    it.setdefault("rir", G.RIR[ctx.tier_key][it["role"]])
    if ex.get("timed"):
        it.pop("reps", None)
        it["duration_s"] = it.get("duration_s") or ctx.tier["timed_s"]
    else:
        it.pop("duration_s", None)
        it["reps"] = it.get("reps") or ctx.reps[it["role"]]
    if not old:   # item novo: segue a idade e o sexo (o que veio do plano já seguia)
        life_stage.apply_item(it, ctx.life, ex.get("mechanic") == "compound")
    return it


def _finish(prep: list[dict], main: list[dict], post: list[dict], ctx: Ctx, budget: float) -> list[dict]:
    for k, it in enumerate(main):
        it.pop("approach_sets", None)
        if k == 0 and it.get("role") == "main" and not it.get("duration_s"):
            it["approach_sets"] = True
    G._fix_supersets(main, ctx.rest("acc"))
    items = prep + main + post
    day = {"items": items, "budget": budget}
    G._fit_time([day], budget)
    items = day["items"]
    G._fix_supersets(items, ctx.rest("acc"))
    return items


def _split(items: list[dict]):
    prep = [i for i in items if (i.get("phase") or "principal") == "preparo"]
    post = [i for i in items if i.get("phase") == "pos"]
    main = [i for i in items if (i.get("phase") or "principal") == "principal"]
    return prep, main, post


def _regions(main: list[dict]) -> set[str]:
    regions = set()
    for it in main:
        ex = catalog.get(it["exercise_id"]) or {}
        if ex.get("kind") != "strength":
            continue
        for m in ex.get("primaryMuscles") or []:
            regions.add("core" if MUSCLES.get(m, {}).get("region") == "core" else MUSCLES.get(m, {}).get("region", "superior"))
    return regions or {"superior"}


def _fresh_prep(main: list[dict], ctx: Ctx, home: bool = False) -> list[dict]:
    regions = _regions(main)
    warm_min = 3 if ctx.budget <= 45 else 5
    if home:
        drills = G._prep_items(regions, ctx.lims, ctx.seed, "cardio_esteira_caminhada", warm_min, False, {"body only"})[1:]
        drills = [d for d in drills if (catalog.get(d["exercise_id"]) or {}).get("equipment") in (None, "body only", "bands")
                  and d["exercise_id"] not in NEEDS_BAR and ((catalog.get(d["exercise_id"]) or {}).get("equipment") != "bands" or "elastico" in (ctx.kit or set()))]
        if drills:
            drills[0]["notes"] = f"Antes: {warm_min} min de corrida parada, polichinelo ou subir escada. Depois, este movimento sem cansar."
        return drills
    machine = G._cardio_machine(ctx.lims, [c for c in (ctx.profile.get("cardio_machines") or []) if catalog.get(c)], ctx.seed)
    return G._prep_items(regions, ctx.lims, ctx.seed, machine, warm_min, False, ctx.equipment)


def _sets_total(items: list[dict]) -> int:
    return sum(int(i.get("sets") or 0) for i in items if is_strength(i))


def _minutes(items: list[dict]) -> float:
    return round(G.time_breakdown(items)["total"])


# ================================================================ motivos ===

def _swap_all(main: list[dict], ctx: Ctx, why_for, *, keep=None, lims=None, eq_ok=None, rank=None) -> tuple[list[dict], list[dict]]:
    """Troca cada exercício (que não deve ficar) por outro do mesmo padrão. Devolve (novos itens, mudanças)."""
    used = {i["exercise_id"] for i in main}
    out, changes = [], []
    for k, it in enumerate(main):
        if not is_strength(it) or (keep and keep(it)):
            out.append(it)
            continue
        pat = pattern_of(it["exercise_id"], it)
        cands = candidates(pat, ctx, lims=lims, avoid=used, eq_ok=eq_ok, rank=rank) if pat else []
        ch = _choose(cands, ctx, k)
        if not ch:
            out.append(it)
            continue
        used.add(ch[0])
        new = _new_item(it, ch[0], ctx, pattern=pat)
        if ch[1] == "cuidado":
            new["notes"] = "cuidado: carga moderada e amplitude sem dor"
        out.append(new)
        changes.append({"type": "troca", "from": it["exercise_id"], "to": ch[0], "why": why_for(it, ch[0])})
    return out, changes


def _r_ocupado(main, ctx, opts):
    busy = {"barbell", "machine", "e-z curl bar", "other", None}
    pref = {"dumbbell": 0, "cable": 1, "body only": 2, "kettlebells": 3, "bands": 4, "machine": 5}

    def eq_ok(ex):
        return ex.get("equipment") not in ("barbell", "e-z curl bar")

    def rank(ex):
        return pref.get(ex.get("equipment"), 9)

    def keep(it):
        return (catalog.get(it["exercise_id"]) or {}).get("equipment") not in busy

    out, changes = _swap_all(main, ctx, lambda it, to: f"{(catalog.get(to) or {}).get('equipment_pt') or 'livre'}: não depende de rack nem de máquina",
                             keep=keep, eq_ok=eq_ok, rank=rank)
    for it in out:
        if is_strength(it) and (catalog.get(it["exercise_id"]) or {}).get("equipment") in ("barbell", "e-z curl bar"):
            changes.append({"type": "ajuste", "from": it["exercise_id"], "to": it["exercise_id"],
                            "why": "fica: não há outra opção que respeite suas limitações; faça quando a barra liberar"})
    # mesma estação seguida: acessórios de polia juntos, de halteres juntos (menos troca de lugar)
    head = [i for i in out if i.get("role") != "acc"]
    acc = sorted([i for i in out if i.get("role") == "acc"], key=lambda i: rank(catalog.get(i["exercise_id"]) or {}))
    out = head + acc
    G._pair_supersets(out)
    why = ["Troquei o que usa barra ou máquina por halteres, polia e peso do corpo: um par de halteres e um banco "
           "fazem quase tudo, sem fila de rack.",
           "Os acessórios ficaram agrupados pelo mesmo equipamento e em bi-set, para você não precisar trocar de lugar a cada exercício."]
    return out, changes, why, None


def _r_tempo(main, ctx, opts):
    out = copy.deepcopy(main)
    changes = []
    n0 = _sets_total(out)
    pairs = G._pair_supersets(out)
    for it in out:
        if it.get("role") == "acc" and (it.get("rest_s") or 0) > 60 and it.get("superset") != "A":
            it["rest_s"] = 60
        if it.get("role") == "sec" and (it.get("rest_s") or 0) > 90:
            it["rest_s"] = 90
    why = ["Os exercícios principais ficaram (são os que mais contam para força e massa).",
           "Acessórios em bi-set (dois exercícios alternados, um descanso só)" + (f": {pairs} par(es)" if pairs else "") +
           ", descanso mais curto e menos séries onde dava.",
           "2–3 séries perto da falha por exercício já mantêm e fazem progredir."]
    return out, changes, why, {"n0": n0}


def _r_cansado(main, ctx, opts):
    def keep(it):
        return it["exercise_id"] not in AXIAL

    def eq_ok(ex):
        return ex["id"] not in AXIAL

    def rank(ex):
        return {"machine": 0, "cable": 1, "dumbbell": 2}.get(ex.get("equipment"), 5)

    out, changes = _swap_all(main, ctx, lambda it, to: "com apoio: exige menos da lombar e do equilíbrio", keep=keep, eq_ok=eq_ok, rank=rank)
    for it in out:
        if not is_strength(it):
            continue
        s = int(it["sets"])
        new = max(2, s - (1 if s <= 3 else 2)) if it.get("role") in ("main", "sec") else max(1, s - 1)
        if new < s:
            changes.append({"type": "ajuste", "from": it["exercise_id"], "to": it["exercise_id"], "why": f"{s} → {new} séries"})
            it["sets"] = new
        it["rir"] = "2–3 (pare antes da falha)"
        it["rpe_target"] = min(float(it.get("rpe_target") or 8), 7)
        if it.get("notes"):
            it["notes"] = re.sub(r"\s*·?\s*(última série: drop set[^·]*|top set:[^·]*)", "", it["notes"]).strip(" ·") or None
    before = _sets_total(main)
    while _sets_total(out) > 0.75 * before:
        accs = [i for i in out if is_strength(i) and i.get("role") == "acc"]
        if not accs or len([i for i in out if is_strength(i)]) <= 3:
            break
        last = accs[-1]
        out.remove(last)
        changes = [c for c in changes if c.get("from") != last["exercise_id"]]
        changes.append({"type": "sai", "from": last["exercise_id"], "to": None, "why": "menos volume hoje (cansaço)"})
    why = ["Cerca de 1/3 menos séries, parando 2–3 repetições antes da falha: o estímulo continua, a fadiga cai bastante.",
           "Exercícios que carregam a coluna em pé trocados por versões com apoio (máquina, polia, banco)."]
    return out, changes, why, None


def _r_dor(main, ctx, opts):
    region = opts.get("region")
    if region not in LIMITATIONS:
        raise ValueError("Diga onde está doendo")
    lims = ctx.lims + [{"key": region, "severity": "evitar"}]
    used = {i["exercise_id"] for i in main}
    out, changes = [], []
    label = LIMITATIONS[region]["label"].split(" (")[0].lower()
    for k, it in enumerate(main):
        if not is_strength(it):
            out.append(it)
            continue
        st = check_exercise(catalog.get(it["exercise_id"]), lims)["status"]
        if st == "ok":
            out.append(it)
            continue
        pat = pattern_of(it["exercise_id"], it)
        cands = candidates(pat, ctx, lims=lims, avoid=used) if pat else []
        ok = [c for c in cands if c[1] == "ok"]
        ch = _choose(ok, ctx, k) or (_choose(cands, ctx, k) if st == "evitar" else None)
        if ch and ch[0] != it["exercise_id"]:
            used.add(ch[0])
            new = _new_item(it, ch[0], ctx, pattern=pat)
            if ch[1] == "cuidado":
                new["notes"] = f"cuidado com o {label}: carga moderada e só a amplitude sem dor"
            out.append(new)
            changes.append({"type": "troca", "from": it["exercise_id"], "to": ch[0], "why": f"poupa o {label}"})
        elif st == "cuidado":
            it = dict(it)
            it["notes"] = f"cuidado com o {label}: carga moderada e só a amplitude sem dor"
            out.append(it)
            changes.append({"type": "ajuste", "from": it["exercise_id"], "to": it["exercise_id"], "why": f"fica, com cuidado no {label}"})
        else:
            changes.append({"type": "sai", "from": it["exercise_id"], "to": None, "why": f"não há versão segura para o {label} hoje"})
    # um exercício de proteção da região, se houver (rotação externa, face pull, ponte de glúteo…)
    for alt in LIMITATIONS[region].get("alternatives", []):
        ex = catalog.get(alt)
        if ex and ex.get("kind") == "strength" and alt not in used and check_exercise(ex, lims)["status"] == "ok":
            new = _new_item(None, alt, ctx, role="acc")
            new["sets"] = 2
            new["notes"] = f"proteção do {label}: leve, controlado, sem dor"
            out.append(new)
            changes.append({"type": "entra", "from": None, "to": alt, "why": f"fortalece em volta do {label} sem forçar"})
            break
    why = [f"Saiu ou mudou o que costuma piorar dor no {label}; o resto do corpo treina normalmente.",
           "Regra de ouro: sem dor durante e no dia seguinte. Dor forte, que piora, com inchaço ou formigamento: pare e "
           "procure um profissional de saúde."]
    if not any(c["type"] != "entra" for c in changes):
        why.insert(0, f"Nada do treino de hoje força o {label}: ficou igual, com um exercício de proteção a mais.")
    return out, changes, why, None


def _sore_from(ctx: Ctx, opts: dict) -> set[str]:
    groups = opts.get("muscles")
    if groups:
        return {m for g in groups for m in MUSCLE_GROUPS.get(g, {}).get("muscles", [])}
    return ctx.sore(70)


def build_template(key: str, ctx: Ctx, *, avoid_muscles: set[str] | None = None, avoid_ids: set[str] | None = None,
                   variant: int = 0) -> list[dict]:
    """Os exercícios principais de um molde do gerador (Superior A, Pernas, Full body…), pelo nível da pessoa."""
    picker = G._Picker(ctx.tier, ctx.lims, ctx.equipment, ctx.seed, ctx.gym, ctx.profile)
    used = set(avoid_ids or set())
    out = []
    for pattern, role in G.DAY_TEMPLATES[key][1]:
        if avoid_muscles and set(G.PATTERN_MUSCLES.get(pattern, [])) & avoid_muscles:
            continue
        cands = candidates(pattern, ctx, avoid=used, eq_ok=(lambda ex: not (set(ex.get("primaryMuscles") or []) & avoid_muscles))
                           if avoid_muscles else None)
        ch = _choose(cands, ctx, variant + len(out)) if cands else picker.pick(pattern, role, used, variant)
        if not ch:
            continue
        used.add(ch[0])
        out.append(_new_item(None, ch[0], ctx, role=role, pattern=pattern))
    return out


def _best_template(ctx: Ctx, *, avoid: set[str], prefer: set[str] | None = None, region: str | None = None) -> str | None:
    miss = ctx.missing()
    best, best_s = None, -1e9
    for key, (_, slots) in G.DAY_TEMPLATES.items():
        heavy = {m for p, r in slots if r in ("main", "sec") for m in G.PATTERN_MUSCLES[p]}
        allm = {m for p, _ in slots for m in G.PATTERN_MUSCLES[p]}
        if heavy & avoid:
            continue
        s = sum(miss.get(m, 0) for m in allm - avoid) + (3 if prefer and key in prefer else 0)
        s -= 2 * len(allm & avoid)
        if region and G._TEMPLATE_REGION.get(key) not in (region, "misto"):
            s -= 4
        if s > best_s:
            best, best_s = key, s
    return best


def _r_dolorido(main, ctx, opts):
    sore = _sore_from(ctx, opts)
    if not sore:
        raise ValueError("Diga qual parte está dolorida")
    sore_sets = sum(int(i["sets"]) for i in main if is_strength(i)
                    and set((catalog.get(i["exercise_id"]) or {}).get("primaryMuscles") or []) & sore)
    total = max(1, _sets_total(main))
    changes = []
    if sore_sets / total >= 0.5:
        # o treino de hoje é justamente do que está dolorido: outro treino, de músculos recuperados e que faltam na semana
        key = _best_template(ctx, avoid=sore, prefer=set(opts.get("templates") or []))
        if not key:
            raise ValueError("Tudo o que dá para treinar está dolorido: hoje vale descanso ou cardio leve")
        new = build_template(key, ctx, avoid_muscles=sore)
        for it in main:
            if is_strength(it):
                changes.append({"type": "sai", "from": it["exercise_id"], "to": None, "why": "músculo ainda dolorido"})
        for it in new:
            changes.append({"type": "entra", "from": None, "to": it["exercise_id"], "why": "músculo recuperado"})
        why = [f"O treino de hoje era quase todo de {_muscles_pt(sore)}, que ainda está dolorido: troquei por "
               f"{G.DAY_TEMPLATES[key][0]}, com músculos recuperados e que estão faltando na semana.",
               "Volte ao treino original quando a dor ceder (em geral 48–72 h); ele continua no plano."]
        return new, changes, why, {"day_name": G.DAY_TEMPLATES[key][0]}
    out = []
    used = {i["exercise_id"] for i in main}
    miss = ctx.missing()
    region = _region_of(main)
    for it in main:
        prim = set((catalog.get(it["exercise_id"]) or {}).get("primaryMuscles") or [])
        if not is_strength(it) or not (prim & sore):
            out.append(it)
            continue
        # no lugar: um padrão da mesma região, de músculo recuperado, o que mais falta na semana
        opts_p = [p for p in G.PATTERNS if not (set(G.PATTERN_MUSCLES.get(p, [])) & sore)
                  and _pattern_region(p) in (region, "neutro") and p not in {pattern_of(x["exercise_id"], x) for x in out}]
        opts_p.sort(key=lambda p: -sum(miss.get(m, 0) for m in G.PATTERN_MUSCLES.get(p, [])))
        placed = False
        for p in opts_p:
            ch = _choose(candidates(p, ctx, avoid=used, eq_ok=lambda ex: not (set(ex.get("primaryMuscles") or []) & sore)), ctx)
            if ch:
                used.add(ch[0])
                out.append(_new_item(it, ch[0], ctx, pattern=p, role="acc" if p not in COMPOUND_PATTERNS else it.get("role")))
                changes.append({"type": "troca", "from": it["exercise_id"], "to": ch[0],
                                "why": f"{_muscles_pt(prim & sore)} ainda dolorido; {_muscles_pt(G.PATTERN_MUSCLES.get(p, []))} está recuperado"})
                placed = True
                break
        if not placed:
            changes.append({"type": "sai", "from": it["exercise_id"], "to": None, "why": "músculo ainda dolorido"})
    why = [f"{_muscles_pt(sore).capitalize()} ainda está se recuperando: saiu do treino de hoje, e no lugar entrou o "
           "que está recuperado e faltando na semana.",
           "Dor muscular de 1–3 dias depois do treino é normal; treinar o mesmo músculo pesado agora rende menos."]
    return out, changes, why, None


def _r_variar(main, ctx, opts):
    recent = {it.get("exercise_id") for s in ctx.sessions[-12:] for it in s.get("items") or []}

    def rank(ex):
        return 1 if ex["id"] in recent else 0

    out, changes = _swap_all(main, ctx, lambda it, to: "variação do mesmo movimento",
                             keep=lambda it: it.get("role") == "main", rank=rank)
    why = ["Secundários e acessórios trocados por outros do mesmo movimento (de preferência os que você não fez nas "
           "últimas semanas).",
           "Os exercícios principais ficaram: são eles que mostram se a carga está subindo."]
    return out, changes, why, None


def _kit_ok(needs: set[str], kit: set[str]) -> bool:
    for n in needs:
        if not any(x in kit for x in n.split("|")):
            return False
    return True


def _r_fora(main, ctx, opts):
    kit = set(opts.get("kit") or [])
    ctx.set_kit(kit)
    out, changes = [], []
    used: set[str] = set()
    for k, it in enumerate(main):
        if not is_strength(it):
            if (catalog.get(it.get("exercise_id")) or {}).get("kind") == "cardio":
                changes.append({"type": "sai", "from": it["exercise_id"], "to": None, "why": "aparelho de cardio"})
                continue
            out.append(it)
            continue
        pat = pattern_of(it["exercise_id"], it)
        opts_h = [(e, n, note) for e, n, note in HOME.get(pat, []) if _kit_ok(n, kit) and e not in used
                  and catalog.get(e) and _status(e, ctx.lims)]
        if not opts_h and pat in ("v_pull",):
            opts_h = [(e, n, note) for e, n, note in HOME["h_pull"] if _kit_ok(n, kit) and e not in used and _status(e, ctx.lims)]
        if not opts_h:
            changes.append({"type": "sai", "from": it["exercise_id"], "to": None,
                            "why": "sem equipamento para esse movimento" + (" (com halteres ou elástico dá)" if not kit else "")})
            continue
        eid, _, note = opts_h[(ctx.seed if k else 0) % min(2, len(opts_h))] if len(opts_h) > 1 and ctx.seed else opts_h[0]
        used.add(eid)
        new = _new_item(it, eid, ctx, pattern=pat)
        loaded = (catalog.get(eid) or {}).get("equipment") in ("dumbbell", "kettlebells")
        if not new.get("duration_s"):
            # halter de casa é leve: 8–12 no principal; sem carga, séries longas até perto do limite
            new["reps"] = ("8-12" if new.get("role") == "main" else "10-15") if loaded else ("10-15" if new.get("role") == "main" else "12-20")
            new["rir"] = "1–2 (perto do limite: com carga leve, é isso que dá o estímulo)"
            new.pop("approach_sets", None)
            if new.get("notes") is None and new.get("role") == "main":
                new["notes"] = "3 s na descida"
        new["notes"] = note or new.get("notes")
        new["rest_s"] = min(int(new.get("rest_s") or 90), 90)
        out.append(new)
        changes.append({"type": "troca", "from": it["exercise_id"], "to": eid, "why": "dá para fazer com o que você tem"})
    have = ", ".join(x["label"].lower() for x in KITS if x["id"] in kit) or "só o peso do corpo"
    why = [f"Montado com {have}: cada exercício trocado pelo movimento mais parecido que dá para fazer aí.",
           "Sem carga alta, o estímulo vem de levar a série até perto do limite (1–2 repetições sobrando): o ganho é parecido."]
    return out, changes, why, {"home": True}


def _r_falta(main, ctx, opts, today_load: dict | None = None):
    miss = dict(ctx.missing())
    for m, v in (today_load or {}).items():  # o treino de hoje sai: o que ele daria volta a faltar
        miss[m] = miss.get(m, 0) + v
    sore = ctx.sore(60)
    ranked = [m for m, v in sorted(miss.items(), key=lambda kv: -kv[1]) if v >= 2 and m not in sore and m in MUSCLE_MAIN]
    if not ranked:
        out, changes, why, extra = _r_variar(main, ctx, opts)
        return out, changes, ["A semana está em dia: nada está faltando. Aqui vai uma variação do treino de hoje."] + why[1:], extra
    out, changes, used, pats = [], [], set(), set()
    budget_sets = max(10, min(SESSION_SETS_CAP - 4, int(ctx.budget / 3.2)))
    for m in ranked:
        if _sets_total(out) >= budget_sets:
            break
        for p in ([MUSCLE_MAIN[m]] + ([G.MUSCLE_PATTERN[m]] if G.MUSCLE_PATTERN.get(m) != MUSCLE_MAIN[m] else [])):
            if p in pats:
                continue
            ch = _choose(candidates(p, ctx, avoid=used), ctx, len(out))
            if not ch:
                continue
            role = "main" if p in COMPOUND_PATTERNS and not any(i.get("role") == "main" for i in out) else \
                ("sec" if p in COMPOUND_PATTERNS else "acc")
            it = _new_item(None, ch[0], ctx, role=role, pattern=p)
            it["sets"] = int(max(2, min(it["sets"], round(miss[m]))))
            used.add(ch[0])
            pats.add(p)
            out.append(it)
            changes.append({"type": "entra", "from": None, "to": ch[0],
                            "why": f"{MUSCLES.get(m, {}).get('pt', m)}: faltam ~{round(miss[m])} séries na semana"})
            break
    out.sort(key=lambda i: {"main": 0, "sec": 1, "acc": 2}.get(i.get("role"), 3))
    for it in main:
        if is_strength(it) and it["exercise_id"] not in used:
            changes.append({"type": "sai", "from": it["exercise_id"], "to": None, "why": "músculo já em dia na semana"})
    why = [f"Montado com o que mais falta na semana: {_muscles_pt(ranked[:4])}.",
           "Músculos que ainda estão se recuperando ficaram de fora."]
    return out, changes, why, {"day_name": "Treino do que falta"}


def _r_doente(main, ctx, opts):
    if opts.get("sick") != "pescoco":
        raise ValueError("Com febre, dor no corpo ou sintomas no peito ou no estômago, hoje é descanso: treinar agora atrasa a "
                         "recuperação e pode ser perigoso para o coração. Volte 24–48 h depois de a febre passar, começando leve.")
    out, changes, why, extra = _r_cansado(main, ctx, opts)
    for it in out:
        if not is_strength(it):
            continue
        s = int(it["sets"])
        new = max(1, round(s * 0.6)) if it.get("role") != "main" else max(2, round(s * 0.6))
        if new < s:
            it["sets"] = new
        it["rpe_target"] = min(float(it.get("rpe_target") or 6), 6)
        it["rir"] = "3–4 (bem longe da falha)"
    while _sets_total(out) > 0.6 * max(1, _sets_total(main)):
        accs = [i for i in out if is_strength(i) and i.get("role") == "acc"]
        if not accs or len([i for i in out if is_strength(i)]) <= 3:
            break
        last = accs[-1]
        out.remove(last)
        changes.append({"type": "sai", "from": last["exercise_id"], "to": None, "why": "menos volume enquanto se recupera"})
    why = ["Resfriado sem febre (só acima do pescoço): cerca de 60% do volume, esforço até 6 de 10, nada de tiros.",
           "Se piorar durante o treino, pare. Febre, dor no corpo ou falta de ar: descanso e, se persistir, procure atendimento."]
    return out, changes, why, None


HANDLERS = {"doente": _r_doente, "ocupado": _r_ocupado, "tempo": _r_tempo, "cansado": _r_cansado, "dor": _r_dor, "dolorido": _r_dolorido,
            "variar": _r_variar, "fora": _r_fora, "falta": _r_falta}


# ============================================================== montar opção ===

def propose(items: list[dict], reason: str, ctx: Ctx, opts: dict | None = None, day_name: str | None = None) -> dict:
    """Outra versão do treino (itens no formato da agenda) e o porquê de cada mudança."""
    opts = opts or {}
    if reason not in HANDLERS:
        raise ValueError("Motivo desconhecido")
    original = copy.deepcopy(items or [])
    before_min, before = _minutes(original), _sets_total(original)
    strength_orig = copy.deepcopy([i for i in original if (i.get("phase") or "principal") == "principal" and is_strength(i)])
    prep, main, post = _split(copy.deepcopy(original))
    budget = float(opts.get("minutes") or ctx.budget)
    if reason == "tempo" and not opts.get("minutes"):
        budget = max(15.0, round(ctx.budget * 0.6 / 5) * 5)
    ctx.budget = budget
    strength_main = [i for i in main if is_strength(i)]
    other_main = [i for i in main if not is_strength(i)]   # cardio/esporte do dia: fica como está (sai fora da academia)
    if reason == "falta":
        new_main, changes, why, extra = _r_falta(strength_main, ctx, opts, today_load=session_load(strength_main))
    else:
        new_main, changes, why, extra = HANDLERS[reason](strength_main, ctx, opts)
    extra = extra or {}
    home = bool(extra.get("home"))
    if not home:
        new_main = new_main + [i for i in other_main if not i.get("endurance") or reason != "tempo"]
    regions_changed = _regions([i for i in new_main if is_strength(i)]) != _regions(strength_main)
    new_prep = _fresh_prep(new_main, ctx, home=True) if home else (_fresh_prep(new_main, ctx) if regions_changed or not prep else prep)
    if reason == "tempo":
        new_prep = [p for p in new_prep if p.get("ramp") != "potencializar"]
        warm = next((p for p in new_prep if p.get("duration_min")), None)
        if warm:
            warm["duration_min"] = 3
        new_prep = [p for p in new_prep if p.get("duration_min")] + [p for p in new_prep if not p.get("duration_min")][:2]
    if reason == "cansado":
        new_prep = [p for p in new_prep if p.get("ramp") != "potencializar"]
    new_post = G._post_items(G._muscle_load(new_main), ctx.lims) if regions_changed or home else post
    if reason == "tempo":
        new_post = new_post[:2]
    out_items = _finish(new_prep, new_main, new_post, ctx, budget)
    # o que o ajuste de tempo cortou também aparece como mudança
    kept = {i["exercise_id"] for i in out_items}
    for it in new_main:
        if is_strength(it) and it["exercise_id"] not in kept and not any(c.get("to") == it["exercise_id"] and c["type"] == "sai" for c in changes):
            changes.append({"type": "sai", "from": it["exercise_id"], "to": None, "why": f"não coube em {round(budget)} min"})
            changes = [c for c in changes if not (c["type"] in ("entra", "troca") and c.get("to") == it["exercise_id"])]
    for it in out_items:
        if not is_strength(it):
            continue
        prev = next((x for x in new_main if x["exercise_id"] == it["exercise_id"]), None)
        orig = next((x for x in strength_orig if x["exercise_id"] == it["exercise_id"]), None)
        if orig and prev and int(it["sets"]) < int(orig.get("sets") or 0) and not any(
                c["type"] == "ajuste" and c["from"] == it["exercise_id"] for c in changes):
            changes.append({"type": "ajuste", "from": it["exercise_id"], "to": it["exercise_id"],
                            "why": f"{orig['sets']} → {it['sets']} séries"})
    after = _sets_total(out_items)
    warnings = []
    if not any(is_strength(i) for i in out_items):
        warnings.append("Não sobrou exercício de força com essas condições. Hoje vale cardio leve, mobilidade ou descanso.")
    elif after < max(4, before * 0.4) and reason not in ("tempo", "cansado"):
        warnings.append("Ficou bem menor que o treino original: considere completar com um exercício extra sugerido.")
    mins = _minutes(out_items)
    if mins > budget * 1.1:
        warnings.append(f"Mesmo enxugando, fica em ~{round(mins)} min (mais que os {round(budget)} min que você tem).")
    names = {c["from"] for c in changes if c.get("from")} | {c["to"] for c in changes if c.get("to")}
    for c in changes:
        c["from_name"] = _name(c["from"]) if c.get("from") else None
        c["to_name"] = _name(c["to"]) if c.get("to") else None
    rinfo = next(r for r in REASONS if r["id"] == reason)
    base_name = re.sub(r"\s*\((?:alternativo|variação)[^)]*\)\s*$", "", day_name or "Treino").strip() or "Treino"
    return {"reason": reason, "label": rinfo["label"], "why": why, "base": rinfo["base"], "changes": changes,
            "items": out_items, "day_name": extra.get("day_name") and f"{extra['day_name']} (alternativo)" or f"{base_name} (alternativo)",
            "minutes": {"before": before_min, "after": mins, "budget": round(budget)},
            "sets": {"before": before, "after": after}, "warnings": warnings, "names": len(names)}


# ================================================================ sugestões ===

def _template_order() -> dict[str, list[tuple[str, str]]]:
    return {k: v[1] for k, v in G.DAY_TEMPLATES.items()}


def next_exercises(session_items: list[dict], ctx: Ctx, n: int = 3, extra: bool = False) -> dict:
    """O que faz sentido fazer em seguida (ou como extra), com o porquê."""
    items = [i for i in session_items if (catalog.get(i.get("exercise_id")) or {}).get("kind") == "strength"]
    have_ids = {i["exercise_id"] for i in session_items}
    have_p = [pattern_of(i["exercise_id"], i) for i in items]
    have_set = {p for p in have_p if p}
    load = session_load(items)
    total_sets = sum(len(i["sets"]) if isinstance(i.get("sets"), list) else int(i.get("sets") or 0) for i in items)
    used_min = round(sum(G.item_time({**i, "sets": len(i["sets"]) if isinstance(i.get("sets"), list) else i.get("sets"),
                                      "phase": "principal"})["total"] for i in items) / 60)
    region = _region_of(items)
    miss = ctx.missing()
    sore = ctx.sore(70)
    # o molde mais parecido com o que já foi feito (ou, com a sessão vazia, o que mais repõe a semana)
    best, best_s = None, -1e9
    for key, slots in _template_order().items():
        pats = [p for p, _ in slots]
        overlap = len(have_set & set(pats))
        if items and not overlap:
            continue
        s = overlap * 10 + sum(miss.get(m, 0) for p in pats if p not in have_set for m in G.PATTERN_MUSCLES[p]) * 0.3
        s -= sum(2 for p in pats if p not in have_set and set(G.PATTERN_MUSCLES[p]) & sore)
        if region in ("superior", "inferior") and G._TEMPLATE_REGION.get(key) not in (region,):
            s -= 6 if G._TEMPLATE_REGION.get(key) == "misto" else 30
        if s > best_s:
            best, best_s = key, s
    slots = [p for p, _ in G.DAY_TEMPLATES[best][1]] if best else []
    roles = dict((p, r) for p, r in reversed(G.DAY_TEMPLATES[best][1])) if best else {}
    done = {"done": False, "message": None}
    if items and (used_min >= ctx.budget * 1.05 or total_sets >= SESSION_SETS_CAP):
        done = {"done": True, "message": f"Treino já completo: {total_sets} séries, ~{used_min} min. Mais que isso rende pouco "
                                          "hoje; se quiser, um abdômen ou alongamento para fechar."}
    cand_p = list(dict.fromkeys(slots + [MUSCLE_MAIN[m] for m in miss if m in MUSCLE_MAIN] +
                                [G.MUSCLE_PATTERN[m] for m in miss if m in G.MUSCLE_PATTERN] + ["core"] + list(G.PATTERNS)))
    tname = None
    if best:
        base_n = re.sub(r"\s+[A-C]$", "", G.DAY_TEMPLATES[best][0].split(" (")[0])
        tname = TEMPLATE_WORDS.get(base_n, base_n.lower())
    last_p = have_p[-1] if have_p else None
    scored = []
    for p in cand_p:
        if p in have_set and p not in ("triceps", "biceps", "h_pull"):
            continue
        ms = G.PATTERN_MUSCLES.get(p, [])
        preg = _pattern_region(p)
        s, why = 0.0, []
        if p in slots:
            pos = [k for k, x in enumerate(slots) if x not in have_set or x == p]
            rank_in = pos.index(slots.index(p)) if slots.index(p) in pos else len(slots)
            s += max(0.0, 6 - rank_in * 0.8)
            if items:
                why.append(f"completa o treino {tname}")
        m_need = sum(min(miss.get(m, 0), 8) for m in ms)
        if m_need:
            s += m_need * 0.6
            top = max(ms, key=lambda m: miss.get(m, 0))
            if miss.get(top, 0) >= 2:
                why.append(f"faltam ~{round(miss[top])} séries de {MUSCLES.get(top, {}).get('pt', top)} na semana")
        sat = max((load.get(m, 0) for m in ms), default=0)
        if sat >= SESSION_MUSCLE_CAP:
            continue
        if p not in slots:
            s -= 2   # fora do molde: só entra se fizer falta (músculo que ainda não trabalhou, semana atrasada)
            if items and ms and all(load.get(m, 0) == 0 for m in ms):
                s += 1.5
                why.append(f"hoje ainda não teve exercício para {_muscles_pt(ms[:1]).lower()}")
        if sat >= 6:
            s -= 3
        if any(m in sore for m in ms):
            sp = min(ctx.recovery.get(m, {}).get("ready_pct", 100) for m in ms)
            if sp < 50:
                continue
            s -= 6
        if region in ("superior", "inferior") and preg not in (region, "neutro"):
            if len(items) >= 3:
                continue
            s -= 12
        if p in COMPOUND_PATTERNS:
            n_comp = sum(1 for x in have_p if x in COMPOUND_PATTERNS)
            s += 2.5 if n_comp < 2 else (-1.5 if len(items) >= 3 else 0)
        elif not items:
            s -= 3   # começar por isolado não faz sentido
        if not items and p in COMPOUND_PATTERNS:
            why.append("comece pelo exercício grande, enquanto você está descansado")
        if last_p and any((last_p, p) == pair or (p, last_p) == pair for pair in G.SUPERSET_PAIRS):
            s += 1.5
            why.append(f"dá para alternar em bi-set com {_name(items[-1]['exercise_id']).lower()}")
        if p == "core":
            s -= 2 if len(items) < 3 else 0
            if not why:
                why.append("fecha o treino sem cansar o que você acabou de treinar")
        if done["done"] and p != "core":
            continue
        scored.append((s, p, why))
    scored.sort(key=lambda x: -x[0])
    out = []
    used = set(have_ids)
    for s, p, why in scored:
        if len(out) >= n:
            break
        ch = _choose(candidates(p, ctx, avoid=used), ctx, len(out))
        if not ch:
            continue
        used.add(ch[0])
        role = roles.get(p) or ("sec" if p in COMPOUND_PATTERNS else "acc")
        if items and role == "main":
            role = "sec"
        it = _new_item(None, ch[0], ctx, role=role, pattern=p)
        sat = max((load.get(m, 0) for m in G.PATTERN_MUSCLES.get(p, [])), default=0)
        if sat >= 4 or extra:
            it["sets"] = min(int(it["sets"]), 2 if sat >= 4 else 3)
        if ch[1] == "cuidado":
            it["notes"] = "cuidado: carga moderada e amplitude sem dor"
        if not why:
            why = ["equilibra o treino de hoje"]
        has_load = ch[0] in ctx.history_ids
        out.append({**it, "name": _name(ch[0]), "why": why[0][0].upper() + why[0][1:] + (". " + ". ".join(w.capitalize() for w in why[1:]) if len(why) > 1 else "") + ".",
                    "known": has_load, "minutes": round(G.item_time(it)["total"] / 60)})
    if not out and not done["done"]:
        done = {"done": True, "message": "O treino de hoje já cobre o que precisava. Se quiser mais, um abdômen ou alongamento fecha bem."}
    return {"suggestions": out, **done, "template": tname,
            "minutes_used": used_min, "budget": round(ctx.budget), "sets": total_sets}
