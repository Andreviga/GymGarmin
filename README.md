# Hybrid Lab (antigo GymGarmin)

App de academia (pessoal primeiro, multiusuário depois) que junta:

- **~870 exercícios** do [free-exercise-db](https://github.com/yuhonas/free-exercise-db) (domínio público) — músculos primários/secundários, equipamento, instruções e imagens — com nomes em português para os mais comuns e busca em PT ("supino", "remada baixa", "cadeira extensora").
- **Aparelhos de cardio unificados** ao catálogo: esteira (caminhada/corrida), bike vertical/horizontal/spinning, air bike, elíptico/transport, remo, escada, ski erg, corda — com impacto, METs e zonas de FC.
- **Análise fisiológica**: séries/semana por músculo (primário = 1, secundário = 0,5) contra faixas mínimo/ótimo/máximo, músculos esquecidos, equilíbrio (puxar × empurrar, posteriores × quadríceps, cadeia posterior × anterior, inferiores × superiores), recuperação estimada por músculo, minutos de cardio × recomendação da OMS e gasto calórico.
- **Limitações do usuário** (joelho, lombar, ombro, cotovelo, punho, cervical, quadril, tornozelo, hipertensão, cardíaco, gestação + palavras próprias): exercícios marcados como *cuidado* / *evitar*, modelos de planilha com troca automática e zona de FC limitada.
- **Envio para o Garmin** de 3 formas (detalhes abaixo).

## Treino automático e agenda que se reajusta

**Gerador** (`gymgarmin/generator.py`) — você informa tempo de prática, objetivo, dias livres, minutos por sessão, equipamentos e limitações:

| Tempo de prática | Nível | Principais | Reps | RPE | Dias de força (máx.) | Ciclo |
|---|---|---|---|---|---|---|
| < 6 meses | Iniciante | 3 séries | 10‑12 | 7 | 3 | 6 semanas |
| 6–24 meses | Intermediário | 4 | 6‑10 | 8 | 4 | 5 |
| 2–5 anos | Avançado | 4 | 5‑8 | 8,5 | 5 | 4 |
| > 5 anos | Muito avançado | 5 | 4‑6 | 9 | 6 | 4 |

- **Objetivo** ajusta reps, descanso, volume e meta de cardio: hipertrofia, força, emagrecimento, condicionamento, saúde e **híbrido** (força + resistência, com controle de interferência: intervalado nunca na véspera de pernas, cardio sempre depois da força, aparelhos de baixo impacto).
- **Divisão** pelo nº de dias: 2–3 full body (PPL para avançados), 4 superior/inferior, 5 sup/inf + PPL, 6 PPL 2×. Dias que sobram viram cardio zona 2 ou intervalado.
- Monta por **padrões de movimento** (agachar, dobrar quadril, unilateral, empurrar horizontal/vertical, puxar…), preferindo máquinas para iniciantes e pesos livres para avançados, variando entre treinos A/B e ciclos, nunca usando exercício "evitar".
- **Toda sessão tem preparo e pós**: aquecimento cardio leve + mobilidade/ativação dos músculos do dia + séries de aproximação no 1º exercício; no fim, alongamento estático dos músculos mais trabalhados.
- **Tempo calculado** por item (≈3,5 s por repetição, descanso entre séries, troca de aparelho, aproximação, cardio, alongamento) e cortado para caber nos minutos da sessão; o volume semanal por músculo é balanceado em direção ao alvo do nível sem passar do máximo recuperável. O cardio completa a meta semanal (finalizadores e dias de cardio) e avisa quando não cabe.
- **Progressão de carga** (dupla progressão) no registro do treino e **mesociclo** com aumento de séries e **semana de deload**.

**Agenda semanal** (`gymgarmin/schedule.py`) — cada treino cai num dia; o que foi registrado vira "feito", o que passou vira "perdido". Ao tocar **"Não vou treinar"** (ou ajustar um perdido):

1. **Move** o treino para um dia livre da semana, se não ficar colado (±1 dia) em outro treino dos mesmos músculos; um cardio no dia vira finalizador.
2. Senão **redistribui** ~75% do volume dos músculos que ficariam abaixo do alvo nos treinos restantes (+1 série nos exercícios que já os treinam ou o exercício perdido), sempre dentro do tempo (+10%).
3. Cardio perdido vira minutos extras nos próximos dias; o que não couber é informado.
4. Tudo pode ser **desfeito**; treinos já enviados ao Garmin ficam marcados e "Reenviar alterações ao Garmin" substitui os antigos.

## Rodando

```bash
pip install -r requirements.txt
python -m gymgarmin            # http://localhost:8000
```

Ele escuta em `0.0.0.0`, então o celular na mesma rede Wi‑Fi abre `http://IP-DO-COMPUTADOR:8000` e pode "Adicionar à tela inicial" (PWA). Os dados ficam em `~/.gymgarmin/gymgarmin.sqlite3` (mude com `GYMGARMIN_HOME`).

Telas: **Hoje** (treino do dia, recuperação, alertas) · **Planilhas** (editor, modelos, importar/exportar CSV, análise do volume planejado, envio ao Garmin) · **Treinar** (registro de séries/reps/kg/RPE com timer de descanso e carga da última vez) · **Análise** · **Exercícios** · **Perfil** (dados, limitações, conexão Garmin).

## Como o treino chega ao Garmin

| Caminho | Como | Prós / contras |
|---|---|---|
| **Arquivo .FIT** | "Baixar .FIT" → relógio no USB → copiar para `GARMIN/NewFiles` | Usa o SDK oficial da Garmin (`garmin-fit-sdk`), funciona offline e sem conta. Precisa de cabo. |
| **Garmin Connect direto** | "Enviar ao Garmin Connect" (+ data) → aparece na biblioteca de treinos e no calendário → sincroniza com o relógio | Mesmo formato JSON do editor web (`/workout-service`). Usa a API **não-oficial** via [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) ≥ 0.3.20. |
| **Volta do relógio** | Perfil → "Importar treinos do relógio" | Traz séries/reps/carga do treino de força (e cardio) para a análise. |

Detalhes técnicos:

- Musculação vira blocos `[exercício (reps ou tempo, carga) → descanso] × séries`, com o par **categoria/exercício do catálogo Garmin** (1.527 exercícios). O mapeamento free-exercise-db → Garmin é automático (`scripts/build_garmin_map.py`) e pode ser corrigido por exercício no app ("Alterar").
- Cardio dentro do treino de força vira passo por tempo (Treadmill, Stationary Bike, Elliptical, Rowing Machine, Stair Stepper…) com alvo de zona de FC; o primeiro item vira aquecimento e o último desaquecimento. Treino só de cardio vira treino de corrida/ciclismo/cardio com intervalados.
- Reps em faixa ("8‑12") usam o topo como alvo e a faixa vai na nota; "falha" termina pelo botão de volta.
- O FIT inclui mensagens `exercise_title`, necessárias para o relógio mostrar o nome do exercício.
- A API oficial de treinos da Garmin (Connect Developer Program / Training API) só é liberada para empresas aprovadas — para distribuir o app a outros usuários, esse é o caminho a solicitar.
- `garth` foi descontinuado em 2026 (a Garmin mudou o login). O `garminconnect` 0.3.x tem login próprio com MFA; a senha não é salva, só os tokens em `~/.gymgarmin/garmin_tokens`.

## Planilha CSV

Excel/Google Planilhas → salvar como CSV. Cabeçalho (PT ou EN, `;` ou `,`):

```
treino;exercicio;series;reps;carga_kg;descanso_s;tempo_s;duracao_min;zona;obs
A;Esteira caminhada;;;;;;10;2;aquecimento
A;Supino reto com barra;4;8-12;60;120;;;;
A;Prancha;3;;;60;45;;;
```

## Referências fisiológicas

As faixas de séries/semana por músculo (`gymgarmin/muscles.py`) são referências práticas para hipertrofia em intermediários, inspiradas na literatura dose‑resposta de volume (ex.: Schoenfeld et al. 2017; Pelland et al. 2024), ajustadas pelo nível e por um multiplicador no perfil. Regras de limitação são conservadoras e educativas — **não substituem médico ou fisioterapeuta**.

## Testes

```bash
pip install -r requirements-dev.txt
pytest
```

Os testes validam o FIT com o decodificador oficial da Garmin e o JSON com o modelo `StrengthWorkout` da própria biblioteca `garminconnect`.

## Estrutura

```
gymgarmin/
  catalog.py        catálogo unificado + busca em português
  muscles.py        músculos, faixas de volume, recuperação, zonas de FC
  limitations.py    regras de lesões/condições
  analysis.py       volume, equilíbrio, esquecidos, recuperação, cardio, análise da planilha
  garmin_export.py  geração .FIT (SDK oficial) e JSON do Garmin Connect
  garmin_sync.py    login/MFA, envio, agendamento e importação de atividades
  generator.py      treino automático (nível, objetivo, divisão, tempo, preparo/pós, cardio)
  schedule.py       agenda semanal e reajuste quando você falta
  progression.py    progressão de carga e mesociclo/deload
  templates.py      modelos de planilha que respeitam as limitações
  plans_io.py       importar/exportar CSV
  db.py, app.py     SQLite + API FastAPI
  static/           PWA (HTML/CSS/JS sem build)
  data/             exercises.json, cardio_machines.json, garmin_catalog.json, garmin_map.json
scripts/build_garmin_map.py
```
