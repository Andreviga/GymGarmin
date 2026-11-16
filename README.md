# GymGarmin

App de academia (pessoal primeiro, multiusuário depois) que junta:

- **~870 exercícios** do [free-exercise-db](https://github.com/yuhonas/free-exercise-db) (domínio público) — músculos primários/secundários, equipamento, instruções e imagens — com nomes em português para os mais comuns e busca em PT ("supino", "remada baixa", "cadeira extensora").
- **Aparelhos de cardio unificados** ao catálogo: esteira (caminhada/corrida), bike vertical/horizontal/spinning, air bike, elíptico/transport, remo, escada, ski erg, corda — com impacto, METs e zonas de FC.
- **Análise fisiológica**: séries/semana por músculo (primário = 1, secundário = 0,5) contra faixas mínimo/ótimo/máximo, músculos esquecidos, equilíbrio (puxar × empurrar, posteriores × quadríceps, cadeia posterior × anterior, inferiores × superiores), recuperação estimada por músculo, minutos de cardio × recomendação da OMS e gasto calórico.
- **Limitações do usuário** (joelho, lombar, ombro, cotovelo, punho, cervical, quadril, tornozelo, hipertensão, cardíaco, gestação + palavras próprias): exercícios marcados como *cuidado* / *evitar*, modelos de planilha com troca automática e zona de FC limitada.
- **Envio para o Garmin** de 3 formas (detalhes abaixo).

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
  templates.py      modelos de planilha que respeitam as limitações
  plans_io.py       importar/exportar CSV
  db.py, app.py     SQLite + API FastAPI
  static/           PWA (HTML/CSS/JS sem build)
  data/             exercises.json, cardio_machines.json, garmin_catalog.json, garmin_map.json
scripts/build_garmin_map.py
```
