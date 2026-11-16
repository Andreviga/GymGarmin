"""Gera gymgarmin/data/garmin_map.json: free-exercise-db -> catálogo de exercícios do Garmin Connect.

Cada exercício do free-exercise-db (yuhonas) recebe o par (category, exercise) do
catálogo do Garmin Connect que melhor representa o movimento. O relógio usa esse
par para mostrar o nome/animação do exercício e contar repetições.

Uso:  python scripts/build_garmin_map.py
Os mapeamentos podem ser corrigidos manualmente no app (tabela garmin_override).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "gymgarmin" / "data"

SYNONYMS = {
    "dumbbells": "dumbbell", "db": "dumbbell", "kettlebells": "kettlebell", "kb": "kettlebell",
    "pushup": "push up", "pushups": "push up", "pullup": "pull up", "pullups": "pull up",
    "chinup": "chin up", "chinups": "chin up", "situp": "sit up", "situps": "sit up",
    "flyes": "flye", "flys": "flye", "fly": "flye", "flies": "flye",
    "pulldown": "pull down", "pulldowns": "pull down", "pushdown": "push down", "pushdowns": "push down",
    "skullcrusher": "skull crusher", "skullcrushers": "skull crusher",
    "stepup": "step up", "stepups": "step up", "goodmorning": "good morning",
    "ez": "ez", "e": "", "z": "", "bar": "bar", "smith": "smith", "machine": "machine",
    "glute": "glute", "glutes": "glute", "triceps": "triceps", "tricep": "triceps",
    "biceps": "biceps", "bicep": "biceps", "abs": "ab", "lat": "lat", "lats": "lat",
}
KEEP_S = {"press", "cross", "triceps", "biceps", "abs", "across", "chess", "bus", "sus", "glutes"}
STOP = {"the", "a", "with", "on", "to", "and", "of", "for", "in", "using", "your", "-"}

# variações que mudam o exercício: se o Garmin cita e o exercício não, penaliza
MODIFIERS = {"close", "wide", "reverse", "single", "one", "alternating", "incline", "decline", "seated",
             "standing", "lying", "kneeling", "weighted", "side", "behind", "overhead", "foam", "roller",
             "swiss", "suspension", "suspended", "ring", "plyo", "jump", "cross", "body", "isometric", "hold",
             "legged", "to", "lunge", "squat", "press", "curl", "row"}

EQUIP_WORDS = {"barbell", "dumbbell", "kettlebell", "cable", "band", "banded", "smith", "machine",
               "medicine", "ball", "swiss", "ez", "trx", "suspension", "sandbag"}

# palavra-chave do movimento -> categoria Garmin (ordem importa: mais específico primeiro)
KEYWORD_CATEGORY: list[tuple[str, str]] = [
    (r"battling rope|battle rope|rope wave", "BATTLE_ROPE"),
    (r"leg curl|hamstring curl|lying curl|seated leg curl|nordic", "LEG_CURL"),
    (r"bench press|chest press|floor press|board press", "BENCH_PRESS"),
    (r"calf|calves|donkey", "CALF_RAISE"),
    (r"farmer|carry|walk with|suitcase", "CARRY"),
    (r"chop|woodchop|wood chop", "CHOP"),
    (r"crunch", "CRUNCH"),
    (r"sit up|situp|v up|jackknife", "SIT_UP"),
    (r"deadlift|rack pull|stiff leg|rdl|romanian", "DEADLIFT"),
    (r"flye|pec deck|butterfly|crossover", "FLYE"),
    (r"hip thrust|glute bridge|bridge|hip lift|hip raise|glute kickback|pull through", "HIP_RAISE"),
    (r"swing", "HIP_SWING"),
    (r"hyperextension|back extension|superman|good morning", "HYPEREXTENSION"),
    (r"lateral raise|front raise|rear delt|reverse flye|side raise|delt raise|y raise", "LATERAL_RAISE"),
    (r"leg raise|knee raise|knee tuck|leg lift|hanging", "LEG_RAISE"),
    (r"lunge|split squat|step up|bulgarian", "LUNGE"),
    (r"clean|snatch|jerk|high pull", "OLYMPIC_LIFT"),
    (r"plank|hollow", "PLANK"),
    (r"jump|hop|bound|plyo|box", "PLYO"),
    (r"pull up|chin up|pull down|muscle up", "PULL_UP"),
    (r"push up", "PUSH_UP"),
    (r"upright row", "ROW"),
    (r"row", "ROW"),
    (r"shoulder press|military|overhead press|arnold|push press|seated press|standing press", "SHOULDER_PRESS"),
    (r"external rotation|internal rotation|face pull|rotator|cuban", "SHOULDER_STABILITY"),
    (r"shrug", "SHRUG"),
    (r"leg press|hack squat|squat|sissy", "SQUAT"),
    (r"triceps|push down|skull crusher|kickback|dip|french press|jm press", "TRICEPS_EXTENSION"),
    (r"curl", "CURL"),
    (r"twist|russian|side bend|oblique|pallof|rollout|ab wheel|windmill|dead bug|bird dog", "CORE"),
    (r"sled", "SLED"),
    (r"tire", "TIRE"),
    (r"sledgehammer|sledge", "SLEDGE_HAMMER"),
    (r"stretch|smr|foam|mobility|circles|roll", "WARM_UP"),
]

MUSCLE_FALLBACK = {
    "chest": "BENCH_PRESS", "shoulders": "SHOULDER_PRESS", "triceps": "TRICEPS_EXTENSION",
    "biceps": "CURL", "forearms": "CURL", "lats": "PULL_UP", "middle back": "ROW", "traps": "SHRUG",
    "lower back": "HYPEREXTENSION", "quadriceps": "SQUAT", "hamstrings": "LEG_CURL", "glutes": "HIP_RAISE",
    "calves": "CALF_RAISE", "abdominals": "CORE", "adductors": "HIP_STABILITY", "abductors": "HIP_STABILITY",
    "neck": "WARM_UP",
}


def norm_tokens(name: str) -> list[str]:
    s = name.lower().replace("’", "'").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    out: list[str] = []
    for w in s.split():
        w = SYNONYMS.get(w, w)
        for part in w.split():
            if part in STOP or not part:
                continue
            if len(part) > 4 and re.search(r"(ch|sh|x)es$", part):
                part = part[:-2]
            elif len(part) > 3 and part.endswith("s") and part not in KEEP_S:
                part = part[:-1]
            out.append(part)
    return out


def text_of(tokens: list[str]) -> str:
    return " ".join(tokens)


def keyword_category(text: str) -> str | None:
    for pattern, cat in KEYWORD_CATEGORY:
        if re.search(rf"\b(?:{pattern})\b", text):
            return cat
    return None


def score(f: set[str], g: set[str], f_cat: str | None, g_cat: str, f_equip: set[str]) -> float:
    if not f or not g:
        return 0.0
    inter = len(f & g)
    s = inter / len(f | g)
    if f_cat and f_cat == g_cat:
        s += 0.25
    elif f_cat and f_cat != g_cat:
        s -= 0.35
    f_eq, g_eq = (f | f_equip) & EQUIP_WORDS, g & EQUIP_WORDS
    if g_eq and not (g_eq & f_eq):
        s -= 0.2  # catálogo Garmin pede um equipamento que o exercício não usa
    s -= 0.12 * len((g - f) & MODIFIERS)
    return s


def build() -> dict[str, dict]:
    exercises = json.loads((DATA / "exercises.json").read_text())
    catalog = json.loads((DATA / "garmin_catalog.json").read_text())
    gtok = [(set(norm_tokens(g["name"])), g) for g in catalog]
    generic = {g["category"]: g for g in catalog if g["exercise"] == g["category"]}

    result: dict[str, dict] = {}
    for ex in exercises:
        toks = norm_tokens(ex["name"])
        f = set(toks)
        f_cat = keyword_category(text_of(toks))
        f_equip = set(norm_tokens(ex.get("equipment") or ""))
        best, best_s = None, -9.0
        for g_set, g in gtok:
            s = score(f, g_set, f_cat, g["category"], f_equip)
            if s > best_s:
                best, best_s = g, s
        if best is not None and best_s >= 0.55:
            match, conf = best, "alta"
        elif best is not None and best_s >= 0.4:
            match, conf = best, "media"
        else:
            cat = f_cat
            if not cat and ex.get("category") == "stretching":
                cat = "WARM_UP"
            if not cat and ex.get("category") == "plyometrics":
                cat = "PLYO"
            if not cat and ex.get("category") == "cardio":
                cat = "CARDIO"
            if not cat:
                prim = (ex.get("primaryMuscles") or ["abdominals"])[0]
                cat = MUSCLE_FALLBACK.get(prim, "TOTAL_BODY")
            match, conf = generic[cat], "generica"
        result[ex["id"]] = {
            "category": match["category"],
            "exercise": match["exercise"],
            "garmin_name": match["name"],
            "confidence": conf,
        }
    return result


def main() -> None:
    mapping = build()
    (DATA / "garmin_map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=0, sort_keys=True))
    from collections import Counter

    print(len(mapping), Counter(v["confidence"] for v in mapping.values()))


if __name__ == "__main__":
    main()
