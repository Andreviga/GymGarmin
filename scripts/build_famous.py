"""Gera gymgarmin/data/famous.json: modelos especiais inspirados em treinos que pessoas famosas DIVULGARAM
(livros, entrevistas, posts oficiais, treinadores nomeados). Cada modelo diz o que é documentado e o que é
aproximado, traz as fontes e um aviso: é inspirado, não é endossado pela pessoa.
Pesquisa: web, nov/2026 (ver 'sources'); 'approx' = reconstrução a partir de fontes secundárias.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "gymgarmin" / "data" / "famous.json"


def S(eid, sets, reps, rest=90, notes=None, **kw):
    d = {"exercise_id": eid, "sets": sets, "reps": reps, "rest_s": rest, **kw}
    if notes:
        d["notes"] = notes
    return d


def T(eid, sets, secs, rest=45, notes=None):
    d = {"exercise_id": eid, "sets": sets, "reps": None, "duration_s": secs, "rest_s": rest}
    if notes:
        d["notes"] = notes
    return d


def C(eid, minutes, zone, notes=None):
    d = {"exercise_id": eid, "duration_min": minutes, "zone": zone}
    if notes:
        d["notes"] = notes
    return d


F = []

F.append({"id": "arnold_golden_six", "person": "Arnold Schwarzenegger", "title": "Golden Six (corpo inteiro)", "icon": "🏆",
  "about": "Rotina de seis exercícios básicos, 3× por semana, atribuída ao começo da carreira do Arnold (influência do Reg Park). "
           "Pouco exercício, muita base: ótima para iniciante e para quem volta.",
  "level": "iniciante", "days_per_week": 3, "confidence": "atribuído",
  "cautions": "O desenvolvimento por trás da nuca original foi trocado pelo desenvolvimento sentado pela frente (mais seguro para o ombro).",
  "sources": ["https://fitnessvolt.com/arnold-golden-six-routine/", "https://www.fitnessandpower.com/training/workout-routines/arnold-schwarzenegger-golden-six"],
  "days": [{"name": "Golden Six (seg, qua, sex)", "items": [
      S("Barbell_Squat", 4, "10", 120), S("Wide-Grip_Barbell_Bench_Press", 3, "10", 120), S("Chin-Up", 3, "máx", 120, "até perto do limite"),
      S("Seated_Barbell_Military_Press", 4, "10", 90, "original: atrás da nuca"), S("Barbell_Curl", 3, "10", 90), S("Sit-Up", 3, "máx", 60)]}]})

F.append({"id": "arnold_basico", "person": "Arnold Schwarzenegger", "title": "Programa Básico, nível 1 (do livro)", "icon": "📘",
  "about": "Do livro The New Encyclopedia of Modern Bodybuilding: cada grupo 2× por semana numa divisão em 3 treinos repetidos "
           "(6 dias), abdominal em todo treino. Estrutura do livro; séries e exercícios aproximados de fontes secundárias.",
  "level": "intermediario", "days_per_week": 6, "confidence": "aproximado",
  "cautions": "Volume alto, de outra época do fisiculturismo. Se não recuperar, use 4 dias (A, B, C, A…).",
  "sources": ["https://www.slideshare.net/slideshow/arnolds-basic-training-program/3321936", "https://www.strengthlog.com/arnold-split", "https://www.liftosaur.com/programs/arnold-split"],
  "days": [
    {"name": "A – Peito e costas", "items": [S("Barbell_Bench_Press_-_Medium_Grip", 4, "10", 120), S("Barbell_Incline_Bench_Press_-_Medium_Grip", 4, "10", 120),
        S("Bent-Arm_Dumbbell_Pullover", 3, "10"), S("Chin-Up", 4, "máx", 120), S("Bent_Over_Barbell_Row", 4, "10", 120), S("Barbell_Deadlift", 3, "6-10", 180),
        S("Crunches", 5, "25", 45)]},
    {"name": "B – Ombros e braços", "items": [S("Clean_and_Press", 4, "8-10", 120), S("Side_Lateral_Raise", 4, "10"), S("Barbell_Curl", 4, "10"),
        S("Dumbbell_Bicep_Curl", 4, "10", 90, "sentado"), S("Close-Grip_Barbell_Bench_Press", 4, "10"), S("Standing_Dumbbell_Triceps_Extension", 4, "10"),
        S("Palms-Up_Barbell_Wrist_Curl_Over_A_Bench", 3, "15", 45), S("Palms-Down_Wrist_Curl_Over_A_Bench", 3, "15", 45), S("Crunches", 5, "25", 45)]},
    {"name": "C – Pernas e lombar", "items": [S("Barbell_Squat", 4, "10", 150), S("Barbell_Lunge", 3, "10", 90), S("Lying_Leg_Curls", 4, "10"),
        S("Standing_Calf_Raises", 5, "15", 60), S("Stiff-Legged_Barbell_Deadlift", 3, "10", 120), S("Crunches", 5, "25", 45)]}]})

F.append({"id": "reg_park_5x5", "person": "Reg Park", "title": "5×5 — fase 1 (do manual dele, 1960)", "icon": "🏋️",
  "about": "Do manual Strength & Bulk Training: os três básicos, 3× por semana, 5 séries de 5 (as 2 primeiras de aquecimento, "
           "as 3 últimas pesadas). Força e massa com pouca coisa.",
  "level": "iniciante", "days_per_week": 3, "confidence": "documentado",
  "cautions": "As duas primeiras séries são de aproximação; suba a carga quando fechar 3×5 na carga de trabalho.",
  "sources": ["https://superstrengthtraining.com/strength-and-bulk-training-reg-park", "https://www.muscleandstrength.com/node/49409"],
  "days": [{"name": "5×5 (seg, qua, sex)", "items": [S("Hyperextensions_Back_Extensions", 3, "10", 60, "aquecimento"),
      S("Barbell_Squat", 5, "5", 180, "2 de aproximação + 3 pesadas"), S("Barbell_Bench_Press_-_Medium_Grip", 5, "5", 180, "2 de aproximação + 3 pesadas"),
      S("Barbell_Deadlift", 5, "5", 180, "2 de aproximação + 3 pesadas")]}]})

F.append({"id": "steve_reeves", "person": "Steve Reeves", "title": "Físico clássico — corpo inteiro 3×", "icon": "🏛️",
  "about": "Do livro Building the Classic Physique the Natural Way: corpo inteiro 3× por semana, parte de cima primeiro e pernas no "
           "fim, 3 séries de 8–12. Era 'natural', antes dos esteroides. Lista de exercícios aproximada (as fontes variam).",
  "level": "intermediario", "days_per_week": 3, "confidence": "aproximado",
  "cautions": "Sessão longa. Iniciante: 1 série de ~20 repetições por exercício (versão do próprio livro).",
  "sources": ["https://www.muscleandstrength.com/articles/steve-reeves-solution-size-strength-health.html", "https://thebarbell.com/steve-reeves-training/"],
  "days": [{"name": "Corpo inteiro (seg, qua, sex)", "items": [S("Upright_Barbell_Row", 3, "8-12", 60), S("Dumbbell_Shoulder_Press", 3, "8-12", 60),
      S("Side_Lateral_Raise", 3, "15", 45), S("Incline_Dumbbell_Press", 3, "8-12", 60), S("Wide-Grip_Lat_Pulldown", 3, "8-12", 60),
      S("One-Arm_Dumbbell_Row", 3, "8-12", 60), S("Incline_Dumbbell_Curl", 3, "8-12", 45), S("Triceps_Pushdown", 3, "8-12", 45),
      S("Front_Barbell_Squat", 3, "15", 90), S("Lying_Leg_Curls", 3, "8-12", 60), S("Standing_Calf_Raises", 3, "15-20", 45),
      C("caminhada_rua", 30, 2, "ele fazia caminhadas rápidas")]}]})

F.append({"id": "bruce_lee", "person": "Bruce Lee", "title": "Força geral em 20 minutos", "icon": "🥋",
  "about": "Das anotações dele (livro de John Little): sessão curta com barra, 3× por semana em dias alternados, para dar força "
           "sem atrapalhar as artes marciais. Nos outros dias: corrida, corda e bicicleta.",
  "level": "iniciante", "days_per_week": 3, "confidence": "documentado",
  "cautions": "Poucas séries: use uma carga desafiadora, mas sem ir à falha.",
  "sources": ["https://www.artofmanliness.com/strength/fitness/bruce-lee-workout/", "https://bodybuilding.com/fun/getting-fit-and-strong-the-bruce-lee-way.htm"],
  "days": [
    {"name": "Força (dias alternados)", "items": [S("Clean_and_Press", 2, "8", 90), S("Barbell_Squat", 2, "12", 90), S("Bent-Arm_Barbell_Pullover", 2, "8", 60),
        S("Barbell_Bench_Press_-_Medium_Grip", 2, "6", 90), S("Good_Morning", 2, "8", 60), S("Barbell_Curl", 2, "8", 60)]},
    {"name": "Corrida e corda", "items": [C("corrida_rua", 25, 2, "aproximado: corrida leve"), C("cardio_corda", 10, 3)]}]})

F.append({"id": "mentzer_heavy_duty", "person": "Mike Mentzer", "title": "Heavy Duty — 1 série até a falha", "icon": "⚡",
  "about": "Dos livros Heavy Duty: pouquíssimo volume e muita intensidade — 1 série por exercício até a falha, com pré-exaustão "
           "(isolado emendado no composto) e 4 dias ou mais de descanso entre treinos.",
  "level": "intermediario", "days_per_week": 2, "confidence": "método documentado, exercícios aproximados",
  "cautions": "Falha só com técnica boa e alguém para ajudar no supino. Faça 1–2 séries leves de aquecimento antes de cada exercício.",
  "sources": ["https://www.ironmanmagazine.com/heavy-duty-mike-mentzers-most-productive-routine", "https://www.strengthlog.com/mike-mentzer-workout-routine/"],
  "days": [
    {"name": "1 – Peito e costas", "items": [S("Butterfly", 1, "6-10", 0, "até a falha · emende já no próximo", superset="A"),
        S("Barbell_Incline_Bench_Press_-_Medium_Grip", 1, "1-3", 180, "até a falha", superset="A"),
        S("Machine_Pullover", 1, "6-10", 0, "até a falha · emende já no próximo", superset="B"),
        S("Underhand_Cable_Pulldowns", 1, "6-10", 180, "até a falha", superset="B"), S("Barbell_Deadlift", 1, "5-8", 180, "até a falha técnica")]},
    {"name": "2 – Pernas", "items": [S("Leg_Extensions", 1, "8-15", 0, "até a falha · emende já no próximo", superset="A"),
        S("Leg_Press", 1, "8-15", 180, "até a falha", superset="A"), S("Lying_Leg_Curls", 1, "6-10", 120, "até a falha"),
        S("Standing_Calf_Raises", 1, "12-20", 90, "até a falha")]},
    {"name": "3 – Ombros e braços", "items": [S("Side_Lateral_Raise", 1, "6-10", 120, "até a falha"), S("Seated_Bent-Over_Rear_Delt_Raise", 1, "6-10", 120, "até a falha"),
        S("Barbell_Curl", 1, "6-10", 120, "até a falha"), S("Triceps_Pushdown", 1, "6-10", 0, "até a falha · emende já no próximo", superset="A"),
        S("Dips_-_Triceps_Version", 1, "3-5", 120, "até a falha", superset="A")]}]})

F.append({"id": "dorian_yates", "person": "Dorian Yates", "title": "Blood and Guts — 4 dias", "icon": "🩸",
  "about": "A divisão dos anos 1990 (Mr. Olympia): 4 dias, 1–2 séries leves de aquecimento e UMA série de trabalho até a falha por "
           "exercício. Exercícios aproximados (a ordem varia nas fontes).",
  "level": "avancado", "days_per_week": 4, "confidence": "aproximado",
  "cautions": "Intensidade muito alta e outra época do fisiculturismo; ele mesmo se lesionou. Para avançados.",
  "sources": ["https://generationiron.com/dorian-yates-blood-guts-workout/", "https://www.muscleandstrength.com/node/47131"],
  "days": [
    {"name": "Peito e bíceps", "items": [S("Barbell_Incline_Bench_Press_-_Medium_Grip", 1, "6-10", 180, "+1–2 de aquecimento; até a falha"),
        S("Dumbbell_Bench_Press", 1, "6-10", 150, "até a falha"), S("Incline_Dumbbell_Flyes", 1, "6-10", 120, "até a falha"),
        S("Cable_Crossover", 1, "8-12", 120, "até a falha"), S("Incline_Dumbbell_Curl", 1, "6-10", 120, "até a falha"), S("Preacher_Curl", 1, "6-10", 120, "até a falha")]},
    {"name": "Costas", "items": [S("Machine_Pullover", 1, "6-10", 120, "até a falha"), S("Close-Grip_Front_Lat_Pulldown", 1, "6-10", 150, "até a falha"),
        S("Reverse_Grip_Bent-Over_Rows", 1, "6-10", 150, "até a falha"), S("One-Arm_Dumbbell_Row", 1, "6-10", 120, "até a falha"),
        S("Seated_Bent-Over_Rear_Delt_Raise", 1, "8-12", 90, "até a falha"), S("Barbell_Deadlift", 1, "6-8", 180, "até a falha técnica")]},
    {"name": "Ombros e tríceps", "items": [S("Machine_Shoulder_Military_Press", 1, "6-10", 150, "até a falha"), S("Side_Lateral_Raise", 1, "8-12", 90, "sentado; até a falha"),
        S("Cable_Seated_Lateral_Raise", 1, "8-12", 90, "até a falha"), S("Triceps_Pushdown", 1, "8-12", 90, "até a falha"),
        S("EZ-Bar_Skullcrusher", 1, "6-10", 120, "até a falha"), S("Cable_One_Arm_Tricep_Extension", 1, "8-12", 90, "até a falha")]},
    {"name": "Pernas", "items": [S("Leg_Extensions", 1, "8-12", 90, "até a falha"), S("Leg_Press", 1, "8-12", 180, "até a falha"), S("Hack_Squat", 1, "8-12", 180, "até a falha"),
        S("Lying_Leg_Curls", 1, "8-12", 90, "até a falha"), S("Stiff-Legged_Barbell_Deadlift", 1, "8-12", 120, "até a falha"),
        S("Standing_Calf_Raises", 1, "8-12", 60, "até a falha"), S("Seated_Calf_Raise", 1, "8-12", 60, "até a falha")]},
    {"name": "Cardio (dias sem musculação)", "items": [C("cardio_bike_vertical", 20, 2, "15–20 min nos dias de folga")]}]})

F.append({"id": "hugh_jackman", "person": "Hugh Jackman", "title": "Wolverine — força primeiro", "icon": "🐺",
  "about": "Preparo para o filme com o treinador Mike Ryan: cada dia gira em torno de um levantamento principal em onda de 4 semanas "
           "(% do 1RM: 60–75, 65–85, 70–90, semana leve), depois acessórios de musculação. Ondas e acessórios aproximados.",
  "level": "intermediario", "days_per_week": 4, "confidence": "aproximado",
  "cautions": "Era preparo de filme (pico), com dieta específica. Use cargas que você conhece; a semana 4 é leve.",
  "sources": ["https://www.muscleandstrength.com/workouts/hugh-jackman-workout-routine", "https://bodybuilding.com/fun/the-true-beast-unleashed-how-hugh-jackman-became-the-wolverine.html"],
  "days": [
    {"name": "Supino + peito", "items": [S("Barbell_Bench_Press_-_Medium_Grip", 4, "5", 120, "onda: 60–75% do 1RM na semana 1, sobe até ~90% na 3, leve na 4"),
        S("Incline_Dumbbell_Press", 4, "8-12"), S("Dips_-_Triceps_Version", 3, "10", 90, "com peso se der"), S("Cable_Crossover", 3, "12", 60)]},
    {"name": "Agachamento + pernas", "items": [S("Barbell_Squat", 4, "5", 150, "onda do 1RM (ver supino)"), S("Front_Barbell_Squat", 4, "10", 120),
        S("Romanian_Deadlift", 4, "10", 120), S("Standing_Calf_Raises", 4, "12", 60)]},
    {"name": "Terra + costas", "items": [S("Barbell_Deadlift", 4, "5", 180, "onda do 1RM"), S("Weighted_Pull_Ups", 4, "6-10", 120),
        S("Bent_Over_Barbell_Row", 4, "8-12", 90), S("Barbell_Curl", 3, "10", 60)]},
    {"name": "Ombros + braços", "items": [S("Standing_Military_Press", 4, "5", 120, "onda do 1RM"), S("Side_Lateral_Raise", 3, "12", 60),
        S("EZ-Bar_Curl", 3, "10", 60), S("EZ-Bar_Skullcrusher", 3, "10", 60)]}]})

F.append({"id": "the_rock", "person": "Dwayne Johnson", "title": "Black Adam — volume e controle", "icon": "🪨",
  "about": "Preparo com o treinador Dave Rienzi: um grupo por dia, muito volume, descidas lentas e bi-sets; cardio de manhã em jejum "
           "(30–50 min). O dia de braço é o publicado; os outros, aproximados.",
  "level": "avancado", "days_per_week": 5, "confidence": "braço documentado; resto aproximado",
  "cautions": "Volume muito alto, preparo de filme. Séries de 12: 4 reps com 3 s na descida + 4 paradas + 4 normais.",
  "sources": ["https://menshealth.com.au/your-guide-to-the-rocks-complete-arm-building-routine-for-black-adam/", "https://www.coachweb.com/workouts/celebrity-workouts/the-rocks-trainer-shares-his-black-adam-workouts-and-theyre-brutal"],
  "days": [
    {"name": "Braços (publicado)", "items": [C("cardio_eliptico", 30, 2, "de manhã"), S("Triceps_Pushdown_-_Rope_Attachment", 4, "20", 60),
        S("Lying_Dumbbell_Tricep_Extension", 4, "12", 60, "4 lentas + 4 paradas + 4 normais"), S("Triceps_Pushdown", 4, "10", 60, "dois cabos"),
        S("Barbell_Curl", 4, "20", 60), S("Concentration_Curls", 4, "12", 60, "4 lentas + 4 paradas + 4 normais")]},
    {"name": "Costas", "items": [C("cardio_esteira_caminhada", 30, 2, "de manhã"), S("Underhand_Cable_Pulldowns", 4, "12", 0, "emende já na remada", superset="A"),
        S("Bent_Over_Barbell_Row", 4, "12", 90, superset="A"), S("Seated_Cable_Rows", 4, "20", 60, "pegada fechada"), S("Straight-Arm_Pulldown", 3, "15", 60)]},
    {"name": "Peito", "items": [C("cardio_eliptico", 30, 2, "de manhã"), S("Incline_Dumbbell_Press", 4, "12", 90, "3 s na descida"),
        S("Machine_Bench_Press", 4, "12", 90), S("Cable_Crossover", 4, "15", 60), S("Dips_-_Triceps_Version", 3, "máx", 60)]},
    {"name": "Ombros", "items": [C("cardio_eliptico", 30, 2, "de manhã"), S("Seated_Dumbbell_Press", 4, "12", 90), S("Side_Lateral_Raise", 4, "15", 45),
        S("Seated_Bent-Over_Rear_Delt_Raise", 4, "15", 45), S("Upright_Barbell_Row", 3, "12", 60)]},
    {"name": "Pernas (o dia mais duro)", "items": [S("Leg_Press", 4, "20", 120), S("Hack_Squat", 4, "12", 120), S("Split_Squat_with_Dumbbells", 3, "12", 90),
        S("Lying_Leg_Curls", 4, "15", 60), S("Leg_Extensions", 4, "15", 60), S("Standing_Calf_Raises", 4, "20", 45)]}]})

F.append({"id": "kipchoge", "person": "Eliud Kipchoge", "title": "Maratonista: 3 sessões-chave + força leve", "icon": "🏃",
  "about": "Como treina a equipe dele (técnico Patrick Sang): 3 sessões fortes por semana (tiros na pista, longão, fartlek), o resto "
           "fácil; e 2 sessões de 60 min de força e mobilidade no colchonete e elástico, 'sem sofrer'. Volume da corrida reduzido para "
           "gente comum (ele faz 180–220 km por semana).",
  "level": "todos", "days_per_week": 5, "confidence": "estrutura documentada, volumes adaptados",
  "cautions": "Comece com o volume que você já corre; aumente aos poucos. As sessões de força servem para qualquer nível.",
  "sources": ["https://www.outsideonline.com/running/eliud-kipchoge-marathon-workout-training-principles/", "https://runnersworldonline.com.au/how-to-train-like-eliud-kipchoge/"],
  "days": [
    {"name": "Tiros (ter)", "items": [C("corrida_rua", 15, 2, "aquecimento"), C("corrida_pista", 25, 4, "ex.: 8–12 × 400 m forte, 1 min trotando"), C("corrida_rua", 10, 1, "soltar")]},
    {"name": "Longão (qui)", "items": [C("corrida_rua", 80, 2, "o mais longo da semana")]},
    {"name": "Fartlek (sáb)", "items": [C("corrida_rua", 45, 3, "1 min forte / 1 min leve, ~20×")]},
    {"name": "Força e mobilidade A", "items": [S("Butt_Lift_Bridge", 3, "12", 45), T("Plank", 3, 40), T("Side_Bridge", 3, 30),
        S("Kettlebell_One-Legged_Deadlift", 3, "10", 45, "sem peso"), S("Monster_Walk", 3, "12", 45), T("Single_Leg_Balance", 3, 30, 30)]},
    {"name": "Força e mobilidade B + corrida fácil", "items": [C("corrida_rua", 40, 1, "bem fácil"), S("Butt_Lift_Bridge", 3, "12", 45),
        T("Plank", 3, 40), S("Monster_Walk", 3, "12", 45), T("Single_Leg_Balance", 3, 30, 30)]}]})

F.append({"id": "kayla_bbg", "person": "Kayla Itsines", "title": "BBG — circuitos de 28 min em casa", "icon": "🔥",
  "about": "Do programa BBG (app Sweat): 3 treinos de 28 min (pernas, braços e abdômen, corpo inteiro) em circuitos de 7 min "
           "fazendo o máximo de voltas, mais caminhadas (LISS) e alongamento. Quase sem equipamento. Exercícios aproximados.",
  "level": "iniciante", "days_per_week": 4, "confidence": "estrutura documentada, exercícios aproximados",
  "cautions": "Muito salto: joelho sensível, troque o agachamento com salto pelo normal.",
  "sources": ["https://www.sweat.com/pages/bbg", "https://kaylaitsines.com/blogs/education/bbg-zero-equipment"],
  "days": [
    {"name": "Pernas e cardio (circuitos de 7 min)", "items": [S("Freehand_Jump_Squat", 4, "15", 0, "circuito: o máximo de voltas em 7 min"),
        S("Bodyweight_Walking_Lunge", 4, "24", 0), S("Step-up_with_Knee_Raise", 4, "24", 0), S("Burpee_Broad_Jump", 4, "10", 60, "burpee")]},
    {"name": "Braços e abdômen", "items": [S("Pushups", 4, "10", 0, "circuito de 7 min"), S("Bench_Dips", 4, "15", 0), S("Air_Bike", 4, "40", 0, "abdominal bicicleta"),
        S("Mountain_Climbers", 4, "40", 60)]},
    {"name": "Corpo inteiro", "items": [S("Freehand_Jump_Squat", 4, "12", 0, "circuito de 7 min"), S("Pushups", 4, "10", 0), S("Bodyweight_Walking_Lunge", 4, "20", 0),
        S("Mountain_Climbers", 4, "30", 60)]},
    {"name": "Caminhada (LISS)", "items": [C("caminhada_rua", 35, 2, "30–45 min, ritmo de conversa")]}]})

F.append({"id": "ramon_dino", "person": "Ramon Dino", "title": "Off-season 2025 — 6 treinos", "icon": "🇧🇷",
  "about": "Divisão revelada pelo treinador Fabrício Pacholok para o off-season de 2025: 6 sessões e 2 descansos, prioridade em peito e "
           "costas. Estrutura da fonte; exercícios e séries aproximados.",
  "level": "avancado", "days_per_week": 6, "confidence": "estrutura documentada, exercícios aproximados",
  "cautions": "Contexto de atleta de elite (Classic Physique). Ajuste o volume à sua recuperação.",
  "sources": ["https://www.lance.com.br/mais-esportes/ramon-dino-revela-peso-atual-dieta-e-protocolo-de-treinamentos-veja.html",
              "https://www.correio24horas.com.br/esportes/revelado-saiba-qual-e-o-peso-atual-dieta-e-protocolo-de-treinamentos-de-ramon-dino-0325"],
  "days": [
    {"name": "1 – Costas e bíceps (puxadas)", "items": [S("Wide-Grip_Lat_Pulldown", 4, "8-12"), S("Pullups", 3, "máx"), S("Straight-Arm_Pulldown", 3, "12"),
        S("Barbell_Curl", 3, "8-12"), S("Incline_Dumbbell_Curl", 3, "10-12")]},
    {"name": "2 – Peito, ombro e tríceps (peito)", "items": [S("Incline_Dumbbell_Press", 4, "8-12"), S("Machine_Bench_Press", 3, "8-12"), S("Cable_Crossover", 3, "12"),
        S("Side_Lateral_Raise", 3, "12-15", 60), S("Triceps_Pushdown", 3, "10-12", 60)]},
    {"name": "3 – Pernas (quadríceps)", "items": [S("Hack_Squat", 4, "8-12", 150), S("Leg_Press", 3, "10-12", 120), S("Leg_Extensions", 3, "12-15", 60), S("Lying_Leg_Curls", 3, "10-12", 60)]},
    {"name": "4 – Costas e bíceps (remadas)", "items": [S("Bent_Over_Barbell_Row", 4, "8-12", 120), S("Seated_Cable_Rows", 3, "10-12"), S("One-Arm_Dumbbell_Row", 3, "10-12"),
        S("Hammer_Curls", 3, "10-12", 60)]},
    {"name": "5 – Peito, ombro e tríceps (ombro)", "items": [S("Seated_Dumbbell_Press", 4, "8-12"), S("Side_Lateral_Raise", 4, "12-15", 60), S("Seated_Bent-Over_Rear_Delt_Raise", 3, "15", 45),
        S("Incline_Dumbbell_Press", 3, "10-12"), S("Cable_Rope_Overhead_Triceps_Extension", 3, "10-12", 60)]},
    {"name": "6 – Pernas (posterior e glúteo)", "items": [S("Romanian_Deadlift", 4, "8-10", 150), S("Lying_Leg_Curls", 3, "10-12"), S("Barbell_Hip_Thrust", 3, "8-12", 120),
        S("Split_Squat_with_Dumbbells", 3, "10", 90), S("Standing_Calf_Raises", 4, "12-15", 45)]}]})

if __name__ == "__main__":
    from gymgarmin import catalog
    bad = [(f["id"], it["exercise_id"]) for f in F for d in f["days"] for it in d["items"] if not catalog.get(it["exercise_id"])]
    assert not bad, bad
    OUT.write_text(json.dumps({"updated": "2026-11", "disclaimer":
        "Inspirado no que a pessoa divulgou (livros, entrevistas, posts oficiais ou treinadores). Não é endossado por ela. "
        "O app troca o que não serve às suas limitações; o resto fica como a fonte.", "templates": F}, ensure_ascii=False, indent=1))
    print(len(F), "modelos")
