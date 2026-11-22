/* HAX (Hybrid Athlete eXperience) – app web (vanilla JS, sem build). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");
const state = { meta: null, profile: null, draftPlan: null, planTab: 0, session: null };

function actAs() { try { return JSON.parse(sessionStorage.getItem("gg_act") || "null"); } catch (_) { return null; } }
/* Cache "mostra já, atualiza depois": cada tela abre na hora com os últimos dados e se atualiza sozinha em seguida.
   Só leituras (GET) entram; qualquer gravação limpa o cache para nunca mostrar dado velho depois de uma mudança. */
const SWR = { mode: null, used: null, mem: new Map(), key: "gg_cache_v1", skip: /^\/api\/(auth|garmin\/status|schedule\/\d+\/prefill)/ };
try { for (const [k, v] of Object.entries(JSON.parse(localStorage.getItem(SWR.key) || "{}"))) SWR.mem.set(k, v); } catch (_) { /* sem cache */ }
function swrSave() {
  clearTimeout(swrSave.t);
  swrSave.t = setTimeout(() => {
    const entries = [...SWR.mem.entries()].slice(-40);
    try { localStorage.setItem(SWR.key, JSON.stringify(Object.fromEntries(entries))); } catch (_) { try { localStorage.removeItem(SWR.key); } catch (_e) { /* */ } }
  }, 400);
}
function swrClear() { SWR.mem.clear(); try { localStorage.removeItem(SWR.key); } catch (_) { /* */ } }
const clone = (v) => (v && typeof v === "object" ? JSON.parse(JSON.stringify(v)) : v);
async function api(path, opts = {}) {
  const act = actAs();
  const isGet = !opts.method || opts.method === "GET";
  const ck = `${act ? act.id : "me"}|${path}`;
  if (isGet && SWR.mode && !SWR.skip.test(path) && SWR.mem.has(ck)) { SWR.used.add(path); return clone(SWR.mem.get(ck)); }
  // leituras da tela atual são canceladas se a pessoa trocar de tela antes de terminarem
  const signal = opts.signal || (isGet && route.ctl ? route.ctl.signal : undefined);
  const o = { ...opts, signal, headers: { "Content-Type": "application/json", ...(act ? { "X-GG-Athlete": String(act.id) } : {}), ...(opts.headers || {}) } };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const gen = SWR.gen || 0;
  if (!isGet) SWR.gen = gen + 1;
  let r;
  try { r = await fetch(path, o); } catch (e0) {
    if (e0 && e0.name === "AbortError") throw e0;
    const err = new Error("Sem conexão com a internet. Tente de novo quando o sinal voltar.");
    err.offline = true; throw err;
  }
  if (!r.ok) {
    let msg = r.statusText; let j = null;
    try { j = await r.json(); msg = j.detail || msg; } catch (_) { /* sem corpo */ }
    if (r.status === 401 && j && j.login) { showAuth("login"); const e = new Error(msg); e.auth = true; throw e; }
    if (r.status === 409 && j && j.existing_id) { const e = new Error(msg); e.status = 409; e.body = j; throw e; }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  const out = ct.includes("json") ? await r.json() : await r.text();
  if (!isGet) { SWR.gen = (SWR.gen || 0) + 1; if (SWR.mem.size) swrClear(); }
  else if (ct.includes("json") && !SWR.skip.test(path) && gen === (SWR.gen || 0)) { SWR.mem.delete(ck); SWR.mem.set(ck, clone(out)); swrSave(); }
  return out;
}

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
function store(key, val) { try { if (val === undefined) return JSON.parse(localStorage.getItem(key)); if (val === null) localStorage.removeItem(key); else localStorage.setItem(key, JSON.stringify(val)); } catch (_) { return null; } }

function openModal(html) { $("#modal-body").innerHTML = html; $("#modal").hidden = false; return $("#modal-body"); }
function closeModal() {
  $("#modal").hidden = true; $("#modal-body").innerHTML = ""; $("#modal-body").classList.remove("busy");
  const hook = closeModal.hook; closeModal.hook = null;  // quem abriu a janela pode pedir para ser avisado quando ela fechar (×, toque fora, botão)
  if (hook) { try { hook(); } catch (_) { /* a tela de trás segue como está */ } }
  if (typeof route === "function" && route.again) { route.again = false; route(); }
}
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.classList.contains("modal-close")) closeModal(); });

const STATUS_CLASS = { esquecido: "bad", abaixo: "warn", manutencao: "", otimo: "ok", alto: "warn", excesso: "bad" };
const LIM_CLASS = { ok: "ok", cuidado: "warn", evitar: "bad" };
const LIM_TXT = { ok: "liberado", cuidado: "cuidado", evitar: "evitar" };
const fmtDate = (iso) => { try { return new Date(iso).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }); } catch (_) { return iso; } };
const zoneOpts = (sel) => [1, 2, 3, 4, 5].map((z) => `<option value="${z}" ${+sel === z ? "selected" : ""}>Z${z}</option>`).join("");

function limBadge(lim) {
  if (!lim || lim.status === "ok") return "";
  return `<span class="pill lim-pill ${LIM_CLASS[lim.status]}" title="${esc(lim.reasons.join("\n"))}">${LIM_TXT[lim.status]}</span>`;
}

/* ------------------------------------------------------------ barras --- */
function muscleBars(rows, key) {
  const max = Math.max(10, ...rows.map((m) => Math.max(m[key], m.landmarks.mrv * 1.15)));
  const pct = (v) => `${Math.min(100, (v / max) * 100)}%`;
  return `<div class="bars">${rows.filter((m) => m.muscle !== "neck" || m[key] > 0).map((m) => {
    const L = m.landmarks;
    return `<div class="bar-row" title="${esc(m.pt)}: ${m[key]} séries — ${esc(m.status_pt)} (mín. ${L.mev}, ótimo ${L.mav_low}–${L.mav_high}, máx. ${L.mrv})">
      <span class="bar-label">${esc(m.pt)}</span>
      <span class="bar-track">
        <span class="bar-band" style="left:0;width:${pct(L.mev)};background:var(--band-low)"></span>
        <span class="bar-band" style="left:${pct(L.mev)};width:calc(${pct(L.mav_low)} - ${pct(L.mev)});background:var(--band-mid)"></span>
        <span class="bar-band" style="left:${pct(L.mav_low)};width:calc(${pct(L.mav_high)} - ${pct(L.mav_low)});background:var(--band-ok)"></span>
        <span class="bar-band" style="left:${pct(L.mav_high)};width:calc(${pct(L.mrv)} - ${pct(L.mav_high)});background:var(--band-mid)"></span>
        <span class="bar-band" style="left:${pct(L.mrv)};right:0;background:var(--band-low)"></span>
        <span class="bar-fill" style="width:${pct(m[key])}"></span>
      </span>
      <span class="bar-val"><span class="pill ${STATUS_CLASS[m.status]}">${m[key]}</span></span>
    </div>`;
  }).join("")}</div>
  <p class="small muted">Faixas: <span class="pill bad">abaixo do mínimo</span> <span class="pill warn">manutenção / alto</span> <span class="pill ok">ótimo</span> — séries/semana (primário = 1, secundário = 0,5).</p>`;
}

function recsHtml(recs) {
  if (!recs.length) return `<p class="muted">Nada a corrigir. Bom equilíbrio! 💪</p>`;
  return recs.map((r) => `<div class="rec ${r.level}">${esc(r.text)}
    ${r.suggestions && r.suggestions.length ? `<div class="chips" style="margin-top:6px">${r.suggestions.map((s) => `<button class="chip" data-ex="${esc(s.id)}">${esc(s.name)}${s.status === "cuidado" ? " ⚠" : ""}</button>`).join("")}</div>` : ""}
  </div>`).join("");
}

function balanceHtml(bal) {
  return `<ul class="list">${bal.map((b) => `<li><div class="row between gap"><span>${esc(b.label)}</span>
    <span class="pill ${b.status === "ok" ? "ok" : b.status === "sem_dados" ? "muted" : "warn"}">${b.ratio ?? "—"}</span></div>
    <div class="small muted">${esc(b.a)} × ${esc(b.b)} séries · ideal ${b.ideal[0]}–${b.ideal[1]} · ${esc(b.message)}</div></li>`).join("")}</ul>`;
}

/* --------------------------------------------------------------- HOJE --- */
const STATUS_INFO = { planejado: ["Planejado", ""], feito: ["Feito ✔", "ok"], perdido: ["Perdido", "bad"], pulado: ["Não treinou", "warn"], substituido: ["Substituído", "muted"] };
const PHASES = [["preparo", "Aquecimento: elevar → ativar → potencializar"], ["principal", "Treino"], ["pos", "Pós-treino (alongamento)"]];
const RAMP_TXT = { elevar: "elevar", ativar: "ativar", potencializar: "potencializar" };
const RIR_TIP = "RIR = repetições que ainda sobrariam no fim da série (0 = falha). Perto da falha estimula mais o músculo.";
const ddmm = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-"); return `${d}/${m}`; };
// data/hora local (toISOString é UTC: depois das 21h no Brasil já seria amanhã)
const localISO = (d = new Date()) => { const p = (x) => String(x).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return localISO(d).slice(0, 10); };

function timeBar(t) {
  if (!t) return "";
  const segs = [["preparo", "Preparo", "var(--c-prep)"], ["forca_trabalho", "Força", "var(--c-work)"], ["forca_descanso", "Descanso", "var(--c-rest)"], ["cardio", "Cardio", "var(--c-cardio)"], ["pos", "Alongamento", "var(--c-post)"]];
  const tot = Math.max(t.total, t.orcamento || 0, 1);
  const on = segs.filter((x) => t[x[0]] > 0);
  return `<div class="tbar">${on.map((x) => `<span style="width:${(t[x[0]] / tot) * 100}%;background:${x[2]}" title="${x[1]}: ${t[x[0]]} min"></span>`).join("")}</div>
    <div class="small muted">${on.map((x) => `<span class="dot" style="background:${x[2]}"></span>${x[1]} ${Math.round(t[x[0]])}′`).join(" ")} · <b>${Math.round(t.total)} min</b>${t.orcamento ? ` de ${t.orcamento}` : ""}</div>`;
}

/* ---- mídia dos exercícios: 2 quadros alternando (como um GIF) ou pictograma desenhado */
const PICTO_SVG = {
  run: '<circle cx="15" cy="4.5" r="2"/><path d="M13 8l-3 3 3 2.5-1.5 5M10 11l-3.5.5M13 8l3.5 2.5 3-1M13.5 13.5l3 3.5 2.5-.5"/>',
  bike: '<circle cx="6" cy="16" r="3.5"/><circle cx="18" cy="16" r="3.5"/><circle cx="14" cy="4.5" r="1.8"/><path d="M6 16l4-7h5l3 7M10 9l4 7M12.5 6.5l1.5 2.5h3"/>',
  swim: '<circle cx="16" cy="6" r="2"/><path d="M3 15c2 1.5 4 1.5 6 0s4-1.5 6 0 4 1.5 6 0M3 19c2 1.5 4 1.5 6 0s4-1.5 6 0 4 1.5 6 0M6 12l5-4 3 2.5 3-1"/>',
  row: '<circle cx="8" cy="6" r="2"/><path d="M3 18h18M8 9l2 5h5M10 14l-3 4M15 14l4-6M19 8l2-1"/>',
  walk: '<circle cx="13" cy="4.5" r="2"/><path d="M13 8l-2 5 2.5 3.5-1 4.5M11 13l-2.5 6.5M12.5 8.5l3 2.5 2 0M12 9l-3 2v3"/>',
  hike: '<circle cx="12" cy="4.5" r="2"/><path d="M12 8l-2 5 2.5 3.5-1 4.5M10 13l-2.5 6.5M12 8.5l3 2v3M17 10v11M3 21l5-6 3 3 4-5 6 8"/>',
  ski: '<circle cx="12" cy="4.5" r="2"/><path d="M8 8v12M16 8v12M12 7.5v6l-3 3M12 13.5l3 3M6 8h4M14 8h4"/>',
  elliptical: '<circle cx="12" cy="4.5" r="2"/><path d="M12 7.5v6M12 9l4-2M12 9l-3 3M12 13.5l-3 5M12 13.5l3 5M5 20h14M17 4l1 6"/>',
  stairs: '<path d="M3 20h4v-4h4v-4h4V8h4V4h2"/><circle cx="9" cy="8" r="1.8"/>',
  rope: '<circle cx="12" cy="4.5" r="2"/><path d="M12 7.5v7M12 10l-4 2M12 10l4 2M12 14.5l-2 6M12 14.5l2 6M8 12c-5 3-5 10 4 10s9-7 4-10"/>',
  kettlebell: '<path d="M8.5 9a3.5 3.5 0 117 0"/><path d="M6.5 11h11l1 6a4 4 0 01-4 4h-5a4 4 0 01-4-4z"/>',
  dumbbell: '<path d="M3 10v4M6 8v8M18 8v8M21 10v4M6 12h12"/>',
  squat: '<circle cx="12" cy="4" r="2"/><path d="M6 9h12M12 6v5l-3 4 3 5M12 11l3 4-1 5M5 20h14"/><path d="M6 9v3M18 9v3"/>',
  abduct: '<circle cx="12" cy="4" r="2"/><path d="M12 6v8M12 9l-3 3M12 9l3 3M12 14l-2 7M12 14l6 5M18 19l3-1M21 18V6"/>',
  yraise: '<circle cx="12" cy="7" r="2"/><path d="M12 9v7M12 10l-6-6M12 10l6-6M12 16l-2 5M12 16l2 5M3 3h3M18 3h3"/>',
  sled: '<circle cx="7" cy="5" r="2"/><path d="M7 7l3 5-4 3-1 5M10 12l5-1M13 15l-2 5M14 9h6v8h-6zM13 20h9"/>',
  armerg: '<circle cx="8" cy="6" r="2"/><path d="M8 8v7l4 5M8 11l5 1M15 12a3 3 0 1 0 0-.1M15 12v9M11 21h8"/>',
  climber: '<circle cx="12" cy="5" r="2"/><path d="M17 2v20M12 7v6l-3 4v4M12 13l3 3h2M12 9l5-3M12 9l-4 2"/>',
  yoga: '<circle cx="12" cy="5" r="2"/><path d="M12 7.5v6M12 9.5l-5 3M12 9.5l5 3M12 13.5l-6 4h12zM4 20.5h16"/>',
  ball: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5l4 3-1.5 4.5h-5L8 10.5zM12 3.5v4M16 10.5l4-1.5M14.5 15l2.5 4M9.5 15L7 19M8 10.5L4 9"/>',
  racket: '<ellipse cx="9.5" cy="8.5" rx="5" ry="6.5" transform="rotate(-35 9.5 8.5)"/><path d="M13 13.5l6.5 7.5M6 6l7 5M8 3.5l6 9M5 10l6 3.5"/><circle cx="19" cy="5" r="1.6"/>',
  glove: '<path d="M7 12V7.5a3 3 0 016 0V9a3.2 3.2 0 016 1.5V14a5 5 0 01-5 5H9a2 2 0 01-2-2zM7 13h3.5M8 19v2.5h8V19"/>',
  pulse: '<path d="M2.5 12h4l2.5-6.5 4 13 2.5-6.5h6"/>',
};
let MEDIA = null;
async function loadMedia() {
  if (MEDIA) return MEDIA;
  try { MEDIA = store("gg_media_v3"); } catch (_) { MEDIA = null; }
  if (!MEDIA || !MEDIA.f) {
    try { MEDIA = await api("/api/media"); store("gg_media_v3", MEDIA); } catch (_) { MEDIA = { base: "", f: {}, p: {} }; }
  }
  return MEDIA;
}
function frames(id) {
  const M = MEDIA; if (!M || !(id in M.f)) return null;
  const folder = M.f[id] || id; return [`${M.base}${folder}/0.jpg`, `${M.base}${folder}/1.jpg`];
}
function pictoHtml(key, cls = "") {
  return `<span class="picto ${cls}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PICTO_SVG[key] || PICTO_SVG.dumbbell}</svg></span>`;
}
// size: "s" (lista), "m" (cartão), "l" (detalhe). Animada alternando início/fim do movimento.
function mediaHtml(id, size = "s", fallback = null) {
  const f = frames(id);
  if (f) return `<span class="demo ${size}"><img loading="lazy" src="${esc(f[0])}" alt=""><img loading="lazy" src="${esc(f[1])}" alt="" class="f2"></span>`;
  const p = (MEDIA && MEDIA.p[id]) || fallback || "dumbbell";
  return pictoHtml(p, size);
}

function itemLine(it) {
  if (it.endurance) {
    const en = it.endurance;
    return `<li class="mline">${mediaHtml(it.exercise_id, "s", { corrida: "run", ciclismo: "bike", natacao: "swim", remo: "row", caminhada: "walk", trilha: "hike" }[en.sport])}<span>${SPORT_ICON[en.sport] || ""} <b>${esc(en.title)}</b> <span class="muted">${esc(endSummary(en))}</span>
      ${en.hard ? `<span class="pill warn small">forte</span>` : ""}${en.role === "longo" ? `<span class="pill small">longo</span>` : ""}
      ${en.note ? `<div class="small muted">${esc(en.note)}</div>` : ""}${endStepsHtml(en)}</span></li>`;
  }
  const name = esc(it._name || it._ex?.name || it.exercise_id);
  let d = "";
  if (it.intervals) d = `${it.warmup_min || 0}′ + ${it.intervals.repeats}× (${it.intervals.work_s}s Z${it.intervals.work_zone} / ${it.intervals.rest_s}s) + ${it.cooldown_min || 0}′`;
  else if (it.duration_min) d = `${it.duration_min} min${it.zone ? " Z" + it.zone : ""}`;
  else if (it.sets) d = `${it.sets}×${it.reps || (it.duration_s ? it.duration_s + "s" : "")}${it.rest_s && it.phase !== "preparo" && it.phase !== "pos" ? ` · ${it.rest_s}s` : ""}${it.rir && !it.deload ? ` · RIR ${String(it.rir).split(" ")[0]}` : ""}`;
  const tags = [it.focus ? `<span class="pill focus-pill small">🎯 foco</span>` : "", it.ramp && RAMP_TXT[it.ramp] ? `<span class="pill muted small">${RAMP_TXT[it.ramp]}</span>` : it.ramp ? `<span class="pill">+${it.ramp} série</span>` : "", it.deload ? `<span class="pill warn">deload</span>` : "", it.added ? `<span class="pill warn">+${it.added}</span>` : "", it.added_from ? `<span class="pill warn">compensação</span>` : "", it.finisher ? `<span class="pill muted">finalizador</span>` : "", it.superset ? `<span class="pill small" title="Bi-set: faça uma série de cada, emendadas, e descanse depois do segundo">bi-set ${it.superset}</span>` : "", it.rotated_from ? `<span class="pill muted small" title="Variação deste ciclo: estímulo novo para o músculo; no próximo ciclo volta o exercício de antes">variação do ciclo</span>` : ""].join(" ");
  return `<li class="mline">${mediaHtml(it.exercise_id, "s")}<span><span data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${name}</span> <span class="muted">${esc(d)}</span> ${tags}</span></li>`;
}

function itemsByPhase(items) {
  return PHASES.map(([ph, label]) => {
    const list = items.filter((i) => (i.phase || "principal") === ph);
    return list.length ? `<div class="small"><b>${label}</b><ul class="plain">${list.map(itemLine).join("")}</ul></div>` : "";
  }).join("");
}

function meter(label, done, target, unit = "") {
  const pct = target ? Math.min(100, (done / target) * 100) : 0;
  return `<div><div class="row between small"><span>${label}</span><b>${done}${unit} / ${target}${unit}</b></div><div class="meter"><span style="width:${pct}%"></span></div></div>`;
}

async function viewHoje(weekDate) {
  const athletePromise = weekDate ? Promise.resolve("") : athleteCards().catch(() => "");
  const [sch, rd, gst, prof, stp] = await Promise.all([api(`/api/schedule${weekDate ? `?date=${weekDate}` : ""}`),
    weekDate ? Promise.resolve(null) : api("/api/readiness").catch(() => null), api("/api/garmin/status").catch(() => null),
    api("/api/profile").catch(() => null), weekDate ? Promise.resolve(null) : api("/api/steps").catch(() => null)]);
  const prt = weekDate || actAs() ? null : await api("/api/partners").catch(() => null);
  const wib = weekDate || actAs() ? null : await api("/api/watch/inbox").catch(() => null);

  if (!sch.plan) {
    const incomplete = profileMissing(state.profile || await api("/api/profile").catch(() => ({}))).length > 0;
    view.innerHTML = `${pageHead("Hoje")}${firstStepsHtml(false)}${partnerCardsHtml(prt)}${installCardHtml("hoje")}<section class="today-card">
      <div class="eyebrow">Comece por aqui</div><div class="tc-title">Monte seu treino</div>
      <p class="muted">Diga há quanto tempo treina, os dias e o tempo que tem. O app monta musculação e corrida juntas, respeita suas limitações e reorganiza a semana quando você faltar.</p>
      ${incomplete ? `<a class="btn block" id="wiz-open" href="#bemvindo">✨ Responder 7 perguntas e montar meu treino</a>` : `<button class="btn block" id="gen-open">Gerar meu treino</button>`}
      <a class="btn ghost block" href="#planilhas" style="margin-top:8px">Montar manualmente</a></section>`;
    const go = $("#gen-open"); if (go) go.onclick = () => openGenerator();
    bindSteps();
    view.onclick = async (ev) => { const t = ev.target.closest("button"); if (!t) return; try { await partnerAction(t.dataset, t, () => viewHoje()); } catch (e) { toast(e.message, 5000); } };
    return;
  }
  const g = sch.goals;
  const missed = sch.entries.filter((e) => e.status === "perdido");
  const cyc = sch.cycle;
  const athleteHtml = await athletePromise;
  const plannedToday = sch.entries.filter((e) => e.date === sch.today && e.status === "planejado");
  // dia aberto: hoje; se hoje já foi feito ou é descanso, o próximo treino
  let sel = weekDate ? (sch.entries.find((e) => e.status === "planejado") || sch.entries[0] || {}).date || sch.week_start : sch.today;
  if (!weekDate && !plannedToday.length) {
    const nx = sch.entries.find((x) => x.date > sch.today && x.status === "planejado");
    if (nx && !sch.entries.some((e) => e.date === sch.today && e.status !== "planejado")) sel = nx.date;
  }
  sch._sel = sel;
  const adjusted = plannedToday.some((e) => e.readiness_backup);
  view.innerHTML = `${weekDate ? pageHead(`Semana de ${ddmm(sch.week_start)}`) : pageHead("Hoje", true, sch.today)}
  ${weekDate ? "" : firstStepsHtml(true) + statsHtml(rd, g, adjusted, plannedToday.length > 0) + (sch.pending || []).map(questionHtml).join("") + watchInboxHtml(wib) + stepsAskHtml(stp, sch.today) + partnerCardsHtml(prt) + legacyHtml(sch) + focusChip(prof) + garminSyncCard(sch, gst) + installCardHtml("hoje")}${athleteHtml}
  ${missed.length ? `<div class="rec alerta"><b>Treino perdido:</b> ${missed.map((e) => `${e.weekday} ${ddmm(e.date)} – ${esc(e.day_name)}`).join(", ")}.
     <div class="row gap wrap" style="margin-top:6px">${missed.map((e) => `<button class="btn sm" data-skip="${e.id}">Ajustar a semana (${e.weekday})</button>`).join("")}</div></div>` : ""}
  ${weekDate ? "" : `<div id="hx"></div>`}
  ${!weekDate && sch.comeback ? `<section class="card wbox"><div class="eyebrow">Que bom te ver</div><b>${sch.comeback.days} dias sem treinar — tudo bem, acontece.</b>
    <p class="small" style="margin:6px 0 10px">Para voltar sem sofrer: um treino curto de 25 min hoje. Uma série por exercício com esforço já mantém força e músculo, e voltar logo é o que mais segura o hábito.</p>
    <button class="btn sm" data-comeback="1">Voltar com 25 min</button></section>` : ""}
  <section class="week">
    <div class="week-head"><button class="icon-btn sm" data-week="${addDays(sch.week_start, -7)}" aria-label="Semana anterior">‹</button>
      <div class="grow"><b>${weekDate ? esc(sch.plan.name) : `Semana de ${ddmm(sch.week_start)}`}</b>
        <span class="small muted">${cyc ? `ciclo ${cyc.cycle} · semana ${cyc.week}/${cyc.length}${cyc.deload ? " · deload" : ""}` : esc(sch.plan.name)}</span></div>
      <button class="icon-btn sm" data-week="${addDays(sch.week_start, 7)}" aria-label="Próxima semana">›</button></div>
    <div class="wstrip" role="tablist">${weekStrip(sch, sel)}</div>
  </section>
  <div id="day-panel">${dayPanel(sch, sel)}</div>
  ${(sch.time_warnings || []).length ? `<details class="rec atencao small"><summary><b>⏱ ${sch.time_warnings.length} ${sch.time_warnings.length > 1 ? "dias passam" : "dia passa"} do seu tempo de treino</b></summary><ul class="plain">${sch.time_warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
  ${cyc && cyc.deload ? `<p class="small rec">Semana de deload: menos séries, carga −10% e RPE ~6 para o corpo assimilar o ciclo.</p>` : ""}
  <section class="card goals"><div class="row between"><h2>Metas da semana</h2><a class="small" href="#analise">Progresso ›</a></div><div class="stack">
    ${meter(`Treinos${g.sessions.extra ? ` (+${g.sessions.extra} extra)` : ""}`, g.sessions.done, g.sessions.planned)}
    ${meter("Séries de força", g.sets.done, g.sets.planned)}
    ${meter("Cardio (min equivalentes)", g.cardio.done, g.cardio.target)}
    ${Object.entries(g.endurance || {}).map(([k, x]) => meter(`${esc(x.name)}`, x.done, x.planned, " " + x.unit)).join("")}
  </div></section>
  ${weekDate ? "" : weekMissHtml(g)}
  <details class="card more"><summary><b>Opções da semana</b></summary><div class="stack" style="padding-bottom:4px">
    <button class="btn ghost block" id="push-week">${sch.entries.some((e) => e.garmin_outdated && e.status === "planejado") ? "Reenviar alterações ao Garmin" : "Enviar semana ao Garmin"}</button>
    <button class="btn ghost block" id="gen-open">Regerar treino</button>
    <button class="btn ghost block" id="rebuild">Refazer a semana pela planilha</button></div></details>`;
  sch._rd = rd;
  bindAgenda(sch);
  if (!weekDate) hojeExtras(sch).catch(() => {});
}

// o que fica faltando na semana, por músculo, mesmo com os treinos que ainda estão na agenda
function weekMissHtml(g) {
  if (g && g.light_week) return `<section class="card miss-week"><h2>O que falta esta semana</h2><p class="small muted" style="margin:0">Semana leve (deload ou polimento): é para treinar menos mesmo. Nada a repor.</p></section>`;
  const rows = (g && g.muscles) || []; if (!rows.length) return "";
  const miss = rows.filter((b) => b.state === "falta");
  return `<section class="card miss-week"><h2>O que falta esta semana</h2>
    ${miss.length ? `<p class="small muted" style="margin:0 0 8px">Séries por músculo que ficam faltando mesmo com os treinos que ainda estão na agenda (treino perdido, pulado ou feito pela metade).</p>
      ${missListHtml(miss)}
      <a class="btn ghost block" href="#treinar/livre" style="margin-top:12px">Montar treino livre com o que falta</a>
      <p class="small muted" style="margin:8px 0 0">Séries por músculo na semana (quando o músculo só ajuda no exercício, conta meia série). Repor é opcional: uma semana mais leve não muda o resultado; o que conta é a sequência das próximas.</p>`
    : `<p class="small ok-txt" style="margin:0">✓ Com os treinos que estão na agenda, cada músculo fecha a semana no volume planejado.</p>`}</section>`;
}
// reforço que a regra antiga espalhou pela semana ao pular um treino: oferece voltar ao plano original
function legacyHtml(sch) {
  const lg = sch.legacy; if (!lg || !lg.days || !lg.days.length) return "";
  return `<section class="rec atencao legacy-card"><b>Ajuste antigo nesta semana</b>
    <p class="small" style="margin:4px 0 6px">Quando um treino foi pulado, a regra antiga espalhou parte dele em outros dias. O app não faz mais isso: falta isolada não precisa ser reposta.</p>
    <ul class="small plain">${lg.days.map((d) => `<li><b>${esc(d.label)}:</b> ${d.lines.map(esc).join("; ")}</li>`).join("")}</ul>
    <div class="row gap wrap" style="margin-top:8px"><button class="btn sm" id="legacy-clean">Voltar ao plano original</button><button class="btn sm ghost" id="legacy-keep">Manter como está</button></div></section>`;
}

/* ------------------------------------------------- HOJE: primeiros passos (quem acabou de entrar) -- */
function firstStepsHtml(hasPlan) {
  if (store("gg_steps_hidden") || actAs()) return "";
  const p = state.profile || {}; const miss = profileMissing(p).map((m) => m[0]); const g = state.garmin || {};
  const items = [
    ["Seus dados", "nome, idade, peso e sexo", !["name", "age", "weight_kg", "sex"].some((k) => miss.includes(k)), "#perfil/dados"],
    ["Rotina de treino", "objetivo, dias e tempo por treino", !["goal", "training_months", "available_days"].some((k) => miss.includes(k)), "#perfil/treino"],
    ["Limitações e lesões", "opcional: deixa o treino mais seguro", !!store("gg_lim_seen") || (p.limitations || []).length > 0, "#perfil/limites"],
    ["Conectar relógio", "opcional: Garmin, Apple ou Android", !!g.connected || !!store("gg_watch_skip"), "#perfil/relogios"],
    ["Gerar seu treino", "o app monta a semana inteira", !!hasPlan, null],
  ];
  const done = items.filter((i) => i[2]).length;
  if (done === items.length) return "";
  const next = items.findIndex((i) => !i[2]);
  return `<section class="steps-card" aria-label="Primeiros passos">
    <div class="row between gap"><b>Primeiros passos · ${done} de ${items.length}</b><button class="act" id="steps-hide">Ocultar</button></div>
    <div class="meter" style="margin:8px 0 4px"><span style="width:${Math.round(100 * done / items.length)}%"></span></div>
    <ol class="steps-list">${items.map(([t, sub, ok, href], i) => {
      const inner = `<span class="st-ic ${ok ? "ok" : i === next ? "next" : ""}" aria-hidden="true">${ok ? "✓" : i + 1}</span><span class="grow"><b>${t}</b><span class="small muted">${ok ? "feito" : sub}</span></span><span class="chev" aria-hidden="true">›</span>`;
      return `<li class="${ok ? "ok" : ""}">${href ? `<a class="step" href="${href}">${inner}</a>` : `<button class="step" id="steps-gen">${inner}</button>`}</li>`;
    }).join("")}</ol></section>`;
}
function bindSteps() {
  const h = $("#steps-hide"); if (h) h.onclick = () => { store("gg_steps_hidden", 1); const c = $(".steps-card"); if (c) c.remove(); };
  const g = $("#steps-gen"); if (g) g.onclick = () => openGenerator();
}

/* ------------------------------------------------- HOJE: semana em tira + cartão do dia -- */
const SPORT_PICTO = { corrida: "run", ciclismo: "bike", natacao: "swim", remo: "row", caminhada: "walk", trilha: "hike" };
const SPORT_WORD = { corrida: "corrida", ciclismo: "bike", natacao: "natação", remo: "remo", caminhada: "caminhada", trilha: "trilha" };
const WD_LONG = ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"];
const WD_SHORT = ["DOM", "SEG", "TER", "QUA", "QUI", "SEX", "SÁB"];
const MONTHS = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"];
// treinos desta semana que mudaram (ou ainda não foram) para o relógio: um toque reenvia só esses
function garminSyncCard(sch, gst) {
  if (!gst || !gst.connected) return "";
  const pend = (sch.entries || []).filter((e) => e.status === "planejado" && e.date >= sch.today);
  const out = pend.filter((e) => e.garmin_outdated); const missing = pend.filter((e) => !e.garmin_workout_id && !e.garmin_outdated);
  const n = out.length + missing.length; if (!n) return "";
  return `<section class="garmin-card"><span class="gc-ico" aria-hidden="true">⌚</span><span class="grow"><b>${n} ${n === 1 ? "treino para atualizar" : "treinos para atualizar"} no relógio</b>
    <span class="small muted">${out.length ? "O treino mudou desde o último envio (regras novas, troca ou regeração)" : "Ainda não foram para o Garmin"}</span></span>
    <button class="btn sm" id="push-outdated">Reenviar ao Garmin</button></section>`;
}

function pageHead(title, greet = false, today = null) {
  const d = today ? new Date(today + "T12:00:00") : new Date();
  const name = state.me && state.me.user && state.me.user.name ? String(state.me.user.name).split(" ")[0] : "";
  const sub = `${WD_LONG[d.getDay()]}, ${d.getDate()} de ${MONTHS[d.getMonth()]}`;
  return `<header class="page-head"><div class="eyebrow">${greet ? sub : ""}</div><h1 class="page-title">${greet && name && name !== "Eu" ? `Olá, ${esc(name)}` : esc(title)}</h1></header>`;
}
function mainIds(e) {
  return (e.items || []).filter((i) => (i.phase || "principal") === "principal").map((i) => i.exercise_id);
}
function dayKind(e) {
  const end = (e.items || []).filter((i) => i.endurance);
  const str = (e.items || []).some((i) => (i.phase || "principal") === "principal" && !i.endurance);
  if (end.length && str) return `musculação + ${SPORT_WORD[end[0].endurance.sport] || "cardio"}`;
  return end.length ? (SPORT_WORD[end[0].endurance.sport] || "cardio") : "musculação";
}
function shortName(e) { return e.sport ? String(e.day_name).replace(/^[^:]+:\s*/, "") : e.day_name; }
function dayLabel(date, sch) {
  const d = new Date(date + "T12:00:00");
  return date === sch.today ? "Hoje" : date === addDays(sch.today, 1) ? "Amanhã" : WD_LONG[d.getDay()][0].toUpperCase() + WD_LONG[d.getDay()].slice(1);
}
function weekStrip(sch, sel) {
  return [0, 1, 2, 3, 4, 5, 6].map((i) => {
    const date = addDays(sch.week_start, i); const d = new Date(date + "T12:00:00");
    const es = sch.entries.filter((e) => e.date === date);
    const st = !es.length ? "rest" : es.every((e) => e.status === "feito") ? "done" : es.some((e) => e.status === "perdido") ? "missed"
      : es.some((e) => e.status === "planejado") ? "planned" : "skipped";
    return `<button class="wd ${st} ${date === sch.today ? "is-today" : ""} ${date === sel ? "sel" : ""}" data-day="${date}" role="tab" aria-selected="${date === sel}"
      aria-label="${WD_LONG[d.getDay()]} ${ddmm(date)}: ${es.length ? es.map((e) => esc(e.day_name)).join(" + ") : "descanso"}">
      <small>${WD_SHORT[d.getDay()]}</small><b>${d.getDate()}</b><i></i></button>`;
  }).join("");
}
function dayPanel(sch, date) {
  const es = sch.entries.filter((e) => e.date === date);
  const lbl = dayLabel(date, sch);
  if (!es.length) {
    const nx = sch.entries.find((x) => x.date > date && x.status === "planejado");
    return `<section class="today-card rest"><div class="eyebrow">${lbl} · ${ddmm(date)}</div><div class="tc-title">Descanso</div>
      <p class="muted">Caminhar, alongar e dormir bem também contam.</p>
      ${date === sch.today ? `<button class="btn block" data-gen="1">✨ Gerar um treino para agora</button>` : ""}
      ${nx ? `<button class="btn ghost block" data-day="${nx.date}" style="margin-top:8px">Próximo: ${esc(dayLabel(nx.date, sch))} · ${esc(shortName(nx))}</button>` : ""}</section>`;
  }
  const planned = es.filter((e) => e.status === "planejado");
  const first = planned[0] || es[0];
  const mins = es.reduce((t, e) => t + (e.time ? e.time.total : 0), 0);
  const all = es.flatMap((e) => (e.items || []).map((it) => ({ ...it, _entry: e })));
  const main = all.filter((i) => (i.phase || "principal") === "principal");
  const extra = all.filter((i) => (i.phase || "principal") !== "principal");
  const nStr = main.filter((i) => !i.endurance).length;
  const runs = main.filter((i) => i.endurance);
  const status = es.every((e) => e.status === "feito") ? [es.some((e) => e.partial) ? "Feito em parte" : "Feito ✔", "ok"] : es.some((e) => e.status === "perdido") ? ["Perdido", "bad"]
    : es.every((e) => e.status === "pulado") ? ["Não treinou", "warn"] : null;
  const strength = es.find((e) => !e.sport);
  const thumbId = strength ? mainIds(strength).find((x) => frames(x)) : null;
  return `<section class="today-card ${status ? "st-" + status[1] : ""}">
    <div class="tc-top"><div class="grow"><div class="eyebrow">${lbl} · ${ddmm(date)}${mins ? ` · ~${Math.round(mins)} min` : ""}</div>
      <div class="tc-title">${es.map((e) => esc(shortName(e))).join(" + ")}</div>
      <div class="tc-chips">${nStr ? `<span>${nStr} exercícios</span>` : ""}${runs.map((r) => `<span>${esc(SPORT_WORD[r.endurance.sport] || "cardio")} ${esc(endSummary(r.endurance).split(" · ")[0])}</span>`).join("")}
        ${es.some((e) => e.race) ? `<span class="bad">prova</span>` : ""}${status ? `<span class="${status[1]}">${status[0]}</span>` : ""}
        ${es.some((e) => e.garmin_outdated) ? `<span class="warn">Garmin desatualizado</span>` : es.some((e) => e.garmin_workout_id) ? `<span class="ok">no relógio</span>` : ""}</div></div>
      ${thumbId ? `<span class="tc-thumb">${mediaHtml(thumbId, "m")}</span>` : runs.length ? `<span class="tc-thumb">${pictoHtml(SPORT_PICTO[runs[0].endurance.sport] || "run", "m")}</span>` : ""}</div>
    ${es.filter((e) => e.alt && e.status === "planejado").map((e) => `<div class="rec small alt-on"><b>🔀 Outra opção: ${esc(e.alt.label.toLowerCase())}</b>
      <div>${esc((e.alt.why || [])[0] || "")}</div>
      <div class="row gap wrap" style="margin-top:6px"><button class="btn sm ghost" data-altwhy="${e.id}">Por quê?</button><button class="btn sm ghost" data-altundo="${e.id}">Voltar ao original</button></div></div>`).join("")}
    ${planned.length ? `<a class="btn block go" href="#treinar/agenda/${first.id}">${date === sch.today ? "Começar treino" : date < sch.today ? "Registrar treino" : "Treinar agora"}</a>` : ""}
    ${es.some((e) => e.status === "perdido") ? `<button class="btn block" data-skip="${es.find((e) => e.status === "perdido").id}">Ajustar a semana</button>` : ""}
    <ul class="exlist">${main.map((it) => {
      const en = it.endurance;
      const name = en ? en.title : (it._name || it._ex?.name || it.exercise_id);
      const sub = en ? endSummary(en) : `${it.sets ? `${it.sets} × ${it.reps || (it.duration_s ? it.duration_s + "s" : "")}` : it.duration_min ? `${it.duration_min} min` : ""}${it.rir && !it.deload ? ` · sobrar ${String(it.rir).split(" ")[0]}` : ""}`;
      return `<li data-ex="${esc(it.exercise_id)}">${mediaHtml(it.exercise_id, "s", en ? SPORT_PICTO[en.sport] : null)}
        <span class="grow" data-ex="${esc(it.exercise_id)}"><b data-ex="${esc(it.exercise_id)}">${esc(name)}</b><span class="small muted" data-ex="${esc(it.exercise_id)}">${esc(sub)}</span></span>
        ${it.superset ? `<span class="tag">bi-set ${it.superset}</span>` : en && en.hard ? `<span class="tag warn">forte</span>` : ""}</li>`;
    }).join("")}</ul>
    ${extra.length ? `<details class="small sub"><summary>Aquecimento e alongamento · ${extra.length}</summary>${itemsByPhase(extra)}</details>` : ""}
    ${es.some((e) => (e.changes || []).length || e.skip_result) ? `<details class="small sub"><summary>O que mudou neste dia</summary><ul class="small changes">${es.flatMap((e) => [...(e.changes || []), ...((e.skip_result || {}).changes || [])]).map((c) => `<li>${esc(c)}</li>`).join("")}</ul></details>` : ""}
    <div class="tc-actions">
      ${planned.filter((e) => e.kind === "forca" && date >= sch.today).map((e) => `<button class="act" data-alt="${e.id}">🔀 Outra opção</button>`).join("")}
      ${!planned.length && date === sch.today && es.every((e) => e.status === "feito") ? `<button class="act" data-gen="1">✨ Treinar mais hoje</button>` : ""}
      ${planned.length ? `<button class="act" data-move="${first.id}" data-date="${date}">Mover</button>
        <button class="act" data-skip="${first.id}">Não vou treinar</button>
        <button class="act" data-push="${first.id}">Enviar ao relógio</button>
        <a class="act" href="/api/schedule/${first.id}/fit">.FIT</a>` : ""}
      ${es.filter((e) => e.status === "pulado" && e.can_undo).map((e, _i, all) => `<button class="act" data-undo="${e.id}">${all.length > 1 ? `Desfazer: ${esc(shortName(e))}` : "Desfazer ajuste"}</button>`).join("")}
      ${es.filter((e) => e.readiness_backup && e.status === "planejado").map((e) => `<button class="act" data-rundo="${e.id}">Desfazer ajuste de prontidão</button>`).join("")}
    </div></section>`;
}
function ringSvg(score, cls) {
  const v = Math.max(0, Math.min(100, +score || 0)), c = 2 * Math.PI * 15.5;
  return `<svg class="ring-svg ${cls}" viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15.5" class="ring-bg"/>
    <circle cx="18" cy="18" r="15.5" class="ring-fg" stroke-dasharray="${(c * v / 100).toFixed(1)} ${c.toFixed(1)}"/></svg>`;
}
const RD_LEVEL = { alta: "Alta", moderada: "Moderada", baixa: "Baixa", muito_baixa: "Muito baixa" };
function statsHtml(rd, g, adjusted, hasToday) {
  const has = rd && rd.score != null;
  const cls = has ? ({ seguir: "ok", moderar: "warn", trocar: "warn", descanso: "bad" }[rd.action] || "ok") : "";
  const sleep = rd && rd.sleep_h ? `${String(Math.floor(rd.sleep_h))}h${String(Math.round((rd.sleep_h % 1) * 60)).padStart(2, "0")}` : "—";
  const s = g && g.sessions ? `${g.sessions.done}/${g.sessions.planned}` : "—";
  const act = has && rd.action !== "seguir" && hasToday;
  return `<section class="stats">
    <button class="stat ready" id="rd-open" aria-label="Prontidão: ver detalhes">
      <span class="ring-box">${ringSvg(has ? rd.score : 0, cls)}<b>${has ? rd.score : "—"}</b></span>
      <span class="stat-txt"><span class="lbl">Prontidão</span><b class="${cls}">${has ? esc(RD_LEVEL[rd.level] || rd.level) : "sem dados"}</b></span></button>
    <a class="stat" href="#saude"><b>${sleep}</b><span class="lbl">Sono</span></a>
    <a class="stat" href="#analise"><b>${s}</b><span class="lbl">Treinos</span></a>
  </section>
  ${act ? `<div class="rec ${cls === "bad" ? "alerta" : "atencao"} small row between gap"><span class="grow">${esc(rd.text)}</span>
    ${adjusted ? `<span class="pill ok">ajustado</span>` : `<button class="btn sm" id="rd-apply">Ajustar hoje</button>`}</div>` : ""}`;
}
async function openReadiness(rd) {
  const body = openModal(rd && rd.score != null ? readinessCard(rd, true, false).replace('class="card readiness', 'class="readiness-modal')
    : `<h2>Prontidão de hoje</h2><p class="muted">Ainda sem dados do relógio para hoje. Ela usa sono, variabilidade da FC e FC de repouso.</p>
       ${state.garmin && state.garmin.connected ? `<button class="btn" id="rd-refresh">Buscar no Garmin</button>` : `<a class="btn" href="#perfil">Conectar relógio</a>`}`);
  body.onclick = async (ev) => {
    const t = ev.target.closest("button, a"); if (!t) return;
    if (t.tagName === "A") return closeModal();
    try {
      if (t.id === "rd-apply") { t.disabled = true; const r = await api("/api/readiness/apply", { method: "POST", body: {} }); closeModal(); toast(r.changed.length ? `Ajustado: ${r.changed.join(", ")}` : "Nada para ajustar hoje", 5000); return viewHoje(); }
      if (t.id === "rd-refresh") { t.disabled = true; await api("/api/readiness/refresh", { method: "POST" }); closeModal(); return viewHoje(); }
    } catch (err) { t.disabled = false; toast(err.message, 5000); }
  };
}

function bindAgenda(sch) {
  bindAthleteCards();
  bindSteps();
  // redesenha a mesma tela em que a pessoa está: "Hoje" (com os cartões do topo) ou a semana que ela abriu
  const again = sch._again = () => viewHoje(location.hash.startsWith("#hoje/") ? location.hash.split("/")[1] : undefined);
  view.onclick = async (ev) => {
    const t = ev.target.closest("button, [data-ex]"); if (!t) return;
    const d = t.dataset;
    try {
      if (d.day) {
        sch._sel = d.day;
        $$(".wd", view).forEach((w) => { const on = w.dataset.day === d.day; w.classList.toggle("sel", on); w.setAttribute("aria-selected", on); });
        const panel = $("#day-panel"); panel.innerHTML = dayPanel(sch, d.day);
        panel.classList.remove("enter"); void panel.offsetWidth; panel.classList.add("enter");
        if (!t.classList.contains("wd")) panel.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      if (d.ex) return showExercise(d.ex);
      if (d.week) { location.hash = `#hoje/${d.week}`; return; }
      if (d.skip) return openSkip(d.skip, sch);
      if (d.match) {
        t.disabled = true; const r = await answerMatch(d.match);
        toast(r.closed ? `${r.closed.day_name} marcado como feito` : "Contou como treino extra", 3500); return again();
      }
      if (d.stag) { t.disabled = true; toast(stepsTagToast(await answerSteps(d.stag)), 5000); return again(); }
      if (await partnerAction(d, t, again)) return;
      if (t.id === "legacy-clean" || t.id === "legacy-keep") {
        t.disabled = true; const keep = t.id === "legacy-keep";
        const r = await api("/api/schedule/legacy", { method: "POST", body: { keep } });
        toast(keep ? "Ok, os treinos ficam como estão." : `Pronto: os treinos voltaram ao plano original${r.removed_sets ? ` (${r.removed_sets} ${r.removed_sets === 1 ? "série de reforço saiu" : "séries de reforço saíram"})` : ""}${state.garmin && state.garmin.connected ? ". Reenvie ao Garmin para atualizar o relógio" : ""}.`, 6000);
        return again();
      }
      if (d.undo) { await api(`/api/schedule/${d.undo}/undo`, { method: "POST" }); toast("Ajuste desfeito"); return again(); }
      if (d.rundo) { await api(`/api/readiness/undo/${d.rundo}`, { method: "POST" }); toast("Treino original de volta"); return again(); }
      if (d.wrev) { t.disabled = true; return openWatchCheck(+d.wrev).finally(() => { t.disabled = false; }); }
      if (d.wok) { t.disabled = true; await api(`/api/watch/inbox/${d.wok}/ok`, { method: "POST" }); toast("Ok, fica como o relógio mandou"); return again(); }
      if (d.alt) return openAlternative(+d.alt, again);
      if (d.gen) return openAlternative(null, again);
      if (d.comeback) { const e = sch.entries.find((x) => x.date === sch.today && x.status === "planejado" && x.kind === "forca"); return openAlternative(e ? e.id : null, again, { reason: "tempo", opts: { minutes: 25 } }); }
      if (d.altundo) { t.disabled = true; const e = await api(`/api/alternative/undo/${d.altundo}`, { method: "POST" }); toast(`Treino original de volta${e.garmin_outdated ? ". Reenvie ao relógio para atualizar" : ""}`, 4000); return again(); }
      if (d.altwhy) { const e = sch.entries.find((x) => String(x.id) === d.altwhy); if (e && e.alt) openModal(altResultHtml(e.alt, null) + `<button class="btn block" onclick="closeModal()">Ok</button>`); return; }
      if (t.id === "rd-open") return openReadiness(sch._rd);
      if (t.id === "rd-apply") { t.disabled = true; const r = await api("/api/readiness/apply", { method: "POST", body: {} }); toast(r.changed.length ? `Ajustado: ${r.changed.join(", ")}` : "Nada para ajustar hoje", 5000); return viewHoje(); }
      if (t.id === "rd-refresh") { t.disabled = true; await api("/api/readiness/refresh", { method: "POST" }); return viewHoje(); }
      if (d.move) {
        const days = [0, 1, 2, 3, 4, 5, 6].map((i) => addDays(sch.week_start, i)).filter((x) => x >= sch.today && x !== d.date);
        const body = openModal(`<h2>Mover para…</h2><div class="chips">${days.map((x) => `<button class="chip" data-to="${x}">${["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"][(new Date(x + "T12:00:00").getDay() + 6) % 7]} ${ddmm(x)}</button>`).join("")}</div>`);
        body.onclick = async (e2) => {
          const to = e2.target.dataset.to; if (!to) return;
          const r = await api(`/api/schedule/${d.move}/move`, { method: "POST", body: { date: to } });
          closeModal(); toast(r.warning || "Treino movido", r.warning ? 6000 : 2500); again();
        };
        return;
      }
      if (d.push) { t.disabled = true; const r = await api(`/api/schedule/${d.push}/push`, { method: "POST" }); toast(`Enviado ao Garmin para ${r.scheduled}`); return again(); }
      if (t.id === "push-outdated") {
        t.disabled = true; t.textContent = "Enviando…";
        const r = await api("/api/schedule/push-week", { method: "POST", body: { only_outdated: true } });
        toast(`⌚ ${r.length} ${r.length === 1 ? "treino atualizado" : "treinos atualizados"} no Garmin. Sincronize o relógio pelo app Garmin Connect.`, 6000); return again();
      }
      if (t.id === "push-week") {
        t.disabled = true;
        const r = await api("/api/schedule/push-week", { method: "POST", body: { only_outdated: false } });
        toast(`${r.length} treinos enviados ao Garmin Connect`); return again();
      }
      if (t.id === "gen-open") return openGenerator();
      if (t.id === "rebuild") { if (!confirm("Recriar os treinos ainda não feitos desta semana a partir da planilha?")) return; await api("/api/schedule/rebuild", { method: "POST" }); return again(); }
    } catch (err) { t.disabled = false; toast(err.message, 5000); if (/login|conectado/i.test(err.message)) location.hash = "#perfil"; }
  };
}

async function openSkip(id, sch) {
  const body = openModal(`<h2>Não vou treinar</h2><div class="sk"><span class="sk-line"></span><span class="sk-line"></span><span class="sk-line"></span></div>`);
  let pre;
  try { pre = await api(`/api/schedule/${id}/skip`, { method: "POST", body: { dry_run: true } }); } catch (e) { body.innerHTML = `<h2>Não vou treinar</h2><p class="pill bad">${esc(e.message)}</p>`; return; }
  const missed = pre.entry.status === "perdido";
  const mover = pre.options.find((o) => o.id === "mover");
  const st = { strategy: pre.recommended, to: (mover && mover.to_date) || null, run: false };
  const dname = (iso) => { const dt = new Date(iso + "T12:00:00"); return `${WD_LONG[dt.getDay()]} ${ddmm(iso)}`; };
  const draw = () => {
    body.innerHTML = `<h2>${missed ? "Treino perdido" : "Não vou treinar"}</h2>
      <p class="muted" style="margin:-6px 0 12px">${esc(pre.entry.day_name)} · ${esc(dname(pre.entry.date))}</p>
      ${pre.pending ? `<div class="rec atencao"><b>Você registrou um treino livre que pode ser este.</b>
        <p class="small" style="margin:4px 0 8px">${pre.pending.coverage != null ? `Ele cobriu cerca de ${pctTxt0(pre.pending.coverage)} deste treino. ` : ""}Se foi ele, marque como feito em vez de pular.</p>
        <button class="btn sm" id="sk-was">Sim, foi esse treino</button></div>` : ""}
      <div class="opts" role="radiogroup" aria-label="O que fazer com este treino">${pre.options.map((o) => `<button class="opt ${st.strategy === o.id ? "on" : ""}" role="radio" aria-checked="${st.strategy === o.id}" data-opt="${o.id}" ${o.available ? "" : "disabled"}>
        <span class="opt-h"><b>${esc(missed && o.id === "seguir" ? "Seguir em frente" : o.title)}</b>${o.recommended ? `<span class="pill ok">recomendado</span>` : ""}</span>
        <span class="small">${esc(o.available ? (o.text || "") : (o.reason || "Indisponível"))}</span>${o.available && o.note && st.strategy === o.id ? `<span class="small muted">${esc(o.note)}</span>` : ""}</button>`).join("")}</div>
      ${st.strategy === "mover" ? `<div class="mv-days"><b class="small">Para qual dia?</b>${(pre.move_days || []).map((x) => `<button class="mv-day ${st.to === x.date ? "on" : ""}" data-to="${x.date}" ${x.ok ? "" : "disabled"}>
        <b>${esc(x.label)}</b><span class="small">${esc(x.ok ? (x.warnings.length ? "Atenção: " + x.warnings.join("; ") : "sem conflitos") : x.reason)}</span></button>`).join("")}</div>` : ""}
      ${pre.run ? `<label class="row gap opt-run"><input type="checkbox" id="sk-run" ${st.run ? "checked" : ""}> <span>Também não vou fazer: ${esc(pre.run.name)}</span></label>` : ""}
      ${pre.why ? `<p class="small muted" style="margin:10px 0">${esc(pre.why)}</p>` : ""}
      <button class="btn block" id="sk-go" ${st.strategy === "mover" && !st.to ? "disabled" : ""}>Confirmar</button>
      <button class="act" id="sk-cancel" style="margin-top:8px">Cancelar</button>`;
  };
  draw();
  body.onchange = (e) => { if (e.target.id === "sk-run") st.run = e.target.checked; };
  body.onclick = async (e) => {
    if (st.busy) return;  // já enviando: ignora toques até a resposta
    const o = e.target.closest("[data-opt]"); if (o) { st.strategy = o.dataset.opt; return draw(); }
    const to = e.target.closest("[data-to]"); if (to) { st.to = to.dataset.to; return draw(); }
    if (e.target.id === "sk-cancel") return closeModal();
    if (e.target.id === "sk-was" && pre.pending) {
      st.busy = true;
      try { const r = await answerMatch(`${pre.pending.session_id}:${id}`); closeModal(); toast(r.closed ? `${r.closed.day_name} marcado como feito` : "Pronto", 3500); (sch._again || viewHoje)(); }
      catch (err) { st.busy = false; toast(err.message, 5000); }
      return;
    }
    if (e.target.id !== "sk-go") return;
    e.target.disabled = true; st.busy = true; e.target.textContent = "Enviando…";
    try {
      const r = await api(`/api/schedule/${id}/skip`, { method: "POST", body: { strategy: st.strategy, to_date: st.strategy === "mover" ? st.to : null, skip_endurance: st.run } });
      body.innerHTML = `<h2>${esc(r.title || "Treino pulado")}</h2>
        <ul>${r.changes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        <p class="small muted">Você pode desfazer pelo cartão do dia.${r.strategy !== "seguir" && state.garmin && state.garmin.connected ? " Treinos que mudaram e já estavam no Garmin foram marcados para reenviar." : ""}</p>
        <button class="btn" id="ok-close">Ok</button>`;
      const card = body.closest(".modal-card"); if (card) card.scrollTop = 0;
      body.onclick = (e2) => { if (e2.target.id === "ok-close") closeModal(); };
      (sch._again || viewHoje)();
    } catch (err) { st.busy = false; draw(); toast(err.message, 5000); }
  };
}

/* ------------------------------------------------------------- GERADOR -- */
const MONTH_OPTS = [[2, "Começando (< 3 meses)"], [4, "3 a 6 meses"], [9, "6 a 12 meses"], [18, "1 a 2 anos"], [36, "2 a 5 anos"], [72, "Mais de 5 anos"]];
async function openGenerator() {
  const [opts, eopts, fresh] = await Promise.all([api("/api/generator/options"), api("/api/endurance/options"), api("/api/profile")]);
  state.profile = fresh;
  const p = state.profile || {};
  const ecfg = JSON.parse(JSON.stringify(p.endurance || {})); ecfg.sports = ecfg.sports || {};
  const avail = p.available_days || [0, 2, 4];
  const nearest = (m) => MONTH_OPTS.reduce((a, b) => (Math.abs(b[0] - (m ?? 12)) < Math.abs(a[0] - (m ?? 12)) ? b : a))[0];
  let seed = 0;
  const body = openModal(`<h2>Gerar treino automático</h2>
    ${state.profile && state.profile.schedule_plan_id ? `<p class="small rec">Gera um treino novo com as regras atuais (descanso pela evidência, aquecimento do exercício, aparelhos novos). Você vê a prévia antes; o anterior fica guardado em Planos. Histórico, cargas, aparelhos fotografados e trocas “usar sempre” continuam.</p>` : ""}
    <div class="grid2">
      <div class="field"><label>Tempo de prática</label><select id="gm-months">${MONTH_OPTS.map(([v, t]) => `<option value="${v}" ${nearest(p.training_months) === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Objetivo</label><select id="gm-goal">${opts.goals.map((g) => `<option value="${g.id}" ${p.goal === g.id ? "selected" : ""}>${esc(g.label)}</option>`).join("")}</select></div>
      <div class="field"><label>Minutos de treino por dia</label><input id="gm-min" type="number" min="20" max="180" step="5" value="${p.session_minutes || 60}"></div>
      <div class="field"><label>Meta de cardio (min/semana, vazio = pelo objetivo)</label><input id="gm-cardio" type="number" min="0" step="10" value="${p.cardio_target_min ?? ""}"></div>
    </div>
    <label>Dias que você pode treinar</label>
    <div class="chips" id="gm-days">${opts.weekdays.map((w, i) => `<label class="chip"><input type="checkbox" value="${i}" ${avail.includes(i) ? "checked" : ""}> ${w}</label>`).join("")}</div>
    <label style="margin-top:12px">Junto com a musculação</label>
    <div class="chips" id="gm-sports">${eopts.sports.map((sp) => `<label class="chip"><input type="checkbox" data-sp="${sp.id}" ${(ecfg.sports[sp.id] || {}).enabled ? "checked" : ""}> ${SPORT_ICON[sp.id] || ""} ${esc(sp.name)}</label>`).join("")}</div>
    <div class="stack" id="gm-sport-opts" style="margin-top:8px">${eopts.sports.map((sp) => { const c = ecfg.sports[sp.id] || {}; return `<div class="gm-sport" data-sp="${sp.id}" ${c.enabled ? "" : "hidden"}>
      <div class="grid2"><div class="field"><label>${SPORT_ICON[sp.id] || ""} ${esc(sp.name)}: onde</label><select data-k="env">${sp.envs.map((e) => `<option value="${e.id}" ${(c.env || sp.envs[0].id) === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></div>
      <div class="field"><label>Vezes por semana</label><input type="number" min="1" max="7" data-k="sessions" value="${c.sessions || sp.sessions}"></div></div></div>`; }).join("")}</div>
    <label class="row gap" id="gm-same-wrap" style="margin-top:8px" ${Object.values(ecfg.sports).some((c) => c.enabled) ? "" : "hidden"}><input type="checkbox" id="gm-same" ${p.same_session !== false ? "checked" : ""}>
      Musculação e esporte no mesmo treino (os minutos acima valem para o dia todo: força primeiro, esporte logo depois)</label>
    <p class="small muted">Entra na mesma semana da musculação: longe dos dias de perna pesada, longo no fim de semana, ~80% leve.
      Metas de prova, triathlon e zonas ficam em <a href="#esportes" id="gm-esportes">Esportes</a>. Limitações e equipamentos vêm do Perfil.</p>
    <div id="gm-out" style="margin-top:12px"></div>
    <div class="gm-bar"><p class="small muted" id="gm-hint">1º veja a prévia. Nada muda até você tocar em <b>Usar este treino</b>.</p>
      <div class="row gap wrap"><button class="btn" id="gm-prev">Ver prévia</button><button class="btn ghost" id="gm-var" hidden>Outra variação</button><button class="btn" id="gm-apply" hidden>✓ Usar este treino</button></div></div>`);
  const read = () => ({
    training_months: +$("#gm-months", body).value, goal: $("#gm-goal", body).value,
    session_minutes: +$("#gm-min", body).value || 60,
    cardio_target_min: $("#gm-cardio", body).value === "" ? null : +$("#gm-cardio", body).value,
    available_days: $$("#gm-days input", body).filter((c) => c.checked).map((c) => +c.value),
    endurance: readSports(),
    same_session: $("#gm-same", body).checked,
  });
  const readSports = () => {
    const out = JSON.parse(JSON.stringify(ecfg));
    $$(".gm-sport", body).forEach((row) => {
      const id = row.dataset.sp; const c = out.sports[id] = { ...(out.sports[id] || {}) };
      c.enabled = $(`#gm-sports input[data-sp="${id}"]`, body).checked;
      c.env = $("[data-k=env]", row).value;
      c.sessions = Math.max(1, Math.min(7, +$("[data-k=sessions]", row).value || 1));
    });
    return out;
  };
  $("#gm-sports", body).onchange = (e) => {
    const id = e.target.dataset.sp;
    if (id) $(`.gm-sport[data-sp="${id}"]`, body).hidden = !e.target.checked;
    $("#gm-same-wrap", body).hidden = !$$("#gm-sports input", body).some((c) => c.checked);
  };
  const preview = async () => {
    const prof = read();
    if (!prof.available_days.length) return toast("Escolha pelo menos um dia");
    $("#gm-out", body).innerHTML = `<p class="muted">Montando…</p>`;
    const plan = await api("/api/generator/preview", { method: "POST", body: { profile: prof, seed } });
    $("#gm-out", body).innerHTML = `<h3>Por que assim</h3><ul class="small">${plan.rationale.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      ${previewDays(plan)}
      <h3>Volume semanal planejado</h3>${muscleBars(plan.analysis.muscles, "sets_week")}`;
    // nomes dos exercícios
    const ids = [...new Set(plan.days.flatMap((d) => d.items.map((i) => i.exercise_id)))];
    const names = {}; await Promise.all(ids.map(async (id) => { try { names[id] = (await api(`/api/exercises/${encodeURIComponent(id)}`)).name; } catch (_) { /* */ } }));
    $$("#gm-out [data-ex]", body).forEach((el) => { if (names[el.dataset.ex]) el.textContent = names[el.dataset.ex]; });
    $("#gm-var", body).hidden = false; $("#gm-apply", body).hidden = false; $("#gm-prev", body).hidden = true;
    $("#gm-hint", body).innerHTML = "Gostou? Toque em <b>✓ Usar este treino</b> para trocar (o anterior fica em Planos).";
  };
  $("#gm-esportes", body).onclick = () => closeModal();
  $("#gm-prev", body).onclick = () => preview().catch((e) => toast(e.message));
  $("#gm-var", body).onclick = () => { seed += 1; preview().catch((e) => toast(e.message)); };
  $("#gm-apply", body).onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Montando a semana…";
    let plan;
    try { plan = await api("/api/generator/apply", { method: "POST", body: { profile: read(), seed } }); }
    catch (err) { e.target.disabled = false; e.target.textContent = "✓ Usar este treino"; return toast(err.message, 6000); }
    state.profile = await api("/api/profile");
    const gst = await api("/api/garmin/status").catch(() => null);
    // a semana nova já fica desenhada atrás da janela
    closeModal();
    view.dataset.route = ""; view.setAttribute("aria-busy", "true");
    if (location.hash.startsWith("#hoje") || !location.hash) route(); else location.hash = "#hoje";
    for (let k = 0; k < 40 && !(view.dataset.route === "hoje" && view.getAttribute("aria-busy") !== "true"); k++) await new Promise((r) => setTimeout(r, 100));
    const done = openModal(`<h2>Treino novo pronto ✓</h2><p>“${esc(plan.name || "Treino")}” virou o seu treino e a semana foi remontada a partir de hoje. O anterior ficou guardado em Planos.</p>
      ${gst && gst.connected ? `<div class="rec"><b>Falta o relógio:</b> o Garmin ainda tem o treino antigo. Envie agora para trocar (os antigos desta semana são apagados lá).</div>
        <button class="btn block" id="gm-push">⌚ Enviar a semana ao Garmin</button>` : ""}
      <a class="btn ghost block" href="#hoje" id="gm-see" style="margin-top:8px">Ver minha semana</a>`);
    const goHoje = () => { closeModal(); route(); };
    $("#gm-see", done).onclick = (ev) => { ev.preventDefault(); goHoje(); };
    const gp = $("#gm-push", done);
    if (gp) gp.onclick = async () => {
      gp.disabled = true; gp.textContent = "Enviando…";
      try { const r = await api("/api/schedule/push-week", { method: "POST", body: { only_outdated: false } }); toast(`⌚ ${r.length} treinos enviados ao Garmin. Sincronize o relógio pelo app Garmin Connect.`, 6000); goHoje(); }
      catch (err) { gp.disabled = false; gp.textContent = "⌚ Tentar de novo"; toast(err.message, 6000); }
    };
  };
}

// dias com musculação + esporte no mesmo treino aparecem juntos, com o tempo total do dia
function previewDays(plan) {
  const same = plan.generator && plan.generator.same_session; const budget = plan.generator && plan.generator.session_minutes;
  const groups = [];
  plan.days.forEach((d) => { const g = groups[groups.length - 1]; if (g && g[0].weekday === d.weekday) g.push(d); else groups.push([d]); });
  const one = (d, inGroup) => `<div class="day"><b>${esc(inGroup ? d.name.split(" – ").slice(1).join(" – ") : d.name)}</b>${timeBar(inGroup ? { ...d.time, orcamento: null } : d.time)}${itemsByPhase(d.items.map((i) => ({ ...i, _name: i._name })))}</div>`;
  return groups.map((g) => {
    if (g.length < 2) return one(g[0], false);
    const tot = Math.round(g.reduce((a, d) => a + d.time.total, 0));
    const head = `<b>${esc(g[0].name.split(" – ")[0])}</b> · ${same ? "mesmo treino" : "dois treinos"}: <b>${tot} min</b>${budget ? ` de ${budget}` : ""}${same ? " — musculação primeiro, corrida logo depois" : ""}`;
    return `<div class="day-group"><div class="small" style="margin:10px 0 4px">${head}</div>${g.map((d) => one(d, true)).join("")}</div>`;
  }).join("");
}

/* ---------------------------------------------------------------- COACH -- */
const LVL = { agir: ["bad", "Agir"], atencao: ["warn", "Atenção"] };
const WELL_ICON = ["", "😀", "🙂", "😐", "😕", "😣"];
function coachChrome() {
  const me = state.me; const isCoach = me && me.logged_in && me.user.is_coach; const act = actAs();
  let pill = $("#coach-pill");
  if (isCoach && !pill) { $(".topbar").insertAdjacentHTML("beforeend", `<a id="coach-pill" class="pill ok" href="#coach" style="text-decoration:none;margin-left:6px">Coach</a>`); }
  if (!isCoach && pill) pill.remove();
  let bar = $("#act-bar");
  if (act && !bar) {
    $(".topbar").insertAdjacentHTML("afterend", `<div id="act-bar" class="rec atencao small row between gap" style="margin:8px 16px 0">
      <span>👤 Você está vendo <b>${esc(act.name)}</b> como coach: tudo que mudar vale para essa conta.</span>
      <button class="btn sm" id="act-exit">Voltar ao painel</button></div>`);
    $("#act-exit").onclick = () => { sessionStorage.removeItem("gg_act"); $("#act-bar").remove(); state.profile = null; location.hash = "#coach"; route(); };
  }
  if (!act && bar) bar.remove();
}
async function viewCoach() {
  if (actAs()) { sessionStorage.removeItem("gg_act"); coachChrome(); }
  const d = await api("/api/coach/athletes");
  const c = d.counts;
  const regions = d.regions;
  const stat = (label, val, cls = "") => `<div class="cstat ${cls}"><b>${val ?? "—"}</b><span>${label}</span></div>`;
  const pct = (v) => v == null ? "—" : `${v}%`;
  const fmt1 = (v) => v == null ? "—" : (+v).toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  const card = (a) => {
    const worst = a.flags.some((f) => f.level === "agir") ? "bad" : a.flags.length ? "warn" : "ok";
    const lims = Object.fromEntries((a.limitations || []).map((l) => [l.key, l.severity]));
    const w = a.wellness || {};
    return `<div class="card coach-card ${worst}" data-aid="${a.id}">
      <div class="row between gap"><h3 style="margin:0"><span class="dot-lg ${worst}"></span>${esc(a.name)}</h3>
        <span class="small muted">${a.last_training ? `último treino ${ddmm(a.last_training)}` : "sem treinos"}</span></div>
      ${a.flags.length ? `<ul class="flags">${a.flags.map((f) => `<li><span class="pill ${LVL[f.level][0]}">${LVL[f.level][1]}</span> ${esc(f.text)}
        <button class="icon-btn mute" title="Silenciar este alerta para este atleta" data-mute="${esc(f.code)}">×</button></li>`).join("")}</ul>`
        : `<p class="small" style="margin:6px 0">✅ Tudo dentro do normal.</p>`}
      <div class="cstats">${stat("adesão 7 dias", pct(a.compliance_7d), (a.compliance_7d ?? 100) < 70 ? "warn" : "")}
        ${stat("prontidão", a.readiness ?? "—")}${stat("sono 7 dias", a.sleep_7d == null ? "—" : `${fmt1(a.sleep_7d)} h`)}
        ${stat("carga semana", a.load_change == null ? "—" : `${a.load_change > 0 ? "+" : ""}${a.load_change}%`)}</div>
      ${a.health_shared === false ? `<p class="small muted">Não compartilha saúde/sono/check-in com você.</p>` : a.checkin_date ? `<p class="small">Check-in ${ddmm(a.checkin_date)}: ${Object.entries(w).map(([k, v]) => `${WELL_ICON[v]} ${esc(({ sleep_q: "sono", fatigue: "cansaço", soreness: "dor muscular", stress: "estresse", mood: "humor" })[k])}`).join(" · ")}
        ${a.checkin_note ? `<br><i>“${esc(a.checkin_note)}”</i>` : ""}</p>` : `<p class="small muted">Sem check-in recente.</p>`}
      ${(a.suggestions || []).map((sg) => `<div class="rec ${sg.severity === "evitar" ? "alerta" : "atencao"} small">Dor ${sg.pain}/10 em <b>${esc(sg.label.toLowerCase())}</b>${sg.worse ? " (piorando)" : ""} →
        sugestão: <b>${sg.severity}</b> e trocar os exercícios de risco${sg.refer ? ". Indique avaliação com fisioterapeuta/médico." : "."}
        <button class="btn sm" data-tension="${sg.region}" data-sev="${sg.severity}">Aplicar</button></div>`).join("")}
      <div class="row gap wrap" style="margin-top:8px">
        <button class="btn sm" data-open="${a.id}" data-name="${esc(a.name)}">Abrir</button>
        <button class="btn sm ghost" data-adjust="hoje">Hoje mais leve</button>
        <button class="btn sm ghost" data-adjust="semana">Semana leve</button>
        <button class="btn sm ghost" data-note>Recado</button></div>
      <details class="tension"><summary class="small"><b>Áreas de tensão</b>${Object.keys(lims).length ? ` · ${Object.entries(lims).filter(([k]) => regions[k]).map(([k, v]) => `${esc(regions[k].toLowerCase())} (${v})`).join(", ")}` : ""}</summary>
        <p class="small muted">Toque para alternar: livre → cuidado → evitar. O app troca na hora os exercícios de risco da planilha e da semana.</p>
        <div class="chips">${Object.entries(regions).map(([k, lbl]) => { const sev = lims[k] || ""; const pain = (a.pain || {})[k];
          return `<button class="chip tchip ${sev}" data-region="${k}" data-cur="${sev}">${esc(lbl)}${pain ? ` <b>${pain}/10</b>` : ""}${sev ? ` · ${sev}` : ""}</button>`; }).join("")}</div></details>
    </div>`;
  };
  view.innerHTML = `<div class="card"><div class="row between gap wrap"><h2 style="margin:0">Coach</h2>
      <button class="btn sm ghost" id="coach-cfg">Alertas ⚙</button></div>
    <div class="row gap wrap" style="margin-top:8px"><span class="pill bad">Agir: ${c.agir}</span><span class="pill warn">Atenção: ${c.atencao}</span><span class="pill ok">Ok: ${c.ok}</span></div>
    <p class="small muted">Quem precisa de você aparece primeiro. Os alertas comparam cada atleta com ele mesmo (últimas 4 semanas) e servem para puxar conversa, não para diagnosticar.</p></div>
    ${d.athletes.length ? d.athletes.map(card).join("") : `<div class="card"><p>Nenhum atleta ainda.</p><p class="small">Cada pessoa autoriza você em <b>Perfil → Meu coach</b> (convide pelo Perfil → Pessoas). Ela escolhe se compartilha saúde e sono e pode remover o acesso quando quiser.</p></div>`}`;
  view.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const cardEl = t.closest("[data-aid]"); const aid = cardEl && cardEl.dataset.aid;
    try {
      if (t.id === "coach-cfg") return coachSettings();
      if (t.dataset.open) { sessionStorage.setItem("gg_act", JSON.stringify({ id: +t.dataset.open, name: t.dataset.name })); state.profile = null; location.hash = "#hoje"; return; }
      if (t.dataset.mute) { await api(`/api/coach/athletes/${aid}/mute`, { method: "POST", body: { code: t.dataset.mute } }); toast("Alerta silenciado para este atleta"); return viewCoach(); }
      if (t.dataset.adjust) {
        const r = await api(`/api/coach/athletes/${aid}/adjust`, { method: "POST", body: { scope: t.dataset.adjust, action: "moderar" } });
        toast(r.changed.length ? `Mais leve: ${r.changed.join(", ")}` : "Nada planejado para ajustar", 4000); return;
      }
      if (t.dataset.note !== undefined) {
        const text = prompt("Recado para o atleta (aparece no Hoje dele):"); if (!text) return;
        await api(`/api/coach/athletes/${aid}/note`, { method: "POST", body: { text } }); toast("Recado enviado"); return;
      }
      if (t.dataset.region || t.dataset.tension) {
        const region = t.dataset.region || t.dataset.tension;
        const next = t.dataset.tension ? t.dataset.sev : ({ "": "cuidado", cuidado: "evitar", evitar: "nenhuma" })[t.dataset.cur];
        t.disabled = true;
        const r = await api(`/api/coach/athletes/${aid}/tension`, { method: "POST", body: { region, severity: next } });
        toast(r.changes.length ? `Trocados: ${r.changes.slice(0, 3).join("; ")}${r.changes.length > 3 ? "…" : ""}` : `${regions[region]}: ${next === "nenhuma" ? "livre" : next}`, 6000);
        return viewCoach();
      }
    } catch (err) { toast(err.message, 5000); }
  };
}
async function coachSettings() {
  const { settings: s, defaults } = await api("/api/coach/settings");
  const rows = [["compliance_warn", "Adesão em 7 dias: atenção abaixo de (%)"], ["compliance_act", "Adesão: agir abaixo de (%)"],
    ["inactive_warn", "Dias sem treinar: atenção a partir de"], ["inactive_act", "Dias sem treinar: agir a partir de"],
    ["readiness_warn", "Prontidão: atenção abaixo de"], ["readiness_act", "Prontidão: agir abaixo de"],
    ["sleep_warn", "Sono (média 7 noites): atenção abaixo de (h)"], ["sleep_act", "Sono: agir abaixo de (h)"],
    ["rhr_warn", "FC de repouso acima do normal: atenção (+bpm)"], ["rhr_act", "FC de repouso: agir (+bpm)"],
    ["load_jump_warn", "Pico de carga semanal: atenção (+%)"], ["load_jump_act", "Pico de carga: agir (+%)"],
    ["wellness_warn", "Check-in: item ruim a partir de (1–5)"],
    ["pain_warn", "Dor: atenção a partir de (0–10)"], ["pain_act", "Dor: trocar/reduzir a partir de"], ["pain_avoid", "Dor: evitar + profissional a partir de"]];
  const body = openModal(`<h2>Alertas do coach</h2>
    <p class="small muted">Valores iniciais baseados em evidência: sono &lt; 7 h (consenso BJSM 2021), pico de carga semana a semana em vez de ACWR (críticas de Impellizzeri/Lolli),
      VFC e check-in comparados com o normal do próprio atleta (Plews, Buchheit, Saw 2016) e dor pelo modelo de Silbernagel (até 5/10, passando até a manhã seguinte e sem piorar).</p>
    <div class="grid2">${rows.map(([k, l]) => `<div class="field"><label>${l} <span class="muted">(padrão ${defaults[k]})</span></label><input type="number" step="any" data-k="${k}" value="${s[k]}"></div>`).join("")}</div>
    <button class="btn" id="cfg-save">Salvar</button>`);
  $("#cfg-save", body).onclick = async () => {
    const out = {}; $$("[data-k]", body).forEach((i) => { out[i.dataset.k] = i.value; });
    await api("/api/coach/settings", { method: "PUT", body: out }); closeModal(); toast("Alertas salvos"); viewCoach();
  };
}
// ---- lado do atleta: check-in e recados
async function athleteCards() {
  if (!(state.me && state.me.logged_in) || actAs()) return "";
  const [ck, inbox] = await Promise.all([api("/api/checkin").catch(() => null), api("/api/coach/inbox").catch(() => null)]);
  let html = "";
  if (inbox && (inbox.notes.length || inbox.changes.length) && (inbox.unread || inbox.changes.length)) {
    html += `<div class="card coach-inbox"><h3>Do seu coach</h3>
      ${inbox.notes.slice(0, 2).map((n) => `<p class="small"><b>${esc(n.coach)}</b> · ${fmtDate(n.at)}<br>${esc(n.text)}</p>`).join("")}
      ${inbox.changes.length ? `<ul class="plain small muted">${inbox.changes.map((x) => `<li>${fmtDate(x.at)}: ${esc(x.text)}</li>`).join("")}</ul>` : ""}
      <button class="btn sm ghost" id="inbox-ok">Ok, vi</button></div>`;
  }
  if (ck && !ck.today) {
    const opts = (k) => [1, 2, 3, 4, 5].map((v) => `<button type="button" class="wbtn" data-w="${k}" data-v="${v}" title="${v}">${WELL_ICON[v]}</button>`).join("");
    html += `<div class="card" id="checkin"><h3>Check-in de hoje <span class="small muted">(10 segundos)</span></h3>
      ${Object.entries(ck.wellness).map(([k, l]) => `<div class="row between gap wrow"><span class="small">${esc(l)}</span><span>${opts(k)}</span></div>`).join("")}
      <p class="small" style="margin:10px 0 4px">Alguma dor ou tensão? Toque na região e dê uma nota de 0 a 10.</p>
      <div class="chips">${Object.entries(ck.regions).map(([k, l]) => `<button type="button" class="chip pchip" data-p="${k}">${esc(l)}</button>`).join("")}</div>
      <div class="row gap" style="margin-top:8px"><input id="ck-note" placeholder="Observação (opcional)" style="flex:1"><button class="btn" id="ck-send">Enviar</button></div></div>`;
  }
  return html;
}
function bindAthleteCards() {
  const ok = $("#inbox-ok"); if (ok) ok.onclick = async () => { await api("/api/coach/inbox/read", { method: "POST" }); ok.closest(".card").remove(); };
  const box = $("#checkin"); if (!box) return;
  const val = {}; const pain = {};
  box.onclick = async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.w) { val[b.dataset.w] = +b.dataset.v; $$(`[data-w="${b.dataset.w}"]`, box).forEach((x) => x.classList.toggle("on", x === b)); return; }
    if (b.dataset.p) {
      const cur = pain[b.dataset.p] || 0; const v = prompt(`Dor em ${b.textContent.replace(/ \d+\/10$/, "")} (0 = nenhuma, 10 = máxima):`, cur || 3);
      if (v === null) return; const n = Math.max(0, Math.min(10, parseInt(v, 10) || 0));
      pain[b.dataset.p] = n; b.textContent = b.textContent.replace(/ \d+\/10$/, "") + (n ? ` ${n}/10` : ""); b.classList.toggle("on", n > 0); return;
    }
    if (b.id === "ck-send") {
      if (!Object.keys(val).length && !Object.keys(pain).length) return toast("Toque nas carinhas ou numa região");
      const r = await api("/api/checkin", { method: "POST", body: { ...val, pain, note: $("#ck-note").value } });
      box.innerHTML = `<p class="small">Check-in enviado ✔${r.suggestions.length ? `<br>${r.suggestions.map((sg) => `Dor ${sg.pain}/10 em ${esc(sg.label.toLowerCase())}: ${sg.severity === "evitar" ? "evite os movimentos que doem e procure um profissional" : "reduza a carga dessa região e troque o que doer"}${sg.worse ? " (piorou)" : ""}.`).join("<br>")} Seu coach foi avisado.` : ""}</p>`;
    }
  };
}
async function coachMineCard() {
  const me = state.me; if (!(me && me.logged_in) || actAs()) return "";
  const m = await api("/api/coach/mine").catch(() => null);
  if (!m || !m.coaches.length) return "";
  return `<div class="card"><h2>Meu coach</h2><p class="small muted">O coach vê seus treinos, planilha e agenda e pode ajustá-los (você vê o que ele mudou no Hoje).
    Saúde, sono e check-in só se você deixar. Ele nunca vê sua senha, seu login do Garmin ou seu código pessoal.</p>
    <ul class="list">${m.coaches.map((c) => `<li class="row between gap wrap"><span><b>${esc(c.name)}</b>${c.linked ? ` <span class="pill ok">tem acesso</span>` : ""}</span>
      <span class="row gap">${c.linked ? `<label class="small row gap" style="margin:0"><input type="checkbox" data-share="${c.id}" ${c.share_health ? "checked" : ""}> saúde e sono</label>
        <button class="btn sm ghost" data-unlink="${c.id}">Remover acesso</button>` : `<button class="btn sm" data-link="${c.id}">Autorizar</button>`}</span></li>`).join("")}</ul></div>`;
}
/* ---- treinar com o companheiro: parceria, puxar o treino do outro, placar ---- */
async function partnerMineCard() {
  const me = state.me; if (!(me && me.logged_in) || actAs()) return "";
  const p = await api("/api/partners").catch(() => null);
  if (!p || !p.enabled || !p.people.length) return "";
  const btn = (x) => x.status === "parceiro" ? `<button class="btn sm ghost" data-premove="${x.id}">Desfazer</button>`
    : x.status === "convidou" ? `<button class="btn sm" data-pacc="${x.id}">Aceitar</button><button class="btn sm ghost" data-premove="${x.id}">Agora não</button>`
    : x.status === "convidado" ? `<span class="small muted">convite enviado</span><button class="btn sm ghost" data-premove="${x.id}">Cancelar</button>`
    : `<button class="btn sm" data-pinv="${x.id}">Convidar</button>`;
  return `<div class="card" id="partner-mine"><h2>Treinar com alguém</h2>
    <p class="small muted">Parceiros de treino veem um do outro só o treino: o que está planejado, o que foi feito (exercícios, séries, tempo e calorias) e o placar da semana. Nunca saúde, sono, passos ou peso. Dá para desfazer quando quiser.</p>
    <ul class="list">${p.people.map((x) => `<li class="row between gap wrap"><span><b>${esc(x.name)}</b> <span class="small muted">${partnerWho(x)}</span>${x.status === "parceiro" ? ` <span class="pill ok">parceiro de treino</span>` : x.status === "convidou" ? ` <span class="pill warn">convidou você</span>` : ""}</span>
      <span class="row gap">${btn(x)}</span></li>`).join("")}</ul></div>`;
}
function bindPartnerMine() {
  const c = $("#partner-mine"); if (!c) return;
  c.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t || t.disabled) return; const d = t.dataset; t.disabled = true;
    try {
      if (d.pinv) { const r = await api(`/api/partners/${d.pinv}/invite`, { method: "POST" }); toast(r.status === "parceiro" ? "Agora vocês são parceiros de treino" : "Convite enviado: aparece para a pessoa na tela Hoje", 4500); }
      else if (d.pacc) { await api(`/api/partners/${d.pacc}/accept`, { method: "POST" }); toast("Parceria de treino aceita"); }
      else if (d.premove) { if (!confirm("Desfazer/cancelar a parceria de treino com esta pessoa?")) { t.disabled = false; return; } await api(`/api/partners/${d.premove}`, { method: "DELETE" }); }
      viewPerfil();
    } catch (err) { t.disabled = false; toast(err.message, 5000); }
  };
}
// botões dos cartões de parceria (Hoje, com ou sem treino montado). Devolve true se o toque era de parceria.
async function partnerAction(d, t, again) {
  if (d.oxuse || d.oxdrop) {
    const cs = store("gg_outbox_conflict") || []; const c = cs[+(d.oxuse || d.oxdrop)]; if (!c) { again(); return true; }
    t.disabled = true;
    if (d.oxuse) { await api(`/api/sessions/${c.existing_id}`, { method: "PUT", body: { ...c.payload, edit: true } }); toast("Pronto: ficou o treino guardado no celular (um registro só).", 5000); }
    else toast("Ok, ficou o registro que já estava.", 4000);
    cs.splice(+(d.oxuse || d.oxdrop), 1); store("gg_outbox_conflict", cs.length ? cs : null); again(); return true;
  }
  if (d.msame || d.mdiff) {  // "é o mesmo treino que o relógio gravou?"
    const [w, a] = (d.msame || d.mdiff).split(":").map(Number); t.disabled = true;
    const r = await api("/api/sessions/merge-answer", { method: "POST", body: { watch_id: w, app_id: a, same: !!d.msame } });
    toast(r.merged ? `Juntei: o treino ficou com o tempo do relógio (${r.minutes} min) e conta uma vez só.` : "Ok, ficam como dois treinos.", 5000);
    again(); return true;
  }
  if (d.paccept) { t.disabled = true; await api(`/api/partners/${d.paccept}/accept`, { method: "POST" }); toast("Parceria de treino aceita"); again(); return true; }
  if (d.pdecline) { t.disabled = true; await api(`/api/partners/${d.pdecline}`, { method: "DELETE" }); again(); return true; }
  if (d.pday) { openPartnerDay(+d.pday, d.pname, d.pdate || undefined); return true; }
  if (d.myses) { openMySession(+d.myses, again); return true; }
  if (d.mydel) {
    if (!confirm("Apagar este registro repetido? O outro continua.")) return true;
    t.disabled = true; await api(`/api/sessions/${d.mydel}`, { method: "DELETE" }); toast("Registro repetido apagado", 4000); again(); return true;
  }
  if (d.pduel) { openDuel(+d.pduel); return true; }
  return false;
}
const kcalTxt = (v, est) => `${est ? "~" : ""}${fmtN(v)} kcal`;
// "✓ Você registrou": o que ficou salvo do treino com essa pessoa (e aviso quando o mesmo treino foi registrado 2 vezes)
const regLine = (r) => `${esc(r.title)} · ${esc(r.time)} · ${r.exercises} ${r.exercises === 1 ? "exercício" : "exercícios"} · ${r.sets} ${r.sets === 1 ? "série" : "séries"}${r.minutes ? ` · ${r.minutes_estimated ? "~" : ""}${r.minutes} min` : ""}${r.no_load ? ` · <span class="no-load">${r.no_load} sem carga</span>` : ""}`;
function registeredHtml(reg, who, together, dups) {
  if (!reg || !reg.length) return "";
  const groups = (dups || []).filter((g) => g.length > 1);
  const inDup = new Set(groups.flat());
  const ok = reg.filter((r) => !inDup.has(r.id));
  const okHtml = ok.length ? `<div class="reg-box small"><b class="ok-txt">✓ ${together ? "Vocês treinaram juntos e você registrou" : "Você registrou"}</b>
    ${ok.map((r) => `<div class="reg-row"><span>${regLine(r)}</span><button class="btn sm ghost" data-myses="${r.id}">Ver e corrigir</button></div>`).join("")}</div>` : "";
  const dupHtml = groups.map((g) => { const rs = g.map((id) => reg.find((r) => r.id === id)).filter(Boolean);
    return `<div class="reg-box dup small"><b>Você registrou o mesmo treino ${rs.length} vezes${rs[0] && rs[0].date === localISO().slice(0, 10) ? " hoje" : ""}.</b> Fique com um só (o outro conta em dobro no placar e na semana):
    ${rs.map((r) => `<div class="reg-row"><span>${regLine(r)}</span><span class="row gap"><button class="btn sm ghost" data-myses="${r.id}">Ver</button><button class="btn sm ghost" data-mydel="${r.id}">Apagar</button></span></div>`).join("")}</div>`; }).join("");
  return dupHtml + okHtml;
}
// um treino MEU salvo: o que ficou registrado, com corrigir e apagar
async function openMySession(id, onChange) {
  let v;
  try { v = await api(`/api/sessions/${id}/summary`); } catch (e) { return toast(e.message, 5000); }
  const reg = (v.summary || {}).registered || {}; const pn = (v.summary || {}).partner_name;
  const setTxt2 = (x) => x.duration_s && !x.reps ? `${x.duration_s} s` : `${x.reps ?? "—"} × ${x.load_kg != null && x.load_kg !== "" ? `${fmtKg(x.load_kg)}` : `<span class="no-load">sem carga</span>`}`;
  const body = openModal(`<h2>${esc(v.title)}</h2>
    <p class="small muted" style="margin-top:-6px">${esc(fmtDate(v.date_full || v.date))}${reg.minutes ? ` · ${reg.minutes_estimated ? "~" : ""}${reg.minutes} min` : ""}${pn ? ` · <span class="pill ok">com ${esc(pn)}</span>` : ""}</p>
    <p class="ok-txt small">✓ Registrado: ${reg.exercises || 0} ${reg.exercises === 1 ? "exercício" : "exercícios"}, ${reg.sets || 0} ${reg.sets === 1 ? "série" : "séries"}</p>
    ${reg.no_load ? `<p class="small rec atencao">${reg.no_load} ${reg.no_load === 1 ? "série ficou" : "séries ficaram"} sem a sua carga: sem ela o app não acompanha sua evolução nesse exercício. Toque em Corrigir para preencher.</p>` : ""}
    <ul class="sv-list small">${(v.items || []).map((i) => i.kind === "forca" ? `<li><b>${esc(i.name)}</b><br>${(i.sets || []).map(setTxt2).join(" · ")}</li>` : `<li><b>${esc(i.name)}</b> · ${i.minutes} min</li>`).join("")}</ul>
    <div class="row gap wrap" style="margin-top:12px">${v.has_sets ? `<button class="btn" id="ms-edit">Corrigir repetições e cargas</button>` : ""}<button class="btn ghost" id="ms-del">Apagar este registro</button></div>`);
  body.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.id === "ms-edit") {
      t.disabled = true;
      try { const d = await api(`/api/sessions/${id}/edit`); const cur = store("gg_session");
        if (cur && (cur.items || []).length && !cur.edit_id && !confirm("Há um treino em andamento guardado neste aparelho. Trocar pela correção deste?")) { t.disabled = false; return; }
        state.session = d; store("gg_session", d); closeModal(); if (location.hash !== "#treinar/editar") location.hash = "#treinar/editar"; else renderSession();
      } catch (err) { t.disabled = false; toast(err.message, 5000); }
    }
    if (t.id === "ms-del") {
      if (!confirm("Apagar este registro? Ele sai do histórico, do placar e da conta da semana.")) return;
      t.disabled = true;
      const r = await api(`/api/sessions/${id}`, { method: "DELETE" });
      closeModal();
      toast(r && r.watch_kept ? `Apaguei o que você anotou. A atividade do relógio (${r.minutes} min) continua no histórico.` : "Registro apagado", 5000);
      if (onChange) onChange();
    }
  };
}
// para não confundir duas contas com o mesmo nome: dono do app, ou desde quando a conta existe
const partnerWho = (x) => (x.owner ? "· dono do app" : x.since ? `· conta criada em ${ddmm(x.since)}` : "");
function watchMergeHtml(qs) {
  return (qs || []).map((q) => `<div class="rec atencao q-card"><b>É o mesmo treino?</b>
    <p class="small" style="margin:4px 0 8px">${ddmm(q.date)}: o relógio gravou <b>${/relógio/i.test(q.watch.title) ? "" : esc(q.watch.title) + " · "}${q.watch.minutes} min</b> às ${esc(q.watch.time)}${q.watch.kcal ? ` (${fmtN(q.watch.kcal)} kcal)` : ""} e você anotou <b>${esc(q.app.title)}</b>${q.app.time ? ` às ${esc(q.app.time)}` : ""} (${q.app.sets} ${q.app.sets === 1 ? "série" : "séries"}). Se for o mesmo, vira um treino só, com o tempo e as calorias do relógio.</p>
    <div class="row gap wrap"><button class="btn sm" data-msame="${q.watch.id}:${q.app.id}">Sim, é o mesmo</button><button class="btn sm ghost" data-mdiff="${q.watch.id}:${q.app.id}">Não, são dois treinos</button></div></div>`).join("");
}
function outboxConflictHtml() {
  const cs = store("gg_outbox_conflict") || [];
  return cs.map((c, k) => { const n = (c.payload.items || []).reduce((a, it) => a + (it.sets || []).length, 0);
    const nl = (c.payload.items || []).reduce((a, it) => a + (it.sets || []).filter((x) => x.load_kg == null || x.load_kg === "").length, 0);
    return `<div class="rec atencao q-card"><b>Esse treino ficou registrado duas vezes</b>
    <p class="small" style="margin:4px 0 8px">O treino que ficou guardado no celular sem internet (${esc(c.payload.day_name || "Treino")} · ${esc(String(c.payload.date || "").slice(11, 16))} · ${n} séries${nl ? `, ${nl} sem carga` : ""}) já tinha sido registrado de novo: ${c.existing ? regLine(c.existing) : "outro registro"}. Qual fica?</p>
    <div class="row gap wrap"><button class="btn sm" data-oxuse="${k}">Ficar com o do celular</button><button class="btn sm ghost" data-oxdrop="${k}">Ficar com o que já está</button></div></div>`; }).join("");
}
function partnerCardsHtml(p) {
  if (!p) return outboxConflictHtml();
  if (!p.enabled) return outboxConflictHtml() + watchMergeHtml(p.merge_questions);
  const req = (p.requests || []).map((x) => `<div class="rec atencao q-card"><b>${esc(x.name || x.first)} quer treinar com você</b> <span class="small muted">${partnerWho(x)}</span>
    <p class="small" style="margin:4px 0 8px">Vocês passam a ver o treino um do outro (exercícios, séries, tempo e calorias) e um placar da semana. Nada de saúde, sono ou passos.</p>
    <div class="row gap wrap"><button class="btn sm" data-paccept="${x.id}">Aceitar</button><button class="btn sm ghost" data-pdecline="${x.id}">Agora não</button></div></div>`).join("");
  const cards = (p.partners || []).map((x) => {
    const t = x.today || {}; const done = t.done || []; const plan = t.planned || [];
    const status = done.length ? `Hoje ${esc(x.first)} já treinou: ${done.map((s) => `${esc(s.title)} (${s.minutes_estimated ? "~" : ""}${s.minutes} min)`).join(", ")}.`
      : plan.length ? `Hoje ${esc(x.first)} tem ${plan.map((e) => esc(e.title)).join(" + ")} na agenda.` : `${esc(x.first)} não tem treino hoje.`;
    const du = x.duel; const m = du ? Object.fromEntries(du.metrics.map((k) => [k.key, k])) : null;
    const lead = !du ? "" : du.score.lead === "me" ? "Você está na frente" : du.score.lead === "partner" ? `${esc(x.first)} está na frente` : "Empatados";
    const reg = t.mine || [];  // o que EU já registrei do treino com essa pessoa hoje
    const together = reg.length && done.some((s) => (s.mine_ids || []).length);
    const rc = x.recent;  // treino de ontem/anteontem que eu ainda não registrei
    const recent = rc ? `<p class="small" style="margin:0 0 8px">${rc.days_ago === 1 ? "Ontem" : "Anteontem"} ${esc(x.first)} fez ${esc(rc.title)} (${rc.minutes_estimated ? "~" : ""}${rc.minutes} min). Se você fez junto e não registrou, dá para puxar.</p>` : "";
    return `<section class="card partner-card"><div class="row between gap"><b>Treino com ${esc(x.first)}</b>${du ? `<span class="pill ${du.score.lead === "me" ? "ok" : ""}">${lead}</span>` : ""}</div>
      <p class="small" style="margin:4px 0 8px">${status}</p>${registeredHtml(reg, x.first, together, t.dups)}${recent}
      ${m ? `<div class="duel-mini small"><span>Semana</span><span>você <b>${m.sessions.me}</b> × <b>${m.sessions.partner}</b> treinos</span><span><b>${(du.est_min || {}).me ? "~" : ""}${fmtN(m.minutes.me)}</b> × <b>${(du.est_min || {}).partner ? "~" : ""}${fmtN(m.minutes.partner)}</b> min</span><span><b>${kcalTxt(m.kcal.me, (du.est || {}).me)}</b> × <b>${kcalTxt(m.kcal.partner, (du.est || {}).partner)}</b></span></div>` : ""}
      <div class="row gap wrap" style="margin-top:8px"><button class="btn sm ${reg.length ? "ghost" : ""}" data-pday="${x.id}" data-pname="${esc(x.first)}">${reg.length ? `Ver treinos de ${esc(x.first)}` : done.length || plan.length ? "Treinar junto / puxar o treino" : "Ver treinos"}</button>
        ${rc ? `<button class="btn sm ${done.length || plan.length ? "ghost" : ""}" data-pday="${x.id}" data-pname="${esc(x.first)}" data-pdate="${rc.date}">Puxar o de ${rc.days_ago === 1 ? "ontem" : "anteontem"}</button>` : ""}
        <button class="btn sm ghost" data-pduel="${x.id}">Placar da semana</button></div></section>`;
  }).join("");
  return outboxConflictHtml() + watchMergeHtml(p.merge_questions) + req + cards;
}
// o dia do parceiro: o que ele planejou e o que já fez; daqui sai o MEU treino com as minhas cargas
async function openPartnerDay(pid, first, day) {
  let d;
  try { d = await api(`/api/partners/${pid}/day${day ? `?date=${day}` : ""}`); } catch (e) { return toast(e.message, 5000); }
  const name = d.partner.first || first || "parceiro";
  const itemsTxt = (its) => its.map((i) => i.kind === "forca" ? `${esc(i.name)}${i.n ? ` ${i.n}×${i.reps ? esc(String(i.reps)) : ""}` : ""}` : `${esc(i.name)}${i.minutes ? ` ${i.minutes} min` : ""}`).join(" · ");
  const prev = addDays(d.date, -1); const today = localISO().slice(0, 10);
  const myId = state.me && state.me.user ? state.me.user.id : null;
  const body = openModal(`<h2>Treino de ${esc(name)}</h2>
    <div class="row between gap" style="margin:-4px 0 10px"><button class="btn sm ghost" data-go="${prev}">‹ ${ddmm(prev)}</button><b>${d.date === today ? "Hoje" : ddmm(d.date)}</b>
      <button class="btn sm ghost" data-go="${d.next}" ${d.date >= today ? "disabled" : ""}>${ddmm(d.next)} ›</button></div>
    ${(d.registered || []).length ? `<h3>O que você registrou ${d.date === today ? "hoje" : "nesse dia"}</h3>${registeredHtml(d.registered, name, d.done.some((s) => (s.mine_ids || []).length), d.duplicates)}` : ""}
    ${d.done.length ? `<h3>Treino de ${esc(name)}</h3>${d.done.map((s) => { const un = s.unnamed_sets || 0; const unTxt = un ? `${un} ${un === 1 ? "série" : "séries"} que o relógio gravou sem o nome do exercício` : "";
      return `<div class="pt-row"><div><b>${esc(s.title)}</b> <span class="small muted">· ${s.date.slice(11, 16)} · ${s.minutes_estimated ? "~" : ""}${s.minutes} min · ${kcalTxt(s.kcal, s.kcal_estimated)}</span></div>
      <div class="small muted">${itemsTxt(s.items) || (un ? unTxt : "sem exercícios registrados (só tempo)")}${un && s.has_sets ? ` · + ${unTxt} (ficam de fora ao puxar)` : ""}</div>
      ${s.with_partner === myId && (s.mine_ids || []).length ? `<p class="small muted" style="margin:4px 0 0">Esse treino ${esc(name)} fez junto com você: você já registrou o seu.</p>`
        : (s.mine_ids || []).length ? `<p class="small ok-txt" style="margin:4px 0 0">✓ Você já registrou esse treino (acima).</p>
          <button class="act" data-pull="${s.id}" data-replace="${s.mine_ids[0]}">Refazer o meu a partir deste (substitui o que salvei)</button>`
        : s.has_sets ? `<button class="btn sm" data-pull="${s.id}">Fiz esse treino com ${esc(name)}: puxar</button>`
        : `<p class="small muted" style="margin:4px 0 0">${un ? `Quando ${esc(name)} der nome a essas séries (Progresso → Conferir), dá para puxar.` : "Sem séries registradas: não há o que puxar."}</p>`}</div>`; }).join("")}` : ""}
    ${d.planned.length ? `<h3>Na agenda de ${esc(name)}</h3>${d.planned.map((e) => `<div class="pt-row"><div><b>${esc(e.title)}</b></div><div class="small muted">${itemsTxt(e.items)}</div>
      ${e.has_sets ? ((e.mine_ids || []).length ? `<p class="small ok-txt" style="margin:4px 0 0">✓ Você já registrou esse treino (acima).</p>` : `<button class="btn sm" data-join="${e.id}">Treinar junto agora</button>`) : `<p class="small muted" style="margin:4px 0 0">Sem musculação nesse treino (corrida e cardio cada um faz no seu ritmo).</p>`}</div>`).join("")}` : ""}
    ${!d.done.length && !d.planned.length ? `<p class="muted">Nenhum treino de ${esc(name)} nesse dia.</p>` : ""}
    <p class="small muted" style="margin-top:12px">“Puxar” copia os exercícios, as séries e as repetições de ${esc(name)}; as cargas são as suas (a de ${esc(name)} aparece só como referência). Se o seu relógio só marcou o tempo, você escolhe juntar com essa atividade na hora de salvar.</p>`);
  const again = () => openPartnerDay(pid, name, d.date);
  body.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t || t.disabled) return; const ds = t.dataset;
    if (ds.go) return openPartnerDay(pid, name, ds.go);
    if (ds.myses) return openMySession(+ds.myses, again);
    if (ds.mydel) { if (!confirm("Apagar este registro repetido? O outro continua.")) return; t.disabled = true; await api(`/api/sessions/${ds.mydel}`, { method: "DELETE" }); toast("Registro repetido apagado", 4000); return again(); }
    if (!ds.pull && !ds.join) return;
    if (ds.replace && !confirm(`Refazer a partir do treino de ${name}? O que você salvou desse treino é substituído (não cria outro registro).`)) return;
    t.disabled = true;
    try {
      const draft = await api(`/api/partners/${pid}/draft`, { method: "POST", body: ds.pull ? { session_id: +ds.pull } : { entry_id: +ds.join } });
      let replaceId = ds.replace ? +ds.replace : null;
      if (!replaceId && (draft.already || []).length) {  // já registrado: não cria outro sem a pessoa decidir
        t.disabled = false;
        return chooseAlready(draft, name, (choice) => {
          if (choice === "ver") return openMySession(draft.already[0].id, again);
          if (choice === "refazer") return startPartnerDraft(draft, draft.already[0].id);
          if (choice === "outro") return startPartnerDraft(draft, null, true);
        });
      }
      return startPartnerDraft(draft, replaceId);
    } catch (err) { t.disabled = false; toast(err.message, 5000); }
  };
}
function chooseAlready(draft, name, done) {
  const a = draft.already;
  const body = openModal(`<h2>Você já registrou esse treino</h2>
    <div class="reg-box small">${a.map((r) => `<div class="reg-row"><span><b class="ok-txt">✓</b> ${regLine(r)}</span></div>`).join("")}</div>
    <p class="small">Puxar de novo criaria um segundo registro do mesmo treino (contaria em dobro no placar e na semana).</p>
    <div class="stack"><button class="btn block" data-ch="ver">Ver o que salvei (e corrigir)</button>
      <button class="btn ghost block" data-ch="refazer">Refazer a partir do treino de ${esc(name)} (substitui o meu)</button>
      <button class="btn ghost block" data-ch="outro">Foi outro treino: registrar separado</button>
      <button class="act" data-ch="nada">Cancelar</button></div>`);
  body.onclick = (e) => { const b = e.target.closest("[data-ch]"); if (!b) return; closeModal(); if (b.dataset.ch !== "nada") done(b.dataset.ch); };
}
function startPartnerDraft(draft, replaceId, allowRepeat) {
  const cur = store("gg_session");
  if (cur && (cur.items || []).length && !confirm("Há um treino em andamento guardado neste aparelho. Trocar por este?")) return;
  const hit = (draft.merge_candidates || []).filter((c) => c.match);  // só vem marcada a atividade do relógio no mesmo horário do treino
  // refazer: a atividade do relógio já está no registro que vai ser substituído
  state.session = { ...draft, view: draft.pulled ? "lista" : undefined, replace_id: replaceId || null, allow_repeat: !!allowRepeat,
    merge_candidates: replaceId ? [] : draft.merge_candidates, merge_into: !replaceId && hit.length === 1 ? hit[0].id : null, merge_answered: hit.length === 1 };
  store("gg_session", state.session); closeModal();
  if (location.hash !== "#treinar/parceiro") location.hash = "#treinar/parceiro"; else renderSession();
}
function partnerDraftHtml(s) {
  if (!s.with_partner) return "";
  const cand = s.merge_candidates || [];
  const who = esc(s.partner_name || "quem treinou com você");
  return `<div class="rec small" style="margin-top:10px"><b>${s.pulled ? `Treino puxado de ${who}` : `Treinando com ${who}`}</b>${s.replace_id ? `<br><span class="ok-txt">Ao salvar, substitui o registro que você já tinha desse treino (não cria outro).</span>` : ""}
    <p style="margin:4px 0">${s.pulled ? `Exercícios, séries e repetições são os de ${who}, já marcados como feitos. Confira as suas cargas (a carga de ${who} aparece embaixo do nome do exercício, só de referência), tire o que você não fez e salve.` : `Mesmos exercícios e séries do treino de ${who}, com as suas cargas.`}</p>
    ${cand.length ? `<p style="margin:6px 0 2px"><b>É a mesma atividade que o seu relógio gravou?</b> Se for, o tempo, a frequência cardíaca e as calorias dela ficam neste treino (sem contar duas vezes).</p>
      ${cand.map((c) => `<label class="row gap" style="margin:2px 0"><input type="radio" name="pmerge" value="${c.id}" ${s.merge_into === c.id ? "checked" : ""}> ${esc(c.title)} · ${esc(c.time)} · ${c.minutes} min${c.kcal ? ` · ${fmtN(c.kcal)} kcal` : ""}</label>`).join("")}
      <label class="row gap" style="margin:2px 0"><input type="radio" name="pmerge" value="" ${s.merge_answered && !s.merge_into ? "checked" : ""}> Não, é outro treino</label>` : ""}
    ${s.skipped_sets ? `<p style="margin:6px 0 0"><b>Ficaram de fora ${s.skipped_sets} ${s.skipped_sets === 1 ? "série" : "séries"}</b> que o relógio de ${who} gravou sem o nome do exercício. Se você fez, use “+ Adicionar exercício” no fim da tela.</p>` : ""}
    ${s.pulled && s.duration_min ? `<label class="row gap" style="margin:8px 0 2px;align-items:center"><b>Tempo do treino</b> <input type="number" id="pdur" inputmode="numeric" min="5" max="400" value="${s.duration_min}" style="width:76px" aria-label="Tempo do treino em minutos"> min</label>
      <p class="muted" style="margin:0">O mesmo de ${who}; ajuste se o seu foi diferente.${cand.length ? " Se juntar com a atividade do relógio, vale o tempo que ela marcou." : ""}</p>` : ""}</div>`;
}
// placar da semana: treinos, tempo e calorias de cada um
async function openDuel(pid, day) {
  let d;
  try { d = await api(`/api/partners/${pid}/duel${day ? `?date=${day}` : ""}`); } catch (e) { return toast(e.message, 5000); }
  const me = d.me, p = d.partner;
  const bar = (m) => { const tot = (m.me + m.partner) || 1; const a = Math.round(100 * m.me / tot);
    const val = (v, who) => m.key === "kcal" ? kcalTxt(v, who.kcal_estimated) : `${m.key === "minutes" && who.minutes_estimated ? "~" : ""}${fmtN(v)}${m.unit ? " " + m.unit : ""}`;
    return `<div class="duel-row"><div class="row between small"><span><b>${val(m.me, me)}</b></span><span class="muted">${esc(m.label)}</span><span><b>${val(m.partner, p)}</b></span></div>
      <div class="duel-bar" role="img" aria-label="${esc(m.label)}: você ${m.me}, ${esc(p.first)} ${m.partner}"><span class="a ${m.lead === "me" ? "lead" : ""}" style="width:${m.me || m.partner ? a : 50}%"></span><span class="b ${m.lead === "partner" ? "lead" : ""}" style="width:${m.me || m.partner ? 100 - a : 50}%"></span></div></div>`; };
  const head = d.score.lead === "me" ? `Você está na frente (${d.score.me} × ${d.score.partner})` : d.score.lead === "partner" ? `${esc(p.first)} está na frente (${d.score.partner} × ${d.score.me})` : `Empate (${d.score.me} × ${d.score.partner})`;
  const cell = (x) => x ? `${x.minutes_estimated ? "~" : ""}${x.minutes} min<br><span class="muted">${kcalTxt(x.kcal, x.kcal_estimated)}</span>` : `<span class="muted">—</span>`;
  const body = openModal(`<h2>Placar da semana</h2>
    <div class="row between gap" style="margin:-4px 0 10px"><button class="btn sm ghost" data-go="${d.prev}">‹ anterior</button><b>${ddmm(d.week_start)} a ${ddmm(d.week_end)}</b>
      <button class="btn sm ghost" data-go="${d.next || ""}" ${d.next ? "" : "disabled"}>próxima ›</button></div>
    <div class="row between"><b>Você</b><span class="pill ${d.score.lead === "me" ? "ok" : ""}">${head}</span><b>${esc(p.first)}</b></div>
    ${d.metrics.map(bar).join("")}
    <table class="duel-days small"><thead><tr><th></th><th>Você</th><th>${esc(p.first)}</th></tr></thead><tbody>
      ${d.days.filter((x) => !x.future).map((x) => `<tr class="${d.together.includes(x.date) ? "tog" : ""}"><td>${["dom", "seg", "ter", "qua", "qui", "sex", "sáb"][new Date(x.date + "T12:00:00").getDay()]} ${ddmm(x.date)}</td><td class="${x.lead === "me" ? "win" : ""}">${cell(x.me)}</td><td class="${x.lead === "partner" ? "win" : ""}">${cell(x.partner)}</td></tr>`).join("")}
    </tbody></table>
    <p class="small muted">Vale um ponto para quem ganha em cada item (treinos, tempo, calorias). ${d.minutes_estimated ? "Tempo com “~” é estimado pelo número de séries (o treino foi anotado sem relógio e sem cronômetro). " : ""}${d.kcal_estimated ? "Calorias com “~” são estimadas pelo tempo e por uma faixa de peso, porque o relógio não informou. " : ""}Calorias dependem do peso de cada um e do relógio: servem para brincar, não para comparar esforço. No dia a dia ganha quem gastou mais calorias.</p>`);
  body.onclick = (e) => { const t = e.target.closest("button[data-go]"); if (t && t.dataset.go) openDuel(pid, t.dataset.go); };
}
function bindCoachMine() {
  $$("[data-link]").forEach((b) => { b.onclick = async () => { await api(`/api/coach/mine/${b.dataset.link}`, { method: "POST" }); toast("Coach autorizado"); viewPerfil(); }; });
  $$("[data-unlink]").forEach((b) => { b.onclick = async () => { if (!confirm("Remover o acesso deste coach?")) return; await api(`/api/coach/mine/${b.dataset.unlink}`, { method: "DELETE" }); viewPerfil(); }; });
  $$("[data-share]").forEach((b) => { b.onchange = async () => { await api(`/api/coach/mine/${b.dataset.share}/share`, { method: "POST", body: { health: b.checked } }); toast(b.checked ? "Saúde e sono compartilhados" : "Saúde e sono não compartilhados"); }; });
  $$("[data-coach]").forEach((b) => { b.onclick = async () => { await api(`/api/admin/users/${b.dataset.coach}/coach`, { method: "POST", body: { on: b.dataset.on === "1" } }); viewPerfil(); }; });
}

/* ---------------------------------------------------------------- CONTA -- */
async function openFamily() {
  const cfg = await api("/api/admin/signup");
  const link = location.origin + "/#criar";
  const msg = `Oi! Estou testando o HAX, um app de treino que monta musculação e corrida juntas e guia o treino com cronômetro. Quero sua opinião 🙂\n\n1) Abra este link e crie sua conta (nome, e-mail e senha): ${link}\n2) Responda 7 perguntas rápidas: o treino sai montado.\n3) Toque em "Instalar" para colocar o ícone na tela do celular.\n4) Qualquer coisa estranha: Perfil → "Mandar sugestão ou problema".`;
  const body = openModal(`<h2>👨‍👩‍👧 Chamar a família</h2>
    <label class="row gap mg-sug"><input type="checkbox" id="fam-open-sw" ${cfg.open ? "checked" : ""}> <span><b>Cadastro aberto</b>: quem tiver o link cria a própria conta, sem convite (${cfg.accounts} de ${cfg.max} contas usadas)</span></label>
    <pre class="inv-msg">${esc(msg)}</pre>
    <a class="btn block wa" href="https://wa.me/?text=${encodeURIComponent(msg)}" target="_blank" rel="noopener">Enviar pelo WhatsApp</a>
    <div class="row gap" style="margin-top:8px"><button class="btn ghost grow" id="fam-share">Compartilhar…</button><button class="btn ghost grow" id="fam-copy">Copiar mensagem</button></div>
    <p class="small muted">Depois que todos entrarem, você pode fechar o cadastro aqui. Convite individual continua em Perfil → Conta e segurança.</p>`);
  $("#fam-open-sw", body).onchange = async (e) => { try { const r = await api("/api/admin/signup", { method: "PUT", body: { open: e.target.checked } }); toast(r.open ? "Cadastro aberto" : "Cadastro fechado"); } catch (err) { toast(err.message); } };
  $("#fam-share", body).onclick = async () => { try { if (navigator.share) await navigator.share({ text: msg }); else { await navigator.clipboard.writeText(msg); toast("Mensagem copiada"); } } catch (_) { /* cancelado */ } };
  $("#fam-copy", body).onclick = async () => { try { await navigator.clipboard.writeText(msg); toast("Mensagem copiada"); } catch (_) { prompt("Copie a mensagem:", msg); } };
}
function inviteMsg(note, link) {
  const nome = (note || "").trim().split(/\s+/)[0];
  return `Oi${nome ? " " + nome : ""}! Estou testando o HAX, um app de treino que monta musculação e corrida juntas, guia o treino com cronômetro e conversa com o relógio. Quero sua opinião 🙂\n\n1) Crie sua conta por este link (é só seu, vale uma vez):\n${link}\n2) Depois toque em "Instalar" para colocar o ícone na tela do celular.\n3) Qualquer coisa estranha, toque em Perfil → "Mandar sugestão ou problema".`;
}
function openFeedback() {
  const where = (location.hash || "#hoje").slice(1, 40);
  const body = openModal(`<h2>💬 Sugestão ou problema</h2>
    <p class="small muted">Conta o que achou, o que faltou ou o que deu errado. Vai direto para quem desenvolve o app.</p>
    <textarea id="fb-text" rows="5" placeholder="ex.: no treino guiado o botão de carga ficou pequeno no meu celular…"></textarea>
    <button class="btn block" id="fb-send" style="margin-top:10px">Enviar</button>`);
  $("#fb-send", body).onclick = async (e) => {
    e.target.disabled = true;
    try {
      await api("/api/feedback", { method: "POST", body: { text: $("#fb-text", body).value, where, device: `${navigator.userAgent.match(/\(([^)]+)\)/)?.[1] || ""} · ${innerWidth}px${isStandalone() ? " · instalado" : ""}` } });
      closeModal(); toast("Recebido, obrigado! 🙏");
    } catch (err) { e.target.disabled = false; toast(err.message, 5000); }
  };
}
async function showAuth(mode, code) {
  document.body.classList.add("auth-mode");
  $("#modal").hidden = true;
  let inv = null;
  if (mode === "convite") { try { inv = await api(`/api/auth/invite/${encodeURIComponent(code)}`); } catch (_) { inv = { valid: false }; } }
  let open = false;
  if (mode !== "convite") { try { open = (await api("/api/public/signup")).open; } catch (_) { open = false; } }
  if (mode === "criar" && open) return showSignup();
  const isReg = mode === "convite" && inv && inv.valid;
  view.innerHTML = `${open && !isReg ? `<div class="auth-hero"><img src="/static/icon-192.png" alt=""><b>HAX</b><span>Seu treino de musculação e corrida, montado e guiado.</span></div>
    <button class="btn block auth-new" id="go-signup">✨ Sou novo: criar minha conta</button><p class="small muted auth-or">ou entre com a sua conta</p>` : ""}
    <div class="card auth-card"><h2>${isReg ? "Criar sua conta" : "Entrar"}</h2>
    ${mode === "convite" && !isReg ? `<p class="pill bad">Convite inválido ou já usado. Peça um novo a quem te convidou.</p>` : ""}
    ${isReg ? `<p class="small muted">Você foi convidado${inv.note ? ` (${esc(inv.note)})` : ""} para o HAX. Seus treinos, planilhas e Garmin ficam só na sua conta.</p>` : ""}
    <form id="auth-form" class="stack">
      ${isReg ? `<div class="field"><label>Seu nome</label><input name="name" autocomplete="name" required></div>` : ""}
      <div class="field"><label>E-mail</label><input name="email" type="email" autocomplete="email" required></div>
      <div class="field"><label>Senha${isReg ? " (mín. 8 caracteres)" : ""}</label><input name="password" type="password" minlength="${isReg ? 8 : 1}" autocomplete="${isReg ? "new-password" : "current-password"}" required></div>
      <button class="btn" type="submit">${isReg ? "Criar conta" : "Entrar"}</button>
    </form>
    ${!isReg ? `<p class="small muted">Esqueceu a senha? Peça ao administrador uma senha provisória.</p>` : ""}</div>`;
  const gs = $("#go-signup"); if (gs) gs.onclick = () => showSignup();
  $("#auth-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    const btn = e.target.querySelector("button"); btn.disabled = true;
    try {
      const me = isReg ? await api("/api/auth/register", { method: "POST", body: { ...f, code } })
        : await api("/api/auth/login", { method: "POST", body: f });
      state.me = me; state.profile = null; state.meta = null;
      clearLocalDrafts();
      if (location.hash.startsWith("#convite")) history.replaceState(null, "", isReg ? "#bemvindo" : "#hoje");
      startApp();
    } catch (err) { toast(err.message, 5000); btn.disabled = false; }
  };
}
// criar conta sem convite: só nome, e-mail e senha; o resto vem no passo a passo
function showSignup() {
  document.body.classList.add("auth-mode");
  view.innerHTML = `<div class="auth-hero"><img src="/static/icon-192.png" alt=""><b>Criar sua conta</b><span>Leva 1 minuto. Depois o app faz 7 perguntas rápidas e monta seu treino.</span></div>
    <div class="card auth-card"><form id="su-form" class="stack">
      <div class="field"><label for="su-name">Seu nome</label><input id="su-name" name="name" autocomplete="given-name" required placeholder="ex.: Rosa"></div>
      <div class="field"><label for="su-email">E-mail</label><input id="su-email" name="email" type="email" inputmode="email" autocomplete="email" autocapitalize="off" required placeholder="voce@email.com"></div>
      <div class="field"><label for="su-pw">Crie uma senha (mín. 8 letras ou números)</label>
        <div class="pw-wrap"><input id="su-pw" name="password" type="password" minlength="8" autocomplete="new-password" required><button type="button" class="act" id="su-eye">mostrar</button></div></div>
      <button class="btn block" type="submit">Criar conta e continuar</button></form>
      <p class="small muted">Seus treinos ficam só na sua conta. Anote o e-mail e a senha: é com eles que você entra de novo.</p>
      <button class="act" id="su-back">Já tenho conta: entrar</button></div>`;
  $("#su-eye").onclick = () => { const i = $("#su-pw"); i.type = i.type === "password" ? "text" : "password"; $("#su-eye").textContent = i.type === "password" ? "mostrar" : "esconder"; };
  $("#su-back").onclick = () => showAuth("entrar");
  $("#su-form").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target)); const btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
    try {
      state.me = await api("/api/auth/signup", { method: "POST", body: f }); state.profile = null; state.meta = null;
      clearLocalDrafts(); history.replaceState(null, "", "#bemvindo"); startApp();
    } catch (err) { btn.disabled = false; toast(err.message, 6000); }
  };
}

/* ---- bem-vindo: 7 perguntas, um toque cada, e o treino sai montado ---- */
const WIZ = { step: 0, d: null };
const WIZ_GOALS = [["hipertrofia", "💪 Ganhar massa muscular", "ficar mais forte e com mais músculo"], ["emagrecimento", "🔥 Emagrecer", "perder gordura mantendo os músculos"],
  ["saude", "❤️ Saúde e disposição", "se mexer bem, dormir melhor, sem exagero"], ["condicionamento", "🫁 Condicionamento", "fôlego e resistência"],
  ["forca", "🏋️ Força", "levantar mais peso"], ["hibrido", "🏃 Musculação + corrida", "os dois juntos, no mesmo plano"]];
const WIZ_PLACES = [["elite", "🏆 Academia premium / de elite", "máquinas articuladas modernas (Hammer Strength, Panatta, Gym80, Technogym…), pêndulo, V-squat", []],
  ["academia", "🏟️ Academia completa", "máquinas, polias, barras e halteres", []], ["pequena", "🏠 Academia pequena / do prédio", "halteres, barra e algumas máquinas", ["barbell", "dumbbell", "machine", "cable"]],
  ["casa", "🛋️ Em casa com halteres ou elástico", "", ["dumbbell", "bands", "kettlebells"]], ["corpo", "🤸 Só o peso do corpo", "sem equipamento", ["bands"]]];
async function viewBemVindo() {
  const [p, meta] = await Promise.all([api("/api/profile"), state.meta ? Promise.resolve(state.meta) : api("/api/meta")]);
  state.profile = p; state.meta = meta;
  const run = (((p.endurance || {}).sports || {}).corrida || {});
  WIZ.d = { name: p.name && p.name !== "Eu" ? p.name : "", sex: p.sex || "", age: p.age ?? "", weight_kg: p.weight_kg ?? "", height_cm: p.height_cm ?? "",
    goal: p.goal || "", training_months: p.training_months ?? "", available_days: p.available_days || [], session_minutes: p.session_minutes || 60,
    place: (p.equipment || []).length ? (WIZ_PLACES.find((x) => JSON.stringify(x[3]) === JSON.stringify(p.equipment)) || ["academia"])[0] : p.gym_type === "premium" ? "elite" : "academia",
    lims: (p.limitations || []).map((l) => l.key).filter((k) => k !== "personalizado"), none: !(p.limitations || []).length,
    run: run.enabled ? (run.sessions || 2) : 0 };
  WIZ.step = 0; renderWiz();
}
function wizOk(step) {
  const d = WIZ.d;
  return [() => d.name.trim() && d.sex && +d.age >= 10 && +d.age <= 100 && +d.weight_kg >= 25 && +d.weight_kg <= 300, () => !!d.goal, () => d.training_months !== "",
    () => d.available_days.length > 0 && d.session_minutes, () => !!d.place, () => d.none || d.lims.length > 0, () => true, () => true][step]();
}
function renderWiz() {
  const d = WIZ.d; const st = WIZ.step; const N = 7;
  const opt = (key, val, title, sub, on) => `<button type="button" class="wz-opt ${on ? "on" : ""}" data-wk="${key}" data-wv="${esc(String(val))}"><b>${title}</b>${sub ? `<span>${sub}</span>` : ""}</button>`;
  const chip = (key, val, txt, on) => `<button type="button" class="wz-chip ${on ? "on" : ""}" data-wk="${key}" data-wv="${esc(String(val))}">${txt}</button>`;
  const steps = [
    () => `<h1 class="wz-q">Oi! Primeiro, sobre você</h1>
      <div class="field"><label for="wz-name">Como quer ser chamado(a)?</label><input id="wz-name" data-wf="name" value="${esc(d.name)}" autocomplete="given-name"></div>
      <label>Sexo (ajusta cargas e frequência cardíaca)</label><div class="wz-row">${chip("sex", "F", "Feminino", d.sex === "F")}${chip("sex", "M", "Masculino", d.sex === "M")}</div>
      <div class="wz-nums"><div class="field"><label for="wz-age">Idade</label><input id="wz-age" data-wf="age" type="number" inputmode="numeric" value="${esc(d.age)}" placeholder="anos"></div>
        <div class="field"><label for="wz-w">Peso (kg)</label><input id="wz-w" data-wf="weight_kg" type="number" inputmode="decimal" step="0.1" value="${esc(d.weight_kg)}" placeholder="kg"></div>
        <div class="field"><label for="wz-h">Altura (cm)</label><input id="wz-h" data-wf="height_cm" type="number" inputmode="numeric" value="${esc(d.height_cm)}" placeholder="opcional"></div></div>`,
    () => `<h1 class="wz-q">Qual é o seu principal objetivo?</h1><div class="wz-opts">${WIZ_GOALS.map(([v, t, s]) => opt("goal", v, t, s, d.goal === v)).join("")}</div>`,
    () => `<h1 class="wz-q">Há quanto tempo você treina musculação?</h1><div class="wz-opts">${[[0, "Nunca treinei / estou parado(a)", "o app começa leve, com máquinas"], ...MONTH_OPTS.slice(1).map(([v, t]) => [v, t, ""])].map(([v, t, s]) => opt("training_months", v, t, s, String(d.training_months) === String(v))).join("")}</div>`,
    () => `<h1 class="wz-q">Quando você pode treinar?</h1><label>Dias da semana (toque em todos que der)</label>
      <div class="wz-row wz-days">${["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"].map((w, i) => chip("day", i, w, d.available_days.includes(i))).join("")}</div>
      <label style="margin-top:14px">Quanto tempo por treino?</label><div class="wz-row">${[30, 45, 60, 75, 90, 120].map((m) => chip("session_minutes", m, `${m} min`, +d.session_minutes === m)).join("")}</div>
      <p class="small muted">O app distribui: não precisa treinar todos os dias marcados.</p>`,
    () => `<h1 class="wz-q">Onde você vai treinar?</h1><div class="wz-opts">${WIZ_PLACES.map(([v, t, s]) => opt("place", v, t, s, d.place === v)).join("")}</div>`,
    () => `<h1 class="wz-q">Tem alguma dor ou lesão?</h1><p class="small muted">O app evita ou adapta os exercícios. Pode marcar mais de uma.</p>
      <div class="wz-opts">${opt("none", 1, "✅ Nenhuma", "", d.none)}${(state.meta.limitations || []).filter((l) => l.id !== "personalizado").map((l) => opt("lim", l.id, esc(l.label), "", d.lims.includes(l.id))).join("")}</div>`,
    () => `<h1 class="wz-q">Quer incluir corrida?</h1><div class="wz-opts">${opt("run", 0, "Não, só musculação", "dá para ligar depois em Perfil → Esportes", +d.run === 0)}${opt("run", 2, "🏃 Sim, 2 vezes por semana", "leve, para fôlego e saúde", +d.run === 2)}${opt("run", 3, "🏃 Sim, 3 vezes por semana", "quer evoluir na corrida", +d.run === 3)}</div>`,
  ];
  const goalT = (WIZ_GOALS.find((g) => g[0] === d.goal) || [])[1] || "—";
  const summary = `<h1 class="wz-q">Tudo certo, ${esc(d.name.split(" ")[0] || "")}! 🎉</h1>
    <ul class="wz-sum"><li><span>Objetivo</span><b>${goalT}</b></li><li><span>Treinos</span><b>${d.available_days.length} dias possíveis · ${d.session_minutes} min</b></li>
      <li><span>Onde</span><b>${(WIZ_PLACES.find((x) => x[0] === d.place) || [])[1] || "—"}</b></li><li><span>Cuidados</span><b>${d.none ? "nenhum" : d.lims.map((k) => esc(((state.meta.limitations || []).find((l) => l.id === k) || {}).label || k)).join(", ")}</b></li>
      <li><span>Corrida</span><b>${+d.run ? `${d.run}× por semana` : "não"}</b></li></ul>
    <p class="small muted">Vou montar a semana com aquecimento, descanso e progressão de carga. Você pode mudar tudo depois no Perfil.</p>`;
  const last = st >= N;
  view.innerHTML = `<div class="wz"><div class="wz-top"><span class="small muted">${last ? "Pronto para montar" : `Pergunta ${st + 1} de ${N}`}</span>
      <div class="meter"><span style="width:${Math.round(100 * Math.min(st, N) / N)}%"></span></div></div>
    <section class="wz-body">${last ? summary : steps[st]()}</section>
    <div class="wz-nav">${st > 0 ? `<button class="btn ghost" id="wz-back">Voltar</button>` : `<button class="btn ghost" id="wz-skip">Depois</button>`}
      <button class="btn" id="wz-next" ${last || wizOk(st) ? "" : "disabled"}>${last ? "✨ Montar meu treino" : "Continuar"}</button></div></div>`;
  const nextBtn = $("#wz-next");
  const refresh = () => { if (!last) nextBtn.disabled = !wizOk(st); };
  view.oninput = (e) => { const k = e.target.dataset.wf; if (k) { d[k] = e.target.value; refresh(); } };
  view.onclick = async (e) => {
    const b = e.target.closest("[data-wk]");
    if (b) {
      const k = b.dataset.wk, v = b.dataset.wv;
      if (k === "day") { const i = +v; d.available_days = d.available_days.includes(i) ? d.available_days.filter((x) => x !== i) : [...d.available_days, i].sort(); b.classList.toggle("on"); return refresh(); }
      if (k === "lim") { d.none = false; d.lims = d.lims.includes(v) ? d.lims.filter((x) => x !== v) : [...d.lims, v]; return renderWiz(); }
      if (k === "none") { d.none = true; d.lims = []; return renderWiz(); }
      d[k] = ["training_months", "session_minutes", "run"].includes(k) ? +v : v;
      if (["goal", "training_months", "place", "run"].includes(k)) { renderWiz(); if (st < N) setTimeout(() => { WIZ.step++; renderWiz(); }, 180); return; }  // escolha única: avança sozinho
      return renderWiz();
    }
    if (e.target.id === "wz-back") { WIZ.step = Math.max(0, st - 1); return renderWiz(); }
    if (e.target.id === "wz-skip") { location.hash = "#hoje"; return; }
    if (e.target.id === "wz-next") {
      if (!last) { WIZ.step++; return renderWiz(); }
      nextBtn.disabled = true; nextBtn.textContent = "Montando seu treino…";
      try { await wizFinish(); } catch (err) { nextBtn.disabled = false; nextBtn.textContent = "✨ Montar meu treino"; toast(err.message, 6000); }
    }
  };
  const first = $(".wz-body input"); if (first && !first.value && st === 0) setTimeout(() => first.focus(), 50);
}
async function wizFinish() {
  const d = WIZ.d; const p = state.profile || {};
  const end = JSON.parse(JSON.stringify(p.endurance || {})); end.sports = end.sports || {};
  end.sports.corrida = { ...(end.sports.corrida || {}), enabled: +d.run > 0, env: (end.sports.corrida || {}).env || "rua", sessions: +d.run || 2 };
  const prof = { name: d.name.trim(), sex: d.sex, age: +d.age, weight_kg: +d.weight_kg, height_cm: d.height_cm ? +d.height_cm : null,
    goal: d.goal, training_months: +d.training_months, available_days: d.available_days, session_minutes: +d.session_minutes,
    equipment: (WIZ_PLACES.find((x) => x[0] === d.place) || [, , , []])[3], gym_type: { elite: "premium", academia: "completa" }[d.place] || "basica", limitations: d.none ? [] : d.lims.map((k) => ({ key: k, severity: "cuidado" })),
    endurance: end, same_session: true };
  if (d.goal === "hibrido" && !(+d.run)) prof.endurance.sports.corrida = { ...prof.endurance.sports.corrida, enabled: true, sessions: 2 };
  await api("/api/profile", { method: "PUT", body: { ...p, ...prof } });
  await api("/api/generator/apply", { method: "POST", body: { profile: prof } });
  state.profile = await api("/api/profile");
  toast(`Pronto, ${prof.name.split(" ")[0]}! Seu treino está montado 💪`, 5000);
  location.hash = "#hoje";
}

function clearLocalDrafts() {
  // rascunhos de treino ficam no aparelho: não passam de uma conta para outra
  SWR.mem.clear();
  try { Object.keys(localStorage).filter((k) => k.startsWith("gg_")).forEach((k) => localStorage.removeItem(k)); } catch (_) { /* */ }
}
async function accountCard() {
  const me = state.me || await api("/api/auth/me").catch(() => null) || {};
  if (me.setup_needed) {
    return `<div class="card"><h2>Sua conta</h2><p class="small">Crie seu login para proteger o app e poder convidar família e amigos.
      Os treinos e planilhas que já estão aqui ficam na sua conta.</p>
      <form id="acc-setup" class="stack"><div class="grid2"><div class="field"><label>E-mail</label><input name="email" type="email" required></div>
      <div class="field"><label>Senha (mín. 8)</label><input name="password" type="password" minlength="8" autocomplete="new-password" required></div></div>
      <button class="btn" type="submit">Criar minha conta</button></form></div>`;
  }
  if (!me.logged_in) return "";
  const u = me.user;
  let admin = "";
  if (u.is_admin) {
    const a = await api("/api/admin/users");
    const base = location.origin + location.pathname;
    admin = `<div class="card"><h2>Pessoas</h2>
      <ul class="list">${a.users.map((x) => `<li class="row between gap"><span><b>${esc(x.name || x.email)}</b> <span class="small muted">${esc(x.email)}${x.is_admin ? " · admin" : x.is_coach ? " · coach" : ""}</span></span>
        ${x.user_id !== u.id ? `<span class="row gap"><button class="btn sm ghost" data-coach="${x.user_id}" data-on="${x.is_coach ? 0 : 1}">${x.is_coach ? "Tirar coach" : "Tornar coach"}</button><button class="btn sm ghost" data-reset="${x.user_id}">Senha provisória</button></span>` : ""}</li>`).join("")}</ul>
      <h3>Convites</h3>
      ${a.invites.length ? `<ul class="list">${a.invites.map((i) => `<li class="row between gap wrap"><span class="small"><b>${esc(i.note || "Convite")}</b><br><span class="muted" style="word-break:break-all">${esc(base)}#convite/${esc(i.code)}</span></span>
        <span class="row gap wrap"><a class="btn sm wa" href="https://wa.me/?text=${encodeURIComponent(inviteMsg(i.note, `${base}#convite/${i.code}`))}" target="_blank" rel="noopener">WhatsApp</a><button class="btn sm ghost" data-share="${esc(inviteMsg(i.note, `${base}#convite/${i.code}`))}">Compartilhar</button><button class="btn sm ghost" data-copy="${esc(base)}#convite/${esc(i.code)}">Copiar link</button><button class="btn sm ghost" data-delinv="${esc(i.code)}">Apagar</button></span></li>`).join("")}</ul>` : `<p class="small muted">Nenhum convite pendente.</p>`}
      <form id="inv-form" class="row gap wrap"><input name="note" placeholder="Para quem? (ex.: Maria)" style="flex:1;min-width:160px"><button class="btn sm" type="submit">Criar convite</button></form>
      <p class="small muted">Cada convite vale para uma pessoa (crie um por pessoa). Ela cria e-mail e senha e começa com o app vazio: perfil, treino e Garmin próprios. Você não vê os treinos dela (só se ela te escolher como coach).</p></div>`;
  }
  return `<div class="card"><h2>Sua conta</h2><p><b>${esc(u.name || "")}</b> <span class="small muted">${esc(u.email)}${u.is_admin ? " · administrador" : ""}</span></p>
    <details><summary class="small">Trocar senha</summary><form id="pw-form" class="stack"><div class="grid2">
      <div class="field"><label>Senha atual</label><input name="current" type="password" autocomplete="current-password" required></div>
      <div class="field"><label>Nova senha (mín. 8)</label><input name="new" type="password" minlength="8" autocomplete="new-password" required></div></div>
      <button class="btn sm" type="submit">Salvar senha</button></form></details>
    <p><button class="btn ghost sm" id="logout">Sair</button></p></div>${admin}`;
}
// passo a passo por marca: o caminho é sempre "app do relógio → Apple Saúde (iPhone) ou Health Connect (Android) → HAX"
const WATCHES = [
  ["garmin", "Garmin"], ["apple", "Apple Watch"], ["amazfit", "Amazfit"], ["xiaomi", "Xiaomi / Redmi"], ["samsung", "Samsung Galaxy"],
  ["pixel", "Pixel / Fitbit"], ["polar", "Polar"], ["huawei", "Huawei"], ["outro", "Outro"],
];
const WATCH_APP = {
  amazfit: ["Zepp", "Perfil → Contas de terceiros (Vincular contas)", "Health Connect", "Apple Saúde"],
  xiaomi: ["Mi Fitness", "Perfil → Dados de terceiros", "Health Connect", "Apple Saúde"],
  samsung: ["Samsung Health", "⋮ → Configurações → Health Connect", "Health Connect", null],
  pixel: ["Fitbit", "Perfil (sua foto) → Health Connect", "Health Connect", null],
  polar: ["Polar Flow", "Configurações → Apps conectados", "Health Connect", "Apple Saúde"],
  huawei: ["Huawei Health", "Eu → Configurações → Compartilhamento de dados", null, "Apple Saúde"],
  outro: ["do relógio", "Configurações / Perfil → Apps ou contas conectadas", "Health Connect", "Apple Saúde"],
};
function watchGuide(brand) {
  const ios = isIOS();
  if (!brand) return `<p class="small muted">Escolha a marca para ver o passo a passo.</p>`;
  if (brand === "garmin") return `<div class="wg"><b>Garmin: conexão direta (a mais completa)</b>
    <p class="small">Em <b>Garmin Connect</b> (logo acima) entre com o e-mail e a senha da Garmin. O HAX <b>manda o treino para o relógio</b> (com aquecimento, séries e descanso) e <b>traz de volta</b> treinos, sono, VFC e Body Battery para ajustar o dia.</p></div>`;
  const tail = (where) => `<li>Toque em <b>Gerar código pessoal</b> (aqui embaixo) e copie.</li>${ios
    ? `<li>No iPhone, crie o atalho do HAX (abra <b>iPhone (Apple Saúde) — app Atalhos</b> logo abaixo). Ele leva ${where} para o HAX todo dia.</li>`
    : `<li>Baixe e instale o app <b>HAX Sync</b> (botão em <b>Android (Health Connect)</b> abaixo), cole o código e toque em <b>Sincronizar</b>. Ele envia sozinho a cada poucas horas.</li>`}`;
  if (brand === "apple") return `<div class="wg"><b>Apple Watch</b><ol class="small">${tail("treinos, sono, FC de repouso, VFC e passos")}</ol>${ios ? "" : `<p class="small muted">O Apple Watch só funciona com iPhone.</p>`}</div>`;
  const [app, menu, hc, ah] = WATCH_APP[brand];
  const hub = ios ? ah : hc;
  if (!hub) {
    return `<div class="wg"><b>${esc((WATCHES.find((w) => w[0] === brand) || [])[1])}</b>
      <p class="small">${brand === "huawei"
        ? "No Android, o app Huawei Health não compartilha com o Health Connect. Opções: use o <b>treino guiado</b> do HAX no celular (registra tudo na hora) ou um app de terceiros que faça a ponte (ex.: Health Sync, pago). No iPhone funciona pelo Apple Saúde."
        : `O app ${esc(app)} não envia para o Apple Saúde no iPhone. Use o <b>treino guiado</b> do HAX no celular para registrar os treinos.`}</p></div>`;
  }
  return `<div class="wg"><b>${esc((WATCHES.find((w) => w[0] === brand) || [])[1])} no ${ios ? "iPhone" : "Android"}</b><ol class="small">
    <li>No app <b>${esc(app)}</b>: ${esc(menu)} → <b>${esc(hub)}</b> → permita tudo (treinos, frequência cardíaca, sono, passos).</li>
    ${!ios && hub === "Health Connect" ? `<li>Android 13 ou anterior: se pedir, instale o <b>Health Connect</b> pela Play Store (no Android 14+ ele já vem no sistema).</li>` : ""}
    ${tail("o que o relógio gravou")}</ol>
    <p class="small muted">Os nomes dos menus mudam um pouco entre versões do app. Procure por “${esc(hub)}”.</p></div>`;
}

// explica, em português simples, por que o treino do relógio não chegou (o app do celular conta o que achou no Health Connect)
function ingestHintHtml(last) {
  if (!last || last.source !== "android") return "";
  const d = last.diag; const tips = [];
  if (!d) {
    if (!last.workouts) tips.push("Nenhum treino veio neste envio. <b>Atualize o app HAX Sync</b> (baixe de novo no botão abaixo e instale por cima): a versão nova manda todos os tipos de treino — a antiga descartava os que o relógio grava como \"outro exercício\", comum em Amazfit e Xiaomi.");
  } else {
    const miss = d.missing || [];
    const PERM = { SLEEP: "Sono", HEART_RATE: "Frequência cardíaca", RESTING_HEART_RATE: "FC de repouso", HEART_RATE_VARIABILITY: "Variabilidade da FC", STEPS: "Passos", EXERCISE: "Exercício",
      DISTANCE: "Distância", TOTAL_CALORIES_BURNED: "Calorias", ACTIVE_CALORIES_BURNED: "Calorias ativas", VO2_MAX: "VO2máx", WEIGHT: "Peso", HEALTH_DATA_IN_BACKGROUND: "ler em segundo plano" };
    const TYPE = { OTHER_WORKOUT: "outro treino", STRENGTH_TRAINING: "musculação", WALKING: "caminhada", RUNNING: "corrida", RUNNING_TREADMILL: "corrida na esteira", BIKING: "bicicleta", BIKING_STATIONARY: "bicicleta ergométrica",
      HIKING: "trilha", ELLIPTICAL: "elíptico", YOGA: "yoga", PILATES: "pilates", HIIT: "HIIT", DANCING: "dança", SWIMMING_POOL: "natação", ROWING_MACHINE: "remo", STAIR_CLIMBING: "escada", STRETCHING: "alongamento" };
    const win = d.workout_days || d.window_days;
    if (d.app && /^1\.([02]|3)$/.test(d.app)) tips.push("Tem versão nova do HAX Sync (com o logo do HAX): baixe no botão abaixo e instale por cima. Seus dados e o código pessoal continuam.");
    if (d.app === "1.1") tips.push("Seu HAX Sync é a versão 1.1, que depois do primeiro envio <b>só olha os últimos 2 dias</b>: treino mais antigo não chega. Baixe de novo no botão abaixo (versão nova, com o logo do HAX), instale por cima e toque em Sincronizar: ela relê os últimos 30 dias.");
    if (miss.some((m) => /EXERCISE/.test(m))) tips.push("O HAX Sync está <b>sem permissão de Exercício</b> no Health Connect. Abra o Health Connect → Permissões do app → HAX Sync → ligue Exercício.");
    else if (!d.exercise_total) tips.push(`O Health Connect <b>não tinha nenhum treino</b> nos últimos ${win || "…"} dias. O relógio só entrega o treino se o app dele estiver ligado ao Health Connect: no <b>Zepp</b> (Amazfit) é Perfil → Adicionar contas → Health Connect; no <b>Mi Fitness</b>, Perfil → Health Connect. Ligue <b>Exercício</b>, frequência cardíaca e sono, abra o app do relógio para ele sincronizar e toque em Sincronizar no HAX Sync.`);
    else {
      const types = Object.entries(d.exercise_types || {}).map(([k, v]) => `${v}× ${esc(TYPE[k] || (/^OTHER_WORKOUT/.test(k) ? "outro treino" : k.toLowerCase().replace(/_/g, " ")))}`).join(", ");
      tips.push(`O relógio entregou ${d.exercise_total} treino(s) nos últimos ${win || "…"} dias (${types})${last.left_out ? `; <b>${last.left_out} não couberam neste envio</b> (os mais antigos): toque em Sincronizar de novo no HAX Sync para trazer o resto` : last.workouts < d.exercise_total ? "; os que já estavam aqui não entram de novo" : ""}.`);
    }
    if (d.steps_days && !d.sleep_days) tips.push("Vieram passos, mas <b>não veio sono</b>: ligue Sono e Frequência cardíaca na ligação do app do relógio com o Health Connect para o HAX calcular a prontidão.");
    if (miss.length && !miss.some((m) => /EXERCISE/.test(m))) tips.push(`Permissões desligadas no Health Connect: ${miss.map((m) => { const k = m.replace(/^READ_/, ""); return esc(PERM[k] || k.toLowerCase().replace(/_/g, " ")); }).join(", ")}.`);
  }
  return tips.length ? `<div class="rec small">${tips.map((t) => `<p style="margin:4px 0">${t}</p>`).join("")}</div>` : "";
}
async function devicesCard() {
  const me = state.me || {};
  if (!me.logged_in) return "";
  const st = await api("/api/account/ingest").catch(() => ({}));
  const last = st.last;
  const url = `${location.origin}/api/ingest`;
  let brand = ""; try { brand = localStorage.getItem("gg_watch_brand") || (state.profile && state.profile.watch_brand) || ""; } catch (_) { /* sem storage */ }
  return `<div class="card" id="devices"><h2>Qual é o seu relógio?</h2>
    <div class="fc-chips" id="wb-list">${WATCHES.map(([k, t]) => `<button type="button" class="wz-chip ${brand === k ? "on" : ""}" data-wb="${k}">${esc(t)}</button>`).join("")}</div>
    <div id="wb-guide">${watchGuide(brand)}</div>
    <div class="wg-what small"><b>O que funciona com cada um</b><br>
      <b>Garmin</b>: o treino vai para o relógio e volta com os dados. <b>Outras marcas</b>: o relógio grava normalmente e treinos, sono, FC de repouso e passos chegam ao HAX pelo Apple Saúde/Health Connect; para seguir o treino série a série, use o <b>treino guiado</b> no celular (só a Garmin aceita treino enviado de fora).</div>
    <h3 style="margin-top:14px">Código pessoal e apps</h3>
    <p class="small">O código é como uma senha só para o celular mandar dados para a sua conta.</p>
    <div class="row gap wrap"><button class="btn sm" id="ing-new">${st.has_token ? "Gerar código novo" : "Gerar código pessoal"}</button>
      ${st.has_token ? `<button class="btn sm ghost" id="ing-del">Desligar</button>` : ""}</div>
    <div id="ing-token"></div>
    <p class="small ${last ? "" : "muted"}">${last ? `Último envio: <b>${fmtDate(last.at)}</b> (${last.source === "android" ? "Android" : "iPhone"}) · ${last.days.length} dia(s) de saúde · ${last.workouts} treino(s) novo(s)${last.skipped ? ` · ${last.skipped} ignorado(s) (muito curto ou não é treino, ex.: meditação)` : ""}` : "Nenhum envio recebido ainda."}</p>
    ${ingestHintHtml(last)}
    <details><summary><b>iPhone (Apple Saúde) — app Atalhos</b></summary><div class="small">
      <ol>
        <li>Abra o app <b>Atalhos</b> → <b>+</b> (novo atalho) → nome "HAX".</li>
        <li>Adicione <b>Encontrar Amostras de Saúde</b>: Tipo <b>Análise do Sono</b>, Data de Início <b>nas últimas 24 horas</b>. Depois <b>Obter Detalhes das Amostras de Saúde → Duração</b> e <b>Calcular Estatísticas → Soma</b> (resultado em horas).</li>
        <li>Repita para <b>Frequência Cardíaca em Repouso</b> (estatística <b>Média</b>), <b>Variabilidade da Frequência Cardíaca</b> (Média) e <b>Passos</b> (Soma, hoje).</li>
        <li>Adicione <b>Dicionário</b> com as chaves: <code>source</code> = apple · <code>date</code> = Data Atual formatada como <b>ISO 8601</b> (só a data) · <code>sleep_h</code> · <code>rhr</code> · <code>hrv</code> · <code>hrv_kind</code> = sdnn · <code>steps</code> (cada uma com o resultado do passo correspondente).</li>
        <li>Opcional (treinos do Apple Watch ou de outro relógio que grava no Apple Saúde): <b>Encontrar Amostras de Saúde</b> do tipo <b>Treinos</b> nos últimos 2 dias → <b>Repetir com Cada</b> → um Dicionário por treino com <code>id</code>, <code>type</code>, <code>start</code> (ISO 8601), <code>duration_min</code>, <code>distance_km</code>, <code>kcal</code>, <code>avg_hr</code> → junte numa lista e ponha em <code>workouts</code>.</li>
        <li>Adicione <b>Obter Conteúdo do URL</b>: URL <code style="word-break:break-all">${esc(url)}</code>, Método <b>POST</b>, Cabeçalho <code>Authorization</code> = <code>Bearer SEU-CÓDIGO</code>, Corpo da Solicitação <b>JSON</b> = o Dicionário.</li>
        <li>Em <b>Automação</b> → <b>Hora do Dia</b> (ex.: 9h, todo dia) → executar o atalho, sem perguntar.</li>
      </ol>
      <p class="muted">Rode uma vez à mão e confira aqui em "Último envio". Os nomes dos menus mudam um pouco entre versões do iOS. Xiaomi (Mi Fitness) e Amazfit (Zepp) no iPhone: ligue a opção <b>Apple Saúde</b> no app do relógio, e o atalho leva os dados junto.</p></div></details>
    <details><summary><b>Android (Health Connect)</b></summary><div class="small">
      <p>Xiaomi (Mi Fitness), Samsung Health, Google Fit, Amazfit (Zepp), Fitbit…: no app do relógio, ligue o compartilhamento com o <b>Health Connect</b>.
        Depois instale o app <b>HAX Sync</b> (Android), cole o código pessoal e toque em Sincronizar. Ele envia sozinho a cada poucas horas.</p>
      <p><a class="btn sm ghost" href="/static/hax-sync.apk" download="HAX-Sync.apk">Baixar app Android (APK)</a></p>
      <p class="muted">Como o app não está na Play Store, o Android pede para permitir "instalar apps desta fonte".</p></div></details>
  </div>`;
}
function bindDevices() {
  const wl = $("#wb-list");
  if (wl) wl.onclick = (e) => {
    const b = e.target.closest("[data-wb]"); if (!b) return;
    try { localStorage.setItem("gg_watch_brand", b.dataset.wb); } catch (_) { /* */ }
    api("/api/profile", { method: "PUT", body: { watch_brand: b.dataset.wb } }).catch(() => {});
    $$("[data-wb]", wl).forEach((x) => x.classList.toggle("on", x === b));
    $("#wb-guide").innerHTML = watchGuide(b.dataset.wb);
  };
  const n = $("#ing-new");
  if (n) n.onclick = async () => {
    if ($("#ing-del") && !confirm("Gerar um código novo? O antigo para de funcionar (atualize o atalho/app).")) return;
    const r = await api("/api/account/ingest-token", { method: "POST" });
    $("#ing-token").innerHTML = `<p class="rec atencao small">Seu código (aparece só agora — copie e guarde): <br><code style="word-break:break-all;font-size:15px">${esc(r.token)}</code>
      <br><button class="btn sm" id="ing-copy">Copiar código</button></p>`;
    $("#ing-copy").onclick = async () => { try { await navigator.clipboard.writeText(r.token); toast("Código copiado"); } catch (_) { prompt("Copie o código:", r.token); } };
  };
  const d = $("#ing-del");
  if (d) d.onclick = async () => { if (!confirm("Desligar? O atalho/app para de enviar.")) return; await api("/api/account/ingest-token", { method: "DELETE" }); viewPerfil(); };
}
function bindAccount() {
  bindDevices();
  bindCoachMine(); bindPartnerMine();
  const st = $("#acc-setup");
  if (st) st.onsubmit = async (e) => {
    e.preventDefault();
    try { state.me = await api("/api/auth/setup", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Conta criada: o app agora pede login."); viewPerfil(); }
    catch (err) { toast(err.message, 5000); }
  };
  const lo = $("#logout");
  if (lo) lo.onclick = async () => { await api("/api/auth/logout", { method: "POST" }); state.me = null; clearLocalDrafts(); showAuth("login"); };
  const pw = $("#pw-form");
  if (pw) pw.onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/account/password", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Senha trocada. Os outros aparelhos vão pedir login de novo."); e.target.reset(); }
    catch (err) { toast(err.message, 5000); }
  };
  const inv = $("#inv-form");
  if (inv) inv.onsubmit = async (e) => {
    e.preventDefault();
    const note = new FormData(e.target).get("note");
    const r = await api("/api/admin/invites", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
    const link = `${location.origin}${location.pathname}#convite/${r.code}`;
    const msg = inviteMsg(note, link);
    await viewPerfil("conta");
    const body = openModal(`<h2>Convite ${note ? `para ${esc(note)}` : "criado"} ✓</h2>
      <p class="small muted">Mande esta mensagem (o link vale para uma pessoa):</p><pre class="inv-msg">${esc(msg)}</pre>
      <a class="btn block wa" id="inv-wa" href="https://wa.me/?text=${encodeURIComponent(msg)}" target="_blank" rel="noopener">Enviar pelo WhatsApp</a>
      <div class="row gap" style="margin-top:8px"><button class="btn ghost grow" id="inv-share">Compartilhar…</button><button class="btn ghost grow" id="inv-copy">Copiar mensagem</button></div>`);
    $("#inv-share", body).onclick = async () => { try { if (navigator.share) await navigator.share({ text: msg }); else { await navigator.clipboard.writeText(msg); toast("Mensagem copiada"); } } catch (_) { /* cancelado */ } };
    $("#inv-copy", body).onclick = async () => { try { await navigator.clipboard.writeText(msg); toast("Mensagem copiada"); } catch (_) { prompt("Copie a mensagem:", msg); } };
  };
  $$("[data-share]").forEach((b) => { b.onclick = async () => { try { if (navigator.share) await navigator.share({ text: b.dataset.share }); else { await navigator.clipboard.writeText(b.dataset.share); toast("Mensagem copiada"); } } catch (_) { /* cancelado */ } }; });
  $$("[data-copy]").forEach((b) => { b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); toast("Link copiado"); } catch (_) { prompt("Copie o link:", b.dataset.copy); } }; });
  $$("[data-delinv]").forEach((b) => { b.onclick = async () => { await api(`/api/admin/invites/${b.dataset.delinv}`, { method: "DELETE" }); viewPerfil(); }; });
  $$("[data-reset]").forEach((b) => { b.onclick = async () => {
    if (!confirm("Gerar uma senha provisória para esta pessoa? A senha atual dela deixa de valer.")) return;
    const r = await api(`/api/admin/users/${b.dataset.reset}/reset-password`, { method: "POST" });
    prompt("Senha provisória (envie para a pessoa; ela troca no Perfil):", r.temporary_password);
  }; });
}

/* ----------------------------------------------------------- PLANILHAS -- */
async function viewPlanilhas() {
  const plans = await api("/api/plans");
  view.innerHTML = `
  <div class="card"><div class="row between wrap gap"><h2>Suas planilhas</h2>
    <div class="row gap wrap"><button class="btn sm" id="gen-plan">Gerar automático</button><button class="btn sm ghost" id="new-plan">Nova</button><button class="btn sm ghost" id="tpl-plan">Modelos</button><button class="btn sm ghost" id="csv-plan">Importar CSV</button></div></div>
    ${plans.length ? `<ul class="list">${plans.map((p) => `<li><a href="#plano/${p.id}" class="row between" style="text-decoration:none;color:inherit">
      <span><b>${esc(p.name)}</b><br><span class="small muted">${p.days.length} treinos · ${p.days_per_week || p.days.length}×/semana</span></span>
      <span class="pill ${p.active ? "ok" : "muted"}">${p.active ? "ativa" : "arquivada"}</span></a></li>`).join("")}</ul>`
      : `<p class="empty">Crie uma planilha do zero, a partir de um modelo ou importando um CSV (Excel/Google Planilhas).</p>`}
  </div>`;
  $("#gen-plan").onclick = () => openGenerator();
  $("#new-plan").onclick = async () => {
    const p = await api("/api/plans", { method: "POST", body: { name: "Minha planilha", days_per_week: state.profile.days_per_week || 3, days: [{ name: "A", items: [] }, { name: "B", items: [] }] } });
    location.hash = `#plano/${p.id}`;
  };
  $("#tpl-plan").onclick = () => {
    const body = openModal(`<h2>Modelos</h2><p class="small muted">Exercícios marcados como “evitar” nas suas limitações são trocados automaticamente.</p>
      <ul class="list">${state.meta.templates.map((t) => `<li><b>${esc(t.name)}</b><div class="small muted">${esc(t.description)}</div>
      <button class="btn sm" style="margin-top:6px" data-tpl="${t.id}">Usar este modelo</button></li>`).join("")}</ul>`);
    body.onclick = async (e) => {
      const id = e.target.dataset.tpl; if (!id) return;
      const p = await api("/api/plans", { method: "POST", body: { template: id } });
      closeModal(); if (p.notes) toast(p.notes, 6000); location.hash = `#plano/${p.id}`;
    };
  };
  $("#csv-plan").onclick = () => {
    const body = openModal(`<h2>Importar planilha (CSV)</h2>
      <p class="small muted">No Excel/Google Planilhas: Arquivo → Baixar/Salvar como → CSV. Colunas: <code>treino; exercicio; series; reps; carga_kg; descanso_s; tempo_s; duracao_min; zona; obs</code>. Nomes em português ou inglês.</p>
      <div class="field"><label>Nome</label><input id="csv-name" value="Planilha importada"></div>
      <div class="field"><label>Arquivo</label><input type="file" id="csv-file" accept=".csv,text/csv"></div>
      <div class="field"><label>…ou cole o conteúdo</label><textarea id="csv-text" placeholder="treino;exercicio;series;reps;carga_kg;descanso_s&#10;A;Supino reto com barra;4;8-12;60;120&#10;A;Esteira caminhada;;;;;;10;2"></textarea></div>
      <div class="row gap"><button class="btn ghost" id="csv-preview">Pré-visualizar</button><button class="btn" id="csv-save">Importar</button></div>
      <div id="csv-out" style="margin-top:10px"></div>`);
    $("#csv-file", body).onchange = async (e) => { const f = e.target.files[0]; if (f) $("#csv-text", body).value = await f.text(); };
    const run = async (save) => {
      const res = await api("/api/plans/import-csv", { method: "POST", body: { csv: $("#csv-text", body).value, name: $("#csv-name", body).value, save } });
      $("#csv-out", body).innerHTML = `<p class="small">${res.matched.length} exercícios reconhecidos${res.unmatched.length ? `, <b>${res.unmatched.length} não encontrados</b>` : ""}.</p>
        <ul class="list small">${res.matched.map((m) => `<li>linha ${m.line}: “${esc(m.input)}” → <b>${esc(m.matched)}</b></li>`).join("")}
        ${res.unmatched.map((m) => `<li class="pill bad">linha ${m.line}: “${esc(m.name)}” não encontrado</li>`).join("")}</ul>`;
      if (save && res.saved) { closeModal(); location.hash = `#plano/${res.saved.id}`; }
    };
    $("#csv-preview", body).onclick = () => run(false).catch((e) => toast(e.message));
    $("#csv-save", body).onclick = () => run(true).catch((e) => toast(e.message));
  };
}

/* -------------------------------------------------------- editor plano -- */
async function viewPlano(id) {
  state.draftPlan = await api(`/api/plans/${id}`);
  state.planTab = Math.min(state.planTab, Math.max(0, state.draftPlan.days.length - 1));
  renderPlano();
}

function renderPlano() {
  const p = state.draftPlan;
  const day = p.days[state.planTab];
  view.innerHTML = `
  <div class="card">
    <div class="row gap"><input id="plan-name" value="${esc(p.name)}" aria-label="Nome da planilha" class="grow">
      <button class="btn" id="plan-save">Salvar</button></div>
    <div class="row gap wrap" style="margin-top:8px">
      <label class="row gap small" style="margin:0">Treinos por semana <input id="plan-dpw" class="num" type="number" min="1" max="14" value="${p.days_per_week || p.days.length}"></label>
      <label class="row gap small" style="margin:0"><input type="checkbox" id="plan-active" ${p.active ? "checked" : ""}> ativa</label>
    </div>
    ${p.notes ? `<p class="small muted">${esc(p.notes)}</p>` : ""}
  </div>
  <div class="tabs">${p.days.map((d, i) => `<button data-day="${i}" class="${i === state.planTab ? "on" : ""}">${esc(d.name)}</button>`).join("")}
    <button id="add-day">+ treino</button></div>
  ${day ? `<div class="card">
    <div class="row gap"><input id="day-name" value="${esc(day.name)}" class="grow" aria-label="Nome do treino">
      <button class="icon-btn" id="del-day" title="Excluir treino">🗑</button></div>
    <div id="day-time" style="margin-top:8px"></div>
    <div style="margin-top:10px">${day.items.map((it, i) => planItemHtml(it, i, day.items.length)).join("") || `<p class="empty">Adicione exercícios ou cardio.</p>`}</div>
    <div class="row gap wrap"><button class="btn sm" id="add-ex">+ Exercício</button><button class="btn sm ghost" id="add-cardio">+ Cardio</button></div>
  </div>
  <div class="card"><h2>Enviar ao Garmin</h2>
    <p class="small muted">Salve antes de exportar. <b>.FIT</b>: copie via USB para <code>GARMIN/NewFiles</code> no relógio. <b>Garmin Connect</b>: envia direto para sua biblioteca de treinos (e agenda no calendário), sincronizando com o relógio.</p>
    <div class="row gap wrap">
      <a class="btn sm ghost" href="/api/plans/${p.id}/days/${state.planTab}/fit">Baixar .FIT</a>
      <input type="date" id="push-date" style="width:auto" value="${localISO().slice(0, 10)}">
      <button class="btn sm" id="push">Enviar ao Garmin Connect</button>
      <button class="btn sm ghost" id="show-json">Ver JSON</button>
    </div>
  </div>` : ""}
  <div class="card" id="plan-analysis"><h2>Análise da planilha</h2><p class="muted small">Calculando…</p></div>
  <div class="row gap wrap" style="margin-bottom:16px"><a class="btn sm ghost" href="/api/plans/${p.id}/csv">Exportar CSV</a><button class="btn sm danger" id="del-plan">Excluir planilha</button></div>`;
  bindPlano();
  $$("textarea.autogrow").forEach(growTA);
  refreshPlanAnalysis();
}
function growTA(t) { t.style.height = "auto"; t.style.height = t.scrollHeight + 2 + "px"; }

function planItemHtml(it, i, n) {
  const ex = it._ex || {};
  const isCardio = (it.exercise_id || "").startsWith("cardio_") || ex.kind === "cardio";
  const timed = !isCardio && (ex.timed || it.duration_s);
  const name = ex.name || it._name || it.exercise_id;
  const f = (k, label, type = "text", extra = "", cls = "") => `<div class="${cls}"><label>${label}</label><input data-f="${k}" data-i="${i}" type="${type}" value="${esc(it[k] ?? "")}" ${extra}></div>`;
  const note = (label) => `<div class="wide"><label>${label}</label><textarea data-f="notes" data-i="${i}" rows="1" class="autogrow">${esc(it.notes ?? "")}</textarea></div>`;
  const tags = [it.phase === "preparo" ? `<span class="pill muted">aquecimento</span>` : it.phase === "pos" ? `<span class="pill muted">volta à calma</span>` : "",
    it.finisher ? `<span class="pill muted">finalizador</span>` : "", it.focus ? `<span class="pill focus-pill">🎯 foco</span>` : "", limBadge(it._lim)].filter(Boolean).join(" ");
  const sub = isCardio ? `${it.duration_min || "?"} min · Z${it.zone || 2}` : timed ? `${it.sets || 1} × ${it.duration_s || "?"} s` : `${it.sets || "?"} × ${it.reps || "?"}${it.load_kg ? ` · ${it.load_kg} kg` : ""}`;
  return `<div class="plan-item${it.phase ? " pi-" + it.phase : ""}">
    <div class="pi-head">
      <button class="pi-media" data-ex="${esc(it.exercise_id)}" aria-label="Ver ${esc(name)}">${mediaHtml(it.exercise_id, "s", isCardio ? "run" : null)}<span class="pi-n">${i + 1}</span></button>
      <div class="pi-title"><b class="link" data-ex="${esc(it.exercise_id)}">${esc(name)}</b>
        <span class="pi-sub">${esc(sub)}</span><span class="pi-tags">${tags}</span></div>
      <button class="pi-rm" data-rm="${i}" aria-label="Remover ${esc(name)}">✕</button>
    </div>
    <div class="fields ${isCardio ? "f-cardio" : "f-str"}">
      ${isCardio ? `${f("duration_min", "Minutos", "number", 'min="1" inputmode="numeric"')}
        <div><label>Zona FC</label><select data-f="zone" data-i="${i}">${zoneOpts(it.zone || 2)}</select></div>
        ${note("Observação (velocidade, inclinação, nível)")}`
      : `${f("sets", "Séries", "number", 'min="1" inputmode="numeric"')}${timed ? f("duration_s", "Tempo (s)", "number", 'inputmode="numeric"') : f("reps", "Reps")}${f("load_kg", "Carga kg", "number", 'step="0.5" inputmode="decimal"')}
        ${f("rest_s", "Pausa (s)", "number", 'step="15" inputmode="numeric"')}${note("Observação")}`}
    </div>
    ${isCardio && it.intervals ? `<p class="small muted">Intervalado: ${it.intervals.repeats}× ${it.intervals.work_s}s Z${it.intervals.work_zone} / ${it.intervals.rest_s}s Z${it.intervals.rest_zone}</p>` : ""}
    <div class="pi-move"><button class="pi-mv" data-mv="${i}:-1" ${i === 0 ? "disabled" : ""}>↑ Subir</button><button class="pi-mv" data-mv="${i}:1" ${i === n - 1 ? "disabled" : ""}>↓ Descer</button></div>
  </div>`;
}

function readPlanForm() {
  const p = state.draftPlan;
  p.name = $("#plan-name").value.trim() || "Minha planilha";
  p.days_per_week = +$("#plan-dpw").value || p.days.length;
  p.active = $("#plan-active").checked;
  const day = p.days[state.planTab];
  if (day) {
    day.name = $("#day-name").value.trim() || day.name;
    $$("[data-f]").forEach((el) => {
      const it = day.items[+el.dataset.i]; const k = el.dataset.f; let v = el.value;
      if (["sets", "rest_s", "duration_s", "duration_min", "zone"].includes(k)) v = v === "" ? null : +v;
      if (k === "rest_s" && v !== (it.rest_s ?? null)) it.rest_custom = true;  // escolhido à mão: o app não recalcula
      else if (k === "load_kg") v = v === "" ? null : parseFloat(v);
      else if (k === "reps") v = /^\d+$/.test(v) ? +v : (v || null);
      if (v === null || v === "") delete it[k]; else it[k] = v;
    });
  }
}

function cleanPlan(p) {
  return { ...p, days: p.days.map((d) => ({ ...d, items: d.items.map((it) => Object.fromEntries(Object.entries(it).filter(([k]) => !k.startsWith("_")))) })) };
}

let analysisTimer;
function refreshPlanAnalysis() {
  clearTimeout(analysisTimer);
  analysisTimer = setTimeout(async () => {
    const box = $("#plan-analysis"); if (!box) return;
    try {
      const a = await api("/api/plans/analyze", { method: "POST", body: cleanPlan(state.draftPlan) });
      const conflicts = a.conflicts.map((c) => `<li><span class="pill ${LIM_CLASS[c.status]}">${LIM_TXT[c.status]}</span> <b>${esc(c.name)}</b> <span class="small muted">(${esc(c.day)})</span><div class="small muted">${esc(c.reasons.join(" · "))}</div></li>`).join("");
      const dt = $("#day-time"); if (dt && a.times) dt.innerHTML = timeBar(a.times[state.planTab]);
      box.innerHTML = `<h2>Análise da planilha</h2>
        <p class="small muted">Volume semanal planejado com ${a.days_per_week} treinos/semana · cardio ${a.cardio_minutes_week} min (${a.cardio_who_equivalent} equivalentes OMS).</p>
        ${muscleBars(a.muscles, "sets_week")}
        ${conflicts ? `<h3>Conflitos com suas limitações</h3><ul class="list">${conflicts}</ul>` : ""}
        <h3>Equilíbrio</h3>${balanceHtml(a.balance)}
        <h3>Sugestões</h3>${recsHtml(a.recommendations)}`;
      // marca limitações nos itens
      const lims = {}; a.conflicts.forEach((c) => (lims[c.exercise_id] = c));
      let changed = false;
      state.draftPlan.days.forEach((d) => d.items.forEach((it) => { const l = lims[it.exercise_id] || null; if ((it._lim || null) !== l) { it._lim = l; changed = true; } }));
      if (changed) $$(".plan-item").forEach((el, i) => { const it = state.draftPlan.days[state.planTab]?.items[i]; const b = $(".pi-tags", el); if (it && b) { $$(".lim-pill", b).forEach((x) => x.remove()); b.insertAdjacentHTML("beforeend", limBadge(it._lim)); } });
    } catch (e) { box.innerHTML = `<p class="pill bad">${esc(e.message)}</p>`; }
  }, 250);
}

async function savePlan(quiet) {
  readPlanForm();
  const saved = await api(`/api/plans/${state.draftPlan.id}`, { method: "PUT", body: cleanPlan(state.draftPlan) });
  const names = {}; state.draftPlan.days.forEach((d) => d.items.forEach((it) => (names[it.exercise_id] = it._ex)));
  saved.days.forEach((d) => d.items.forEach((it) => (it._ex = names[it.exercise_id])));
  state.draftPlan = saved;
  if (!quiet) toast("Planilha salva");
}

function bindPlano() {
  const p = state.draftPlan;
  const ensureNames = async () => {
    const missing = new Set(); p.days.forEach((d) => d.items.forEach((it) => { if (!it._ex) missing.add(it.exercise_id); }));
    if (!missing.size) return;
    await Promise.all([...missing].map(async (id) => { try { const ex = await api(`/api/exercises/${encodeURIComponent(id)}`); p.days.forEach((d) => d.items.forEach((it) => { if (it.exercise_id === id) it._ex = ex; })); } catch (_) { /* id desconhecido */ } }));
    renderPlano();
  };
  ensureNames();
  $("#plan-save").onclick = () => savePlan().catch((e) => toast(e.message));
  view.oninput = (e) => { if (e.target.matches("textarea.autogrow")) growTA(e.target); if (e.target.matches("[data-f], #plan-dpw")) { readPlanForm(); refreshPlanAnalysis(); } };
  view.onchange = view.oninput;
  $$("[data-day]").forEach((b) => (b.onclick = () => { readPlanForm(); state.planTab = +b.dataset.day; renderPlano(); }));
  $("#add-day").onclick = () => { readPlanForm(); p.days.push({ name: String.fromCharCode(65 + p.days.length), items: [] }); state.planTab = p.days.length - 1; renderPlano(); };
  const dd = $("#del-day"); if (dd) dd.onclick = () => { if (!confirm("Excluir este treino?")) return; p.days.splice(state.planTab, 1); state.planTab = Math.max(0, state.planTab - 1); renderPlano(); };
  $$("[data-rm]").forEach((b) => (b.onclick = () => { readPlanForm(); p.days[state.planTab].items.splice(+b.dataset.rm, 1); renderPlano(); }));
  $$("[data-mv]").forEach((b) => (b.onclick = () => { readPlanForm(); const [i, d] = b.dataset.mv.split(":").map(Number); const items = p.days[state.planTab].items; [items[i], items[i + d]] = [items[i + d], items[i]]; renderPlano(); }));
  loadMedia().then((m) => { if (m && !$(".pi-media .demo") && $(".pi-media") && Object.keys(m.f || {}).length) { readPlanForm(); renderPlano(); } }).catch(() => {});
  const addEx = (kind) => pickExercise(kind, (ex) => {
    readPlanForm();
    const item = ex.kind === "cardio" ? { exercise_id: ex.id, duration_min: 10, zone: 2 }
      : { exercise_id: ex.id, sets: 3, reps: ex.timed ? undefined : "8-12", rest_s: 90, ...(ex.timed ? { duration_s: 30 } : {}) };
    item._ex = ex; p.days[state.planTab].items.push(item); renderPlano();
  });
  const ae = $("#add-ex"); if (ae) ae.onclick = () => addEx(null);
  const ac = $("#add-cardio"); if (ac) ac.onclick = () => addEx("cardio");
  $("#del-plan").onclick = async () => { if (!confirm("Excluir a planilha inteira?")) return; await api(`/api/plans/${p.id}`, { method: "DELETE" }); location.hash = "#planilhas"; };
  const push = $("#push");
  if (push) push.onclick = async () => {
    push.disabled = true;
    try {
      await savePlan(true);
      const r = await api(`/api/plans/${p.id}/days/${state.planTab}/push`, { method: "POST", body: { date: $("#push-date").value || null } });
      openModal(`<h2>Enviado ✔</h2><p>“${esc(r.name)}” está na sua biblioteca do Garmin Connect${r.scheduled ? ` e agendado para <b>${esc(r.scheduled)}</b>` : ""}.</p>
        <p class="small muted">Sincronize o relógio pelo app Garmin Connect. ${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Abrir no Garmin Connect</a>` : ""}</p>`);
    } catch (e) { toast(e.message, 5000); if (/login|conectado/i.test(e.message)) location.hash = "#perfil"; }
    finally { push.disabled = false; }
  };
  const sj = $("#show-json");
  if (sj) sj.onclick = async () => {
    await savePlan(true);
    const j = await api(`/api/plans/${p.id}/days/${state.planTab}/connect.json`);
    openModal(`<h2>JSON do Garmin Connect</h2><p class="small muted">Formato do editor de treinos (workout-service).</p>
      <a class="btn sm ghost" href="/api/plans/${p.id}/days/${state.planTab}/connect.json?download=1">Baixar</a><pre class="json">${esc(JSON.stringify(j, null, 2))}</pre>`);
  };
  view.onclick = (e) => { const t = e.target.closest && e.target.closest("[data-ex]"); if (t) showExercise(t.dataset.ex); };
}

/* ----------------------------------------------------- seletor exercício */
function pickExercise(kind, onPick, opts = {}) {
  const missing = (opts.missing || []).slice(0, 6);
  const body = openModal(`<h2>${kind === "cardio" ? "Aparelho de cardio" : "Adicionar exercício"}</h2>
    ${missing.length ? `<div class="pk-miss"><span class="small muted">Falta na semana:</span>${missing.map((m) => `<button class="chip ${opts.muscle === m.key ? "on" : ""}" data-pkm="${esc(m.key)}">${esc(m.pt)} <b>${fmtSets(m.missing)}</b></button>`).join("")}</div>` : ""}
    <div class="row gap"><input id="pk-q" placeholder="Buscar (ex.: supino, remada, esteira)" ${opts.muscle ? "" : "autofocus"} class="grow">
      <select id="pk-m" style="width:auto"><option value="">Músculo</option>${state.meta.muscles.map((m) => `<option value="${m.key}" ${opts.muscle === m.key ? "selected" : ""}>${esc(m.pt)}</option>`).join("")}</select></div>
    <label class="row gap small" style="margin:8px 0"><input type="checkbox" id="pk-hide" checked> ocultar exercícios a evitar</label>
    <ul class="list" id="pk-list"></ul>`);
  let t;
  const load = async () => {
    if (!$("#pk-q", body)) return;  // a janela já fechou (busca atrasada)
    const qs = new URLSearchParams({ q: $("#pk-q", body).value, limit: 40, hide_avoid: $("#pk-hide", body).checked });
    const mus = $("#pk-m", body).value;
    if (kind) qs.set("kind", kind);
    else if (mus && !$("#pk-q", body).value.trim()) { qs.set("kind", "strength"); qs.set("train", "true"); }  // "quero treinar este músculo": os exercícios certos primeiro
    if (mus) qs.set("muscle", mus);
    const res = await api(`/api/exercises?${qs}`);
    if (!$("#pk-list", body)) return;
    $("#pk-list", body).innerHTML = res.map((ex) => `<li class="ex-item" data-pick="${esc(ex.id)}">
      ${mediaHtml(ex.id, "s")}
      <span class="grow"><b>${esc(ex.name)}</b> ${limBadge(ex.limitation)}<br><span class="small muted">${esc(ex.primary_pt.join(", "))} · ${esc(ex.equipment_pt || "")}</span></span></li>`).join("") || `<li class="empty">Nada encontrado.</li>`;
    body._res = res;
  };
  body.oninput = () => { clearTimeout(t); t = setTimeout(load, 200); };
  body.onchange = body.oninput;
  body.onclick = (e) => {
    const m = e.target.closest("[data-pkm]");
    if (m) { const sel = $("#pk-m", body); sel.value = sel.value === m.dataset.pkm ? "" : m.dataset.pkm; $$("[data-pkm]", body).forEach((b) => b.classList.toggle("on", b.dataset.pkm === sel.value)); return load(); }
    const li = e.target.closest("[data-pick]"); if (!li) return;
    const ex = (body._res || []).find((x) => x.id === li.dataset.pick); if (!ex) return;
    clearTimeout(t); closeModal(); onPick(ex);
  };
  load();
}
const fmtSets = (v) => (Math.round(v * 2) / 2).toLocaleString("pt-BR");
// item novo num treino em andamento (livre ou guiado)
function newSessionItem(ex) {
  return ex.kind === "cardio" ? { exercise_id: ex.id, name: ex.name, kind: "cardio", plan: {}, duration_min: 10, zone: 2, images: ex.images, limitation: ex.limitation, added: true }
    : { exercise_id: ex.id, name: ex.name, kind: ex.kind, plan: { rest_s: 90 }, sets: [1, 2, 3].map(() => ({ reps: 10, load_kg: null, done: false })), images: ex.images, limitation: ex.limitation, timed: ex.timed, added: true,
      pm: ex.kind === "strength" ? ex.primaryMuscles || [] : [], sm: ex.kind === "strength" ? ex.secondaryMuscles || [] : [] };
}
// tirar um exercício do treino em andamento (selecionou o aparelho errado)
function removeSessionItem(i) {
  const s = state.session; const it = s.items[i]; if (!it) return false;
  const doneN = (it.sets || []).filter((x) => x.done).length;
  if ((doneN || it.done) && !confirm(`Tirar ${it.name} do treino? ${doneN ? (doneN === 1 ? "A série marcada será apagada." : `As ${doneN} séries marcadas serão apagadas.`) : "Ele está marcado como feito."}`)) return false;
  const order = fxOrder(s); const pos = order.indexOf(i);
  s.items.splice(i, 1);
  s.fsel = null; s.restEnd = null; s.restOver = false; s.workEnd = null; s.prepEnd = null; s.timeUp = false; s.lastSet = null;
  if (Number.isInteger(s.fx)) {
    if (pos === s.fx) s.fx = Number.isInteger(s.fxBack) ? s.fxBack : null;  // estava neste exercício: volta para onde veio (ou ao 1º pendente)
    else if (pos >= 0 && pos < s.fx) s.fx -= 1;
    if (Number.isInteger(s.fx)) s.fx = Math.max(0, Math.min(s.fx, s.items.length));
  }
  s.fxBack = null;
  if (!s.items.length && !s.schedule_id && !s.plan_id) s.view = "lista";  // treino livre vazio: volta para montar
  store("gg_session", s.items.length || s.schedule_id || s.plan_id ? s : null); toast(`${it.name} saiu do treino`);
  return true;
}

/* --------------------------------------------------- detalhe exercício -- */
async function showExercise(id) {
  const ex = await api(`/api/exercises/${encodeURIComponent(id)}`);
  const g = ex.garmin || {};
  const body = openModal(`<h2>${esc(ex.name)}</h2>
    ${ex.name !== ex.name_en ? `<p class="small muted">${esc(ex.name_en)}</p>` : ""}
    <div class="ex-hero">${mediaHtml(ex.id, "l")}</div>
    ${ex.my_machine ? myMachineChip(ex.my_machine, false) : ""}
    ${ex.machine_id ? `<button class="btn ghost block" data-machine="${esc(ex.machine_id)}" style="margin:-2px 0 10px">▶ Ver o aparelho em vídeo</button>` : ""}
    ${ex.kind !== "cardio" ? `<button class="act" id="ex-mm" style="margin:0 0 10px">📷 ${ex.my_machine ? "Editar meu aparelho" : "Fotografar o aparelho da minha academia"}</button>` : ""}
    ${ex.image_like ? `<p class="small muted img-note">Foto de um movimento parecido (${esc(ex.image_like)}): o aparelho da sua academia pode ser diferente.</p>` : ""}
    ${ex.top_machine || ex.evidence ? `<div class="evidence"><b>${ex.top_machine ? "Por que está entre os melhores" : "Sobre este aparelho"}</b><p class="small">${esc(ex.evidence)}</p>
      <p class="small muted">${ex.availability_br === "comum" ? "Comum nas academias brasileiras." : ex.availability_br === "média" ? "Aparece em academias médias e grandes." : "Mais raro: academias grandes e premium."}${ex.lengthened ? " Trabalha o músculo alongado." : ""}</p>
      ${(ex.sources || []).length ? `<p class="small">${ex.sources.map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener">fonte ${i + 1}</a>`).join(" · ")}</p>` : ""}</div>` : ""}
    ${ex.images.length > 1 ? `<div class="imgs">${ex.images.map((s, i) => `<figure><img loading="lazy" src="${esc(s)}" alt=""><figcaption class="small muted">${i ? "fim do movimento" : "início"}</figcaption></figure>`).join("")}</div>` : ""}
    <p><span class="pill">${esc(ex.category_pt)}</span> <span class="pill muted">${esc(ex.equipment_pt || "")}</span> <span class="pill muted">${esc(ex.level_pt || "")}</span>
      ${ex.mechanic_pt && ex.mechanic_pt !== "—" ? `<span class="pill muted">${esc(ex.mechanic_pt)}</span>` : ""} ${ex.impact ? `<span class="pill muted">impacto ${esc(ex.impact)}</span>` : ""}</p>
    <p><b>Primários:</b> ${esc(ex.primary_pt.join(", ") || "—")}<br><b>Secundários:</b> ${esc(ex.secondary_pt.join(", ") || "—")}</p>
    ${ex.limitation.status !== "ok" ? `<div class="rec ${ex.limitation.status === "evitar" ? "alerta" : "atencao"}"><b>${LIM_TXT[ex.limitation.status].toUpperCase()}</b><br>${ex.limitation.reasons.map(esc).join("<br>")}</div>` : ex.limitation.reasons.length ? `<div class="rec">${ex.limitation.reasons.map(esc).join("<br>")}</div>` : ""}
    ${ex.last ? `<p class="small"><b>Última vez</b> (${fmtDate(ex.last.date)}): ${ex.last.sets ? ex.last.sets.map((s) => `${s.reps ?? "-"}×${s.load_kg ?? 0}kg`).join(", ") : `${ex.last.duration_min} min Z${ex.last.zone}`}</p>` : ""}
    ${ex.last && ex.last.sets ? `<p><a class="btn sm ghost" href="#evolucao/${encodeURIComponent(ex.id)}" data-close>📈 Ver evolução</a></p>` : ""}
    ${ex.instructions && ex.instructions.length ? `<details><summary>Execução</summary><ol class="small">${ex.instructions.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details>` : ""}
    <div class="hr"></div>
    <div class="row between gap wrap"><div class="small">No Garmin: <b>${esc(g.garmin_name || g.category)}</b> <span class="muted">(${esc(g.category)} / ${esc(g.exercise)})</span>
      <span class="pill ${g.confidence === "alta" || g.confidence === "manual" ? "ok" : "warn"}">${esc(g.confidence)}</span></div>
      ${ex.machine ? "" : `<button class="btn sm ghost" id="g-edit">Alterar</button>`}</div>
    <div id="g-box"></div>`);
  const evo = $("[data-close]", body); if (evo) evo.onclick = () => closeModal();
  const exmm = $("#ex-mm", body);
  if (exmm) exmm.onclick = () => openMyMachine({ ...(ex.my_machine || {}), exercise_id: ex.id, exercise_name: ex.name }, () => showExercise(ex.id));
  const mb = $("[data-machine]", body); if (mb) mb.onclick = () => showMachine(mb.dataset.machine);
  const ge = $("#g-edit", body);
  if (ge) ge.onclick = () => {
    $("#g-box", body).innerHTML = `<input id="g-q" placeholder="Buscar no catálogo do Garmin (inglês)" value="${esc(ex.name_en.split(" ").slice(0, 2).join(" "))}"><ul class="list small" id="g-list"></ul>`;
    const run = async () => {
      const res = await api(`/api/garmin/catalog?q=${encodeURIComponent($("#g-q", body).value)}`);
      $("#g-list", body).innerHTML = res.map((r) => `<li><button class="chip" data-gc="${esc(r.category)}" data-ge="${esc(r.exercise)}">${esc(r.name)}</button> <span class="muted">${esc(r.category)}</span></li>`).join("");
    };
    $("#g-q", body).oninput = run; run();
    $("#g-list", body).onclick = async (e) => {
      const b = e.target.closest("[data-gc]"); if (!b) return;
      await api(`/api/exercises/${encodeURIComponent(ex.id)}/garmin`, { method: "PUT", body: { category: b.dataset.gc, exercise: b.dataset.ge } });
      toast("Mapeamento Garmin atualizado"); showExercise(ex.id);
    };
  };
}

/* ------------------------------------------------------------- TREINAR -- */
async function viewTreinar(planId, idx) {
  if (planId === "parceiro" || planId === "editar" || planId === "pronto") {  // treino puxado do parceiro, "treinar junto", correção ou gerado: o rascunho já está montado
    const ok = (x) => x && (planId === "editar" ? x.edit_id : planId === "pronto" ? x.generated : x.with_partner);
    const draft = ok(state.session) ? state.session : store("gg_session");
    if (ok(draft)) { state.session = draft; return renderSession(); }
    return viewTreinar();
  }
  if (planId === "livre") {
    const draft = store("gg_session");
    if (draft && draft.edit_id) {  // correção de um treino salvo largada no meio: não é um treino livre de hoje
      if (confirm(`Você tinha começado a corrigir “${draft.day_name || "um treino"}” de ${fmtDate(draft.date)} e não salvou. Continuar a correção? (Cancelar descarta a correção; o registro fica como estava)`)) { state.session = draft; location.hash = "#treinar/editar"; return; }
      store("gg_session", null);
      const sch0 = await api("/api/schedule").catch((e) => { if (e && e.name === "AbortError") throw e; return null; });
      return startFree(sch0);
    }
    if (draft && (draft.schedule_id || draft.plan_id)) return viewTreinar();  // já existe outro treino em andamento: a pessoa escolhe
    if (draft) {
      const started = (draft.items || []).some((it) => it.done || (it.sets || []).some((x) => x.done));
      const sameDay = String(draft.date || "").slice(0, 10) === localISO().slice(0, 10);
      // rascunho de hoje, ou com séries já feitas que a pessoa confirma: continua; vazio ou esquecido de outro dia: começa do zero
      if (sameDay || (started && confirm(`Continuar o treino livre de ${fmtDate(draft.date)}? (Cancelar começa um novo, com a data de hoje)`))) { state.session = draft; return renderSession(); }
      store("gg_session", null);
    }
    const sch = await api("/api/schedule").catch((e) => { if (e && e.name === "AbortError") throw e; return null; });
    return startFree(sch);
  }
  if (planId === "agenda") {
    const draft = store("gg_session");
    if (draft && String(draft.schedule_id) === String(idx)) state.session = draft;
    else state.session = await api(`/api/schedule/${idx}/prefill`);
    return renderSession();
  }
  if (planId === undefined) {
    const draft = store("gg_session");
    const [plansAll, sch] = await Promise.all([api("/api/plans"), api("/api/schedule").catch(() => null)]);
    const plans = plansAll.filter((p) => p.active);
    const agenda = sch && sch.entries ? sch.entries.filter((e) => e.status === "planejado" && e.date >= sch.today) : [];
    const row = (href, title, sub, hl) => `<a class="pick ${hl ? "hl" : ""}" href="${href}"><span class="grow"><b>${esc(title)}</b><span class="small muted">${esc(sub)}</span></span><span class="chev" aria-hidden="true">›</span></a>`;
    view.innerHTML = `${draft ? `<section class="today-card"><div class="eyebrow">${draft.edit_id ? "Correção não salva" : "Treino em andamento"}</div><div class="tc-title">${esc(draft.day_name || "Treino livre")}</div>
      <p class="muted small" style="margin:0 0 12px">${fmtDate(draft.date)}${draft.edit_id ? " · o registro continua como estava até você salvar a correção" : ""}</p>
      <button class="btn block" id="resume">${draft.edit_id ? "Continuar a correção" : "Continuar"}</button><button class="act" id="discard" style="margin-top:8px">${draft.edit_id ? "Descartar a correção" : "Descartar"}</button></section>` : ""}
      ${agenda.length ? `<h2 class="sec-title">Da sua agenda</h2><div class="picks">${agenda.slice(0, 7).map((e) => row(`#treinar/agenda/${e.id}`, `${dayLabel(e.date, sch)} · ${shortName(e)}`,
        `${e.time ? `~${Math.round(e.time.total)} min · ` : ""}${dayKind(e)}`, e.date === sch.today)).join("")}</div>` : ""}
      ${plans.map((p) => `<h2 class="sec-title">${esc(p.name)}</h2><div class="picks">${p.days.map((d, i) => row(`#treinar/${p.id}/${i}`, d.name, `${(d.items || []).length || ""} ${(d.items || []).length ? "exercícios" : "da planilha"}`)).join("")}</div>`).join("")
        || (agenda.length ? "" : `<p class="muted">Sem planilhas ativas.</p>`)}
      <button class="btn block" id="gen-now" style="margin-top:14px">✨ Gerar um treino agora</button>
      <button class="btn ghost block" id="free" style="margin-top:8px">Treino livre</button>`;
    $("#gen-now").onclick = () => openAlternative(null, () => viewTreinar());
    const r = $("#resume"); if (r) r.onclick = () => { state.session = draft; renderSession(); };
    const d = $("#discard"); if (d) d.onclick = () => { store("gg_session", null); viewTreinar(); };
    $("#free").onclick = () => startFree(sch);
    return;
  }
  const draft = store("gg_session");
  if (draft && String(draft.plan_id) === String(planId) && draft.day_name && draft._idx === +idx) state.session = draft;
  else { state.session = await api(`/api/sessions/prefill/${planId}/${idx}`); state.session._idx = +idx; }
  renderSession();
}

// ---------------------------------------------------------------- nota de esforço, dados que ajustam o plano e calor
const EFFORT_TXT = ["Nada", "Muito leve", "Leve", "Moderado", "Um pouco puxado", "Puxado", "Puxado+", "Muito puxado", "Muito puxado+", "Quase máximo", "Máximo"];
function askEffort(id) {
  if (!$("#modal").hidden) return;  // outra janela aberta (ex.: pergunta da agenda): não empilha
  const body = openModal(`<h2>Quão puxado foi o treino?</h2><p class="small muted">De 0 a 10, o treino inteiro. Com essa nota o app mede a carga da semana e percebe quando você precisa de uma semana mais leve.</p>
    <div class="effort-grid">${EFFORT_TXT.map((t, k) => `<button class="chip eff" data-eff="${k}"><b>${k}</b><span>${t}</span></button>`).join("")}</div>
    <button class="act" id="eff-skip" style="margin-top:10px">Agora não</button>`);
  body.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    if (t.id === "eff-skip") return closeModal();
    if (t.dataset.eff !== undefined) {
      t.disabled = true;
      try { await api(`/api/sessions/${id}`, { method: "PUT", body: { srpe: +t.dataset.eff } }); closeModal(); toast("Anotado ✔"); }
      catch (err) { t.disabled = false; toast(err.message, 5000); }
    }
  };
}
async function hojeExtras(sch) {
  const box = $("#hx"); if (!box) return;
  const outdoor = sch.entries.some((e) => e.date === sch.today && e.status === "planejado" && (e.items || []).some((it) => it.endurance
    && ["corrida", "ciclismo", "caminhada", "trilha"].includes(it.endurance.sport) && !["esteira", "indoor", "rolo", "piscina"].includes(it.endurance.env)));
  const [ad, wx] = await Promise.all([api("/api/adaptive").catch(() => null), outdoor ? api("/api/weather/today").catch(() => null) : Promise.resolve(null)]);
  let html = "";
  const w = wx && wx.advice;
  if (w && w.show) {
    const at = w.at || w.worst;
    html += `<section class="card wbox wx-${esc(w.level)}"><div class="eyebrow">🌡️ Calor hoje${w.city ? ` · ${esc(w.city)}` : ""}</div>
      <b>${w.at ? `No seu horário (${w.at.hour}h)` : `No pico (${w.worst.hour}h)`}: ${at.temp.toFixed(0)}°C, umidade ${at.rh}% · índice de calor ${at.wbgt.toFixed(0)}</b>
      <p class="small" style="margin:6px 0 0">${esc(w.text)} Melhor horário hoje: <b>${w.best.hour}h</b> (${w.best.temp.toFixed(0)}°C).</p></section>`;
  }
  for (const p of (ad && ad.items) || []) {
    html += `<section class="card wbox ad-card"><div class="eyebrow">📈 Pelos seus dados</div><b>${esc(p.title)}</b>
      <ul class="plain small" style="margin:6px 0 8px">${(p.why || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      <div class="row gap wrap"><button class="btn sm" data-adapply="${esc(p.kind)}">${esc(p.action)}</button><button class="act" data-addismiss="${esc(p.kind)}">Agora não</button></div></section>`;
  }
  box.innerHTML = html;
  box.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    try {
      if (t.dataset.adapply) {
        t.disabled = true;
        const r = await api("/api/adaptive/apply", { method: "POST", body: { kind: t.dataset.adapply } });
        toast(t.dataset.adapply === "deload" ? `Semana leve aplicada${r.changed.length ? `: ${r.changed.join(", ")}` : ""}. Dá para desfazer em cada dia.` : `Feito: ${r.changed.join("; ") || "sem mudança"}. Vale a partir da próxima semana.`, 6000);
        return viewHoje();
      }
      if (t.dataset.addismiss) { await api("/api/adaptive/dismiss", { method: "POST", body: { kind: t.dataset.addismiss } }); t.closest(".card").remove(); }
    } catch (err) { t.disabled = false; toast(err.message, 5000); }
  };
}

// ---------------------------------------------------------------- personalização detalhada (saúde, fase, corpo, corrida, rotina)
function persSummary(p) {
  const a = p.personal || {};
  const n = Object.entries(a).filter(([k, v]) => !k.startsWith("_") && (Array.isArray(v) ? v.length : v !== null && v !== "" && v !== undefined)).length;
  return n ? `${n} ${n === 1 ? "resposta" : "respostas"} · ajusta o treino com base em pesquisas` : "saúde, remédios, corpo, corrida e rotina (opcional)";
}
function persForm(p) {
  const box = $("#pers-box"); if (!box) return;
  const Q = (state.meta && state.meta.personal_questions) || [];
  const a = JSON.parse(JSON.stringify(p.personal || {}));
  const ctx = () => ({ sex: [p.sex || null], running: [!!((((p.endurance || {}).sports || {}).corrida) || {}).enabled] });
  const shown = (cond) => !cond || Object.entries(cond).every(([k, vals]) => {
    if (vals === "any") return Array.isArray(a[k]) && a[k].length > 0;
    const c = ctx(); const v = k in c ? c[k][0] : (a[k] ?? null);
    return vals.includes(v);
  });
  const exName = (id) => (a._names || {})[id] || id.replace(/_/g, " ");
  const field = (q) => {
    if (!shown(q.show_if)) return "";
    const v = a[q.id];
    if (q.type === "multi") return `<div class="pq"><label>${esc(q.label)}</label><div class="chips">${q.options.map(([k, t]) => `<button type="button" class="chip pc ${(v || []).includes(k) ? "on" : ""}" data-pm="${q.id}" data-pv="${k}" aria-pressed="${(v || []).includes(k)}">${esc(t)}</button>`).join("")}</div></div>`;
    if (q.type === "single") return `<div class="pq"><label>${esc(q.label)}</label><div class="chips">${q.options.map(([k, t]) => `<button type="button" class="chip pc ${v === k ? "on" : ""}" data-ps="${q.id}" data-pv="${k}" aria-pressed="${v === k}">${esc(t)}</button>`).join("")}</div></div>`;
    if (q.type === "number") return `<div class="pq field"><label>${esc(q.label)}</label><input type="number" inputmode="numeric" data-pn="${q.id}" value="${esc(v ?? "")}" min="${q.min ?? 0}" max="${q.max ?? 999}"></div>`;
    if (q.type === "text") return `<div class="pq field"><label>${esc(q.label)}</label><input data-ptx="${q.id}" value="${esc(v ?? "")}" placeholder="${esc(q.placeholder || "")}" maxlength="60"></div>`;
    if (q.type === "exercises") return `<div class="pq"><label>${esc(q.label)}</label><div class="chips">${(v || []).map((id) => `<button type="button" class="chip pc on" data-pxdel="${esc(id)}">${esc(exName(id))} ✕</button>`).join("")}<button type="button" class="chip" data-pxadd="${q.id}">+ exercício</button></div></div>`;
    return "";
  };
  const render = () => {
    box.innerHTML = `<p class="small muted">Tudo opcional. Cada resposta muda o treino com base em diretrizes e estudos (ACSM, NSCA, ADA, COI, BJSM…), e o “Por que este treino” mostra o que mudou. Não substitui médico ou fisioterapeuta.</p>
      ${Q.filter((s) => shown(s.show_if)).map((s) => `<h3 style="margin-top:14px">${esc(s.section)}</h3>${s.intro ? `<p class="small muted">${esc(s.intro)}</p>` : ""}${s.items.map(field).join("")}`).join("")}
      <button class="btn block" id="pers-save" type="button" style="margin-top:14px">Salvar personalização</button>
      <button class="btn ghost block" id="pers-regen" type="button" style="margin-top:8px">🔄 Gerar meu treino com estas respostas</button>`;
  };
  render();
  box.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const d = t.dataset;
    if (d.pm) { const cur = new Set(a[d.pm] || []); if (cur.has(d.pv)) cur.delete(d.pv); else cur.add(d.pv); a[d.pm] = [...cur]; return render(); }
    if (d.ps) { a[d.ps] = a[d.ps] === d.pv ? null : d.pv; return render(); }
    if (d.pxdel) { a.dislikes = (a.dislikes || []).filter((x) => x !== d.pxdel); return render(); }
    if (d.pxadd) return pickExercise(null, (ex) => { a.dislikes = [...new Set([...(a.dislikes || []), ex.id])]; a._names = { ...(a._names || {}), [ex.id]: ex.name }; closeModal(); render(); });
    if (t.id === "pers-save" || t.id === "pers-regen") {
      $$("[data-pn]", box).forEach((i) => { a[i.dataset.pn] = i.value === "" ? null : +i.value; });
      $$("[data-ptx]", box).forEach((i) => { a[i.dataset.ptx] = i.value.trim() || null; });
      t.disabled = true;
      try {
        state.profile = await api("/api/profile", { method: "PUT", body: { personal: a } });
        p.personal = state.profile.personal;
        const sum = $("#sec-pers summary .small.muted"); if (sum) sum.textContent = persSummary(state.profile);
        if (t.id === "pers-regen") return openGenerator();
        toast("Personalização salva. Toque em “Gerar meu treino com estas respostas” para aplicar.", 5000);
      } catch (err) { toast(err.message, 5000); } finally { t.disabled = false; }
    }
  };
  box.oninput = (e) => { const i = e.target; if (i.dataset.pn) a[i.dataset.pn] = i.value === "" ? null : +i.value; if (i.dataset.ptx) a[i.dataset.ptx] = i.value.trim() || null; };
}

// ---------------------------------------------------------------- treino do relógio: conferir exercício por exercício
function watchInboxHtml(w) {
  const list = (w && w.items) || []; if (!list.length) return "";
  return list.map((x) => {
    const when = fmtDate(x.date) + " " + String(x.date).slice(11, 16);
    const bits = [x.guesses ? `${x.guesses} ${x.guesses === 1 ? "exercício com palpite" : "exercícios com palpite"} do relógio` : "",
      x.unknown_sets ? `${x.unknown_sets} ${x.unknown_sets === 1 ? "série sem nome" : "séries sem nome"}` : "",
      x.no_load ? `${x.no_load} ${x.no_load === 1 ? "série sem carga" : "séries sem carga"}` : ""].filter(Boolean);
    const msg = x.time_only ? "O relógio gravou só o tempo, a FC e as calorias. Anote os exercícios para contar no músculo, na semana e na evolução de cargas."
      : bits.length ? `Para conferir: ${bits.join(", ")}.` : "Confira nomes, repetições e cargas.";
    return `<section class="card wbox"><div class="eyebrow">⌚ Chegou do ${esc(x.source)}</div>
      <b>${esc(x.title)}</b> <span class="small muted">· ${esc(when)}${x.duration_min ? ` · ${x.duration_min} min` : ""}${x.sets ? ` · ${x.sets} séries` : ""}</span>
      <p class="small" style="margin:6px 0 10px">${esc(msg)}</p>
      <div class="row gap wrap"><button class="btn sm" data-wrev="${x.id}">${x.time_only ? "Anotar os exercícios" : "Conferir exercício por exercício"}</button>
        <button class="act" data-wok="${x.id}">${x.time_only ? "Deixar só o tempo" : "Está tudo certo"}</button></div></section>`;
  }).join("");
}
async function openWatchCheck(id) {
  const cur = store("gg_session");
  if (cur && (cur.items || []).length && cur.edit_id !== id && !confirm(`Você tem um treino aberto (“${cur.day_name || "Treino"}”). Trocar pela conferência do relógio? (o treino aberto é descartado)`)) return;
  const d = await api(`/api/sessions/${id}/edit`);
  state.session = d; store("gg_session", d);
  if (location.hash !== "#treinar/editar") location.hash = "#treinar/editar"; else renderSession();
}
function watchHeadHtml(s) {
  const w = s.watch || {};
  const meas = [w.duration_min ? `${w.duration_min} min` : "", w.avg_hr ? `FC média ${Math.round(w.avg_hr)}` : "", w.kcal ? `${Math.round(w.kcal)} kcal` : ""].filter(Boolean).join(" · ");
  if (w.time_only && !s.items.length) return `<div class="rec small" style="margin-top:10px"><b>⌚ O ${esc(w.source || "relógio")} gravou ${meas || "o treino"}, sem os exercícios.</b>
    Anote o que você fez: o tempo, a FC e as calorias do relógio continuam valendo.
    <div class="row gap wrap" style="margin-top:8px">${s.planned && s.planned.items.length && !s.planned_used ? `<button class="btn sm" id="w-plan">Preencher com o planejado (${esc(s.planned.name || "treino do dia")})</button>` : ""}</div></div>`;
  const pick = s.items.filter((it) => it.need_pick).length, guess = s.items.filter((it) => it.watch_guess && !it.confirm_guess && it.exercise_id === it.orig_id).length;
  return `<div class="rec small" style="margin-top:10px"><b>⌚ Treino do ${esc(w.source || "relógio")}${meas ? ` · ${meas}` : ""}</b><br>
    Confira cada exercício: o nome, as repetições e a carga. Toque em 🔁 para trocar um exercício e em 🗑 para tirar o que não fez.
    ${pick ? `<br><b>${pick} ${pick === 1 ? "bloco precisa" : "blocos precisam"} do nome do exercício.</b>` : ""}${guess ? `<br>${guess} ${guess === 1 ? "exercício é palpite" : "exercícios são palpites"} do relógio.` : ""}
    <br><span class="muted">O que você trocar o app aprende: as próximas séries desse tipo já chegam com o nome certo.</span></div>`;
}
function swapItem(it, ex) {
  it.exercise_id = ex.id; it.name = ex.name; it.need_pick = false; it.suggest = null; it.confirm_guess = false;
  if (ex.images) it.images = ex.images; if (ex.timed !== undefined) it.timed = ex.timed; if (ex.limitation) it.limitation = ex.limitation;
  it.pm = ex.primaryMuscles || it.pm || []; it.sm = ex.secondaryMuscles || it.sm || []; it.last = null; it.suggestion = null;
}

// ---------------------------------------------------------------- outra opção para hoje / gerar treino agora
function altResultHtml(p, entryId) {
  const ch = (c) => c.type === "troca" ? `<b>${esc(c.from_name)}</b> → <b>${esc(c.to_name)}</b>: ${esc(c.why)}`
    : c.type === "sai" ? `Sai <b>${esc(c.from_name)}</b>: ${esc(c.why)}` : c.type === "entra" ? `Entra <b>${esc(c.to_name)}</b>: ${esc(c.why)}`
    : `<b>${esc(c.from_name)}</b>: ${esc(c.why)}`;
  const mins = p.minutes || {};
  return `<h2>${esc(p.label)}</h2>
    <ul class="alt-why">${(p.why || []).map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
    ${mins.after ? `<p class="small muted">⏱ ${entryId !== null && mins.before ? `~${mins.before} → ` : "~"}${mins.after} min${p.sets ? ` · ${entryId !== null && p.sets.before ? `${p.sets.before} → ` : ""}${p.sets.after} séries` : ""}</p>` : ""}
    ${(p.warnings || []).map((w) => `<p class="rec atencao small">${esc(w)}</p>`).join("")}
    ${(p.preview || []).length ? `<h3>Como fica</h3><ol class="alt-list">${p.preview.map((x) => `<li><b>${esc(x.name)}</b> <span class="small muted">${x.sets ? `${x.sets} × ${x.reps || (x.duration_s ? x.duration_s + "s" : "")}` : ""}${x.superset ? ` · bi-set ${esc(x.superset)}` : ""}</span>${x.notes ? `<div class="small muted">${esc(x.notes)}</div>` : ""}</li>`).join("")}</ol>` : ""}
    ${(p.changes || []).length ? `<details class="small sub"><summary>O que mudou · ${p.changes.length}</summary><ul class="plain alt-ch">${p.changes.map((c) => `<li>${ch(c)}</li>`).join("")}</ul></details>` : ""}
    ${p.base ? `<details class="small sub"><summary>Por que funciona</summary><p>${esc(p.base)}</p></details>` : ""}`;
}
async function openAlternative(entryId, again, preset) {
  let o; try { o = await api("/api/alternative/options"); } catch (e) { return toast(e.message, 5000); }
  const st = { reason: null, opts: {}, seed: 0 };
  const body = openModal("");
  const title = entryId ? "Outra opção para hoje" : "Gerar um treino agora";
  const chips = (list, key, multi) => `<div class="chips">${list.map((x) => { const on = multi ? (st.opts[key] || []).includes(x.id) : st.opts[key] === x.id;
    return `<button class="chip ${on ? "on" : ""}" data-k="${key}" data-v="${esc(x.id)}" data-multi="${multi ? 1 : ""}" aria-pressed="${on}">${esc(x.label)}</button>`; }).join("")}</div>`;
  const step1 = () => {
    st.reason = null; st.opts = {}; st.seed = 0;
    body.innerHTML = `<h2>${title}</h2><p class="small muted">O que está acontecendo? O app troca só o necessário e explica cada mudança.</p>
      <div class="picks alt-reasons">${o.reasons.filter((r) => entryId || r.id !== "variar").map((r) => `<button class="pick" data-r="${r.id}"><span class="alt-ic" aria-hidden="true">${r.icon}</span>
        <span class="grow"><b>${esc(entryId || r.id !== "falta" ? r.label : "O que está faltando na semana")}</b><span class="small muted">${esc(r.hint)}</span></span><span class="chev" aria-hidden="true">›</span></button>`).join("")}</div>`;
  };
  const step2 = () => {
    const r = o.reasons.find((x) => x.id === st.reason);
    if (!r.asks) return preview();
    if (r.asks === "muscles" && !st.opts.muscles) st.opts.muscles = [...(o.sore_groups || [])];
    if (r.asks === "minutes" && !st.opts.minutes) st.opts.minutes = Math.max(15, Math.round(o.minutes * 0.6 / 5) * 5);
    body.innerHTML = `<h2>${r.icon} ${esc(r.label)}</h2>
      ${r.asks === "minutes" ? `<div class="field"><label for="alt-min">Quantos minutos você tem?</label><input id="alt-min" type="number" inputmode="numeric" min="10" max="180" value="${st.opts.minutes}"></div>
        <div class="chips">${[20, 30, 40, 45].map((m) => `<button class="chip" data-min="${m}">${m} min</button>`).join("")}</div>` : ""}
      ${r.asks === "region" ? `<p class="small muted">Onde está doendo?</p>${chips(o.regions, "region", false)}<p class="small muted" style="margin-top:8px">Dor forte, que piora, com inchaço ou formigamento: não treine a região e procure avaliação.</p>` : ""}
      ${r.asks === "muscles" ? `<p class="small muted">O que ainda está dolorido?${(o.sore_groups || []).length ? " (já marquei o que você treinou há pouco)" : ""}</p>${chips(o.muscle_groups, "muscles", true)}` : ""}
      ${r.asks === "kit" ? `<p class="small muted">O que você tem aí? (nada marcado = só o peso do corpo)</p>${chips(o.kits.filter((k) => k.id !== "corpo"), "kit", true)}` : ""}
      ${r.asks === "sick" ? `<p class="small muted">Como você está?</p>${chips([{ id: "pescoco", label: "Só nariz/garganta, sem febre" }, { id: "febre", label: "Febre, dor no corpo, peito ou estômago" }], "sick", false)}` : ""}
      <button class="btn block" id="alt-go" style="margin-top:14px">Ver a opção</button><button class="act" id="alt-back" style="margin-top:8px">‹ Outro motivo</button>`;
  };
  const preview = async () => {
    body.innerHTML = `<h2>${title}</h2><p class="muted">Montando…</p>`;
    try {
      const p = await api("/api/alternative/preview", { method: "POST", body: { entry_id: entryId, reason: st.reason, opts: st.opts, seed: st.seed } });
      body.innerHTML = `${altResultHtml(p, entryId)}
        <button class="btn block" id="alt-use" style="margin-top:12px">${entryId ? "Usar esta opção hoje" : "Começar este treino"}</button>
        <div class="row gap wrap" style="margin-top:8px"><button class="act" id="alt-more">↻ Gerar outra</button><button class="act" id="alt-back">‹ Outro motivo</button></div>
        ${entryId ? `<p class="small muted">O treino original fica guardado: no Hoje, “Voltar ao original” desfaz.</p>` : ""}`;
    } catch (e) {
      body.innerHTML = `<h2>${title}</h2><p class="rec atencao">${esc(e.message)}</p><button class="act" id="alt-back">‹ Outro motivo</button>`;
    }
  };
  const use = async (btn) => {
    btn.disabled = true;
    const payload = { entry_id: entryId, reason: st.reason, opts: st.opts, seed: st.seed };
    if (entryId) {
      const e = await api("/api/alternative/apply", { method: "POST", body: payload });
      closeModal(); toast(`Treino de hoje trocado${e.garmin_workout_id ? ". Reenvie ao relógio para atualizar" : ""}. Dá para voltar ao original.`, 5000);
      return again && again();
    }
    const cur = store("gg_session");
    if (cur && (cur.items || []).length && !confirm(`Você tem um treino em andamento (“${cur.day_name || "Treino"}”). Trocar pelo treino gerado?`)) { btn.disabled = false; return; }
    const d = await api("/api/alternative/draft", { method: "POST", body: payload });
    state.session = d; store("gg_session", d); closeModal();
    if (location.hash !== "#treinar/pronto") location.hash = "#treinar/pronto"; else renderSession();
  };
  if (preset) { st.reason = preset.reason; st.opts = { ...(preset.opts || {}) }; preview(); } else step1();
  body.onclick = async (ev) => {
    const t = ev.target.closest("button"); if (!t) return;
    try {
      if (t.dataset.r) { st.reason = t.dataset.r; return step2(); }
      if (t.id === "alt-back") return step1();
      if (t.dataset.min) { st.opts.minutes = +t.dataset.min; const i = $("#alt-min"); if (i) i.value = t.dataset.min; return; }
      if (t.dataset.k) {
        const k = t.dataset.k, v = t.dataset.v;
        if (t.dataset.multi) { const cur = st.opts[k] || []; st.opts[k] = cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]; }
        else st.opts[k] = st.opts[k] === v ? null : v;
        return step2();
      }
      if (t.id === "alt-go") {
        const r = o.reasons.find((x) => x.id === st.reason);
        if (r.asks === "minutes") { const m = +($("#alt-min") || {}).value; if (!(m >= 10)) return toast("Diga quantos minutos você tem"); st.opts.minutes = m; }
        if (r.asks === "region" && !st.opts.region) return toast("Toque onde está doendo");
        if (r.asks === "muscles" && !(st.opts.muscles || []).length) return toast("Toque no que está dolorido");
        if (r.asks === "sick" && !st.opts.sick) return toast("Toque em como você está");
        return preview();
      }
      if (t.id === "alt-more") { st.seed += 1; return preview(); }
      if (t.id === "alt-use") return await use(t);
    } catch (e) { t.disabled = false; toast(e.message, 5000); }
  };
}

// --------------------------------------------- sugestões: próximo exercício (treino livre) e extras
function sugPayload(s) {
  return s.items.filter((it) => fxStrength(it) && (it.phase || "principal") === "principal")
    .map((it) => ({ exercise_id: it.exercise_id, sets: Math.max(1, (it.sets || []).filter((x) => x.done).length || (it.sets || []).length) }));
}
function addSuggested(s, sug) {
  const it = { ...sug.item, added: true, fresh: true, suggested: true };
  if (s.edit_id || s.replace_id || s.pulled) (it.sets || []).forEach((x) => { x.done = true; });
  s.items.push(it); store("gg_session", s);
  toast(`${it.name} entrou no treino`);
}
async function openSuggest(s, after, extra) {
  const body = openModal(`<h2>${extra ? "Exercícios extras" : "Próximo exercício"}</h2><p class="muted">Pensando no que combina com o seu treino…</p>`);
  let seed = 0, added = false;
  const load = async () => {
    try {
      const r = await api("/api/suggest/next", { method: "POST", body: { items: sugPayload(s), n: 3, extra, seed, kit: s.kit || null } });
      body.innerHTML = `<h2>${extra ? "Exercícios extras" : "Próximo exercício"}</h2>
        ${r.message ? `<p class="rec ${r.suggestions.length ? "" : "atencao"} small">${esc(r.message)}</p>` : `<p class="small muted">Pelo que você já fez hoje${r.template ? ` (parece um treino ${esc(r.template)})` : ""}, pelo que falta na semana e pela recuperação de cada músculo.</p>`}
        <div class="sug-list">${r.suggestions.map((x, k) => `<div class="sug-row">${mediaHtml(x.exercise_id, "m")}<div class="grow"><b>${esc(x.name)}</b>
          <div class="small muted">${x.sets} × ${esc(String(x.reps || (x.duration_s ? x.duration_s + "s" : "")))} · ~${x.minutes} min</div><div class="small">${esc(x.why)}</div></div>
          <button class="btn sm" data-sugadd="${k}">Adicionar</button></div>`).join("")}</div>
        <div class="row gap wrap" style="margin-top:10px">${r.suggestions.length ? `<button class="act" id="sug-more">↻ Outras sugestões</button>` : ""}<button class="act" id="sug-close">Fechar</button></div>`;
      body._sug = r.suggestions;
    } catch (e) { body.innerHTML = `<p class="rec atencao">${esc(e.message)}</p><button class="act" id="sug-close">Fechar</button>`; }
  };
  body.onclick = (ev) => {
    const t = ev.target.closest("button"); if (!t) return;
    if (t.id === "sug-close") return closeModal();
    if (t.id === "sug-more") { seed += 1; return load(); }
    if (t.dataset.sugadd !== undefined) {
      addSuggested(s, body._sug[+t.dataset.sugadd]); added = true; t.disabled = true; t.textContent = "✓ Adicionado";
      // extra: dá para pôr até 2; próximo exercício: um e pronto
      if (!extra || body.querySelectorAll("[data-sugadd]:disabled").length >= 2) closeModal();
    }
  };
  // ao fechar (por aqui ou pelo ✕), a tela do treino mostra o que entrou
  closeModal.hook = () => { if (added) after(); };
  await load();
}
// treino livre: logo abaixo dos exercícios, o próximo que faz sentido (busca só quando o treino muda)
async function loadNextSug(s) {
  const box = $("#next-sug"); if (!box) return;
  const sig = JSON.stringify(sugPayload(s)) + "|" + (s._sugSeed || 0);
  const show = (r) => {
    const b = $("#next-sug"); if (!b) return;
    const x = (r.suggestions || [])[0];
    if (!x) { b.innerHTML = `<span class="small muted">${esc(r.message || "Treino completo por hoje.")}</span>`; return; }
    b.innerHTML = `<div class="eyebrow">Próximo exercício sugerido</div><div class="sug-row">${mediaHtml(x.exercise_id, "m")}<div class="grow"><b>${esc(x.name)}</b>
      <div class="small muted">${x.sets} × ${esc(String(x.reps || (x.duration_s ? x.duration_s + "s" : "")))}</div><div class="small">${esc(x.why)}</div></div></div>
      <div class="row gap wrap" style="margin-top:8px"><button class="btn sm" id="ns-add">Adicionar</button><button class="act" id="ns-other">Outra sugestão</button></div>`;
    $("#ns-add").onclick = () => { addSuggested(s, x); renderSession(); };
    $("#ns-other").onclick = () => { s._sugSeed = (s._sugSeed || 0) + 1; loadNextSug(s); };
  };
  if (s._sugCache && s._sugCache.sig === sig) return show(s._sugCache.r);
  try { const r = await api("/api/suggest/next", { method: "POST", body: { items: sugPayload(s), n: 1, seed: s._sugSeed || 0, kit: s.kit || null } }); s._sugCache = { sig, r }; show(r); }
  catch (e) { const b = $("#next-sug"); if (b) b.remove(); }
}

// treino livre: começa na lista (para montar) e já sabe o que falta na semana
function startFree(sch, muscle) {
  const missing = (((sch || {}).goals || {}).muscles || []).filter((m) => m.state === "falta").map((m) => ({ key: m.key, pt: m.pt, missing: m.missing }));
  state.session = { date: localISO(), day_name: "Treino livre", items: [], view: "lista", missing };
  renderSession();
  if (muscle) pickExercise(null, (ex) => { state.session.items.push(newSessionItem(ex)); renderSession(); }, { muscle, missing });
}

function renderSession() {
  const s = state.session;
  if (s.view !== "lista") return renderFocus();
  fxLeave();
  // treino livre ainda sem nenhum exercício não vira "treino em andamento" (senão volta dias depois com a data antiga)
  if (s.items.length || s.schedule_id || s.plan_id) store("gg_session", s); else store("gg_session", null);
  const idxs = s.items.map((it, i) => [it, i]);
  const phaseOf = (it) => it.phase || "principal";
  const compact = (ph) => idxs.filter(([it]) => phaseOf(it) === ph);
  const compactCard = (ph, title) => compact(ph).length ? `<div class="card"><h3>${title}</h3><ul class="list">${compact(ph).map(([it, i]) => `<li class="row gap">
      <button class="check ${it.done ? "on" : ""}" data-pdone="${i}">✓</button>${mediaHtml(it.exercise_id, "m")}
      <span class="grow"><b data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${esc(it.name)}</b><br><span class="small muted">${it.kind === "cardio" ? `${it.duration_min} min Z${it.zone}` : it.plan && it.plan.reps ? `${it.plan.sets > 1 ? it.plan.sets + " × " : ""}${esc(it.plan.reps)} reps` : `${(it.plan && it.plan.duration_s) || 30}s${(it.plan && it.plan.sets) > 1 ? ` × ${it.plan.sets}` : ""}`}${it.plan && it.plan.notes ? " · " + esc(it.plan.notes) : ""}</span></span></li>`).join("")}</ul></div>` : "";
  const mainIt = s.items.filter((it) => phaseOf(it) === "principal");
  const doneN = mainIt.filter((it) => it.kind === "cardio" || it.endurance ? it.done : (it.sets || []).length && it.sets.every((x) => x.done)).length;
  const when = (() => { try { const d = new Date(s.date); return d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" }) + " · " + d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); } catch (_) { return ""; } })();
  view.innerHTML = `<section class="today-card sess-head"><div class="eyebrow">${s.watch_review ? "Conferindo treino do relógio" : s.edit_id ? "Corrigindo treino salvo" : s.replace_id ? "Refazendo treino salvo" : "Treino em andamento"}</div>
      <div class="tc-title">${esc(s.day_name || "Treino")}</div>
      <button class="btn block" id="to-focus" style="margin:2px 0 12px">▶ Treinar um exercício por vez</button>
      <div class="tc-chips"><span id="s-prog" class="${doneN && doneN === mainIt.length ? "ok" : ""}">${doneN} de ${mainIt.length} ${mainIt.length === 1 ? "exercício feito" : "exercícios feitos"}</span></div>
      <div class="meter" style="margin-top:10px"><span style="width:${mainIt.length ? Math.round(100 * doneN / mainIt.length) : 0}%"></span></div>
      <details class="small sub" style="margin-top:8px"><summary>Data e hora: ${esc(when)}</summary>
        <div class="field" style="margin:0 0 6px"><label for="s-date">Data e hora do treino</label><input type="datetime-local" id="s-date" value="${esc((s.date || "").slice(0, 16))}"></div></details>
      ${s.deload ? `<p class="small rec">Semana de deload: menos séries, carga −10%, RPE ~6.</p>` : ""}
      ${s.watch_review ? watchHeadHtml(s) : ""}
      ${(s.alerts || []).length ? `<details class="rec atencao small" style="margin-top:10px"><summary><b>⚠️ Para o seu perfil</b></summary><ul class="plain">${s.alerts.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></details>` : ""}
      ${s.generated && s.alt ? `<details class="rec small" style="margin-top:10px"><summary><b>✨ Gerado: ${esc(s.alt.label.toLowerCase())}</b></summary><ul class="plain">${(s.alt.why || []).map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
      ${s.edit_id && !s.watch_review ? `<div class="rec small" style="margin-top:10px"><b>Você está corrigindo um treino já registrado.</b> Mude repetições e cargas e toque em Salvar correção: o mesmo registro é atualizado (não cria outro).${(s.kept_items || []).length ? ` Ficam como estão: ${esc(s.kept_items.join(", "))}.` : ""}</div>` : partnerDraftHtml(s)}</section>
    ${missingChipsHtml(s)}
    ${compactCard("preparo", "Aquecimento — elevar, ativar, potencializar")}
    ${idxs.filter(([it]) => phaseOf(it) === "principal").map(([it, i]) => sessionItemHtml(it, i)).join("")}
    ${!s.schedule_id && !s.plan_id && !s.edit_id && !s.with_partner && s.items.some(fxStrength) ? `<div id="next-sug" class="card next-sug"><span class="small muted">Procurando o próximo exercício que combina…</span></div>` : ""}
    ${compactCard("pos", "Pós-treino — alongamento")}
    <div class="card"><div class="row gap wrap"><button class="btn sm ghost" id="s-add">+ Adicionar exercício</button><button class="btn sm ghost" id="s-sug">✨ Sugerir exercício</button></div>
      <div class="field" style="margin-top:10px"><label>Como foi o treino?</label><textarea id="s-notes" placeholder="Sensações, dores, energia…">${esc(s.notes || "")}</textarea></div>
      <button class="btn block" id="s-finish">${s.watch_review ? "Salvar conferência" : s.edit_id ? "Salvar correção" : s.replace_id ? "Salvar (substitui o registro anterior)" : "Finalizar e salvar"}</button>
      <button class="act" id="s-cancel" style="margin-top:10px">${s.edit_id ? "Cancelar correção" : "Descartar treino"}</button></div>`;
  bindSession();
}

// treino livre: atalhos para o que ainda falta na semana (o app não escolhe por você, só mostra)
function missingChipsHtml(s) {
  if (s.schedule_id || s.plan_id) return "";
  // desconta o que já foi marcado neste treino (músculo principal conta 1 série; auxiliar, meia)
  const got = {};
  s.items.forEach((it) => { const n = (it.sets || []).filter((x) => x.done).length; if (!n) return;
    (it.pm || []).forEach((m) => { got[m] = (got[m] || 0) + n; }); (it.sm || []).forEach((m) => { got[m] = (got[m] || 0) + n / 2; }); });
  const miss = (s.missing || []).map((m) => ({ ...m, missing: m.missing - (got[m.key] || 0) })).filter((m) => m.missing >= 1);
  if (!miss.length) return s.items.length ? "" : `<p class="muted" style="margin:4px 2px 12px">Toque em “+ Adicionar exercício” para começar. O que você fizer entra na conta da semana.</p>`;
  return `<section class="card miss-card"><b>O que ainda falta nesta semana</b>
    <p class="small muted" style="margin:2px 0 8px">Séries que a agenda não vai cobrir. Toque num músculo para escolher o exercício:</p>
    <div class="chips">${miss.map((m) => `<button class="chip" data-miss="${esc(m.key)}">${esc(m.pt)} <b>${fmtSets(m.missing)}</b></button>`).join("")}</div></section>`;
}

function sessionItemHtml(it, i) {
  const plan = it.plan || {};
  const lastTxt = it.last ? (it.last.sets ? it.last.sets.map((x) => `${x.reps ?? "-"}×${x.load_kg ?? 0}`).join(" · ") : `${it.last.duration_min} min Z${it.last.zone}`) : "";
  const head = `<div class="row gap">${mediaHtml(it.exercise_id, "m")}
    <div class="grow"><b data-ex="${esc(it.exercise_id)}" style="cursor:pointer">${esc(it.name)}</b> ${limBadge(it.limitation)}
    <div class="small muted">${it.kind === "cardio" ? `${plan.duration_min || ""} min · Z${plan.zone || ""}` : `${plan.sets || plan.reps || plan.duration_s ? `${plan.sets || ""}×${plan.reps || (plan.duration_s ? plan.duration_s + "s" : "")} ${plan.load_kg ? "· " + plan.load_kg + " kg" : ""} · ` : ""}descanso ${plan.rest_s || 90}s${plan.rir && !plan.deload ? ` · <span title="${RIR_TIP}">sobrar ${esc(plan.rir)} reps</span>` : ""}`}
    ${(it.warmup || []).length ? `<br>Aquecimento: ${esc(it.warmup.map((w) => `${w.reps}×${w.load_kg ? w.load_kg + " kg" : "~" + w.pct + "%"}`).join(" · "))}` : ""}
    ${lastTxt ? `<br>Última: ${esc(lastTxt)}` : ""}${it.suggestion && it.suggestion.note ? `<br><b>${esc(it.suggestion.note)}</b>` : ""}${plan.notes ? `<br>${esc(plan.notes)}` : ""}
    ${it.from_partner && (it.sets || []).some((x) => x.partner_load != null) ? `<br>Carga de ${esc((state.session || {}).partner_name || "parceiro")}: ${esc(it.sets.map((x) => x.partner_load ?? "—").join(" · "))} kg (só referência)` : ""}</div></div></div>`;
  if (it.endurance) {
    const en = it.endurance; const meters = ["natacao", "remo"].includes(en.sport);
    return `<div class="card"><div><b>${SPORT_ICON[en.sport] || ""} ${esc(en.title)}</b> <span class="small muted">${esc(endSummary(en))}</span>
      ${en.note ? `<div class="small">${esc(en.note)}</div>` : ""}${endStepsHtml(en, true)}</div>
      <div class="grid2" style="margin-top:8px">
      <div><label>Distância (${meters ? "m" : "km"})</label><input inputmode="decimal" data-c="dist_in" data-i="${i}" value="${esc(it.dist_in ?? "")}" placeholder="${meters ? (en.dist_m || "") : (en.dist_m ? (en.dist_m / 1000).toFixed(1) : "")}"></div>
      <div><label>Tempo (h:mm:ss ou mm:ss)</label><input data-t="time_str" data-i="${i}" value="${esc(it.time_str ?? "")}" placeholder="${fmtS(en.dur_s)}"></div>
      <div><label>FC média</label><input type="number" data-c="avg_hr" data-i="${i}" value="${esc(it.avg_hr ?? "")}"></div>
      ${en.sport === "ciclismo" ? `<div><label>Potência média (W)</label><input type="number" data-c="avg_power" data-i="${i}" value="${esc(it.avg_power ?? "")}"></div>` : ""}
      <div><label>Feito</label><button class="check ${it.done ? "on" : ""}" data-cdone="${i}">✓</button></div></div>
      <p class="small muted" id="pace-${i}">${esc(paceTxt(it))}</p></div>`;
  }
  if (it.kind === "cardio") {
    return `<div class="card">${head}<div class="grid2" style="margin-top:8px">
      <div><label>Minutos</label><input type="number" data-c="duration_min" data-i="${i}" value="${esc(it.duration_min ?? "")}"></div>
      <div><label>Zona</label><select data-c="zone" data-i="${i}">${zoneOpts(it.zone)}</select></div>
      <div><label>FC média</label><input type="number" data-c="avg_hr" data-i="${i}" value="${esc(it.avg_hr ?? "")}"></div>
      <div><label>Feito</label><button class="check ${it.done ? "on" : ""}" data-cdone="${i}">✓</button></div></div>
      <button class="act drop-ex" data-dropitem="${i}" style="margin-top:8px" aria-label="Tirar ${esc(it.name)} do treino">🗑 Tirar do treino</button></div>`;
  }
  const timed = it.timed && it.sets.some((x) => x.duration_s);
  const s0 = state.session || {};
  const pickBox = it.need_pick ? `<div class="rec atencao small wpick"><b>⌚ O relógio não disse qual exercício foi.</b> Pelas séries abaixo, qual foi?
      <div class="row gap wrap" style="margin-top:6px">${it.suggest ? `<button class="btn sm" data-pickto="${i}" data-pid="${esc(it.suggest.id)}" data-pname="${esc(it.suggest.name)}">Foi ${esc(it.suggest.name)}</button>` : ""}
      <button class="btn sm ghost" data-swap="${i}">${it.suggest ? "Outro" : "Escolher o exercício"}</button></div>
      ${it.sets.length > 1 ? `<div class="muted" style="margin-top:6px">Tem dois exercícios aqui? Toque em ✂ na série em que o outro começou.</div>` : ""}</div>`
    : it.watch_guess && !it.confirm_guess && it.exercise_id === it.orig_id ? `<div class="rec small wguess">⌚ O relógio só disse “${esc(it.watch_guess)}”; o app chutou este exercício.
      <div class="row gap wrap" style="margin-top:6px"><button class="btn sm" data-gok="${i}">✓ Está certo</button><button class="btn sm ghost" data-swap="${i}">Trocar</button></div></div>` : "";
  return `<div class="card${it.need_pick ? " need-pick" : ""}">${head}${pickBox}
    <table class="set-table" style="margin-top:8px"><thead><tr><th>#</th><th>${timed ? "Tempo s" : "Reps"}</th><th>kg</th><th>RPE</th><th></th></tr></thead><tbody>
    ${it.sets.map((x, j) => `<tr class="${x.done ? "done" : ""}"><td>${j + 1}${it.need_pick && j > 0 ? ` <button class="mini" data-split="${i}:${j}" aria-label="Outro exercício começa na série ${j + 1}">✂</button>` : ""}</td>
      <td><input class="num" inputmode="numeric" data-s="${timed ? "duration_s" : "reps"}" data-i="${i}" data-j="${j}" value="${esc(timed ? x.duration_s ?? "" : x.reps ?? "")}"></td>
      <td><input class="num" inputmode="decimal" data-s="load_kg" data-i="${i}" data-j="${j}" value="${esc(x.load_kg ?? "")}"${x.partner_load != null ? ` placeholder="sua" aria-label="Sua carga (a do parceiro foi ${esc(x.partner_load)} kg)"` : ""}></td>
      <td><select class="num" data-s="rpe" data-i="${i}" data-j="${j}"><option value=""></option>${[6, 7, 8, 9, 10].map((r) => `<option ${+x.rpe === r ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td><button class="check ${x.done ? "on" : ""}" data-done="${i}:${j}" aria-label="Série feita">✓</button></td></tr>`).join("")}
    </tbody></table>
    <div class="row gap wrap" style="margin-top:6px"><button class="btn sm ghost" data-addset="${i}">+ série</button><button class="btn sm ghost" data-rmset="${i}">− série</button>
      ${s0.edit_id && !it.need_pick ? `<button class="btn sm ghost" data-swap="${i}">🔁 Trocar exercício</button>` : ""}
      <button class="act drop-ex" data-dropitem="${i}" aria-label="Tirar ${esc(it.name)} do treino">🗑 Tirar do treino</button></div></div>`;
}

function sessionProgress() {
  const s = state.session; const el = $("#s-prog"); if (!s || !el) return;
  const main = s.items.filter((it) => (it.phase || "principal") === "principal");
  const n = main.filter((it) => it.kind === "cardio" || it.endurance ? it.done : (it.sets || []).length && it.sets.every((x) => x.done)).length;
  el.textContent = `${n} de ${main.length} ${main.length === 1 ? "exercício feito" : "exercícios feitos"}`; el.classList.toggle("ok", n && n === main.length);
  const bar = el.closest(".sess-head").querySelector(".meter span"); if (bar) bar.style.width = `${main.length ? Math.round(100 * n / main.length) : 0}%`;
}
// campos de número do treino (carga, repetições, tempo): tocar seleciona o número, e digitar substitui
// (com o cursor no meio do "12", digitar 15 dava "1152")
function selectSoon(el) {
  const sel = () => { try { if (document.activeElement === el) el.select(); } catch (_) { /* */ } };
  setTimeout(sel, 0); setTimeout(sel, 120);  // depois do toque terminar (ele reposiciona o cursor)
}
document.addEventListener("focusin", (e) => {
  const el = e.target;
  if (!(el instanceof HTMLInputElement) || !el.matches("input[data-s], input[data-fv], input[data-lv], input[data-c]") || !el.value) return;
  selectSoon(el);
  el.addEventListener("mouseup", (ev) => ev.preventDefault(), { once: true });
});
function bindSession() {
  const s = state.session;
  const save = () => { store("gg_session", s); sessionProgress(); };
  view.oninput = (e) => {
    const el = e.target;
    if (el.id === "s-date") { s.date = el.value; s.date_touched = true; }
    if (el.id === "s-notes") s.notes = el.value;
    if (el.dataset.s) {
      const v = el.value === "" ? null : parseFloat(el.value.replace(",", ".")); const it = s.items[+el.dataset.i]; const j = +el.dataset.j;
      const old = it.sets[j][el.dataset.s]; it.sets[j][el.dataset.s] = Number.isFinite(v) ? v : null;
      if (el.dataset.s === "load_kg" && e.type === "change") {  // ao sair do campo: leva a carga para as séries seguintes ainda vazias/iguais
        const from = el.dataset.was === undefined ? old : (el.dataset.was === "" ? null : +el.dataset.was);
        fxCarry(it, j, "load_kg", from, it.sets[j].load_kg);
        // a tela é atualizada logo depois da troca de campo: o campo em que a pessoa acabou de tocar fica como está
        // (ela vai digitar ali; escrever o número copiado nele fazia "12" + "15" virar "1152")
        setTimeout(() => {
          it.sets.forEach((y, k) => { if (k > j && (!y.done || it.from_partner || it.fresh)) { const inp = $(`input[data-s="load_kg"][data-i="${el.dataset.i}"][data-j="${k}"]`);
            if (!inp) return;
            inp.value = y.load_kg ?? "";
            // o campo em que a pessoa acabou de tocar também recebe a carga copiada, com o número selecionado:
            // se ela digitar, substitui (antes grudava: "12" + "15" virava "1152"); se não digitar, fica a copiada
            if (inp === document.activeElement) { inp.dataset.was = String(y.load_kg ?? ""); selectSoon(inp); } } });
          save();
        }, 0);
        delete el.dataset.was;
      } else if (el.dataset.s === "load_kg" && el.dataset.was === undefined) el.dataset.was = old ?? "";
    }
    if (el.dataset.c) { s.items[+el.dataset.i][el.dataset.c] = el.value === "" ? null : +String(el.value).replace(",", "."); }
    if (el.dataset.t) { s.items[+el.dataset.i][el.dataset.t] = el.value; }
    if (el.dataset.c || el.dataset.t) { const pe = $(`#pace-${el.dataset.i}`); if (pe) pe.textContent = paceTxt(s.items[+el.dataset.i]); }
    save();
  };
  view.onchange = view.oninput;
  view.onclick = (e) => {
    const t = e.target;
    if (t.id === "to-focus") { s.view = "foco"; s.fx = null; s.fsel = null; stopTimer(); return renderSession(); }
    if (t.dataset.ex) return showExercise(t.dataset.ex);
    if (t.dataset.done) {
      const [i, j] = t.dataset.done.split(":").map(Number); const set = s.items[i].sets[j]; set.done = !set.done; save();
      t.classList.toggle("on", set.done); t.closest("tr").classList.toggle("done", set.done);
      if (set.done) startTimer((s.items[i].plan || {}).rest_s || 90, t);
    }
    if (t.dataset.cdone) { const it = s.items[+t.dataset.cdone]; it.done = !it.done; t.classList.toggle("on", it.done); save(); }
    if (t.dataset.pdone) { const it = s.items[+t.dataset.pdone]; it.done = !it.done; t.classList.toggle("on", it.done); save(); }
    if (t.dataset.addset) { const it = s.items[+t.dataset.addset]; const last = it.sets[it.sets.length - 1] || {}; it.sets.push({ ...last, done: !!(s.edit_id || s.replace_id || s.pulled) }); renderSession(); }
    if (t.dataset.rmset) { const it = s.items[+t.dataset.rmset]; it.sets.pop(); renderSession(); }
    if (t.dataset.dropitem && removeSessionItem(+t.dataset.dropitem)) return renderSession();
    if (t.dataset.swap) { const it = s.items[+t.dataset.swap]; return pickExercise("strength", (ex) => { swapItem(it, ex); save(); renderSession(); }, { missing: s.missing }); }
    if (t.dataset.pickto) { swapItem(s.items[+t.dataset.pickto], { id: t.dataset.pid, name: t.dataset.pname }); save(); return renderSession(); }
    if (t.dataset.gok) { s.items[+t.dataset.gok].confirm_guess = true; save(); return renderSession(); }
    if (t.dataset.split) {
      const [i, j] = t.dataset.split.split(":").map(Number); const it = s.items[i];
      const rest = it.sets.splice(j);
      s.items.splice(i + 1, 0, { ...it, sets: rest, name: "Exercício seguinte: qual foi?", suggest: null, exercise_id: it.orig_id || it.exercise_id });
      save(); return renderSession();
    }
    if (t.id === "w-plan" && s.planned) {
      s.planned.items.forEach((p) => { const it = JSON.parse(JSON.stringify(p)); (it.sets || []).forEach((x) => { x.done = true; }); s.items.push(it); });
      s.planned_used = true; save(); toast("Treino planejado preenchido: ajuste repetições e cargas do que você fez, e tire o que não fez", 6000); return renderSession();
    }
    const mk = t.closest("[data-miss]");
    if (mk) addEx(mk.dataset.miss);
  };
  const addEx = (muscle) => pickExercise(null, (ex) => { const it = newSessionItem(ex);
    if (s.edit_id || s.replace_id || s.pulled) { (it.sets || []).forEach((x) => { x.done = true; }); if (it.kind === "cardio") it.done = true; it.fresh = true; }  // treino já feito: o que acrescenta é o que fez
    s.items.push(it); renderSession(); }, { muscle, missing: s.missing });
  $("#s-add").onclick = () => addEx();
  $("#s-sug").onclick = () => openSuggest(s, () => renderSession(), !!(s.schedule_id || s.plan_id));
  if ($("#next-sug")) loadNextSug(s);
  $("#s-cancel").onclick = () => { if (confirm(s.edit_id ? "Cancelar a correção? O registro fica como estava." : "Descartar este treino?")) { store("gg_session", null); state.session = null; location.hash = "#hoje"; } };
  $("#s-finish").onclick = finishSession;
  $$("[name=pmerge]").forEach((r) => { r.onchange = () => { s.merge_into = +r.value || null; s.merge_answered = true; store("gg_session", s); }; });
  const pdur = $("#pdur"); if (pdur) pdur.onchange = () => { const v = Math.round(+pdur.value); s.duration_min = v >= 5 && v <= 400 ? v : null; store("gg_session", s); };
}
async function finishSession() {
    const s = state.session; const save = () => store("gg_session", s);
    if ((s.merge_candidates || []).length && !s.merge_answered) {  // decide se o treino conta uma ou duas vezes: não dá para pular
      if (s.view !== "lista") { s.view = "lista"; save(); renderSession(); }  // no modo "um exercício por vez" a pergunta fica na lista
      const box = $("[name=pmerge]"); if (box) box.scrollIntoView({ block: "center", behavior: "smooth" });
      return toast("Antes de salvar, responda aqui: é a mesma atividade que o seu relógio gravou?", 6500);
    }
    if (s.watch_review && !s.unpicked_ack) {
      const n = s.items.filter((it) => it.need_pick && (it.sets || []).some((x) => x.done)).length;
      if (n) { s.unpicked_ack = true; save(); const el = $(".need-pick"); if (el) el.scrollIntoView({ block: "center" });
        return toast(`${n} ${n === 1 ? "bloco de séries ficou" : "blocos de séries ficaram"} sem o exercício: ${n === 1 ? "conta" : "contam"} só no total de séries da semana (não no músculo nem na evolução). Escolha o exercício, ou salve de novo para deixar assim.`, 9000); }
    }
    if ((s.edit_id || s.replace_id) && !s.unchecked_ack) {  // correção: linha preenchida sem ✓ seria descartada sem aviso
      const off = s.items.reduce((n, it) => n + (fxStrength(it) ? (it.sets || []).filter((x) => !x.done && (x.reps || x.duration_s)).length : 0), 0);
      if (off) { s.unchecked_ack = true; save(); return toast(`${off} ${off === 1 ? "série está" : "séries estão"} sem o ✓ e ${off === 1 ? "não vai" : "não vão"} para o registro. Toque no ✓ das que você fez, ou em salvar de novo para deixar de fora.`, 8000); }
    }
    if (s.pulled || s.edit_id) {  // série feita sem a MINHA carga: marca em laranja e só salva no segundo toque
      const isBlank = (it, x) => x.done && !x.duration_s && (x.load_kg == null || x.load_kg === "") && (it.from_partner ? x.partner_load != null : !it.timed);
      const blank = s.items.reduce((n, it) => n + (fxStrength(it) ? (it.sets || []).filter((x) => isBlank(it, x)).length : 0), 0);
      if (blank && !s.blank_ack) {
        if (s.view !== "lista") { s.view = "lista"; save(); renderSession(); }
        s.items.forEach((it, i) => (it.sets || []).forEach((x, j) => { if (fxStrength(it) && isBlank(it, x)) { const inp = $(`input[data-s="load_kg"][data-i="${i}"][data-j="${j}"]`); if (inp) inp.classList.add("need-load"); } }));
        const first = $("input.need-load"); if (first) { first.scrollIntoView({ block: "start" }); window.scrollBy(0, -110); }  // no alto: o aviso fica embaixo
        s.blank_ack = true; save();
        return toast(`${blank} ${blank === 1 ? "série está" : "séries estão"} sem a sua carga (em laranja)${s.pulled ? `; a de ${s.partner_name || "quem treinou com você"} é só referência e não é salva` : ""}. Preencha, ou toque em salvar de novo para salvar assim.`, 8000);
      }
    }
    const main = s.items.filter((it) => (it.phase || "principal") === "principal");
    let anyDone = main.some((it) => it.kind === "cardio" ? it.done : (it.sets || []).some((x) => x.done));
    if (!anyDone) {
      if (!confirm("Nenhuma série marcada como feita. Salvar todas as séries preenchidas como feitas?")) return;
      main.forEach((it) => { if (it.kind === "cardio") it.done = true; else (it.sets || []).forEach((x) => { if (x.reps || x.duration_s) x.done = true; }); });
    }
    const payload = {
      date: s.date, plan_id: s.plan_id || null, day_name: s.day_name, notes: s.notes, schedule_id: s.schedule_id || null,
      items: s.items.filter((it) => it.kind === "cardio" || (it.phase || "principal") === "principal").map((it) => it.endurance
        ? (it.done ? endPayload(it) : null)
        : it.kind === "cardio"
        ? (it.done ? { exercise_id: it.exercise_id, duration_min: it.duration_min, zone: it.zone, avg_hr: it.avg_hr } : null)
        : { exercise_id: it.exercise_id, ...(it.orig_id ? { orig_id: it.orig_id } : {}), ...(it.confirm_guess ? { confirm_guess: true } : {}), ...((it.plan && it.plan.deload) ? { deload: true } : {}), sets: it.sets.filter((x) => x.done).map(({ reps, load_kg, rpe, duration_s }) => ({ reps, load_kg, rpe, duration_s })) })
        .filter((it) => it && (it.duration_min || (it.sets && it.sets.length))),
    };
    if (!payload.items.length) return toast("Nada para salvar ainda: marque pelo menos uma série ou um cardio como feito.", 5000);
    if (s.edit_id || s.replace_id) return saveOver(s, payload);
    payload.client_id = s.client_id || (s.client_id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    if (s.with_partner) payload.with_partner = s.with_partner;
    if (s.date_touched) payload.time_set = true;  // a pessoa digitou a hora do treino: vale como horário de verdade ao comparar com o relógio
    if (s.allow_repeat) payload.allow_repeat = true;  // a pessoa disse que é outro treino
    if (s.merge_into) payload.merge_into = s.merge_into;
    else {
      if (s.pulled && s.duration_min) payload.duration_min = s.duration_min;  // sem relógio: o tempo do treino feito junto
      if ((s.merge_candidates || []).length) payload.no_merge = true;          // viu a atividade do relógio e disse "é outro treino"
    }
    if (!payload.duration_min && !s.pulled && !s.date_touched && !s.edit_id) {
      // treino feito agora, com o app aberto: vai o tempo de verdade (do início até salvar). Anotado depois, em poucos
      // minutos, ou deixado aberto por horas, não vale como tempo de treino
      const mins = Math.round((Date.now() - new Date(s.date).getTime()) / 60000);
      const nsets = payload.items.reduce((a, it) => a + (it.sets || []).length, 0);
      if (nsets && mins >= Math.max(8, nsets * 1.5) && mins <= 240) payload.duration_min = mins;
    }
    save();
    const btn = $("#s-finish"); btn.disabled = true;
    let saved;
    try { saved = await api("/api/sessions", { method: "POST", body: payload }); } catch (err) {
      btn.disabled = false;
      if (err.status === 409 && err.body) return chooseOnConflict(s, err.body);
      if (!err.offline) return toast(err.message, 5000);
      // academia sem sinal: guarda no celular e envia sozinho quando a conexão voltar
      const box = store("gg_outbox") || []; if (!box.some((x) => x.client_id === payload.client_id)) box.push(payload); store("gg_outbox", box);
      store("gg_session", null); state.session = null; stopTimer(); fxLeave();
      toast("Sem internet: o treino ficou guardado no celular e será enviado sozinho quando a conexão voltar.", 7000);
      location.hash = "#hoje"; return;
    }
    store("gg_session", null); state.session = null; stopTimer(); fxLeave();
    if (saved && saved.id) setTimeout(() => askEffort(saved.id), 700);
    const prs = (saved && saved.prs) || [];
    const prTxt = prs.length ? `🏆 Recorde em ${prs.map((p) => `${p.name} (${prText(p)})`).join(", ")}` : "";
    const cr = saved && saved.credit;
    // treino livre, treino com parceiro, ou treino que não fechou sozinho o que estava na agenda: mostra o que contou e o que falta
    if (cr && (!s.schedule_id || s.with_partner || cr.question || cr.kept_open || cr.extra)) return showSaved(saved, cr, prTxt);
    toast(prs.length ? `Treino salvo! ${prTxt}` : "Treino salvo! 🎉", prs.length ? 6000 : 2600);
    location.hash = "#hoje";
}

// ao salvar, o servidor avisou que esse treino já está registrado: a pessoa escolhe (nada se perde)
function chooseOnConflict(s, info) {
  const body = openModal(`<h2>Esse treino já está registrado</h2>
    <div class="reg-box small"><div class="reg-row"><span><b class="ok-txt">✓</b> ${info.existing ? regLine(info.existing) : ""}</span></div></div>
    <p class="small">Salvar de novo criaria um segundo registro do mesmo treino (contaria em dobro). O que você anotou agora continua aqui.</p>
    <div class="stack"><button class="btn block" data-cf="trocar">Usar o que anotei agora (substitui o registro)</button>
      <button class="btn ghost block" data-cf="ver">Ver o que já está salvo</button>
      <button class="btn ghost block" data-cf="outro">Foi outro treino: salvar separado</button>
      <button class="act" data-cf="nada">Voltar ao treino</button></div>`);
  body.onclick = (e) => { const b = e.target.closest("[data-cf]"); if (!b) return; closeModal(); const c = b.dataset.cf;
    if (c === "trocar") { s.replace_id = info.existing_id; store("gg_session", s); return finishSession(); }
    if (c === "outro") { s.allow_repeat = true; store("gg_session", s); return finishSession(); }
    if (c === "ver") return openMySession(info.existing_id); };
}
// correção de um treino salvo (ou "refazer a partir do treino do parceiro"): atualiza o MESMO registro
async function saveOver(s, payload) {
  const id = s.edit_id || s.replace_id;
  const btn = $("#s-finish"); if (btn) btn.disabled = true;
  let saved;
  try { saved = await api(`/api/sessions/${id}`, { method: "PUT", body: { ...payload, edit: true, ...(s.watch_review ? { watch_review: true } : {}), ...(s.replace_id && s.with_partner ? { with_partner: s.with_partner } : {}) } }); }
  catch (err) { if (btn) btn.disabled = false; return toast(err.message, 6000); }
  store("gg_session", null); state.session = null; stopTimer(); fxLeave();
  if (s.watch_review && saved && saved.id && saved.srpe == null) setTimeout(() => askEffort(saved.id), 700);
  view.innerHTML = `<section class="today-card saved-card"><div class="eyebrow">${s.watch_review ? "Treino do relógio conferido ✓" : s.edit_id ? "Correção salva ✓" : "Registro refeito ✓"}</div>
    <div class="tc-title">${esc(saved.day_name || "Treino")}</div>
    <p class="small">O mesmo registro foi atualizado: continua um treino só no histórico, no placar e na semana.</p>
    ${savedListHtml(saved.summary)}
    <a class="btn block" href="#hoje" style="margin-top:16px">Voltar para o Hoje</a></section>`;
  window.scrollTo(0, 0); view.onclick = view.oninput = view.onchange = null;
}
// o que ficou salvo: exercício por exercício, com as cargas (série sem carga aparece em laranja)
function savedListHtml(sm) {
  if (!sm || !(sm.items || []).length) return "";
  const r = sm.registered || {};
  const st = (x) => x.duration_s && !x.reps ? `${x.duration_s} s` : `${x.reps ?? "—"}×${x.load_kg != null && x.load_kg !== "" ? fmtKg(x.load_kg).replace(" kg", "") : `<span class="no-load">?</span>`}`;
  return `<h3 class="sv-h">O que ficou registrado${sm.partner_name ? ` <span class="pill ok">com ${esc(sm.partner_name)}</span>` : ""}</h3>
    <ul class="sv-list small">${sm.items.map((i) => i.kind === "forca" ? `<li><b>${esc(i.name)}</b> · ${(i.sets || []).map(st).join("  ")}${i.sets && i.sets.some((x) => x.load_kg != null) ? " kg" : ""}</li>` : `<li><b>${esc(i.name)}</b> · ${i.minutes} min</li>`).join("")}</ul>
    ${r.no_load ? `<p class="small rec atencao" style="margin-top:8px">${r.no_load} ${r.no_load === 1 ? "série ficou" : "séries ficaram"} sem a sua carga (“?”). Dá para completar em Progresso → Histórico → tocar no treino → Corrigir.</p>` : ""}`;
}

/* ---- depois de salvar: o que o treino fechou na agenda e o que ainda falta na semana ---- */
const pctTxt0 = (c) => (c == null ? "" : `${Math.round(c * 100)}%`);
function questionHtml(q) {
  return `<div class="rec atencao q-card"><b>Esse treino livre foi o ${esc(q.day_name)} (${esc(q.label)})?</b>
    <p class="small" style="margin:4px 0 8px">${q.coverage != null ? `Ele cobriu cerca de ${pctTxt0(q.coverage)} do que estava planejado. ` : ""}${q.kind === "futuro"
      ? "Se foi, marco esse treino como feito e ele sai do dia dele." : "Se foi, marco o treino como feito."} Se não, conta como treino extra e o planejado continua na agenda.</p>
    <div class="row gap wrap"><button class="btn sm" data-match="${q.session_id}:${q.entry_id}">Sim, foi esse</button>
      <button class="btn sm ghost" data-match="${q.session_id}:extra">Não, foi um extra</button></div></div>`;
}
function missListHtml(rows, limit = 6) {
  const more = rows.slice(limit);
  return `<ul class="miss-list">${rows.slice(0, limit).map((b) => `<li><span class="grow">${esc(b.pt)}<span class="small muted"> · ${fmtSets(b.projected)} de ${fmtSets(b.ref)} séries</span></span><b>faltam ${fmtSets(b.missing)}</b></li>`).join("")}</ul>
    ${more.length ? `<p class="small muted" style="margin:6px 0 0">E também: ${more.map((b) => `${esc(b.pt)} (${fmtSets(b.missing)})`).join(", ")}.</p>` : ""}`;
}
function balanceAfterHtml(rows) {
  if (!rows || !rows.length) return "";
  const miss = rows.filter((b) => b.state === "falta");
  return miss.length ? `<h3 class="sv-h">O que ainda falta nesta semana</h3><p class="small muted" style="margin:0 0 6px">Mesmo fazendo os treinos que restam na agenda:</p>${missListHtml(miss)}
      <p class="small muted" style="margin:8px 0 0">Séries por músculo na semana (quando o músculo só ajuda no exercício, conta meia série). Não é obrigatório repor: uma semana um pouco mais leve não muda o resultado.</p>`
    : `<p class="small ok-txt" style="margin:12px 0 0">✓ Com os treinos que ainda estão na agenda, a semana fecha no volume planejado.</p>`;
}
async function answerMatch(val) {
  const [sid, target] = String(val).split(":");
  return api(`/api/sessions/${sid}/match`, { method: "POST", body: target === "extra" ? { extra: true } : { schedule_id: +target } });
}
function showSaved(saved, cr, prTxt) {
  const closedTxt = (c) => `Fechou o treino <b>${esc(c.day_name)}</b> (${esc(c.label)})${c.coverage != null ? `: cobriu ${pctTxt0(c.coverage)} do planejado` : ""}.${c.was_skipped ? " Ele estava como pulado; o ajuste do pulo foi desfeito." : ""}`;
  const head = cr.closed ? closedTxt(cr.closed)
    : cr.extra ? `Contou como <b>treino extra</b>.${cr.kept_open ? ` O treino de ${cr.kept_open.date === localISO().slice(0, 10) ? "hoje" : esc(cr.kept_open.label)} (${esc(cr.kept_open.day_name)}) continua ${cr.kept_open.status === "perdido" ? "como perdido" : "na agenda"}.` : ""}`
    : cr.kept_open ? `Você fez ${pctTxt0(cr.kept_open.coverage)} do treino <b>${esc(cr.kept_open.day_name)}</b>: ele continua na agenda.` : "";
  const sm = saved.summary || {};
  view.innerHTML = `<section class="today-card saved-card"><div class="eyebrow">Treino salvo ✓</div>
    <div class="tc-title">${cr.work_sets ? `${cr.work_sets} ${cr.work_sets === 1 ? "série de trabalho" : "séries de trabalho"}` : "Treino registrado"}</div>
    ${sm.partner_name ? `<p><b class="ok-txt">✓ Registrado como treino com ${esc(sm.partner_name)}.</b> Ele aparece no cartão “Treino com ${esc(sm.partner_name)}” do Hoje e no placar. Não precisa puxar de novo.</p>` : ""}
    ${prTxt ? `<p>${esc(prTxt)}</p>` : ""}
    <div id="sv-credit">${head ? `<p>${head}</p>` : ""}${cr.question ? questionHtml({ ...cr.question, session_id: saved.id }) : ""}
      ${cr.kept_open && !cr.extra && !cr.question ? `<button class="btn ghost block" data-match="${saved.id}:${cr.kept_open.entry_id}">Marcar como feito mesmo assim</button>` : ""}</div>
    ${cr.added && cr.added.length ? `<h3 class="sv-h">O que entrou na conta da semana</h3><div class="tc-chips">${cr.added.map((x) => `<span>${esc(x.pt)} +${fmtSets(x.sets)}</span>`).join("")}</div>
      <p class="small muted" style="margin:6px 0 0">Séries de trabalho por músculo (aquecimento não conta; músculo auxiliar conta metade).</p>` : ""}
    <div id="sv-bal">${balanceAfterHtml(cr.balance)}</div>
    ${savedListHtml(sm)}
    <a class="btn block" href="#hoje" style="margin-top:16px">Ver minha semana</a></section>`;
  window.scrollTo(0, 0);
  view.oninput = view.onchange = null;
  view.onclick = async (e) => {
    const b = e.target.closest("[data-match]"); if (!b) return;
    b.disabled = true;
    try {
      const r = await answerMatch(b.dataset.match);
      $("#sv-credit").innerHTML = `<p>${r.closed ? closedTxt(r.closed) : "Contou como <b>treino extra</b>. O treino planejado continua na agenda."}</p>`;
      $("#sv-bal").innerHTML = balanceAfterHtml(r.balance);
    } catch (err) { b.disabled = false; toast(err.message, 5000); }
  };
}

/* ---- minha academia: foto e regulagem do aparelho, troca por equivalente ---- */
// reduz a foto no celular antes de enviar (lado maior 1280 px, JPEG ~0,8): rápido mesmo com 4G fraco
function shrinkPhoto(file, max = 1280, q = 0.8) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file); const img = new Image();
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", q));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Não deu para ler a foto")); };
    img.src = url;
  });
}
const myMachineChip = (mm, tappable = true) => mm ? `<${tappable ? "button" : "div"} class="mm-chip" ${tappable ? `data-mm="edit"` : ""}>${mm.photo_url ? `<img src="${esc(mm.photo_url)}" alt="">` : `<span class="mm-ph" aria-hidden="true">📷</span>`}
  <span class="grow"><b>Seu aparelho${mm.name ? `: ${esc(mm.name)}` : ""}</b>${mm.settings ? `<span>Regulagem: ${esc(mm.settings)}</span>` : `<span class="muted">Toque para anotar a regulagem</span>`}</span></${tappable ? "button" : "div"}>` : "";

// cadastrar/editar o aparelho da pessoa para um exercício (foto + nome + regulagem); st guarda o que já foi preenchido
function openMyMachine(st, onSaved) {
  st = { suggest: true, ...st };
  const isAdmin = !!(state.me && state.me.user && state.me.user.is_admin);
  const body = openModal(`<h2>${st.id ? "Seu aparelho" : "📷 Fotografar meu aparelho"}</h2>
    <p class="small muted">Para o exercício <b>${esc(st.exercise_name || "—")}</b> <button class="act" id="mg-ex">trocar exercício</button></p>
    <label class="photo-pick">${st.image || st.photo_url ? `<img id="mg-prev" src="${esc(st.image || st.photo_url)}" alt="">` : `<span class="pp-empty">📷<b>Tirar foto do aparelho</b><span class="small">ou escolher da galeria</span></span>`}
      <input type="file" id="mg-file" accept="image/*" capture="environment" ${st.id ? "disabled" : ""}></label>
    <div class="field"><label for="mg-name">Nome do aparelho</label><input id="mg-name" value="${esc(st.name || st.exercise_name || "")}" placeholder="ex.: Leg press 45° (Matrix)"></div>
    <div class="field"><label for="mg-set">Regulagem (o que você ajusta)</label><input id="mg-set" value="${esc(st.settings || "")}" placeholder="ex.: banco 4, encosto 2, pino da perna no 3"></div>
    ${st.id ? "" : `<label class="row gap mg-sug"><input type="checkbox" id="mg-sug" ${st.suggest ? "checked" : ""}> <span>Sugerir para o catálogo do HAX${isAdmin ? " (vai para a sua área do desenvolvedor)" : " (o desenvolvedor aprova)"}</span></label>`}
    <button class="btn block" id="mg-save" ${st.exercise_id ? "" : "disabled"}>Salvar</button>
    ${st.id ? `<button class="act" id="mg-del" style="margin-top:10px">Remover este aparelho</button>` : ""}
    <p class="small muted" style="margin-top:10px">Da próxima vez que esse exercício aparecer, o app mostra a foto e a regulagem.</p>`);
  const keep = () => { st.name = $("#mg-name", body).value; st.settings = $("#mg-set", body).value; const sg = $("#mg-sug", body); if (sg) st.suggest = sg.checked; };
  $("#mg-ex", body).onclick = () => { keep(); pickExercise(null, (ex) => { st.exercise_id = ex.id; st.exercise_name = ex.name; if (!st.name || st.name === st.prev_ex) st.name = ex.name; st.prev_ex = ex.name; openMyMachine(st, onSaved); }); };
  $("#mg-file", body).onchange = async (e) => {
    const f = e.target.files && e.target.files[0]; if (!f) return;
    keep();
    try { st.image = await shrinkPhoto(f); openMyMachine(st, onSaved); } catch (err) { toast(err.message); }
  };
  const del = $("#mg-del", body);
  if (del) del.onclick = async () => { if (!confirm("Remover este aparelho?")) return; await api(`/api/my-gym/machines/${st.id}`, { method: "DELETE" }); closeModal(); toast("Removido"); onSaved && onSaved(null); };
  $("#mg-save", body).onclick = async (e) => {
    keep(); e.target.disabled = true;
    try {
      const out = st.id
        ? await api(`/api/my-gym/machines/${st.id}`, { method: "PATCH", body: { name: st.name, settings: st.settings, exercise_id: st.exercise_id } })
        : await api("/api/my-gym/machines", { method: "POST", body: { image: st.image || null, exercise_id: st.exercise_id, name: st.name, settings: st.settings, suggest: !!(st.image && st.suggest) } });
      closeModal(); toast(st.id ? "Regulagem salva" : out.suggested ? "Aparelho salvo e enviado para o catálogo 👍" : "Aparelho salvo 👍");
      onSaved && onSaved(out);
    } catch (err) { e.target.disabled = false; toast(err.message, 5000); }
  };
}

// trocar o exercício no meio do treino por um que faz o mesmo movimento
async function openSwap(it, onPick) {
  const body = openModal(`<h2>Trocar ${esc(it.name)}</h2><p class="small muted">Aparelho ocupado ou sua academia não tem? Estes fazem o mesmo trabalho:</p><div id="sw-list" class="sk"><span class="sk-line"></span><span class="sk-line"></span></div>`);
  const s = state.session;
  let alts = [];
  try { alts = await api(`/api/exercises/${encodeURIComponent(it.exercise_id)}/alternatives`); } catch (e) { $("#sw-list", body).innerHTML = `<p class="pill bad">${esc(e.message)}</p>`; return; }
  $("#sw-list", body).className = "";
  $("#sw-list", body).innerHTML = `${alts.length ? `<ul class="list sw-list">${alts.map((x) => `<li><button class="sw-row" data-sw="${esc(x.id)}">${x.my_machine && x.my_machine.photo_url ? `<img class="sw-mine" src="${esc(x.my_machine.photo_url)}" alt="">` : mediaHtml(x.id, "s")}
      <span class="grow"><b>${esc(x.name)}</b><span class="small muted">${esc(x.equipment_pt || "")}${x.my_machine ? ` · <span class="ok-txt">seu aparelho${x.my_machine.settings ? ` (${esc(x.my_machine.settings)})` : ""}</span>` : ""}</span></span>${limBadge(x.limitation)}</button></li>`).join("")}</ul>` : `<p class="muted">Não achei equivalente no catálogo.</p>`}
    ${s && s.plan_id ? `<label class="row gap mg-sug"><input type="checkbox" id="sw-plan"> <span>Usar sempre: trocar na planilha e nos próximos treinos</span></label>` : ""}
    <button class="act" id="sw-photo" style="margin-top:12px">📷 Minha academia tem outro aparelho para isso</button>`;
  body.onclick = async (e) => {
    const b = e.target.closest("[data-sw]");
    if (b) { const also = !!($("#sw-plan", body) || {}).checked; closeModal(); return onPick(b.dataset.sw, also); }
    if (e.target.id === "sw-photo") openMyMachine({ exercise_id: it.exercise_id, exercise_name: it.name }, (mm) => { it.my_machine = mm; store("gg_session", state.session); renderSession(); });
  };
}

async function fxSwap(it, toId, alsoPlan) {
  const s = state.session; const i = s.items.indexOf(it); const p = it.plan || {};
  const keepKeys = ["sets", "reps", "duration_s", "rest_s", "role", "phase", "rpe_target", "rir", "rest_custom"];
  const done = (it.sets || []).filter((x) => x.done);
  const req = Object.fromEntries(keepKeys.filter((k) => p[k] != null).map((k) => [k, p[k]]));
  req.exercise_id = toId; req.sets = Math.max(1, (it.sets || []).length - done.length) || p.sets || 3;
  req.warmup_sets = done.length ? [] : (p.warmup_sets || []);  // mesma rampa de aquecimento, recalculada no novo exercício
  let fresh;
  try { fresh = await api("/api/sessions/item", { method: "POST", body: req }); } catch (e) { return toast(e.message, 5000); }
  fresh.swapped_from = it.name;
  if (done.length) { it.sets = done; s.items.splice(i + 1, 0, fresh); } else s.items[i] = fresh;
  s.fsel = null; s.restEnd = null; s.restOver = false; s.workEnd = null; s.prepEnd = null;
  s.fx = fxOrder(s).indexOf(done.length ? i + 1 : i);
  store("gg_session", s); renderSession();
  toast(`Trocado por ${fresh.name}`);
  if (alsoPlan && s.plan_id) {
    try { const r = await api(`/api/plans/${s.plan_id}/swap`, { method: "POST", body: { from: it.exercise_id, to: toId } }); toast(`Planilha atualizada (${r.changed} ${r.changed === 1 ? "lugar" : "lugares"})`); }
    catch (e) { toast(e.message, 5000); }
  }
}

/* ---- foco: músculo ou aptidão (volume, frequência, ordem, técnica e blocos próprios) ---- */
const FOCUS_SKILL_TXT = {
  forca: "o levantamento escolhido 2× por semana: um dia pesado (3–5 reps, 3 min de descanso) e um de técnica",
  potencia: "saltos/arremessos logo após o aquecimento, com poucas repetições e descanso completo",
  folego: "um intervalado 4×4 min (ou finalizador 6×1 min) por semana — dos que mais sobem o VO₂máx",
  resistencia: "acessórios com 15–20 repetições e descanso curto",
  mobilidade: "+2 exercícios de mobilidade no aquecimento de todo treino",
  core: "um exercício de estabilidade do tronco em todo treino",
  postura: "costas altas e deltoide posterior nos treinos de superior + mobilidade torácica",
  pegada: "caminhada do fazendeiro / punho 2× por semana, no fim do treino",
  equilibrio: "exercício unilateral de perna 2× por semana + 30 s em um pé",
};
async function loadFocusBox(p) {
  const box = $("#focus-box"); if (!box) return;
  let opt; try { opt = await api("/api/focus/options"); } catch (e) { box.innerHTML = `<p class="pill bad">${esc(e.message)}</p>`; return; }
  const cur = p.focus || null;
  const st = { muscles: [...((cur && cur.muscles) || [])], skills: [...((cur && cur.skills) || [])], level: (cur && cur.level) || "forte", lift: (cur && cur.lift) || "agachamento", weeks: (cur && cur.weeks) || 8 };
  const week = cur ? Math.max(1, Math.floor((Date.now() - new Date(cur.start + "T12:00").getTime()) / (7 * 864e5)) + 1) : 0;
  const mName = Object.fromEntries(opt.muscles.map((m) => [m.id, m.name]));
  const render = () => {
    const what = [
      ...st.muscles.map((m) => `<li><b>${esc(mName[m])}</b>: mais séries por semana (faixa alta, sem passar do que dá para recuperar), em 2–3 dias, no começo do treino e com myo-reps na última série</li>`),
      ...st.skills.map((s) => `<li><b>${esc((opt.skills.find((x) => x.id === s) || {}).label)}</b>: ${FOCUS_SKILL_TXT[s]}</li>`),
      ...(st.muscles.length && st.level === "forte" ? ["<li>O resto do corpo fica em <b>manutenção</b> (~1/3 do volume já mantém a massa) para sobrar tempo e recuperação</li>"] : []),
    ];
    box.innerHTML = `${cur ? `<div class="fc-cur"><b>Foco atual:</b> ${esc(cur.label || "")} · semana ${Math.min(week, cur.weeks)} de ${cur.weeks}${week > cur.weeks ? " · <b>bloco terminou</b>: veja o resultado em Progresso e escolha o próximo" : ""}</div>` : ""}
      <p class="small muted">Quer melhorar algo específico? Escolha até 2 músculos e/ou 2 aptidões. O app mexe em várias coisas ao mesmo tempo e acompanha o progresso por um bloco de semanas.</p>
      <label>Músculos (até 2)</label>
      <div class="fc-chips">${opt.muscles.map((m) => `<button type="button" class="wz-chip ${st.muscles.includes(m.id) ? "on" : ""}" data-fm="${esc(m.id)}">${esc(m.name)}</button>`).join("")}</div>
      <label style="margin-top:12px">Aptidões (até 2)</label>
      <div class="fc-skills">${opt.skills.map((s) => `<button type="button" class="wz-opt ${st.skills.includes(s.id) ? "on" : ""}" data-fs="${esc(s.id)}"><b>${s.icon} ${esc(s.label)}</b><span>${esc(s.desc)}</span></button>`).join("")}</div>
      ${st.skills.includes("forca") ? `<div class="field" style="margin-top:10px"><label>Qual levantamento?</label><select id="fc-lift">${opt.lifts.map((l) => `<option value="${l.id}" ${st.lift === l.id ? "selected" : ""}>${esc(l.name)}</option>`).join("")}</select></div>` : ""}
      ${st.muscles.length ? `<label style="margin-top:12px">Intensidade do foco</label><div class="wz-row">${[["forte", "Forte"], ["leve", "Leve"]].map(([v, t]) => `<button type="button" class="wz-chip ${st.level === v ? "on" : ""}" data-fl="${v}">${t}</button>`).join("")}</div>
        <p class="small muted" style="margin-top:-4px">${st.level === "forte" ? "Forte: o foco ganha volume e o resto vai para manutenção." : "Leve: o foco ganha um pouco mais e o resto continua igual."}</p>` : ""}
      <label style="margin-top:6px">Duração do bloco</label><div class="wz-row">${[6, 8, 12].map((w) => `<button type="button" class="wz-chip ${+st.weeks === w ? "on" : ""}" data-fw="${w}">${w} semanas</button>`).join("")}</div>
      ${what.length ? `<div class="fc-what"><b>O que vai mudar no seu treino</b><ul>${what.join("")}</ul></div>` : ""}
      <button class="btn block" id="fc-save" ${st.muscles.length || st.skills.length ? "" : "disabled"}>🎯 Montar treino com este foco</button>
      ${cur ? `<button class="act" id="fc-clear" style="margin-top:10px">Tirar o foco (voltar ao equilibrado)</button>` : ""}`;
  };
  render();
  box.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const tog = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : arr.length >= 2 ? (toast("Até 2 — tire um para escolher outro"), arr) : [...arr, v]);
    if (t.dataset.fm) { st.muscles = tog(st.muscles, t.dataset.fm); return render(); }
    if (t.dataset.fs) { st.skills = tog(st.skills, t.dataset.fs); return render(); }
    if (t.dataset.fl) { st.level = t.dataset.fl; return render(); }
    if (t.dataset.fw) { st.weeks = +t.dataset.fw; return render(); }
    if (t.id === "fc-clear") {
      if (!confirm("Tirar o foco? O treino volta ao equilibrado quando você gerar de novo.")) return;
      await api("/api/profile", { method: "PUT", body: { focus: null } }); state.profile = null; toast("Foco removido"); openGenerator();
    }
    if (t.id === "fc-save") {
      const same = cur && JSON.stringify(cur.muscles) === JSON.stringify(st.muscles) && JSON.stringify(cur.skills) === JSON.stringify(st.skills);
      const lift = $("#fc-lift", box); if (lift) st.lift = lift.value;
      t.disabled = true;
      try {
        await api("/api/profile", { method: "PUT", body: { focus: { ...st, ...(same ? { start: cur.start } : {}) } } });
        state.profile = null; toast("Foco salvo — veja a prévia do treino 👇"); openGenerator();
      } catch (err) { t.disabled = false; toast(err.message, 5000); }
    }
  };
  box.onchange = (e) => { if (e.target.id === "fc-lift") st.lift = e.target.value; };
}

// Hoje: lembrete do bloco de foco
function focusChip(p) {
  const f = p && p.focus; if (!f) return "";
  const week = Math.max(1, Math.floor((Date.now() - new Date(f.start + "T12:00").getTime()) / (7 * 864e5)) + 1);
  return week > f.weeks
    ? `<a class="focus-chip done" href="#perfil/foco">🎯 Bloco de foco terminou (${esc(f.label || "")}) — veja o resultado e escolha o próximo ›</a>`
    : `<a class="focus-chip" href="#analise">🎯 Foco: ${esc(f.label || "")} · semana ${week} de ${f.weeks} <span aria-hidden="true">›</span></a>`;
}

function focusCard(fp) {
  if (!fp || !fp.focus) return "";
  const f = fp.focus; const ms = f.muscles || [];
  const bars = ms.map((m) => {
    const tgt = fp.targets[m]; const ws = fp.weeks.slice(-6);
    if (!ws.some((w) => w.sets[m])) return `<div class="fc-m"><b>${esc(fp.names[m])}</b> <span class="small muted">· alvo ${tgt || "—"} séries por semana</span><p class="small muted" style="margin:4px 0 0">Ainda sem séries registradas — a cada treino o gráfico semanal aparece aqui.</p></div>`;
    const max = Math.max(tgt || 0, ...ws.map((w) => w.sets[m] || 0), 1);
    return `<div class="fc-m"><b>${esc(fp.names[m])}</b> <span class="small muted">séries por semana${tgt ? ` · alvo ${tgt}` : ""}</span>
      <div class="fc-bars">${ws.map((w) => `<span class="fc-bar ${addDays(w.week, 6) >= f.start.slice(0, 10) ? "" : "pre"}" title="semana de ${esc(ddmm(w.week))}"><em>${w.sets[m] || 0}</em><i style="height:calc((100% - 16px) * ${((w.sets[m] || 0) / max).toFixed(3)})"></i></span>`).join("")}
        ${tgt ? `<span class="fc-target" style="bottom:calc((100% - 16px) * ${(tgt / max).toFixed(3)})"></span>` : ""}</div></div>`;
  }).join("");
  const lifts = fp.lifts.map((l) => `<li><b>${esc(l.name)}</b>: força estimada ${l.first} → ${l.last} kg <span class="${l.change_pct >= 0 ? "ok-txt" : "muted"}">(${l.change_pct >= 0 ? "+" : ""}${l.change_pct}%)</span></li>`).join("");
  const week = Math.max(1, Math.floor((Date.now() - new Date(f.start + "T12:00").getTime()) / (7 * 864e5)) + 1);
  return `<div class="card focus-card"><h2>🎯 Seu foco: ${esc(fp.label)}</h2>
    <p class="small muted">Semana ${Math.min(week, f.weeks)} de ${f.weeks}${week > f.weeks ? " — bloco terminou: mantenha se ainda está evoluindo, ou troque o foco em Perfil" : ""}. Barras cinzas: as 2 semanas antes do foco, para comparar.</p>
    ${fp.applied ? "" : `<div class="rec atencao">O foco ainda não está no seu treino. <a href="#perfil/foco">Gerar o treino com foco</a></div>`}
    ${bars}${lifts ? `<ul class="list small">${lifts}</ul>` : (ms.length ? `<p class="small muted">A evolução de força aparece depois de 2 treinos registrados com os exercícios do foco.</p>` : "")}
    ${(f.skills || []).length ? `<p class="small">Aptidões no treino (marcadas com “foco”): ${esc(f.skills.map((s) => FOCUS_SKILL_TXT[s]).join("; "))}.</p>` : ""}</div>`;
}

async function loadMyGym() {
  const box = $("#mygym-box"); if (!box) return;
  let list = [];
  try { list = await api("/api/my-gym/machines"); } catch (e) { box.innerHTML = `<p class="pill bad">${esc(e.message)}</p>`; return; }
  box.innerHTML = `<p class="small muted">Fotografe os aparelhos da sua academia e anote a regulagem (banco, encosto, pino). No treino o app mostra a foto e a regulagem, e na troca de aparelho os seus aparecem primeiro.</p>
    ${list.length ? `<ul class="list mg-list">${list.map((m) => `<li><button class="mg-row" data-mgid="${esc(m.id)}">${m.photo_url ? `<img src="${esc(m.photo_url)}" alt="">` : `<span class="mm-ph">📷</span>`}
      <span class="grow"><b>${esc(m.name)}</b><span class="small muted">${esc(m.exercise_name || m.exercise_id)}${m.settings ? ` · ${esc(m.settings)}` : ""}</span></span><span class="chev">›</span></button></li>`).join("")}</ul>` : ""}
    <button class="btn ghost block" id="mg-new">📷 Fotografar um aparelho</button>`;
  box.onclick = (e) => {
    const r = e.target.closest("[data-mgid]");
    if (r) { const m = list.find((x) => x.id === r.dataset.mgid); return openMyMachine({ ...m }, loadMyGym); }
    if (e.target.id === "mg-new") pickExercise(null, (ex) => openMyMachine({ exercise_id: ex.id, exercise_name: ex.name }, loadMyGym));
  };
}

/* ---- área do desenvolvedor: fotos de aparelhos viram aparelhos do catálogo ---- */
async function viewDev() {
  if (!(state.me && state.me.user && state.me.user.is_admin) && state.me && state.me.logged_in) { view.innerHTML = `<p class="muted">Só o administrador acessa esta área.</p>`; return; }
  const q = await api("/api/dev/queue");
  const catOpts = (sel) => q.categories.map((c) => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.name)}</option>`).join("");
  const guessCat = (eid) => { const e = (eid || "").toLowerCase(); return /leg|squat|lunge|calf|hack/.test(e) ? "pernas" : /glute|hip_thrust|bridge/.test(e) ? "gluteo" : /row|pull|lat|chin/.test(e) ? "costas" : /bench|chest|fly|press_machine|dip/.test(e) ? "peito" : /shoulder|lateral|delt|military/.test(e) ? "ombros" : /curl|tricep|bicep/.test(e) ? "bracos" : /crunch|ab|plank|torso/.test(e) ? "core" : "funcional"; };
  view.innerHTML = `<p class="small muted" style="margin:2px 4px 12px">Só você (administrador) vê esta área. O que for aprovado aqui entra na galeria de aparelhos para todos.</p>
    <div class="card"><h2>Adicionar aparelho com foto</h2><p class="small muted">Na academia: fotografe, diga o nome e quais exercícios dá para fazer. Entra na galeria de aparelhos para todos na hora.</p>
      <button class="btn block" id="dv-new">📷 Fotografar aparelho novo</button></div>
    <h2 class="sec-title">Feedback (${(q.feedback || []).filter((f) => !f.done).length} novos)</h2>
    ${(q.feedback || []).length ? `<div class="picks fb-list">${q.feedback.map((f) => `<div class="pick fb ${f.done ? "done" : ""}"><span class="grow"><b>${esc(f.user_name || "—")}</b><span class="small muted">${fmtDate(f.created)} · tela ${esc(f.where || "—")}${f.device ? ` · ${esc(f.device)}` : ""}</span>
      <span class="fb-text">${esc(f.text)}</span></span><button class="act" data-fbdone="${esc(f.id)}" data-v="${f.done ? 0 : 1}">${f.done ? "Reabrir" : "✓ Resolvido"}</button></div>`).join("")}</div>` : `<p class="muted small" style="margin:0 4px 14px">Ainda nenhum. Todo mundo tem o botão “Mandar sugestão ou problema” no Perfil.</p>`}
    <h2 class="sec-title">Fotos para aprovar (${q.pending.length})</h2>
    ${q.pending.length ? q.pending.map((x) => `<div class="card dv-card" data-q="${esc(x.qid)}">
      <img class="dv-img" src="${esc(x.photo_url)}" alt="">
      <p class="small muted">Enviada por <b>${esc(x.user_name || "—")}</b> · ${fmtDate(x.created)}${x.exercise_name ? ` · usada para <b>${esc(x.exercise_name)}</b>` : ""}${x.settings ? ` · regulagem: ${esc(x.settings)}` : ""}</p>
      <div class="field"><label>Nome do aparelho</label><input data-k="name" value="${esc(x.name || "")}"></div>
      <div class="grid2"><div class="field"><label>Categoria</label><select data-k="category">${catOpts(guessCat(x.exercise_id))}</select></div>
        <div class="field"><label>Ou juntar a um aparelho que já existe</label><select data-k="merge_into"><option value="">— aparelho novo —</option>${q.machines.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("")}</select></div></div>
      <label>Exercícios que dá para fazer</label><div class="chips dv-exs" data-exs="${esc(x.exercise_id || "")}">${x.exercise_id ? `<span class="chip" data-rm-ex="${esc(x.exercise_id)}">${esc(x.exercise_name)} ✕</span>` : ""}<button class="chip" data-add-ex>+ exercício</button></div>
      <div class="field" style="margin-top:10px"><label>O que dá para fazer / dica de uso</label><textarea data-k="what" rows="2" style="min-height:64px" placeholder="ex.: Empurre a plataforma com os pés na largura do quadril…"></textarea></div>
      <div class="row gap"><button class="btn grow" data-approve>Aprovar</button><button class="btn ghost" data-discard>Descartar</button></div></div>`).join("") : `<p class="muted small" style="margin:0 4px 14px">Nenhuma foto na fila. Quando alguém fotografar um aparelho e marcar “sugerir para o catálogo”, ela aparece aqui.</p>`}
    <h2 class="sec-title">Aparelhos da comunidade (${q.custom.length})</h2>
    ${q.custom.length ? `<div class="picks">${q.custom.map((m) => `<div class="pick"><a class="grow" href="#aparelhos/${esc(m.id)}"><b>${esc(m.name)}</b><span class="small muted">${esc((q.categories.find((c) => c.id === m.category) || {}).name || "")}</span></a><button class="act" data-del-m="${esc(m.id)}">Remover</button></div>`).join("")}</div>` : `<p class="muted small" style="margin:0 4px">Ainda nenhum.</p>`}`;
  const exsOf = (card) => [...card.querySelectorAll("[data-rm-ex]")].map((c) => c.dataset.rmEx);
  view.onclick = async (e) => {
    const card = e.target.closest("[data-q]");
    const fb = e.target.closest("[data-fbdone]");
    if (fb) { await api(`/api/dev/feedback/${fb.dataset.fbdone}/done`, { method: "POST", body: { done: fb.dataset.v === "1" } }); return viewDev(); }
    if (e.target.id === "dv-new") return devNewMachine(q, catOpts, guessCat);
    const del = e.target.closest("[data-del-m]");
    if (del) { if (!confirm("Remover este aparelho da galeria?")) return; await api(`/api/dev/machines/${del.dataset.delM}`, { method: "DELETE" }); toast("Removido"); return viewDev(); }
    if (!card) return;
    const rm = e.target.closest("[data-rm-ex]"); if (rm) return rm.remove();
    if (e.target.closest("[data-add-ex]")) {
      return pickExercise(null, (ex) => { closeModal(); if (exsOf(card).includes(ex.id)) return; e.target.closest("[data-add-ex]").insertAdjacentHTML("beforebegin", `<span class="chip" data-rm-ex="${esc(ex.id)}">${esc(ex.name)} ✕</span>`); });
    }
    const val = (k) => (card.querySelector(`[data-k="${k}"]`) || {}).value;
    if (e.target.closest("[data-discard]")) { await api(`/api/dev/queue/${card.dataset.q}/discard`, { method: "POST" }); toast("Descartada"); return viewDev(); }
    if (e.target.closest("[data-approve]")) {
      try {
        await api(`/api/dev/queue/${card.dataset.q}/approve`, { method: "POST", body: { name: val("name"), category: val("category"), merge_into: val("merge_into"), exercise_ids: exsOf(card), what: val("what") } });
        toast("Aprovado: já está na galeria 🎉"); viewDev();
      } catch (err) { toast(err.message, 5000); }
    }
  };
}

function devNewMachine(q, catOpts, guessCat, st = {}) {
  const body = openModal(`<h2>Aparelho novo</h2>
    <label class="photo-pick">${st.image ? `<img src="${esc(st.image)}" alt="">` : `<span class="pp-empty">📷<b>Tirar foto do aparelho</b><span class="small">ou escolher da galeria</span></span>`}<input type="file" id="dn-file" accept="image/*" capture="environment"></label>
    <div class="field"><label>Nome</label><input id="dn-name" value="${esc(st.name || "")}" placeholder="ex.: Remada articulada (Hammer)"></div>
    <div class="field"><label>Categoria</label><select id="dn-cat">${catOpts(st.category || guessCat((st.exs || [])[0] && st.exs[0].id))}</select></div>
    <label>Exercícios que dá para fazer</label><div class="chips">${(st.exs || []).map((x) => `<span class="chip">${esc(x.name)}</span>`).join("")}<button class="chip" id="dn-add">+ exercício</button></div>
    <div class="field" style="margin-top:10px"><label>O que dá para fazer / dica</label><textarea id="dn-what" rows="2" style="min-height:64px">${esc(st.what || "")}</textarea></div>
    <button class="btn block" id="dn-save" ${st.image && (st.exs || []).length ? "" : "disabled"}>Publicar na galeria</button>
    <p class="small muted">Precisa de foto e de pelo menos um exercício.</p>`);
  const keep = () => { st.name = $("#dn-name", body).value; st.category = $("#dn-cat", body).value; st.what = $("#dn-what", body).value; };
  $("#dn-file", body).onchange = async (e) => { const f = e.target.files && e.target.files[0]; if (!f) return; keep(); try { st.image = await shrinkPhoto(f); } catch (err) { return toast(err.message); } devNewMachine(q, catOpts, guessCat, st); };
  $("#dn-add", body).onclick = () => { keep(); pickExercise(null, (ex) => { st.exs = [...(st.exs || []).filter((x) => x.id !== ex.id), { id: ex.id, name: ex.name }]; if (!st.name) st.name = ex.name; devNewMachine(q, catOpts, guessCat, st); }); };
  $("#dn-save", body).onclick = async (e) => {
    keep(); e.target.disabled = true;
    try { await api("/api/dev/machines", { method: "POST", body: { image: st.image, name: st.name, category: st.category, what: st.what, exercise_ids: st.exs.map((x) => x.id) } }); closeModal(); toast("Publicado na galeria 🎉"); viewDev(); }
    catch (err) { e.target.disabled = false; toast(err.message, 5000); }
  };
}

/* ---- treino guiado: um exercício por vez, descanso cronometrado ---- */
const PH_RANK = { preparo: 0, principal: 1, pos: 2 };
const fxPh = (it) => it.phase || "principal";
const fxStrength = (it) => !(it.kind === "cardio" || it.endurance) && fxPh(it) === "principal" && (it.sets || []).length;
const fxDone = (it) => fxStrength(it) ? it.sets.every((x) => x.done) : !!it.done;
const fxOrder = (s) => s.items.map((_, i) => i).sort((a, b) => (PH_RANK[fxPh(s.items[a])] - PH_RANK[fxPh(s.items[b])]) || a - b);
const mmss = (t) => `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
// série atual: aquecimento (w) enquanto houver e nenhuma válida foi feita; senão a 1ª válida pendente
function fxCur(it, s) {
  const W = it.warmup || [];
  if (Number.isInteger(s.fsel)) {
    if (s.fsel < 0 && -s.fsel - 1 < W.length) { const k = -s.fsel - 1; return { w: true, k, x: W[k] }; }
    if (s.fsel >= 0 && s.fsel < it.sets.length) return { w: false, k: s.fsel, x: it.sets[s.fsel] };
  }
  const wk = W.findIndex((x) => !x.done && !x.skip);
  if (wk >= 0 && !it.sets.some((x) => x.done)) return { w: true, k: wk, x: W[wk] };
  let k = it.sets.findIndex((x) => !x.done); if (k < 0) k = it.sets.length - 1;
  return { w: false, k, x: it.sets[k] };
}
const WARM_REST = 45, WARM_LAST_REST = 75;
function fxCarry(it, k, f, old, val) {
  if (f !== "load_kg" || !it || !it.sets) return;
  // no treino puxado as séries já vêm marcadas como feitas: a carga digitada desce do mesmo jeito para as que estão vazias/iguais
  for (let j = k + 1; j < it.sets.length; j++) { const y = it.sets[j]; if ((!y.done || it.from_partner || it.fresh) && (y.load_kg == null || y.load_kg === old)) y.load_kg = val; }
}
// a série que acabou de ser feita (para corrigir repetições e carga enquanto o descanso já corre)
function fxLast_(s) {
  const L = s.lastSet; const it = L && s.items[L.i]; if (!it || !fxStrength(it)) return null;
  const x = L.w ? (it.warmup || [])[L.k] : it.sets[L.k];
  return x && x.done ? { it, x, w: L.w, k: L.k } : null;
}
function fxJustHtml(s) {
  const L = fxLast_(s); if (!L) return "";
  const { it, x } = L; const timed = it.timed && it.sets.some((y) => y.duration_s); const fld = timed ? "duration_s" : "reps";
  const st = (f, label, step, mode) => `<div class="fx-step"><label>${label}</label><div class="stepper">
    <button data-lst="${f}:-${step}" aria-label="menos ${label}">−</button><input data-lv="${f}" inputmode="${mode}" value="${esc(x[f] ?? "")}" aria-label="${label} da série feita">
    <button data-lst="${f}:${step}" aria-label="mais ${label}">+</button></div></div>`;
  return `<div class="fx-just"><div class="fx-just-h"><span class="small">Você acabou de fazer · ajuste se precisar</span>
      <b>${esc(it.name)} · ${L.w ? `aquecimento ${L.k + 1}` : `série ${L.k + 1}`}</b></div>
    <div class="fx-inputs">${st(fld, timed ? "Segundos" : "Repetições", timed ? 5 : 1, "numeric")}${st("load_kg", "Carga (kg)", 2.5, "decimal")}</div>
    ${L.w ? "" : `<div class="fx-rpe"><span class="small muted">Esforço</span>${[6, 7, 8, 9, 10].map((r) => `<button class="${+x.rpe === r ? "on" : ""}" data-lrpe="${r}">${r}</button>`).join("")}</div>`}</div>`;
}
function fxNextPos(s, order, pos) {
  for (let p = pos + 1; p < order.length; p++) if (!fxDone(s.items[order[p]])) return p;
  for (let p = 0; p < pos; p++) if (!fxDone(s.items[order[p]])) return p;
  return order.length;
}
const fxSetTxt = (it, x) => x ? `${it.timed && x.duration_s ? `${x.duration_s} s` : `${x.reps ?? "?"} reps`}${x.load_kg ? ` × ${x.load_kg} kg` : ""}` : "";
function fxPlanTxt(it) {
  const p = it.plan || {};
  if (it.endurance) return endSummary(it.endurance);
  if (it.kind === "cardio") return `${it.duration_min || p.duration_min || ""} min · zona ${it.zone || p.zone || 2}`;
  const free = !p.reps && !p.duration_s && !it.timed;  // adicionado na hora: séries livres, sem alvo de repetições
  const what = p.reps ? `${esc(String(p.reps))} reps` : free ? "" : `${p.duration_s || 30} s`;
  return `${p.sets > 1 ? p.sets + " × " : ""}${what}${fxPh(it) === "principal" ? `${what ? " · " : ""}descanso ${p.rest_s ?? 90} s` : ""}${p.rir && !p.deload ? ` · sobrar ${esc(p.rir)} reps` : ""}`;
}

// som, vibração e tela acesa
let AC = null, WL = null, fxInt = null, fxLast = null;
function audioUnlock() { try { AC = AC || new (window.AudioContext || window.webkitAudioContext)(); if (AC.state === "suspended") AC.resume(); } catch (_) { /* sem áudio */ } }
function beep(f = 880, d = 0.15, vol = 0.3) {
  try { if (!AC) return; const o = AC.createOscillator(), g = AC.createGain(); o.frequency.value = f; o.connect(g); g.connect(AC.destination);
    g.gain.setValueAtTime(vol, AC.currentTime); g.gain.exponentialRampToValueAtTime(0.001, AC.currentTime + d); o.start(); o.stop(AC.currentTime + d); } catch (_) { /* sem áudio */ }
}
function alarm() { beep(988, 0.25); setTimeout(() => beep(1318, 0.35), 280); try { navigator.vibrate && navigator.vibrate([300, 120, 300]); } catch (_) { /* sem vibração */ } }
async function wakeOn() { try { if ("wakeLock" in navigator && !WL && !document.hidden) { WL = await navigator.wakeLock.request("screen"); WL.addEventListener("release", () => { WL = null; }); } } catch (_) { WL = null; } }
function wakeOff() { try { WL && WL.release(); } catch (_) { /* já liberado */ } WL = null; }
function fxLeave() { clearInterval(fxInt); fxInt = null; wakeOff(); document.body.classList.remove("focus-on"); }
document.addEventListener("visibilitychange", () => { if (!document.hidden && document.body.classList.contains("focus-on")) { wakeOn(); fxTick(); } });

// nada avança sozinho: o fim do descanso ou do tempo só avisa (som + vibração); trocar é sempre no seu toque
function fxTick() {
  const s = state.session; if (!s || !document.body.classList.contains("focus-on")) return;
  const el = $("#fx-clock"), bar = $("#fx-bar span"), now = Date.now();
  const show = (left, total) => { if (el) el.textContent = mmss(left); if (bar && total) bar.style.width = `${Math.min(100, 100 * (1 - left / total))}%`; };
  const beeps = (left) => { if (left !== fxLast && left > 0 && left <= 3) beep(660, 0.12, 0.22); fxLast = left; };
  if (s.restEnd) {
    const over = now - s.restEnd;
    if (over < 0) { const left = Math.ceil(-over / 1000); show(left, s.restTotal); return beeps(left); }
    if (!s.restOver) {
      s.restOver = true; store("gg_session", s); if (over < 8000) alarm();
      const sec = $(".fx-rest"); if (!sec) return renderFocus();
      sec.classList.add("over"); const lbl = $("#fx-rest-lbl"); if (lbl) lbl.textContent = "Descanso terminado";
      $$(".fx-rest [data-when=rest]").forEach((x) => { x.hidden = true; }); $$(".fx-rest [data-when=over]").forEach((x) => { x.hidden = false; });
      const go = $(".fx-rest .fx-skip"); if (go && s.items[fxOrder(s)[s.fx]]) go.textContent = "▶ Começar";
    }
    if (el) el.textContent = "+" + mmss(Math.floor(over / 1000));
    return;
  }
  if (s.prepEnd) {
    const left = Math.ceil((s.prepEnd - now) / 1000);
    if (left > 0) { if (el) el.textContent = String(left); if (left !== fxLast) beep(523, 0.1, 0.2); fxLast = left; return; }
    s.prepEnd = null; s.workEnd = now + s.workTotal * 1000; fxLast = null; beep(1046, 0.3); store("gg_session", s); return renderFocus();
  }
  if (s.workEnd) {
    const left = Math.max(0, Math.ceil((s.workEnd - now) / 1000));
    show(left, s.workTotal); beeps(left);
    if (left > 0) return;
    if (now - s.workEnd < 8000) alarm();
    s.workEnd = null; s.workKind = null; s.timeUp = true; store("gg_session", s);
    toast("Tempo! Toque em ✓ quando terminar"); renderFocus();
  }
}

function fxSetDone() {
  const s = state.session; const order = fxOrder(s); const it = s.items[order[s.fx]]; if (!it) return;
  const c = fxCur(it, s); c.x.done = true; s.fsel = null; s.timeUp = false; s.workEnd = null; s.prepEnd = null;
  s.lastSet = { i: order[s.fx], w: c.w, k: c.k };
  if (!c.w) fxCarry(it, c.k, "load_kg", null, c.x.load_kg);
  let rest;
  if (c.w) rest = (it.warmup || []).some((y) => !y.done && !y.skip) ? WARM_REST : WARM_LAST_REST;
  else { rest = (it.plan || {}).rest_s ?? 90; if (it.sets.every((y) => y.done)) s.fx = fxNextPos(s, order, s.fx); }
  if (rest > 0) { s.restTotal = rest; s.restEnd = Date.now() + rest * 1000; s.restOver = false; fxLast = null; }
  store("gg_session", s); renderFocus();
  if (s.restEnd) window.scrollTo(0, 0);
}

function fxNextHtml(s, order) {
  const it = s.items[order[s.fx]];
  if (!it) return `<div class="fx-next"><span class="grow"><span class="small">A seguir</span><b>Fim do treino 🎉</b></span></div>`;
  const c = fxStrength(it) ? fxCur(it, s) : null;
  const what = !c ? esc(fxPlanTxt(it).replace(/&amp;/g, "&"))
    : c.w ? `Aquecimento ${c.k + 1} de ${it.warmup.length} · ${esc(fxSetTxt(it, c.x))}${c.x.load_kg ? "" : ` (~${c.x.pct}% da carga)`}`
    : `Série ${c.k + 1} de ${it.sets.length} · ${esc(fxSetTxt(it, c.x))}`;
  return `<div class="fx-next">${mediaHtml(it.exercise_id, "m", it.kind === "cardio" ? "run" : null)}<span class="grow"><span class="small">A seguir</span>
    <b>${esc(it.name)}</b><span class="small">${what}</span></span></div>`;
}

function renderFocus() {
  const s = state.session; const order = fxOrder(s);
  s.view = "foco";
  if (!Number.isInteger(s.fx) || s.fx > order.length) { const p = order.findIndex((i) => !fxDone(s.items[i])); s.fx = p < 0 ? order.length : p; }
  store("gg_session", s); stopTimer();
  document.body.classList.add("focus-on"); wakeOn();
  if (!fxInt) fxInt = setInterval(fxTick, 250);
  const doneN = order.filter((i) => fxDone(s.items[i])).length;
  const pos = s.fx; const it = s.items[order[pos]];
  const top = `<div class="fx-top"><button class="fx-nav" data-fx="prev" ${pos <= 0 ? "disabled" : ""} aria-label="Exercício anterior">‹</button>
    <div class="grow"><div class="fx-count">${esc(s.day_name || "Treino")} · ${it ? `${pos + 1} de ${order.length}` : "fim"}</div>
      <div class="meter"><span style="width:${order.length ? Math.round(100 * doneN / order.length) : 0}%"></span></div></div>
    <button class="fx-nav" data-fx="next" ${pos >= order.length ? "disabled" : ""} aria-label="Próximo exercício">›</button></div>`;
  const tools = `<div class="fx-tools"><button class="act" data-fx="list">☰ Ver lista</button><button class="act" data-fx="add">+ Exercício</button></div>`;
  let body;
  if (s.restEnd) {
    const left = Math.max(0, Math.ceil((s.restEnd - Date.now()) / 1000));
    const just = fxJustHtml(s);
    const clock = `<div class="fx-clock" id="fx-clock">${s.restOver ? "+" + mmss(Math.floor((Date.now() - s.restEnd) / 1000)) : mmss(left)}</div>`;
    const go = `<button class="btn ${just ? "" : "block"} fx-skip" data-fx="r-skip">${!it ? "Terminei" : s.restOver ? "▶ Começar" : "Estou pronto"}</button>`;
    // com a série recém-feita editável em cima, o cronômetro e o botão ficam juntos numa barra fixa embaixo (sempre visíveis)
    body = `<section class="fx-rest ${s.restOver ? "over" : ""} ${just ? "has-just" : ""}">${just}
      <div class="eyebrow" id="fx-rest-lbl" aria-live="polite">${s.restOver ? "Descanso terminado" : "Descanso"}</div>
      ${just ? "" : clock}
      <div class="fx-bar" id="fx-bar" data-when="rest" ${s.restOver ? "hidden" : ""}><span style="width:${Math.round(100 * (1 - left / (s.restTotal || 1)))}%"></span></div>
      <div id="fx-next-box">${fxNextHtml(s, order)}</div>
      <div class="fx-rest-btns" data-when="rest" ${s.restOver ? "hidden" : ""}><button class="btn ghost" data-fx="r-15">−15 s</button><button class="btn ghost" data-fx="r+15">+15 s</button></div>
      ${just ? `<div class="fx-dock">${clock}${go}</div>` : go}
      <p class="small" data-when="over" ${s.restOver ? "" : "hidden"} style="margin:10px 0 0;opacity:.8">Sem pressa: só muda quando você tocar.</p></section>`;
  } else if (!it) {
    const sets = s.items.reduce((n, x) => n + (fxStrength(x) ? x.sets.filter((y) => y.done).length : 0), 0);
    body = `<section class="today-card fx-end"><div class="eyebrow">Treino concluído</div><div class="tc-title">${esc(s.day_name || "Treino")} ✓</div>
      <p class="muted">${doneN} de ${order.length} exercícios · ${sets} séries feitas</p>
      ${s.edit_id ? "" : `<button class="btn ghost block" data-fx="sug" style="margin-bottom:10px">✨ Sugerir 1–2 exercícios extras</button>`}
      <div class="field"><label for="s-notes">Como foi o treino?</label><textarea id="s-notes" placeholder="Sensações, dores, energia…">${esc(s.notes || "")}</textarea></div>
      <button class="btn block" id="s-finish">${s.watch_review ? "Salvar conferência" : s.edit_id ? "Salvar correção" : s.replace_id ? "Salvar (substitui o registro anterior)" : "Finalizar e salvar"}</button>
      <button class="act" data-fx="list" style="margin-top:10px">Revisar na lista</button> <button class="act" id="s-cancel" style="margin-top:10px">${s.edit_id ? "Cancelar correção" : "Descartar treino"}</button></section>`;
  } else {
    const i = order[pos]; const p = it.plan || {};
    const eyebrow = it.endurance ? "Resistência" : it.kind === "cardio" ? "Cardio" : fxPh(it) === "preparo" ? "Aquecimento" : fxPh(it) === "pos" ? "Alongamento" : "Exercício";
    const compact = fxStrength(it);
    const extra = `${p.notes ? `<div class="fx-plan small">${esc(p.notes)}</div>` : ""}
      ${it.last && it.last.sets ? `<div class="fx-plan small">Última vez: ${esc(it.last.sets.map((x) => `${x.reps ?? "-"}×${x.load_kg ?? 0}`).join(" · "))}</div>` : ""}`;
    const head = compact
      ? `<div class="fx-head"><div class="fx-thumb" data-ex="${esc(it.exercise_id)}">${mediaHtml(it.exercise_id, "l")}</div>
          <div class="fx-hd"><div class="eyebrow">${eyebrow}</div><h2 class="fx-name" data-ex="${esc(it.exercise_id)}">${esc(it.name)}</h2>
          <div class="fx-plan">${fxPlanTxt(it)}</div>${limBadge(it.limitation)}</div></div>
        ${myMachineChip(it.my_machine)}
        ${it.suggestion && it.suggestion.note ? `<div class="fx-tip">${esc(it.suggestion.note)}</div>` : ""}`
      : `<div class="fx-media" data-ex="${esc(it.exercise_id)}">${mediaHtml(it.exercise_id, "l", it.kind === "cardio" ? "run" : null)}</div>
      <div class="eyebrow" style="margin-top:12px">${eyebrow}</div>
      <h2 class="fx-name" data-ex="${esc(it.exercise_id)}">${esc(it.name)} ${limBadge(it.limitation)}</h2>
      <div class="fx-plan">${fxPlanTxt(it)}</div>${extra}${it.kind !== "cardio" && !it.endurance ? myMachineChip(it.my_machine) : ""}`;
    const workClock = (label, sec, kind) => s.prepEnd
      ? `<div class="fx-work prep"><div class="eyebrow">Prepare-se</div><div class="fx-clock" id="fx-clock">${Math.max(1, Math.ceil((s.prepEnd - Date.now()) / 1000))}</div>
          <button class="act" data-fx="work-stop">Cancelar</button></div>`
      : s.workEnd
      ? `<div class="fx-work"><div class="eyebrow">Valendo</div><div class="fx-clock" id="fx-clock">${mmss(Math.max(0, Math.ceil((s.workEnd - Date.now()) / 1000)))}</div><div class="fx-bar" id="fx-bar"><span></span></div>
          <button class="act" data-fx="work-stop" style="margin-top:8px">Parar cronômetro</button></div>`
      : `${s.timeUp ? `<div class="fx-timeup">Tempo! ✓ Toque abaixo quando terminar</div>` : ""}<button class="btn ghost block fx-start" data-fx="work" data-sec="${sec}" data-kind="${kind}">▶ ${s.timeUp ? "De novo" : label}</button>`;
    if (fxStrength(it)) {
      const c = fxCur(it, s); const k = c.k; const x = c.x; const W = it.warmup || [];
      const timed = it.timed && it.sets.some((y) => y.duration_s);
      const wRest = W.some((y, j) => j !== k && !y.done && !y.skip) ? WARM_REST : WARM_LAST_REST;
      const fld = timed ? "duration_s" : "reps";
      const stepper = (f, label, step, mode) => `<div class="fx-step"><label>${label}</label><div class="stepper">
        <button data-st="${f}:-${step}" aria-label="menos">−</button><input data-fv="${f}" inputmode="${mode}" value="${esc(x[f] ?? "")}" aria-label="${label}">
        <button data-st="${f}:${step}" aria-label="mais">+</button></div></div>`;
      body = `<section class="fx-card">${head}
        <div class="fx-sets">${W.map((y, j) => `<button class="fx-dot warm ${y.done ? "done" : ""} ${y.skip && !y.done ? "skip" : ""} ${c.w && j === k ? "cur" : ""}" data-fset="${-(j + 1)}" aria-label="Aquecimento ${j + 1}${y.done ? " feito" : ""}">${y.done ? "✓" : "A" + (j + 1)}</button>`).join("")}${W.length ? `<span class="fx-sep" aria-hidden="true"></span>` : ""}${it.sets.map((y, j) => `<button class="fx-dot ${y.done ? "done" : ""} ${!c.w && j === k ? "cur" : ""}" data-fset="${j}" aria-label="Série ${j + 1}${y.done ? " feita" : ""}">${y.done ? "✓" : j + 1}</button>`).join("")}</div>
        <div class="fx-setlbl ${c.w ? "warm" : ""}">${c.w ? `Aquecimento ${k + 1} de ${W.length} · ~${x.pct}% da carga` : `Série ${k + 1} de ${it.sets.length}`}${x.done ? " · feita" : ""}</div>
        ${c.w && k === 0 && !x.done ? `<div class="fx-plan small" style="text-align:center;margin:-4px 0 8px">Leve e rápido, sem cansar: prepara articulações e o padrão do movimento.</div>` : ""}
        <div class="fx-inputs">${stepper(fld, timed ? "Segundos" : "Repetições", timed ? 5 : 1, "numeric")}${stepper("load_kg", "Carga (kg)", 2.5, "decimal")}</div>
        ${c.w ? "" : `<div class="fx-rpe"><span class="small muted">Esforço</span>${[6, 7, 8, 9, 10].map((r) => `<button class="${+x.rpe === r ? "on" : ""}" data-rpe="${r}">${r}</button>`).join("")}</div>`}
        ${timed && !x.done && !c.w ? workClock(`Iniciar ${x.duration_s || p.duration_s || 30} s`, x.duration_s || p.duration_s || 30, "set") : ""}
        ${x.done ? `<button class="btn ghost block fx-go" data-fx="undo-set">Desmarcar ${c.w ? "aquecimento" : "série"} ${k + 1}</button>`
          : c.w ? `<button class="btn block fx-go warm" data-fx="set">✓ Aquecimento ${k + 1} feito · descansar ${mmss(wRest)}</button>`
          : `<button class="btn block fx-go" data-fx="set">✓ Série ${k + 1} feita${(p.rest_s ?? 90) > 0 ? ` · descansar ${mmss(p.rest_s ?? 90)}` : ""}</button>`}
        ${c.w ? `<div class="fx-setmini"><button class="act" data-fx="skipwarm">Pular aquecimento</button></div>` : ""}
        <div class="fx-extra">${extra}</div>
        <div class="fx-setmini fx-gym"><button class="act" data-fx="swap">⇄ Trocar aparelho</button>${it.my_machine ? "" : `<button class="act" data-fx="mymachine">📷 Meu aparelho</button>`}</div>
        ${it.sets.length ? `<div class="fx-setmini"><button class="act" data-fx="addset">+ série</button>${it.sets.length > 1 ? `<button class="act" data-fx="rmset">− série</button>` : ""}<button class="act" data-fx="skip">Pular exercício</button></div>` : ""}
        <div class="fx-setmini"><button class="act drop-ex" data-fx="drop">🗑 Tirar do treino${it.added ? " (escolhi errado)" : ""}</button></div>
      </section>`;
    } else if (it.endurance) {
      body = `<section class="fx-card">${head}</section>${sessionItemHtml(it, i)}
        <button class="btn block fx-go" data-fx="done">${it.done ? "Feito ✓ — próximo" : "✓ Feito — próximo"}</button>`;
    } else if (it.kind === "cardio") {
      body = `<section class="fx-card">${head}
        ${workClock(`Cronômetro de ${it.duration_min || 10} min`, (it.duration_min || 10) * 60, "item")}
        <div class="grid2" style="margin-top:10px"><div><label>Minutos</label><input type="number" inputmode="numeric" data-c="duration_min" data-i="${i}" value="${esc(it.duration_min ?? "")}"></div>
          <div><label>Zona</label><select data-c="zone" data-i="${i}">${zoneOpts(it.zone)}</select></div>
          <div><label>FC média</label><input type="number" inputmode="numeric" data-c="avg_hr" data-i="${i}" value="${esc(it.avg_hr ?? "")}"></div></div>
        <button class="btn block fx-go" data-fx="done">${it.done ? "Feito ✓ — próximo" : "✓ Feito — próximo"}</button>
        <div class="fx-setmini"><button class="act" data-fx="skip">Pular</button><button class="act drop-ex" data-fx="drop">🗑 Tirar do treino</button></div></section>`;
    } else {
      const sec = (p.duration_s || (p.reps ? 0 : 30)) * (p.sets || 1);
      body = `<section class="fx-card">${head}
        ${sec ? workClock(`Iniciar ${sec >= 60 ? mmss(sec) + " min" : sec + " s"}`, sec, "item") : ""}
        <button class="btn block fx-go" data-fx="done">${it.done ? "Feito ✓ — próximo" : "✓ Feito — próximo"}</button>
        <div class="fx-setmini"><button class="act" data-fx="skip">Pular</button><button class="act drop-ex" data-fx="drop">🗑 Tirar do treino</button></div></section>`;
    }
  }
  view.innerHTML = top + body + (s.restEnd && it ? "" : tools);  // no descanso depois da última série dá para pôr mais um exercício
  bindFocus();
}

function bindFocus() {
  const s = state.session; const save = () => store("gg_session", s);
  const order = fxOrder(s); const it = s.items[order[s.fx]];
  view.oninput = (e) => {
    const el = e.target;
    if (el.id === "s-notes") s.notes = el.value;
    const num = (v) => { const n = v === "" ? null : parseFloat(String(v).replace(",", ".")); return Number.isFinite(n) ? n : null; };
    if (el.dataset.fv && it && fxStrength(it)) { const c = fxCur(it, s); const old = c.x[el.dataset.fv]; c.x[el.dataset.fv] = num(el.value); if (!c.w) fxCarry(it, c.k, el.dataset.fv, old, c.x[el.dataset.fv]); }
    if (el.dataset.lv) { const L = fxLast_(s); if (L) { const old = L.x[el.dataset.lv]; L.x[el.dataset.lv] = num(el.value); if (!L.w) fxCarry(L.it, L.k, el.dataset.lv, old, L.x[el.dataset.lv]); const nb = $("#fx-next-box"); if (nb) nb.innerHTML = fxNextHtml(s, order); } }
    if (el.dataset.c) s.items[+el.dataset.i][el.dataset.c] = el.value === "" ? null : +String(el.value).replace(",", ".");
    if (el.dataset.t) s.items[+el.dataset.i][el.dataset.t] = el.value;
    if (el.dataset.c || el.dataset.t) { const pe = $(`#pace-${el.dataset.i}`); if (pe) pe.textContent = paceTxt(s.items[+el.dataset.i]); }
    if (el.dataset.s) s.items[+el.dataset.i].sets[+el.dataset.j][el.dataset.s] = el.value === "" ? null : parseFloat(el.value.replace(",", "."));
    save();
  };
  view.onchange = view.oninput;
  view.onclick = (e) => {
    audioUnlock();
    const t = e.target.closest("[data-fx],[data-fset],[data-st],[data-rpe],[data-ex],[data-cdone],[data-mm],[data-lst],[data-lrpe]"); if (!t) return;
    if (t.dataset.lst || t.dataset.lrpe) {
      const L = fxLast_(s); if (!L) return;
      if (t.dataset.lrpe) { L.x.rpe = +L.x.rpe === +t.dataset.lrpe ? null : +t.dataset.lrpe; $$(".fx-just [data-lrpe]").forEach((b) => b.classList.toggle("on", +b.dataset.lrpe === L.x.rpe)); return save(); }
      const [f, d] = t.dataset.lst.split(":"); const old = L.x[f];
      L.x[f] = Math.max(0, Math.round(((+L.x[f] || 0) + +d) * 100) / 100); if (!L.w) fxCarry(L.it, L.k, f, old, L.x[f]);
      const inp = $(`[data-lv="${f}"]`); if (inp) inp.value = L.x[f]; const nb = $("#fx-next-box"); if (nb) nb.innerHTML = fxNextHtml(s, order);
      return save();
    }
    const saveMM = (mm) => { it.my_machine = mm; save(); renderFocus(); };
    if (t.dataset.mm && it) { const mm = it.my_machine || {}; return openMyMachine({ ...mm, exercise_id: it.exercise_id, exercise_name: it.name }, saveMM); }
    if (t.dataset.fx === "mymachine" && it) return openMyMachine({ exercise_id: it.exercise_id, exercise_name: it.name }, saveMM);
    if (t.dataset.fx === "swap" && it) return openSwap(it, (to, also) => fxSwap(it, to, also));
    if (t.dataset.ex && !t.dataset.fx) return showExercise(t.dataset.ex);
    if (t.dataset.cdone) { const x = s.items[+t.dataset.cdone]; x.done = !x.done; t.classList.toggle("on", x.done); return save(); }
    if (t.dataset.fset) { s.fsel = +t.dataset.fset; s.workEnd = null; s.prepEnd = null; s.timeUp = false; return renderFocus(); }
    if (t.dataset.st && it) {
      const [f, d] = t.dataset.st.split(":"); const c = fxCur(it, s); const x = c.x; const old = x[f];
      x[f] = Math.max(0, Math.round(((+x[f] || 0) + +d) * 100) / 100); if (!c.w) fxCarry(it, c.k, f, old, x[f]);
      const inp = $(`[data-fv="${f}"]`); if (inp) inp.value = x[f]; return save();
    }
    if (t.dataset.rpe && it) { const x = fxCur(it, s).x; x.rpe = +x.rpe === +t.dataset.rpe ? null : +t.dataset.rpe; $$(".fx-rpe [data-rpe]").forEach((b) => b.classList.toggle("on", +b.dataset.rpe === x.rpe)); return save(); }
    const a = t.dataset.fx;
    if (a === "set") { s.fxBack = null; return fxSetDone(); }
    if (a === "drop" && it) { if (removeSessionItem(order[s.fx])) renderSession(); return; }
    if (a === "undo-set") { fxCur(it, s).x.done = false; s.lastSet = null; save(); return renderFocus(); }
    if (a === "skipwarm") { (it.warmup || []).forEach((w) => { if (!w.done) w.skip = true; }); s.fsel = null; save(); return renderFocus(); }
    if (a === "done") { it.done = true; s.workEnd = null; s.prepEnd = null; s.timeUp = false; s.fx = fxNextPos(s, order, s.fx); save(); return renderFocus(); }
    if (a === "skip") { s.workEnd = null; s.prepEnd = null; s.timeUp = false; s.fsel = null; s.fx = Math.min(order.length, s.fx + 1); save(); return renderFocus(); }
    if (a === "prev" || a === "next") { s.lastSet = null; s.restEnd = null; s.restOver = false; s.workEnd = null; s.prepEnd = null; s.timeUp = false; s.fsel = null; s.fx = Math.max(0, Math.min(order.length, s.fx + (a === "next" ? 1 : -1))); save(); return renderFocus(); }
    if (a === "r-15" || a === "r+15") { s.restEnd = Math.max(Date.now(), s.restEnd + (a === "r+15" ? 15000 : -15000)); s.restTotal = Math.max(s.restTotal, Math.ceil((s.restEnd - Date.now()) / 1000)); save(); return fxTick(); }
    if (a === "r-skip") { s.restEnd = null; s.restOver = false; s.lastSet = null; save(); return renderFocus(); }
    if (a === "work") { s.workTotal = +t.dataset.sec; s.prepEnd = Date.now() + 5000; s.workEnd = null; s.timeUp = false; s.workKind = t.dataset.kind; fxLast = null; save(); return renderFocus(); }
    if (a === "work-stop") { s.workEnd = null; s.prepEnd = null; save(); return renderFocus(); }
    if (a === "addset" && it) { const last = it.sets[it.sets.length - 1] || {}; it.sets.push({ ...last, done: !!(s.edit_id || s.replace_id || s.pulled) && !!last.done }); save(); return renderFocus(); }
    if (a === "rmset" && it && it.sets.length > 1) { it.sets.pop(); s.fsel = null; save(); return renderFocus(); }
    if (a === "list") {
      const left = s.restEnd ? Math.ceil((s.restEnd - Date.now()) / 1000) : 0;
      s.view = "lista"; s.restEnd = null; s.restOver = false; s.workEnd = null; s.prepEnd = null; save(); renderSession(); if (left > 0) startTimer(left); return;
    }
    if (a === "sug") return openSuggest(s, () => { const order2 = fxOrder(s); s.fx = order2.indexOf(s.items.length - 1); save(); renderFocus(); }, true);
    if (a === "add") return pickExercise(null, (ex) => {
      const cur = fxOrder(s)[s.fx];  // onde a pessoa estava (para voltar se ela tirar o exercício que acabou de pôr)
      s.items.push(newSessionItem(ex));
      const order2 = fxOrder(s);
      s.fxBack = cur === undefined ? null : order2.indexOf(cur);
      s.fx = order2.indexOf(s.items.length - 1); save(); renderFocus();
    }, { missing: s.missing });
  };
  const fin = $("#s-finish"); if (fin) fin.onclick = finishSession;
  const cancel = $("#s-cancel"); if (cancel) cancel.onclick = () => { if (confirm(s.edit_id ? "Cancelar a correção? O registro fica como estava." : "Descartar este treino?")) { store("gg_session", null); state.session = null; fxLeave(); location.hash = "#hoje"; } };
}

/* ---- timer de descanso ---- */
let timerEnd = 0, timerInt;
function startTimer(sec, from) {
  timerEnd = Date.now() + sec * 1000; $("#timer").hidden = false; document.body.classList.add("timer-on"); tick();
  clearInterval(timerInt); timerInt = setInterval(tick, 250);
  // deixa a próxima série (ou o próximo exercício) visível acima do cronômetro
  requestAnimationFrame(() => {
    const all = $$("#view [data-done]"); const k = from ? all.indexOf(from) : -1;
    const next = all.slice(k + 1).find((b) => !b.classList.contains("on"));
    if (!next) return;
    const row = next.closest("tr") || next; const rb = row.getBoundingClientRect(); const top = $("#timer").getBoundingClientRect().top;
    if (rb.bottom > top - 12 || rb.top < 60) window.scrollBy({ top: rb.bottom - (top - 12), behavior: "smooth" });
  });
}
function stopTimer() { clearInterval(timerInt); $("#timer").hidden = true; document.body.classList.remove("timer-on"); }
function tick() {
  const left = Math.max(0, Math.round((timerEnd - Date.now()) / 1000));
  $("#timer-value").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  if (left <= 0) { stopTimer(); try { navigator.vibrate && navigator.vibrate([200, 100, 200]); } catch (_) { /* sem vibração */ } toast("Descanso terminado — próxima série!"); }
}
$("#timer").addEventListener("click", (e) => {
  const a = e.target.dataset.timer; if (!a) return;
  if (a === "stop") return stopTimer();
  timerEnd += (+a) * 1000; tick();
});

/* ------------------------------------------------------------- ANÁLISE -- */
async function viewAnalise() {
  const [rep, sessions, hist, ld, fp, wr, prt] = await Promise.all([api("/api/report/weekly"), api("/api/sessions?limit=30"),
    api("/api/history").catch(() => ({ exercises: [], recent_prs: [] })), api("/api/load?days=90").catch(() => null),
    api("/api/focus/progress").catch(() => null), api("/api/watch/review").catch(() => null), actAs() ? null : api("/api/partners").catch(() => null)]);
  const pName = Object.fromEntries(((prt && prt.people) || []).map((p) => [p.id, p.first]));
  const c = rep.cardio;
  view.innerHTML = `${focusCard(fp)}
  <a class="card link-card row between gap" href="#saude"><span><b>😴 Saúde e sono</b><br><span class="small muted">Sono, recuperação, FC de repouso, HRV, VO2máx e o efeito dos treinos — pelo relógio</span></span><span class="chev">›</span></a>
  ${loadCard(ld)}
  ${watchReviewCard(wr)}
  ${historyCard(hist)}
  <div class="card"><h2>Volume por músculo — últimos 7 dias</h2>${muscleBars(rep.muscles, "sets_7d")}
    <details><summary class="small">Detalhes por músculo</summary><ul class="list small">${rep.muscles.map((m) => `<li><b>${esc(m.pt)}</b>: ${m.sets_7d} séries (${m.direct_7d} diretas, ${m.indirect_7d} indiretas) · média 4 sem. ${m.sets_avg_4w} · ${m.frequency_7d}× na semana · ${m.days_since === null ? "nunca treinado" : `há ${m.days_since} dias`} · tonelagem ${m.tonnage_7d} kg</li>`).join("")}</ul></details></div>
  <div class="card"><h2>O que ajustar</h2>${recsHtml(rep.recommendations)}</div>
  <div class="card"><h2>Equilíbrio muscular</h2>${balanceHtml(rep.balance)}</div>
  <div class="card"><h2>Cardio</h2>
    <div class="grid2"><div><div class="kpi">${c.minutes_total} min</div><div class="small muted">total na semana</div></div>
      <div><div class="kpi">${c.kcal_estimate} kcal</div><div class="small muted">estimativa (MET)</div></div></div>
    <p class="small">Leve ${c.light} · Moderado ${c.moderate} · Vigoroso ${c.vigorous} min → <b>${c.who_equivalent}</b> min equivalentes.<br>${esc(c.message)}</p>
    <div class="meter"><span style="width:${Math.min(100, c.who_equivalent / 3)}%"></span></div>
    <p class="small muted">${Object.entries(c.by_machine).map(([k, v]) => `${esc(k)}: ${v} min`).join(" · ")}</p></div>
  <div class="card"><h2>Histórico</h2><p class="small muted" style="margin-top:0">Toque num treino para ver o que ficou salvo e corrigir.</p>${sessions.length ? `<ul class="list">${sessions.map((s) => { const nl = s.items.reduce((a, it) => a + (it.sets ? it.sets.filter((x) => (x.load_kg == null || x.load_kg === "") && !x.duration_s).length : 0), 0);
      return `<li class="row between gap"><span class="grow sess-row" data-myses="${s.id}"><b>${esc(s.day_name || s.notes || "Treino")}</b> <span class="small muted">${fmtDate(s.date)} ${s.source === "garmin" ? "· Garmin" : ""}</span>${s.with_partner && pName[s.with_partner] ? ` <span class="pill ok">com ${esc(pName[s.with_partner])}</span>` : ""}<br>
      <span class="small muted">${s.items.length} exercícios · ${s.items.reduce((a, it) => a + (it.sets ? it.sets.length : 0), 0)} séries${nl ? ` · <span class="no-load">${nl} sem carga</span>` : ""}</span></span>
      <button class="icon-btn" data-del="${s.id}" title="Excluir">🗑</button></li>`; }).join("")}</ul>` : `<p class="empty">Nenhum treino registrado.</p>`}</div>`;
  view.onclick = async (e) => {
    if (e.target.closest("#wr-open")) return openWatchReview(wr, viewAnalise);
    const ms = e.target.closest("[data-myses]"); if (ms && !e.target.closest("[data-ex]")) return openMySession(+ms.dataset.myses, viewAnalise);
    const h = e.target.closest("[data-hist]"); if (h) { location.hash = `#evolucao/${encodeURIComponent(h.dataset.hist)}`; return; }
    if (e.target.dataset.ex) return showExercise(e.target.dataset.ex);
    if (e.target.dataset.del && confirm("Excluir este registro?")) {
      const r = await api(`/api/sessions/${e.target.dataset.del}`, { method: "DELETE" });
      if (r && r.watch_kept) toast(`Apaguei o que você anotou. A atividade do relógio (${r.minutes} min) continua no histórico; apague também se não quiser.`, 7000);
      viewAnalise();
    }
  };
}

/* ---- relógio: séries sem nome e categorias que o app chutou ---- */
function watchReviewCard(w) {
  if (!w) return "";
  const open = w.categories.length || w.sessions.length;
  const answered = (w.confirmed || []).length || (w.labeled || []).length || (w.dismissed || []).length;
  if (!open && !answered) return "";
  if (!open) return `<section class="card wr-card"><div class="row between gap"><span><b>Do relógio: tudo conferido</b><br><span class="small muted">Dá para rever ou corrigir o que você respondeu.</span></span>
    <button class="btn sm ghost" id="wr-open">Rever</button></div></section>`;
  const parts = [];
  if (w.categories.length) parts.push(`${w.guess_sets} séries em que o relógio disse só o tipo (${w.categories.slice(0, 3).map((c) => esc(c.label.toLowerCase())).join(", ")}${w.categories.length > 3 ? "…" : ""})`);
  if (w.sessions.length) parts.push(`${w.sessions.reduce((a, s) => a + s.sets, 0)} séries recentes sem nome nenhum`);
  return `<section class="card wr-card"><h2>Do relógio: falta dizer o exercício</h2>
    <p class="small" style="margin-top:0">${parts.join(" e ")}. As que têm só o tipo contam no músculo do palpite; as sem nome contam apenas no total de séries. Com o exercício certo entram no volume por músculo e na evolução de cargas.</p>
    <button class="btn block" id="wr-open">Conferir em 1 minuto</button></section>`;
}
const setTxt = (x) => `${x.reps}×${x.load_kg ? fmtKg(x.load_kg).replace(" kg", "") : "—"}`;
async function openWatchReview(w0, onDone) {
  let w = w0, changed = false, busy = false, top = 0;
  const at = location.hash;
  const splits = new Set();  // "sessão:item:posição" onde a pessoa mandou dividir um bloco
  try { w = await api("/api/watch/review"); } catch (_) { /* usa o que a tela já tinha */ }
  const finish = () => { if (changed && onDone && location.hash === at) onDone(); };
  const scroller = () => $("#modal .modal-card");
  // As divisões que a pessoa fez valem até ela dar nome ao bloco. Depois de uma resposta os números de item mudam;
  // cada divisão é reencontrada pela ordem das séries sem nome do treino (e conferida pelas repetições/carga).
  const flat = (s) => s.blocks.flatMap((b) => b.sets.map((x) => ({ item: b.item, i: x.i, sig: `${x.reps}|${x.load_kg}` })));
  const keepSplits = (t) => {  // t = o que a resposta mexe: {session_id, item, from, to} (bloco que ganha nome) ou {session_id} (treino todo)
    const old = [...splits].map((k) => {
      const [sid, item, i] = k.split(":").map(Number); const s = w.sessions.find((x) => x.session_id === sid); const seq = s ? flat(s) : [];
      const o = seq.findIndex((x) => x.item === item && x.i === i);
      return o > 0 ? { sid, o, sig: seq[o].sig, prev: seq[o - 1].sig } : null; }).filter(Boolean);
    let cut = null;
    if (t && t.to != null) {
      const s = w.sessions.find((x) => x.session_id === t.session_id); const seq = s ? flat(s) : [];
      const a = seq.findIndex((x) => x.item === t.item && x.i === t.from);
      if (a >= 0) cut = [a, a + seq.filter((x) => x.item === t.item && x.i >= t.from && x.i < t.to).length];
    }
    return (nw) => {
      splits.clear();
      old.forEach((p) => {
        let o = p.o;
        if (t && t.session_id === p.sid) { if (!cut || (o >= cut[0] && o <= cut[1])) return; if (o > cut[1]) o -= cut[1] - cut[0]; }
        const s = ((nw && nw.sessions) || []).find((x) => x.session_id === p.sid); const seq = s ? flat(s) : [];
        if (o > 0 && seq[o] && seq[o].sig === p.sig && seq[o - 1].sig === p.prev) splits.add(`${p.sid}:${seq[o].item}:${seq[o].i}`);
      });
    };
  };
  // blocos como a pessoa vê: os do servidor, divididos onde ela tocou
  const blocksOf = (s) => s.blocks.flatMap((b) => {
    const cuts = b.sets.map((x) => x.i).filter((i, k) => k > 0 && splits.has(`${s.session_id}:${b.item}:${i}`));
    const edges = [b.from, ...cuts, b.to];
    return edges.slice(0, -1).map((from, k) => ({ item: b.item, from, to: edges[k + 1], sets: b.sets.filter((x) => x.i >= from && x.i < edges[k + 1]), suggestion: k === 0 ? b.suggestion : null }));
  });
  const draw = () => {
    const key = (s, b) => `${s.session_id}:${b.item}:${b.from}:${b.to}`;
    const body = openModal(`<h2>O que o relógio gravou</h2>
      ${w.categories.length ? `<h3>O relógio disse só o tipo</h3>
        <p class="small muted" style="margin-top:0">Ele reconhece "rosca" ou "supino", mas não qual. Diga o que costuma ser: vale para os treinos já importados e para os próximos. Dá para trocar depois.</p>
        <div class="stack">${w.categories.map((c) => `<div class="wr-cat"><div><b>${esc(c.label)}</b> <span class="small muted">· ${c.sets} ${c.sets === 1 ? "série" : "séries"} em ${c.sessions} ${c.sessions === 1 ? "treino" : "treinos"}</span></div>
          <div class="small">Hoje conta como <b>${esc(c.current.name)}</b>.</div>
          <div class="chips" style="margin-top:6px">${c.options.map((o, i) => `<button class="chip ${i ? "" : "on"}" data-wcat="${esc(c.category)}" data-ex="${esc(o.id)}">${i ? esc(o.name) : "✓ É isso mesmo"}</button>`).join("")}
            <button class="chip" data-wcat-pick="${esc(c.category)}">Outro…</button></div></div>`).join("")}</div>` : ""}
      ${w.sessions.length ? `<h3>Séries sem nome</h3>
        <p class="small muted" style="margin-top:0">O app separou onde a carga recomeça; cada bloco deve ser um exercício. Se um bloco juntou dois exercícios, <b>toque na série em que o segundo começa</b> para dividir. Se não lembrar, tudo bem.</p>
        ${w.sessions.map((s) => `<div class="wr-sess"><b>${ddmm(s.date)} · ${esc(s.title)}</b>
          ${s.planned ? `<div class="small muted">Treino planejado no dia: ${esc(s.planned)}</div>` : ""}
          ${blocksOf(s).map((b) => `<div class="wr-block"><div class="wr-sets">${b.sets.map((x, k) => `<button class="wr-set" ${k ? `data-wsplit="${s.session_id}:${b.item}:${x.i}" aria-label="Dividir o bloco na série ${setTxt(x)}"` : "disabled"}>${setTxt(x)}</button>`).join("")}</div>
            <div class="row gap wrap">${b.suggestion ? `<button class="btn sm" data-wlab="${key(s, b)}" data-ex="${esc(b.suggestion.id)}">Foi ${esc(b.suggestion.name)}</button>` : ""}
              <button class="btn sm ghost" data-wlab-pick="${key(s, b)}">${b.suggestion ? "Outro exercício" : "Escolher exercício"}</button></div></div>`).join("")}
          <button class="act" data-wdis="${s.session_id}" style="margin-top:8px">Não lembro deste treino</button></div>`).join("")}` : ""}
      ${(w.confirmed || []).length || (w.labeled || []).length || (w.dismissed || []).length ? `<h3>Já respondido</h3>
        <ul class="plain small wr-done">
          ${(w.confirmed || []).map((c) => `<li class="row between gap"><span>${esc(c.label)} = <b>${esc(c.exercise.name)}</b></span><button class="act" data-wcat-pick="${esc(c.category)}">Trocar</button></li>`).join("")}
          ${(w.labeled || []).map((l) => `<li class="row between gap"><span>${ddmm(l.date)} · ${esc(l.sets.map(setTxt).join("  "))} = <b>${esc(l.exercise.name)}</b></span><button class="act" data-wun="${l.session_id}:${l.item}">Desfazer</button></li>`).join("")}
          ${(w.dismissed || []).map((d) => `<li class="row between gap"><span>${ddmm(d.date)} · ${esc(d.title)} <span class="muted">(${d.sets} ${d.sets === 1 ? "série" : "séries"} sem nome)</span></span><button class="act" data-wask="${d.session_id}">Perguntar de novo</button></li>`).join("")}
        </ul>` : ""}
      ${!w.categories.length && !w.sessions.length ? `<p class="ok-txt">✓ Nada pendente: o que o relógio gravou já tem exercício.</p>` : ""}
      <button class="btn ghost block" id="wr-close" style="margin-top:14px">Fechar</button>`);
    closeModal.hook = finish;  // fechar no ×, tocando fora ou no botão: a tela de trás se atualiza
    scroller().scrollTop = top;  // redesenhar não joga a pessoa de volta para o topo
    const expectOf = (k) => { const [sid, item, from, to] = k.split(":").map(Number); const s = w.sessions.find((x) => x.session_id === sid);
      const b = s && blocksOf(s).find((x) => x.item === item && x.from === from && x.to === to);
      return { session_id: sid, item, from, to, expect: b ? b.sets.map((x) => [x.reps, x.load_kg]) : null }; };
    const call = async (path, payload, okMsg, touched) => {
      if (busy) return; busy = true; body.classList.add("busy");  // um pedido por vez: dois toques seguidos não se atropelam
      const remap = keepSplits(touched);
      try { const r = await api(path, { method: "POST", body: payload }); changed = true; w = r; remap(w); toast(okMsg(r), 3500); }
      catch (err) { toast(err.message, 5000); try { w = await api("/api/watch/review"); remap(w); } catch (_) { /* fica como está */ } }
      busy = false; body.classList.remove("busy"); draw();
    };
    const catMsg = (r) => `Anotado: ${r.exercise.name}${r.changed ? ` (${r.changed} ${r.changed === 1 ? "treino atualizado" : "treinos atualizados"})` : ""}`;
    const labMsg = (r) => `Anotado: ${r.exercise.name}`;
    body.onclick = (e) => {
      const t = e.target.closest("button"); if (!t || t.disabled || busy) return;
      if (t.id === "wr-close") return closeModal();
      const d = t.dataset;
      top = scroller().scrollTop;
      if (d.wsplit) { splits.add(d.wsplit); return draw(); }
      if (d.wcat) return call("/api/watch/category", { category: d.wcat, exercise_id: d.ex }, catMsg);
      if (d.wlab) { const b = expectOf(d.wlab); return call("/api/watch/label", { ...b, exercise_id: d.ex }, labMsg, b); }
      if (d.wdis) return call("/api/watch/dismiss", { session_id: +d.wdis }, () => "Ok, não pergunto mais sobre esse treino", { session_id: +d.wdis });
      if (d.wask) return call("/api/watch/dismiss", { session_id: +d.wask, undo: true }, () => "Voltei a perguntar sobre esse treino", { session_id: +d.wask });
      if (d.wun) { const [sid, item] = d.wun.split(":").map(Number); return call("/api/watch/unlabel", { session_id: sid, item }, () => "Desfeito: as séries voltaram a ficar sem nome", { session_id: sid }); }
      // "Outro…": o seletor de exercício abre por cima. Escolheu: grava e volta para cá; fechou sem escolher: volta para cá também
      const pick = (path, payload, okMsg, touched) => {
        let picked = false;
        closeModal.hook = null;
        pickExercise("strength", (ex) => { picked = true; call(path, { ...payload, exercise_id: ex.id }, okMsg, touched); });
        closeModal.hook = () => { if (!picked) setTimeout(draw, 0); };
      };
      if (d.wcatPick) return pick("/api/watch/category", { category: d.wcatPick }, catMsg);
      if (d.wlabPick) { const b = expectOf(d.wlabPick); return pick("/api/watch/label", b, labMsg, b); }
    };
  };
  draw();
}

/* ------------------------------------------------- EVOLUÇÃO DE CARGAS -- */
const METRIC = {
  carga: { label: "1RM estimado", unit: " kg", fmt: (v) => `${(+v).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} kg` },
  reps: { label: "Reps máximas", unit: " reps", fmt: (v) => `${v} reps` },
  tempo: { label: "Tempo máximo", unit: " s", fmt: (v) => fmtSec(v) },
};
const PR_TXT = { e1rm: "1RM est.", top_load: "carga", rep_load: "carga × reps", volume: "volume", max_reps: "reps", max_duration_s: "tempo" };
const HIST_STATUS = { progredindo: ["Progredindo", "ok"], estavel: ["Estável", ""], estagnado: ["Estagnado", "warn"], caindo: ["Caindo", "bad"], inicio: ["Começando", "muted"], irregular: ["Dados irregulares", "warn"] };
const fmtKg = (v) => `${(+v || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} kg`;
function fmtSec(v) { v = Math.round(v || 0); return v >= 60 ? `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")} min` : `${v} s`; }
function prText(p) {
  if (p.rep_pr && p.prs && p.prs.length === 1 && p.prs[0] === "rep_load") return `${fmtKg(p.rep_pr.load_kg)} para ${p.rep_pr.reps} reps`;
  if (p.metric === "carga" && p.best_set) return `${fmtKg(p.best_set.load_kg)} × ${p.best_set.reps}`;
  if (p.metric === "reps") return `${p.max_reps} reps`;
  return fmtSec(p.max_duration_s);
}
function pctTxt(v) { return v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toLocaleString("pt-BR")}%`; }
function spark(vals) {
  if (!vals || vals.length < 2) return `<svg class="spark" viewBox="0 0 80 24" aria-hidden="true"></svg>`;
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * 78 + 1},${22 - ((v - mn) / r) * 20}`).join(" ");
  return `<svg class="spark" viewBox="0 0 80 24" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function historyCard(h) {
  if (!h.exercises.length) return `<div class="card"><h2>Evolução de cargas</h2><p class="empty">Registre treinos (no app ou importando do Garmin) para ver a evolução de cada exercício.</p></div>`;
  const prs = h.recent_prs.slice(0, 6);
  return `<div class="card"><h2>Evolução de cargas</h2>
    ${prs.length ? `<div class="pr-strip">${prs.map((p) => `<button class="pr-chip" data-hist="${esc(p.exercise_id)}">🏆 <b>${esc(p.name)}</b><span class="small muted">${ddmm(p.date)} · ${esc(prText(p))}</span></button>`).join("")}</div>` : ""}
    <ul class="list hist-list">${h.exercises.map((r) => { const sm = r.summary; const st = HIST_STATUS[sm.status] || ["", ""]; const m = METRIC[sm.metric] || METRIC.carga;
      return `<li class="hist-row" data-hist="${esc(r.exercise_id)}">
        <span class="grow"><b>${esc(r.name)}</b> <span class="pill small ${st[1]}">${st[0]}</span><br>
        <span class="small muted">${sm.sessions} treino${sm.sessions > 1 ? "s" : ""} · último ${ddmm(sm.last_date)} · 30 dias ${pctTxt(sm.change_30d_pct)}</span></span>
        <span class="hist-val">${spark(r.spark)}<b>${sm.last_value == null ? "—" : esc(m.fmt(sm.last_value))}</b></span></li>`; }).join("")}</ul>
    <p class="small muted">Valor principal: 1RM estimado (fórmula de Epley) para exercícios com carga; reps máximas no peso corporal; tempo máximo nos isométricos.</p></div>`;
}
function historyChart(points, metric) {
  if (points.length < 2) return `<p class="empty">O gráfico aparece a partir do segundo treino deste exercício.</p>`;
  const W = 340, H = 180, L = 40, R = 10, T = 12, B = 26;
  const day = (iso) => new Date(iso + "T12:00:00").getTime();
  const x0 = day(points[0].date), x1 = day(points[points.length - 1].date) || x0 + 1;
  const series = [points.map((p) => p.value)];
  if (metric === "carga") series.push(points.map((p) => p.top_load));
  const all = series.flat().filter((v) => v > 0);
  let mn = Math.min(...all), mx = Math.max(...all); const pad = (mx - mn) * 0.12 || mx * 0.05 || 1; mn = Math.max(0, mn - pad); mx += pad;
  const X = (iso) => L + ((day(iso) - x0) / ((x1 - x0) || 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - mn) / (mx - mn)) * (H - T - B);
  const path = (vals) => vals.map((v, i) => `${i ? "L" : "M"}${X(points[i].date).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const ticks = [mn, (mn + mx) / 2, mx];
  const m = METRIC[metric];
  return `<svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Gráfico de ${esc(m.label)} ao longo do tempo">
    ${ticks.map((t) => `<line x1="${L}" x2="${W - R}" y1="${Y(t)}" y2="${Y(t)}" class="grid"/><text x="${L - 4}" y="${Y(t) + 3}" text-anchor="end" class="axis">${Math.round(t)}</text>`).join("")}
    <text x="${L}" y="${H - 6}" class="axis">${ddmm(points[0].date)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end" class="axis">${ddmm(points[points.length - 1].date)}</text>
    ${series[1] ? `<path d="${path(series[1])}" class="line2"/>` : ""}
    <path d="${path(series[0])}" class="line1"/>
    ${points.map((p) => `<circle cx="${X(p.date)}" cy="${Y(p.value)}" r="${p.prs.length ? 4.5 : 3}" class="${p.prs.length ? "dot pr" : "dot"}"><title>${ddmm(p.date)}: ${m.fmt(p.value)}${p.best_set ? ` (${fmtKg(p.best_set.load_kg)} × ${p.best_set.reps})` : ""}${p.prs.length ? " — recorde" : ""}</title></circle>`).join("")}
  </svg>
  <div class="row gap small muted"><span><i class="lg lg1"></i>${esc(m.label)}</span>${series[1] ? `<span><i class="lg lg2"></i>Carga máxima</span>` : ""}<span><i class="lg lgpr"></i>Recorde</span></div>`;
}
async function viewEvolucao(id) {
  if (!id) return viewAnalise();  // sem exercício escolhido: a lista fica no Progresso
  const h = await api(`/api/history/${encodeURIComponent(id)}`);
  const sm = h.summary;
  if (!sm.sessions) { view.innerHTML = `<div class="card"><a href="#analise" class="small">← Análise</a><h2>${esc(h.name)}</h2><p class="empty">Ainda não há treinos registrados com este exercício.</p></div>`; return; }
  const m = METRIC[sm.metric] || METRIC.carga; const st = HIST_STATUS[sm.status] || ["", ""];
  const kpis = sm.metric === "carga"
    ? [["1RM estimado (melhor)", m.fmt(sm.best.e1rm)], ["Carga máxima", fmtKg(sm.best.top_load)], ["Maior volume", `${Math.round(sm.best.volume).toLocaleString("pt-BR")} kg`], ["Desde o início", pctTxt(sm.change_total_pct)]]
    : sm.metric === "reps" ? [["Reps máximas", `${sm.best.max_reps}`], ["Desde o início", pctTxt(sm.change_total_pct)], ["30 dias", pctTxt(sm.change_30d_pct)], ["Treinos", sm.sessions]]
    : [["Tempo máximo", fmtSec(sm.best.max_duration_s)], ["Desde o início", pctTxt(sm.change_total_pct)], ["30 dias", pctTxt(sm.change_30d_pct)], ["Treinos", sm.sessions]];
  const rows = [...h.points].reverse();
  view.innerHTML = `<div class="card"><a href="#analise" class="small">← Análise</a>
    <h2>${esc(h.name)}</h2><p class="small muted">${esc(h.primary_pt.join(", "))}</p>
    <p><span class="pill ${st[1]}">${st[0]}</span> <span class="small">${esc(sm.message)}</span></p>
    <div class="grid2 kpis">${kpis.map(([k, v]) => `<div><div class="kpi">${esc(v)}</div><div class="small muted">${esc(k)}</div></div>`).join("")}</div></div>
  ${sm.bodyweight ? `<p class="small muted card">O 1RM estimado soma o peso do corpo (${fmtKg(sm.bodyweight)}, do Perfil) com a anilha — assim barra só com o corpo e barra com peso entram na mesma curva.</p>` : ""}
  <div class="card"><h3>${esc(m.label)} ao longo do tempo</h3>${historyChart(h.points.filter((p) => !p.suspect), sm.metric)}</div>
  <div class="card"><h3>Treinos</h3><div class="table-wrap"><table class="hist-table"><thead><tr><th>Data</th><th>Melhor série</th><th>${sm.metric === "carga" ? "1RM est." : "Máx."}</th><th>Volume</th><th>Séries</th></tr></thead><tbody>
    ${rows.map((p) => `<tr${p.prs.length ? ' class="pr-row"' : p.suspect ? ' class="suspect-row"' : ""}><td>${ddmm(p.date)}${p.prs.length ? ` <span title="Recorde: ${p.prs.map((k) => PR_TXT[k]).join(", ")}">🏆</span>` : ""}${p.suspect ? (p.suspect_why === "sem_carga" ? ' <span title="Sem carga registrada (o relógio grava 0 kg quando o peso não é digitado). Não entra em recordes nem na tendência.">⚠</span>' : ' <span title="Fora da curva: parece erro de digitação ou exercício trocado pelo relógio. Não entra em recordes nem na tendência — corrija o treino se for erro.">⚠</span>') : ""}${p.source === "garmin" ? ' <span class="small muted" title="Importado do Garmin">G</span>' : ""}</td>
      <td>${p.best_set ? `${fmtKg(p.best_set.load_kg)} × ${p.best_set.reps}` : sm.metric === "tempo" ? fmtSec(p.max_duration_s) : `${p.max_reps} reps`}</td>
      <td>${esc(m.fmt(p.value))}${p.e1rm_weak ? '<span class="small muted" title="Muitas reps: estimativa menos precisa">*</span>' : ""}</td>
      <td>${p.volume ? Math.round(p.volume).toLocaleString("pt-BR") : "—"}</td>
      <td>${p.sets}</td></tr>`).join("")}
  </tbody></table></div><p class="small muted">🏆 = recorde · G = importado do Garmin${rows.some((p) => p.suspect) ? " · ⚠ = fora da curva ou sem carga registrada, ignorado nos recordes e na tendência" : ""}</p>${rows.some((p) => p.e1rm_weak) ? `<p class="small muted">* série com mais de 12 reps: o 1RM estimado é menos preciso.</p>` : ""}</div>`;
}

/* ------------------------------------------------- RESISTÊNCIA / ESPORTES -- */
const SPORT_ICON = { corrida: "🏃", ciclismo: "🚴", natacao: "🏊", remo: "🚣", caminhada: "🚶", trilha: "🥾", multi: "🏅" };
const STEP_PT = { warmup: "Aquecimento", cooldown: "Desaquecimento", interval: "Tiro", steady: "Contínuo", recovery: "Recuperação", rest: "Descanso" };
const UNIT_TXT = { km: "km", h: "h", m: "m" };
function fmtS(sec) { sec = Math.round(sec || 0); const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), x = sec % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
function parseDur(v) {
  if (v === null || v === undefined || v === "") return null;
  const parts = String(v).trim().replace(",", ".").split(":").map(Number);
  if (parts.some((x) => Number.isNaN(x))) return null;
  if (parts.length === 1) return parts[0] * 60; // só número = minutos
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] * 3600 + parts[1] * 60 + parts[2];
}
function distTxt(m, sport) { if (!m) return ""; return ["natacao", "remo"].includes(sport) ? `${Math.round(m)} m` : `${(m / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} km`; }
function endSummary(en) { return [distTxt(en.dist_m, en.sport), en.dur_s ? `~${fmtS(en.dur_s)}` : ""].filter(Boolean).join(" · "); }
function stepTxt(st, sport) {
  if (st.kind === "repeat") return `${st.n}× [ ${st.steps.map((x) => stepTxt(x, sport)).join(" | ")} ]`;
  const amount = st.dist_m && !(st.kind === "recovery" && sport !== "natacao") && sport !== "ciclismo" && sport !== "trilha" ? distTxt(st.dist_m, sport) : fmtS(st.dur_s);
  const tgt = st.target && st.target.label ? ` · ${st.target.label}` : "";
  return `${STEP_PT[st.kind] || st.kind} ${amount}${tgt}${st.note ? ` (${st.note})` : ""}`;
}
function endStepsHtml(en, open) {
  if (!en.steps) return "";
  return `<details ${open ? "open" : ""}><summary class="small">Passos e alvos</summary><ol class="small steps">${en.steps.map((st) => `<li>${esc(stepTxt(st, en.sport))}</li>`).join("")}</ol></details>`;
}
function paceTxt(it) {
  const en = it.endurance || {}; const secs = parseDur(it.time_str); const d = +it.dist_in;
  if (!secs || !d) return "";
  if (en.sport === "natacao") return `Ritmo: ${fmtS(secs / (d / 100))}/100 m`;
  if (en.sport === "remo") return `Split: ${fmtS(secs / (d / 500))}/500 m`;
  if (["ciclismo"].includes(en.sport)) return `Velocidade: ${(d / (secs / 3600)).toFixed(1)} km/h`;
  return `Ritmo: ${fmtS(secs / d)}/km`;
}
function endPayload(it) {
  const en = it.endurance; const meters = ["natacao", "remo"].includes(en.sport);
  const secs = parseDur(it.time_str) || en.dur_s;
  const out = { exercise_id: it.exercise_id, sport: en.sport, duration_min: Math.round((secs / 60) * 10) / 10, zone: en.zone };
  const d = it.dist_in !== null && it.dist_in !== undefined && it.dist_in !== "" ? +it.dist_in : (en.dist_m ? (meters ? en.dist_m : en.dist_m / 1000) : null);
  if (d) out.distance_km = meters ? d / 1000 : d;
  if (it.avg_hr) out.avg_hr = it.avg_hr;
  if (it.avg_power) out.avg_power = it.avg_power;
  return out;
}
function readinessCard(rd, hasToday = true, adjusted = false) {
  if (!rd) return "";
  if (rd.score === null || rd.score === undefined) {
    return state.garmin && state.garmin.connected ? `<div class="card flat small row between gap"><span>Prontidão de hoje: sem dados do relógio ainda.</span><button class="btn sm ghost" id="rd-refresh">Buscar no Garmin</button></div>` : "";
  }
  const cls = { seguir: "ok", moderar: "warn", trocar: "warn", descanso: "bad" }[rd.action] || "";
  return `<div class="card readiness ${cls}"><div class="row between gap"><h2 style="margin:0">Prontidão de hoje</h2><span class="kpi">${rd.score}</span></div>
    <p><span class="pill ${cls}">${esc({ alta: "Alta", moderada: "Moderada", baixa: "Baixa", muito_baixa: "Muito baixa" }[rd.level] || rd.level)}</span> ${esc(rd.text)}</p>
    <p class="small muted">${esc((rd.reasons || []).join(" · "))}${rd.sleep_h ? ` · ${rd.sleep_h} h de sono` : ""}${rd.rhr ? ` · FC repouso ${rd.rhr}` : ""}</p>
    <p class="small"><a href="#saude">😴 ${rd.night ? `Noite: ${esc(rd.night.text)} — ` : ""}ver saúde e sono ›</a></p>
    <div class="row gap wrap">${rd.action !== "seguir" && hasToday && !adjusted ? `<button class="btn sm" id="rd-apply">Ajustar o treino de hoje</button>` : ""}${adjusted ? `<span class="pill ok">Treino de hoje já ajustado</span>` : ""}<button class="btn sm ghost" id="rd-refresh">Atualizar</button></div></div>`;
}
function loadChart(series) {
  if (!series || series.length < 7) return "";
  const W = 340, H = 170, L = 30, R = 8, T = 10, B = 22;
  const vals = series.flatMap((p) => [p.ctl, p.atl, p.tsb]);
  const mn = Math.min(0, ...vals), mx = Math.max(10, ...vals);
  const X = (i) => L + (i / (series.length - 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - mn) / (mx - mn)) * (H - T - B);
  const path = (k) => series.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p[k]).toFixed(1)}`).join(" ");
  return `<svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Condicionamento, fadiga e forma nos últimos ${series.length} dias">
    <line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" class="grid"/>
    ${[mn, mx].map((t) => `<text x="${L - 4}" y="${Y(t) + 3}" text-anchor="end" class="axis">${Math.round(t)}</text>`).join("")}
    <text x="${L}" y="${H - 6}" class="axis">${ddmm(series[0].date)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end" class="axis">${ddmm(series[series.length - 1].date)}</text>
    <path d="${path("tsb")}" class="line-tsb"/><path d="${path("atl")}" class="line2"/><path d="${path("ctl")}" class="line1"/></svg>
    <div class="row gap small muted"><span><i class="lg lg1"></i>Condicionamento</span><span><i class="lg lg2"></i>Fadiga</span><span><i class="lg lgtsb"></i>Forma</span></div>`;
}
function loadCard(ld) {
  if (!ld) return "";
  const st = { risco: "bad", subida_rapida: "warn", produtivo: "ok", equilibrado: "ok", descansado: "", inicio: "muted" }[ld.status] || "";
  const by = Object.entries(ld.week.by_sport || {});
  return `<div class="card"><h2>Carga de treino (todas as modalidades)</h2>
    <div class="grid2 kpis"><div><div class="kpi">${Math.round(ld.ctl)}</div><div class="small muted">condicionamento (42 dias)</div></div>
      <div><div class="kpi">${Math.round(ld.atl)}</div><div class="small muted">fadiga (7 dias)</div></div>
      <div><div class="kpi">${ld.form > 0 ? "+" : ""}${Math.round(ld.form)}</div><div class="small muted">forma</div></div>
      <div><div class="kpi">${ld.ramp_week > 0 ? "+" : ""}${(+ld.ramp_week).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}</div><div class="small muted">variação em 7 dias</div></div></div>
    <p><span class="pill ${st}">${esc({ risco: "Risco", subida_rapida: "Subida rápida", produtivo: "Produtivo", equilibrado: "Equilibrado", descansado: "Descansado", inicio: "Começando" }[ld.status] || ld.status)}</span> <span class="small">${esc(ld.message)}</span></p>
    ${loadChart(ld.series)}
    ${by.length ? `<p class="small">Últimos 7 dias: ${by.map(([k, v]) => `${SPORT_ICON[k] || (k === "musculacao" ? "🏋️" : "❤️")} ${Math.round(v.tss)}${v.km ? ` (${v.km.toLocaleString("pt-BR")} km)` : ""}`).join(" · ")}</p>` : ""}
    <p class="small muted">Cada treino vira uma carga (100 = 1 h no limiar), por potência, ritmo, FC ou RPE da musculação. <a href="#esportes">Esportes e zonas</a></p></div>`;
}

function hybridCard(p) {
  const sp = p.splits;
  return `<div class="card"><h2>Prova híbrida · ${p.division === "pro" ? "Pro" : "Open"} ${p.sex === "F" ? "feminino" : "masculino"}</h2>
    <p>${p.predicted ? `Previsão hoje: <b>${esc(p.predicted)}</b>${p.target ? ` · alvo ${esc(p.target)}` : ""}` : "Registre um teste de corrida para ver a previsão."}
      ${p.run_km ? `<br><span class="small muted">Corrida ~${esc(p.run_km)} por km com a fadiga das estações · roxzone ~${esc(p.transitions)}</span>` : ""}</p>
    ${sp ? `<p class="small"><b>Para fazer ${esc(p.target)}:</b> cada km em ~${esc(sp.run_km)} (corrida ${esc(sp.run_total)}), estações ${esc(sp.stations_total)}, roxzone ${esc(sp.roxzone)}.</p>` : ""}
    <ul class="exlist hy-list">${p.stations.map((s, i) => `<li><span class="st-ic" aria-hidden="true">${i + 1}</span><span class="grow"><b>${esc(s.name)}</b><span class="small muted">${esc([s.standard, s.load].filter(Boolean).join(" · "))}</span></span>
      <span class="hy-times"><b>${esc(sp ? sp.stations[i].time : s.time)}</b><span class="small muted">${sp ? `previsão ${esc(s.time)}` : "previsão"}</span></span></li>`).join("")}</ul>
    <p class="small muted">Pesos da temporada 2025/26 — confira no regulamento oficial da sua prova. HYROX é marca da HYROX World GmbH; o HAX não tem afiliação, só prepara para o formato.</p></div>`;
}
async function viewEsportes() {
  const [opts, ov] = await Promise.all([api("/api/endurance/options"), api("/api/endurance/overview")]);
  const cfg = ov.config || {}; const sc = cfg.sports || {}; const ev = cfg.event || {};
  const wd = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
  const sportBox = (sp) => {
    const c = sc[sp.id] || {}; const g = c.goal || {}; const unit = UNIT_TXT[sp.unit];
    const recent = ov.recent && ov.recent[sp.id];
    return `<details class="sport-box" ${c.enabled ? "open" : ""}><summary><label class="row gap" style="margin:0;display:inline-flex"><input type="checkbox" data-sp="${sp.id}" data-k="enabled" ${c.enabled ? "checked" : ""}> ${SPORT_ICON[sp.id]} <b>${esc(sp.name)}</b></label></summary>
      <div class="grid2">
        <div class="field"><label>Onde</label><select data-sp="${sp.id}" data-k="env">${sp.envs.map((e) => `<option value="${e.id}" ${c.env === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Sessões por semana</label><input type="number" min="1" max="7" data-sp="${sp.id}" data-k="sessions" value="${c.sessions || sp.sessions}"></div>
        <div class="field"><label>Volume atual (${unit}/semana)</label><input type="number" step="any" min="0" data-sp="${sp.id}" data-k="current_weekly" value="${c.current_weekly ?? ""}" placeholder="${recent ? `Garmin: ${(+recent).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}` : "pelo nível"}"></div>
        <div class="field"><label>Dia do longo</label><select data-sp="${sp.id}" data-k="long_day"><option value="">automático</option>${wd.map((w, i) => `<option value="${i}" ${String(c.long_day) === String(i) ? "selected" : ""}>${w}</option>`).join("")}</select></div>
        ${sp.id === "natacao" ? `<div class="field"><label>Piscina (m)</label><select data-sp="${sp.id}" data-k="pool_m">${[25, 50, 20, 33].map((x) => `<option ${+(c.pool_m || 25) === x ? "selected" : ""}>${x}</option>`).join("")}</select></div>` : ""}
        <div class="field"><label>Meta</label><select data-sp="${sp.id}" data-k="goal_type">
          <option value="" ${!g.type ? "selected" : ""}>Sem meta (manter)</option><option value="volume" ${g.type === "volume" ? "selected" : ""}>Volume semanal</option>
          ${sp.race_distances.length ? `<option value="prova" ${g.type === "prova" ? "selected" : ""}>Prova (distância × tempo)</option>` : ""}</select></div>
        <div class="field goal-volume"><label>Meta de volume (${unit}/semana)</label><input type="number" step="any" data-sp="${sp.id}" data-k="weekly" value="${g.weekly ?? ""}"></div>
        <div class="field goal-race"><label>Distância da prova (${sp.id === "natacao" || sp.id === "remo" ? "m" : "km"})</label><input type="number" step="any" list="dl-${sp.id}" data-sp="${sp.id}" data-k="distance" value="${g.distance ?? ""}"><datalist id="dl-${sp.id}">${sp.race_distances.map((d) => `<option value="${d}">`).join("")}</datalist></div>
        <div class="field goal-race"><label>Tempo alvo (h:mm:ss)</label><input data-sp="${sp.id}" data-k="target" value="${esc(g.target ?? "")}" placeholder="ex.: 48:00"></div>
        <div class="field goal-race"><label>Data da prova</label><input type="date" data-sp="${sp.id}" data-k="date" value="${esc(g.date ?? "")}"></div>
      </div></details>`;
  };
  const pr = ov.predictions || {}; const prs = pr.sports || {};
  const anchorsHtml = ov.anchors.map((a) => `<li class="row between gap"><span><b>${esc(a.label)}</b>: ${esc(a.text)}${a.date ? ` <span class="small muted">(${esc(a.source || "")}, ${ddmm(a.date)})</span>` : ""}</span></li>`).join("");
  const sugHtml = (ov.suggestions || []).map((x, i) => `<li class="row between gap"><span><b>${esc(x.label)}</b>: ${esc(x.current_txt)} → <b>${esc(x.value_txt)}</b><br><span class="small muted">${esc(x.source)}</span></span><button class="btn sm" data-sug="${i}">Aplicar</button></li>`).join("");
  const zonesHtml = Object.entries(ov.zones || {}).map(([sp, zs]) => `<details><summary>${SPORT_ICON[sp]} Zonas – ${esc(opts.sports.find((x) => x.id === sp).name)}</summary>
      <table class="hist-table"><tbody>${zs.map((z) => `<tr><td>Z${z.zone}</td><td>${esc(z.name)}</td><td>${esc(z.label)}</td></tr>`).join("")}</tbody></table></details>`).join("");
  const goalTxt = (gx) => gx ? `Meta ${esc(gx.target || "—")} · previsão hoje <b>${esc(gx.predicted)}</b> — ${gx.status === "no_alvo" ? "já no alvo ✅" : gx.status === "possivel" ? "alcançável até a prova 👍" : `ambicioso: realista ~${esc(gx.realistic || "")}`}` : "";
  const evp = pr.event;
  view.innerHTML = `
  <div class="card"><h2>Esportes de resistência</h2><p class="small muted">Escolha as modalidades e metas: o gerador distribui junto com a musculação (longo no fim de semana, sessões fortes longe do longo e de perna pesada, ~80% leve).</p>
    <div class="stack" id="sp-list">${opts.sports.map(sportBox).join("")}</div></div>
  <div class="card"><h2>Prova multiesporte</h2><div class="grid2">
    <div class="field"><label>Evento</label><select id="ev-type"><option value="">Nenhum</option>${opts.events.map((e) => `<option value="${e.id}" ${ev.type === e.id ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></div>
    <div class="field"><label>Data</label><input type="date" id="ev-date" value="${esc(ev.date || "")}"></div>
    <div class="field"><label>Tempo alvo (h:mm:ss)</label><input id="ev-target" value="${esc(ev.target || "")}" placeholder="ex.: 2:45:00"></div>
    <div class="field" id="ev-div-wrap" ${ev.type === "hibrida" ? "" : "hidden"}><label>Categoria</label><select id="ev-div">${[["open", "Open"], ["pro", "Pro"], ["doubles", "Duplas"]].map(([v, t]) => `<option value="${v}" ${(ev.division || "open") === v ? "selected" : ""}>${t}</option>`).join("")}</select></div></div>
    <p class="small muted">O evento define as metas de natação, bike e corrida, a fase de cada semana e o brick (corrida logo após o pedal). <b>Prova híbrida</b> (formato HYROX): 8 × 1 km + 8 estações — o app troca a sessão forte por corrida comprometida, põe estações no treino de força e marca simulados a 6 e 3 semanas da prova.</p></div>
    ${evp && evp.stations ? hybridCard(evp) : ""}
  <div class="card"><h2>Opções</h2>
    <label class="row gap"><input type="checkbox" id="opt-tests" ${cfg.tests !== false ? "checked" : ""}> Agendar testes para calibrar as zonas (a cada ~8 semanas)</label>
    <label class="row gap"><input type="checkbox" id="opt-ready" ${cfg.readiness_auto ? "checked" : ""}> Ajustar o treino do dia sozinho pela prontidão do relógio (dá para desfazer)</label>
    <div class="row gap wrap" style="margin-top:8px"><button class="btn" id="end-save">Salvar</button><button class="btn ghost" id="end-gen">Salvar e regerar o treino</button></div></div>
  <div class="card"><h2>Referências de intensidade</h2><ul class="list small">${anchorsHtml}</ul>
    ${sugHtml ? `<h3>Sugestões de atualização</h3><ul class="list small">${sugHtml}</ul>` : ""}
    <details><summary>Registrar teste / valor</summary><div class="stack small">
      <div class="row gap wrap"><b>🏃 Corrida</b><input id="t-run-d" type="number" step="any" placeholder="km (ex. 5)" style="width:90px"><input id="t-run-t" placeholder="tempo 24:30" style="width:110px"><button class="btn sm" data-test="corrida">Calcular</button></div>
      <div class="row gap wrap"><b>🚴 Ciclismo</b><input id="t-bike-p" type="number" placeholder="média 20 min (W)" style="width:150px"><span>ou FTP</span><input id="t-bike-f" type="number" placeholder="W" style="width:80px"><button class="btn sm" data-test="ciclismo">Salvar</button></div>
      <div class="row gap wrap"><b>🏊 Natação</b><input id="t-sw-4" placeholder="400 m 7:20" style="width:110px"><input id="t-sw-2" placeholder="200 m 3:25" style="width:110px"><button class="btn sm" data-test="natacao">Calcular CSS</button></div>
      <div class="row gap wrap"><b>🚣 Remo</b><input id="t-row" placeholder="2 km 7:40" style="width:110px"><button class="btn sm" data-test="remo">Salvar</button></div>
      <div class="row gap wrap"><b>❤️ FC</b><input id="t-lthr" type="number" placeholder="limiar" style="width:90px"><button class="btn sm" data-test="lthr">Salvar</button><input id="t-hrmax" type="number" placeholder="máxima" style="width:90px"><button class="btn sm" data-test="hrmax">Salvar</button></div>
    </div></details>
    ${zonesHtml}</div>
  <div class="card"><h2>Previsões</h2>
    ${prs.corrida ? `<p>🏃 VDOT ${prs.corrida.vdot}: ${Object.entries(prs.corrida.times).map(([d, t]) => `${d} km ${esc(t)}`).join(" · ")}${prs.corrida.goal ? `<br><span class="small">${goalTxt(prs.corrida.goal)}</span>` : ""}</p>` : ""}
    ${prs.natacao ? `<p>🏊 CSS ${esc(prs.natacao.css)}: ${Object.entries(prs.natacao.times).map(([d, t]) => `${d} m ${esc(t)}`).join(" · ")}</p>` : ""}
    ${prs.ciclismo ? `<p>🚴 FTP ${prs.ciclismo.ftp} W (${prs.ciclismo.w_kg} W/kg): ${Object.entries(prs.ciclismo.speed_kmh).map(([k, v]) => `${esc(k)} ${v} km/h`).join(" · ")}</p>` : ""}
    ${prs.remo ? `<p>🚣 2 km ${esc(prs.remo["2k"])} (${esc(prs.remo.split)})</p>` : ""}
    ${evp ? `<p>🏅 <b>${esc(evp.name)}</b>${evp.date ? ` em ${ddmm(evp.date)}` : ""}: ${evp.predicted ? `previsão <b>${esc(evp.predicted)}</b>${evp.target ? ` (alvo ${esc(evp.target)}${evp.gap_s !== null ? `, ${evp.gap_s > 0 ? "+" : ""}${fmtS(Math.abs(evp.gap_s))}${evp.gap_s > 0 ? " acima" : " abaixo"}` : ""})` : ""}` : `faltam referências: ${esc(evp.missing_anchors.join(", "))}`}
      <br><span class="small muted">${evp.legs.map((l) => `${SPORT_ICON[l.sport]} ${l.distance}${l.sport === "natacao" ? " m" : " km"} ${esc(l.time || "—")}`).join(" · ")} · transições ${esc(evp.transitions)}</span></p>` : ""}
    ${!Object.keys(prs).length && !evp ? `<p class="empty">Registre um teste ou sincronize o Garmin para ver previsões.</p>` : ""}</div>`;
  const toggleGoals = () => $$(".sport-box").forEach((box) => {
    const t = $("[data-k=goal_type]", box).value;
    $$(".goal-volume", box).forEach((x) => { x.hidden = t !== "volume"; });
    $$(".goal-race", box).forEach((x) => { x.hidden = t !== "prova"; });
  });
  toggleGoals();
  const read = () => {
    const sportsOut = {};
    opts.sports.forEach((sp) => {
      const v = (k) => { const el = $(`[data-sp="${sp.id}"][data-k="${k}"]`); return el ? (el.type === "checkbox" ? el.checked : el.value) : undefined; };
      const gt = v("goal_type");
      const num = (x) => (x === "" || x === undefined ? null : +String(x).replace(",", "."));
      const goal = gt === "volume" ? { type: "volume", weekly: num(v("weekly")) } : gt === "prova" ? { type: "prova", distance: num(v("distance")), target: v("target") || null, date: v("date") || null } : {};
      sportsOut[sp.id] = { enabled: v("enabled"), env: v("env"), sessions: num(v("sessions")), current_weekly: num(v("current_weekly")),
        long_day: v("long_day") === "" ? null : +v("long_day"), goal, ...(sp.id === "natacao" ? { pool_m: num(v("pool_m")) } : {}) };
    });
    const et = $("#ev-type").value;
    return { sports: sportsOut, event: et ? { type: et, date: $("#ev-date").value || null, target: $("#ev-target").value || null, ...(et === "hibrida" ? { division: $("#ev-div").value } : {}) } : null,
      tests: $("#opt-tests").checked, readiness_auto: $("#opt-ready").checked };
  };
  view.onchange = (e) => { if (e.target.dataset.k === "goal_type") toggleGoals(); if (e.target.id === "ev-type") $("#ev-div-wrap").hidden = e.target.value !== "hibrida"; };
  view.onclick = async (e) => {
    const t = e.target.closest("button"); if (!t) return;
    try {
      if (t.id === "end-save" || t.id === "end-gen") {
        const body = read();
        if (body.event && !body.event.date) return toast("Informe a data do evento");
        await api("/api/endurance", { method: "PUT", body });
        state.profile = await api("/api/profile");
        toast("Esportes salvos");
        if (t.id === "end-gen") return openGenerator();
        return;
      }
      if (t.dataset.sug !== undefined) {
        const x = ov.suggestions[+t.dataset.sug];
        await api("/api/anchors", { method: "POST", body: { key: x.key, value: x.value, source: x.source } });
        toast(`${x.label} atualizado: ${x.value_txt}`); return viewEsportes();
      }
      if (t.dataset.test) {
        const k = t.dataset.test; let data = {};
        if (k === "corrida") data = { distance_km: +$("#t-run-d").value || 5, time: $("#t-run-t").value };
        if (k === "ciclismo") data = $("#t-bike-f").value ? { ftp: +$("#t-bike-f").value } : { power_20min: +$("#t-bike-p").value };
        if (k === "natacao") data = { t400: $("#t-sw-4").value, t200: $("#t-sw-2").value };
        if (k === "remo") data = { t2k: $("#t-row").value };
        if (k === "lthr") data = { value: +$("#t-lthr").value };
        if (k === "hrmax") data = { value: +$("#t-hrmax").value };
        const r = await api("/api/anchors/test", { method: "POST", body: { sport: k, data } });
        toast(`Referência salva: ${r.text}`); return viewEsportes();
      }
    } catch (err) { toast(err.message, 5000); }
  };
}

/* ---------------------------------------------------------- EXERCÍCIOS -- */
const EX_CHIPS = [["", "", "Todos"], ["machine", "", "Aparelhos"], ["cable", "", "Polia"], ["dumbbell", "", "Halteres"], ["barbell", "", "Barra"],
  ["body only", "", "Peso do corpo"], ["", "cardio", "Cardio"], ["", "stretching", "Alongamento"]];
async function viewExercicios() {
  const last = store("gg_ex_filter") || {};
  view.innerHTML = `<div class="ex-search">
    <input id="ex-q" type="search" placeholder="Buscar: supino, hack, remada, esteira…" value="${esc(last.q || "")}" autocomplete="off">
    <div class="chip-row" id="ex-chips">${EX_CHIPS.map(([e, k, t], i) => `<button type="button" class="fchip ${(last.e || "") === e && (last.k || "") === k ? "on" : ""}" data-i="${i}">${t}</button>`).join("")}</div>
    <div class="row gap">
      <select id="ex-m" class="grow"><option value="">Todos os músculos</option>${state.meta.muscles.map((m) => `<option value="${m.key}" ${last.m === m.key ? "selected" : ""}>${esc(m.pt)}</option>`).join("")}</select>
      <label class="row gap small hide-av" style="margin:0"><input type="checkbox" id="ex-hide" ${last.h ? "checked" : ""}> ocultar “evitar”</label>
    </div>
    <input type="hidden" id="ex-e" value="${esc(last.e || "")}"><input type="hidden" id="ex-k" value="${esc(last.k || "")}">
  </div>
  <p class="small muted" id="ex-count"></p>
  <ul class="ex-grid" id="ex-list"></ul>`;
  let t;
  const load = async () => {
    const f = { q: $("#ex-q").value, m: $("#ex-m").value, e: $("#ex-e").value, k: $("#ex-k").value, h: $("#ex-hide").checked };
    store("gg_ex_filter", f);
    const qs = new URLSearchParams({ q: f.q, limit: 120, hide_avoid: f.h });
    if (f.m) qs.set("muscle", f.m); if (f.e) qs.set("equipment", f.e); if (f.k) qs.set("kind", f.k);
    const res = await api(`/api/exercises?${qs}`);
    const list = $("#ex-list"); if (!list) return;  // saiu da tela antes da resposta chegar (4G lento)
    $("#ex-count").textContent = res.length ? `${res.length}${res.length >= 120 ? "+" : ""} exercícios` : "";
    list.innerHTML = res.map((ex) => `<li class="tile" data-ex="${esc(ex.id)}" title="${esc(ex.name)}">
      ${mediaHtml(ex.id, "tile")}${ex.limitation && ex.limitation.status !== "ok" ? `<span class="tile-lim ${ex.limitation.status}" data-ex="${esc(ex.id)}">${ex.limitation.status === "evitar" ? "evitar" : "cuidado"}</span>` : ""}
      ${ex.top_machine ? `<span class="tile-new" data-ex="${esc(ex.id)}">top</span>` : ""}
      <span class="tile-cap" data-ex="${esc(ex.id)}"><b data-ex="${esc(ex.id)}">${esc(ex.name)}</b><span data-ex="${esc(ex.id)}">${esc(ex.primary_pt.slice(0, 2).join(", "))}</span></span></li>`).join("") || `<li class="empty">Nada encontrado.</li>`;
  };
  view.oninput = () => { clearTimeout(t); t = setTimeout(load, 200); };
  view.onchange = view.oninput;
  view.onclick = (e) => {
    const c = e.target.closest(".fchip");
    if (c) {
      const [eq, k] = EX_CHIPS[+c.dataset.i]; $("#ex-e").value = eq; $("#ex-k").value = k;
      $$(".fchip", view).forEach((x) => x.classList.toggle("on", x === c)); return load();
    }
    const li = e.target.closest("[data-ex]"); if (li) showExercise(li.dataset.ex);
  };
  load();
}

/* ----------------------------------------------------------- APARELHOS -- */
const CAT_PICTO = { pernas: "squat", gluteo: "abduct", costas: "row", peito: "dumbbell", ombros: "yraise", bracos: "dumbbell", core: "dumbbell", cardio: "run", funcional: "sled" };
async function viewAparelhos(openId) {
  const last = store("gg_mx_filter") || {};
  const data = await api(`/api/machines`);
  view.innerHTML = `<div class="ex-search">
    <input id="mx-q" type="search" placeholder="Buscar: leg press 90, glúteo, marca do aparelho…" value="${esc(last.q || "")}" autocomplete="off">
    <div class="chip-row" id="mx-chips"><button type="button" class="fchip ${!last.c ? "on" : ""}" data-c="">Todos</button>${data.categories.map((c) => `<button type="button" class="fchip ${last.c === c.key ? "on" : ""}" data-c="${c.key}">${esc(c.pt)}</button>`).join("")}</div>
  </div>
  <p class="small muted" id="mx-count"></p>
  <ul class="mx-grid" id="mx-list"></ul>
  <p class="small muted" style="margin-top:14px">Vídeos do YouTube, tocados pelo player oficial, com o crédito de cada canal. Fotos de licença aberta (Wikimedia Commons).</p>`;
  const draw = () => {
    const q = $("#mx-q").value.trim(); const c = ($(".fchip.on", view) || {}).dataset ? $(".fchip.on", view).dataset.c : "";
    store("gg_mx_filter", { q, c });
    const norm = (x) => x.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    const words = norm(q).split(/\s+/).filter(Boolean);  // nome, apelido ("leg press 90"), marca ("total health") ou exercício
    const items = data.items.filter((m) => {
      if (c && m.category !== c) return false;
      if (!words.length) return true;
      const tok = (x) => x.split(/[^a-z0-9]+/).filter(Boolean);
      const nw = tok(m.s || norm(`${m.name} ${m.name_en || ""}`)); const bw = tok(m.b || ""); const qw = tok(words.join(" "));
      // por começo de palavra: "press" não casa com "descompressão"; marca só vale sozinha ("total health"), não misturada a pedaço de nome
      const inName = qw.map((w) => nw.some((n) => n.startsWith(w))); const inBrand = qw.map((w) => bw.some((b) => b.startsWith(w)));
      if (!qw.every((_, i) => inName[i] || inBrand[i])) return false;
      const phrase = qw.join(" ");
      return inName.some(Boolean) || (phrase.length >= 4 && (" " + bw.join(" ")).includes(" " + phrase));
    });
    $("#mx-count").textContent = `${items.length} aparelho${items.length === 1 ? "" : "s"}`;
    $("#mx-list").innerHTML = items.map((m) => `<li class="mx-card" data-machine="${esc(m.id)}">
      <span class="mx-img" data-machine="${esc(m.id)}">${m.thumb ? `<img loading="lazy" src="${esc(m.thumb)}" alt="" data-machine="${esc(m.id)}">` : pictoHtml(CAT_PICTO[m.category] || "dumbbell", "tile")}
        ${m.has_video ? `<span class="mx-play" aria-hidden="true">▶</span>` : ""}${m.community ? `<span class="tile-new">comunidade</span>` : m.is_new ? `<span class="tile-new">novo</span>` : ""}</span>
      <span class="mx-cap" data-machine="${esc(m.id)}"><b data-machine="${esc(m.id)}">${esc(m.name)}</b><span class="small muted" data-machine="${esc(m.id)}">${m.n_exercises} ${m.n_exercises === 1 ? "exercício" : "exercícios"}${m.availability_br === "rara" ? " · academias grandes" : ""}</span></span></li>`).join("")
      || `<li class="empty">Nada encontrado.</li>`;
  };
  view.oninput = draw;
  view.onclick = (e) => {
    const chip = e.target.closest(".fchip");
    if (chip) { $$(".fchip", view).forEach((x) => x.classList.toggle("on", x === chip)); return draw(); }
    const card = e.target.closest("[data-machine]"); if (card) showMachine(card.dataset.machine);
  };
  draw();
  if (openId) showMachine(decodeURIComponent(openId));
}
function ytEmbed(v) {
  // "lite embed": mostra a capa e só carrega o player do YouTube quando a pessoa toca (economiza dados e bateria)
  return `<div class="yt" data-yt="${esc(v.id)}" role="button" tabindex="0" aria-label="Tocar vídeo: ${esc(v.title || "demonstração")}">
    <img loading="lazy" src="https://i.ytimg.com/vi/${esc(v.id)}/hqdefault.jpg" alt=""><span class="yt-play" aria-hidden="true">▶</span></div>
    <p class="small muted yt-credit">${v.channel ? `Vídeo: ${esc(v.channel)}` : "Vídeo do YouTube"}${v.title ? ` · ${esc(v.title)}` : ""} · <a href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">abrir no YouTube</a></p>`;
}
// fabricantes: com a página do produto quando a pesquisa achou; avisa quando o aparelho ainda não foi conferido no site da marca
function machineBrandsHtml(m) {
  const bm = (m.brand_models || []).filter((b) => /^https:\/\//.test(b.url || ""));
  const linked = new Set(bm.map((b) => b.brand));
  const plain = (m.brands || []).filter((b) => !linked.has(b) && ![...linked].some((l) => l.includes(b) || b.includes(l)));
  if (!bm.length && !plain.length) return m.is_new && m.confirmed === false ? `<p class="small muted">Sem fabricante confirmado para este aparelho.</p>` : "";
  const seen = new Set(), byBrand = new Map();
  bm.forEach((b) => { const k = b.brand + "|" + b.url; if (seen.has(k)) return; seen.add(k); if (!byBrand.has(b.brand)) byBrand.set(b.brand, []); byBrand.get(b.brand).push(b); });
  const links = [], cut = [];  // até 2 páginas por marca e 14 no total; marca que não coube continua na lista, como texto
  byBrand.forEach((list, brand) => {
    if (links.length >= 14) return cut.push(brand);
    list.slice(0, 2).forEach((b) => links.push(`<a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.brand)}${b.model ? ` <span class="muted">(${esc(b.model)})</span>` : ""}</a>`));
  });
  return `<h3>Fabricantes</h3><p class="small mx-brands">${[...links, ...cut.map(esc), ...plain.map(esc)].join(" · ")}</p>
    ${bm.length ? `<p class="small muted">Páginas dos fabricantes encontradas em busca (nov/2026); modelos e endereços podem mudar.</p>` : ""}`;
}
async function showMachine(id) {
  const m = await api(`/api/machines/${encodeURIComponent(id)}`);
  const vids = m.videos || []; const embeds = vids.filter((v) => v.embed); const links = vids.filter((v) => !v.embed);
  const ph = (m.photos || [])[0];
  const body = openModal(`<h2>${esc(m.name)}</h2>${m.name_en && m.name_en !== m.name ? `<p class="small muted">${esc(m.name_en)}</p>` : ""}
    <p><span class="pill">${esc(m.category_pt || "")}</span> ${m.availability_br ? `<span class="pill muted">${m.availability_br === "comum" ? "comum no Brasil" : m.availability_br === "média" ? "academias médias e grandes" : "academias grandes e premium"}</span>` : ""}</p>
    ${embeds.length ? ytEmbed(embeds[0]) : ""}
    ${ph ? `<figure class="mx-photo"><img loading="lazy" src="${esc(ph.url)}" alt="Foto do aparelho ${esc(m.name)}"><figcaption class="small muted">Foto: ${esc(ph.author || (String(ph.url).startsWith("/api/photos/") ? "comunidade HAX" : "Wikimedia Commons"))}${ph.license ? ` · ${esc(ph.license)}` : ""}${ph.page ? ` · <a href="${esc(ph.page)}" target="_blank" rel="noopener">fonte</a>` : ""}</figcaption></figure>` : ""}
    ${m.why ? `<div class="evidence"><b>Por que vale a pena</b><p class="small">${esc(m.why)}</p></div>` : ""}
    ${(m.exercises || []).length ? `<h3>O que dá para fazer</h3><ul class="exlist mx-ex">${m.exercises.map((x) => `<li><span class="grow"><b>${esc(x.name)}</b><span class="small muted">${esc(x.how || "")}</span></span>
      ${x.video ? (x.video.embed ? `<button class="act" data-ytx="${esc(x.video.id)}" aria-label="Ver vídeo: ${esc(x.name)}">▶ vídeo</button>` : `<a class="act" href="https://www.youtube.com/watch?v=${esc(x.video.id)}" target="_blank" rel="noopener">▶ YouTube</a>`) : ""}
      ${x.exercise_id ? `<button class="act" data-exl="${esc(x.exercise_id)}" aria-label="Detalhes do exercício">›</button>` : ""}</li>`).join("")}</ul>` : ""}
    ${(m.linked || []).length ? `<div class="chips" style="margin:8px 0 4px">${m.linked.map((x) => `<button class="chip" data-exl="${esc(x.id)}">${esc(x.name)} ›</button>`).join("")}</div>` : ""}
    ${(m.settings || []).length ? `<h3>Regulagens</h3><ul class="plain small">${m.settings.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
    ${(m.tips || []).length ? `<h3>Dicas</h3><ul class="plain small">${m.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
    ${m.how_to_recognize ? `<h3>Como reconhecer na academia</h3><p class="small">${esc(m.how_to_recognize)}</p>` : ""}
    ${m.id_note ? `<p class="small muted">${esc(m.id_note)}</p>` : ""}
    ${embeds.length > 1 || links.length ? `<h3>Mais vídeos</h3><ul class="plain small">${[...embeds.slice(1), ...links].map((v) => `<li><a href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener">${esc(v.title || "Vídeo")}</a>${v.channel ? ` · ${esc(v.channel)}` : ""}</li>`).join("")}</ul>` : ""}
    ${machineBrandsHtml(m)}`);
  body.onclick = (e) => {
    const y = e.target.closest("[data-yt]");
    if (y) { y.outerHTML = `<div class="yt on"><iframe src="https://www.youtube-nocookie.com/embed/${esc(y.dataset.yt)}?autoplay=1&playsinline=1&rel=0" title="Vídeo do exercício" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe></div>`; return; }
    const ex = e.target.closest("[data-exl]"); if (ex) return showExercise(ex.dataset.exl);
    const yx = e.target.closest("[data-ytx]");
    if (yx) {
      // troca o vídeo de cima pelo da variação e sobe até ele
      const top = $(".yt", body); const html = `<div class="yt on"><iframe src="https://www.youtube-nocookie.com/embed/${esc(yx.dataset.ytx)}?autoplay=1&playsinline=1&rel=0" title="Vídeo da variação" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe></div>`;
      if (top) top.outerHTML = html; else body.querySelector("p").insertAdjacentHTML("afterend", html);
      $(".modal-card").scrollTo({ top: 0, behavior: "smooth" });
    }
  };
  body.onkeydown = (e) => { if (e.key === "Enter" && e.target.dataset.yt) e.target.click(); };
}

/* --------------------------------------------------------------- SAÚDE -- */
const LEVEL_PILL = { ok: ["bom", "ok"], atencao: ["atenção", "warn"], alerta: ["alerta", "bad"] };
const NIGHT_PILL = { bom: "ok", regular: "warn", ruim: "bad", sem_dados: "muted" };
const fmtH = (h) => { if (h == null) return "—"; const t = Math.round(h * 60); return `${Math.floor(t / 60)}h${String(t % 60).padStart(2, "0")}`; };
const nf = (v, d = 0) => (v == null ? "—" : (+v).toLocaleString("pt-BR", { maximumFractionDigits: d }));
const STATUS_TXT = { PRODUCTIVE: "Produtivo", MAINTAINING: "Mantendo", RECOVERY: "Recuperação", PEAKING: "No pico", OVERREACHING: "Sobrecarga",
  UNPRODUCTIVE: "Improdutivo", STRAINED: "Esgotado", DETRAINING: "Perdendo forma", NO: "Sem status" };

function stagesBar(sl) {
  const segs = [["deep_pct", "Profundo", "#3b5bdb"], ["light_pct", "Leve", "#74c0fc"], ["rem_pct", "REM", "#b197fc"]];
  const on = segs.filter((x) => sl[x[0]] != null);
  if (!on.length) return "";
  return `<div class="tbar" style="height:14px">${on.map((x) => `<span style="width:${sl[x[0]]}%;background:${x[2]}" title="${x[1]} ${nf(sl[x[0]])}%"></span>`).join("")}</div>
    <div class="small muted">${on.map((x) => `<span class="dot" style="background:${x[2]}"></span>${x[1]} ${nf(sl[x[0]])}%`).join(" ")}${sl.awake_min != null ? ` · acordado ${sl.awake_min} min` : ""}</div>`;
}

/* ---- passos: o total do relógio separado em treino × dia a dia; dia fora do costume pergunta o que foi ---- */
const fmtN = (n) => (n == null ? "—" : Math.round(n).toLocaleString("pt-BR"));
function stepsWhen(day, today) {
  const diff = Math.round((new Date(today + "T12:00:00") - new Date(day + "T12:00:00")) / 864e5);
  const wd = WD_LONG[new Date(day + "T12:00:00").getDay()];
  return diff === 1 ? "Ontem" : diff === 2 ? "Anteontem" : `${wd[0].toUpperCase()}${wd.slice(1)} (${ddmm(day)})`;
}
function stepsAskHtml(s, today, { note = true } = {}) {
  if (!s) return "";
  today = today || s.today || localISO().slice(0, 10);  // "ontem/anteontem" pela data do servidor (fuso de Brasília)
  const lab = Object.fromEntries((s.options || []).map((o) => [o.id, o.label]));
  const short = Object.fromEntries((s.options || []).map((o) => [o.id, o.short]));
  const q = (s.pending || []).map((p) => {
    const order = [p.hint, ...Object.keys(lab).filter((id) => id !== p.hint)].filter((id) => lab[id]);
    return `<div class="rec atencao q-card steps-q"><b>${stepsWhen(p.date, today)} você deu ${fmtN(p.daily)} passos fora dos treinos. O que foi?</b>
      <p class="small" style="margin:4px 0 8px">Seu costume é perto de ${fmtN(p.baseline)} por dia${p.workout ? ` (os ${fmtN(p.workout)} passos do treino desse dia já estão fora da conta)` : ""}. Com a resposta o app conta o esforço certo e não confunde esse dia com a sua rotina.</p>
      <div class="row gap wrap">${order.map((id, i) => `<button class="btn sm ${i ? "ghost" : ""}" data-stag="${p.date}:${id}">${esc(short[id] || lab[id])}</button>`).join("")}</div></div>`;
  }).join("");
  const ev = s.recent_event;
  const walked = ev && ev.counted;  // a caminhada estimada ainda está no histórico (a pessoa pode ter apagado)
  const noteHtml = note && ev && !q ? `<div class="rec small steps-note"><b>${stepsWhen(ev.date, today)}: ${esc(ev.label.toLowerCase())}</b> (${fmtN(ev.steps)} passos${ev.short_night ? ", e a noite foi curta" : ev.late_night ? ", e você foi dormir tarde" : ""}).
    ${walked ? "Já entrou na sua carga como caminhada leve." : "Não conta como treino, mas explica um cansaço a mais."} Se as pernas estiverem pesadas hoje, baixe a carga ou encurte o treino: um dia mais leve não atrapalha a semana.</div>` : "";
  return q + noteHtml;
}
async function answerSteps(v) {
  const [day, tag] = v.split(":");
  return api("/api/steps/tag", { method: "POST", body: { date: day, tag: tag === "limpar" ? null : tag } });
}
function stepsTagToast(r) {
  const t = r && r.tag;
  if (!t) return "Resposta desfeita";
  if (t.session_id) return `Anotado. Entrou como caminhada leve de ${fmtDur(t.minutes)} na sua carga.`;
  return t.tag === "nada" ? "Anotado: dia normal. Vou perguntar menos sobre dias assim." : "Anotado. Esse dia não conta como treino.";
}
const fmtDur = (min) => (min >= 60 ? `${Math.floor(min / 60)} h${min % 60 ? ` ${String(Math.round(min % 60)).padStart(2, "0")}` : ""}` : `${Math.round(min)} min`);
function stepsChart(s) {
  const ser = s.series || [];
  if (ser.length < 3) return "";
  const W = 340, H = 130, L = 46, Rm = 8, T = 10, B = 20;  // margem que cabe "12,5 mil"
  const hi = Math.max(...ser.map((p) => p.total), s.baseline || 0, 1000);
  const X = (i) => L + ((i + 0.5) / ser.length) * (W - L - Rm);
  const Y = (v) => T + (1 - v / (hi * 1.08)) * (H - T - B);
  const bw = Math.max(3, (W - L - Rm) / ser.length - 3);
  const bar = (i, from, to, fill) => (to > from ? `<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${Y(to).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0.5, Y(from) - Y(to)).toFixed(1)}" rx="1.5" fill="${fill}"/>` : "");
  return `<svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Passos por dia nas últimas ${ser.length} dias: dia a dia, treinos e eventos">
    ${s.baseline ? `<line x1="${L}" x2="${W - Rm}" y1="${Y(s.baseline)}" y2="${Y(s.baseline)}" class="grid" stroke-dasharray="4 3"/>` : ""}
    ${ser.map((p, i) => { const ev = (p.parts || {}).evento || 0; const wk = p.workout - ev;
      return bar(i, 0, p.daily, "var(--brand)") + bar(i, p.daily, p.daily + wk, "var(--ok)") + bar(i, p.daily + wk, p.total, "var(--warn)"); }).join("")}
    <text x="${L - 4}" y="${Y(hi) + 4}" text-anchor="end" class="axis">${hi >= 10000 ? nf(hi / 1000, 0) + " mil" : nf(hi / 1000, 1) + " mil"}</text>
    <text x="${L - 4}" y="${Y(0)}" text-anchor="end" class="axis">0</text>
    <text x="${L}" y="${H - 4}" class="axis">${ddmm(ser[0].date)}</text><text x="${W - Rm}" y="${H - 4}" text-anchor="end" class="axis">${ddmm(ser[ser.length - 1].date)}</text>
  </svg>
  <div class="small muted"><span class="dot" style="background:var(--brand);margin-left:0"></span>dia a dia <span class="dot" style="background:var(--ok)"></span>treinos (corrida, caminhada)${ser.some((p) => (p.parts || {}).evento) ? ` <span class="dot" style="background:var(--warn)"></span>evento` : ""}${s.baseline ? " · tracejado: seu costume" : ""}</div>`;
}
function stepsCardHtml(s, today) {
  if (!s || !s.has_data) return "";
  const a = s.avg7 || {}, prev = (s.avg_prev || {}).daily;
  const delta = a.daily != null && prev ? (a.daily - prev) / prev : null;
  const trend = delta == null ? "" : Math.abs(delta) < 0.1 ? "no mesmo nível das 4 semanas anteriores" : `${delta > 0 ? "acima" : "abaixo"} das 4 semanas anteriores (${fmtN(prev)}/dia)`;
  const tags = Object.entries(s.tags || {}).sort((x, y) => (x[0] < y[0] ? 1 : -1)).slice(0, 6);
  return `<div class="card" id="steps-card"><h2>Passos: dia a dia × treinos</h2>
    <p class="small muted" style="margin-top:0">O total do relógio mistura a corrida ou a caminhada com o resto do dia. Aqui eles ficam separados, para mostrar se a sua rotina ficou mais ativa e não só se você correu.</p>
    <div class="grid2">
      <div><div class="kpi">${fmtN(a.daily)}</div><div class="small muted">passos/dia no dia a dia (7 dias)${trend ? ` · ${trend}` : ""}</div></div>
      <div><div class="kpi">${fmtN(a.workout)}</div><div class="small muted">passos/dia vindos de treinos${s.estimated ? " (parte estimada pela duração)" : ""}</div></div>
    </div>
    ${stepsChart(s)}
    ${stepsAskHtml(s, today, { note: false })}
    ${tags.length ? `<div class="small" style="margin-top:10px"><b>Dias que você explicou</b></div><ul class="plain small steps-tags">${tags.map(([d, t]) => `<li class="row between gap"><span>${ddmm(d)} · ${esc(t.label || t.tag)} <span class="muted">(${fmtN(t.steps)} passos${t.minutes ? `, contou ${fmtDur(t.minutes)} de caminhada leve` : ""})</span></span><button class="act" data-stag="${d}:limpar">Desfazer</button></li>`).join("")}</ul>` : ""}
    <p class="small muted" style="margin-bottom:0">Quando o dia a dia passa muito do seu costume${s.threshold ? ` (hoje, acima de ${fmtN(s.threshold)} passos)` : ""} sem treino que explique, o app pergunta se foi festival, passeio ou outra coisa. Festival, passeio e caminhada entram na sua carga como caminhada leve.</p></div>`;
}

function healthChart(series, key, { label, unit = "", band = null, ref = null, bars = false, color = "var(--brand)" } = {}) {
  const pts = series.map((p, i) => [i, p[key]]).filter((p) => p[1] != null);
  if (pts.length < 3) return "";
  const W = 340, H = 120, L = 34, R = 8, T = 10, B = 20;
  const vals = pts.map((p) => p[1]).concat(band || []).concat(ref != null ? [ref] : []);
  const lo = bars ? 0 : Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.12 || 1;
  const mn = bars ? 0 : lo - pad, mx = hi + pad;
  const X = (i) => L + (i / Math.max(1, series.length - 1)) * (W - L - R);
  const Y = (v) => T + (1 - (v - mn) / (mx - mn)) * (H - T - B);
  const bw = Math.max(3, (W - L - R) / series.length - 3);
  return `<div class="small" style="margin-top:8px"><b>${esc(label)}</b></div>
  <svg class="hist-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)} nos últimos ${series.length} dias">
    ${band ? `<rect x="${L}" width="${W - L - R}" y="${Y(band[1])}" height="${Math.max(1, Y(band[0]) - Y(band[1]))}" fill="var(--ok-bg)"/>` : ""}
    ${ref != null ? `<line x1="${L}" x2="${W - R}" y1="${Y(ref)}" y2="${Y(ref)}" class="grid" stroke-dasharray="4 3"/>` : ""}
    ${bars ? pts.map(([i, v]) => `<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${Y(v).toFixed(1)}" width="${bw.toFixed(1)}" height="${(Y(0) - Y(v)).toFixed(1)}" rx="2" fill="${ref != null && v < ref ? "var(--warn)" : color}"/>`).join("")
    : `<path d="${pts.map(([i, v], k) => `${k ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}" class="line1" style="stroke:${color}"/>`}
    <text x="${L - 4}" y="${Y(hi) + 4}" text-anchor="end" class="axis">${nf(hi, 1)}</text>
    <text x="${L - 4}" y="${Y(lo) + 4}" text-anchor="end" class="axis">${nf(lo, 1)}</text>
    <text x="${L}" y="${H - 4}" class="axis">${ddmm(series[0].date)}</text><text x="${W - R}" y="${H - 4}" text-anchor="end" class="axis">${ddmm(series[series.length - 1].date)}</text>
  </svg>${unit ? `<div class="small muted">${esc(unit)}</div>` : ""}`;
}

async function viewSaude() {
  const h = await api("/api/health");
  const head = `<div class="card"><div class="row between gap wrap"><h2 style="margin:0">Do seu relógio</h2>
      ${h.garmin.connected ? `<button class="btn sm ghost" id="h-refresh">Atualizar do relógio</button>` : ""}</div>
    <p class="small muted">Sono, recuperação e condicionamento lidos do seu relógio Garmin — e como o treino está mexendo com eles.
      Não substitui avaliação médica: sintomas ou valores fora do normal por vários dias, procure um médico.</p></div>`;
  if (!h.has_data) {
    view.innerHTML = head + `<div class="card"><p>${h.garmin.connected
      ? "Ainda sem dados de saúde do relógio. Toque em <b>Atualizar do relógio</b> — o app busca as últimas semanas aos poucos."
      : "Conecte o Garmin em <a href=\"#perfil\">Perfil → Garmin</a>. Com o relógio no pulso também à noite, o app passa a mostrar sono, HRV, FC de repouso, estresse e VO2máx."}</p>
      <p class="small muted">HRV, Training Readiness e idade física dependem do modelo do relógio.</p></div>`;
  } else {
    const n = h.last_night;
    const wk = h.weekly || {};
    const extra = n ? [n.hrv != null ? `HRV ${nf(n.hrv)} ms` : "", n.rhr != null ? `FC de repouso ${nf(n.rhr)} bpm` : "",
      n.resp != null ? `respiração ${nf(n.resp, 1)}/min` : "", n.spo2 != null ? `SpO₂ ${nf(n.spo2)}%` : ""].filter(Boolean).join(" · ") : "";
    view.innerHTML = head + `
    ${(h.alerts || []).map((a) => `<div class="rec alerta">⚠ ${esc(a)}</div>`).join("")}
    ${n ? `<div class="card"><div class="row between gap"><h2 style="margin:0">Noite de ${ddmm(n.date)}</h2>
        <span class="kpi">${n.score != null ? nf(n.score) : fmtH(n.duration_h)}</span></div>
      <p><span class="pill ${NIGHT_PILL[n.level] || "muted"}">${esc({ bom: "Sono bom", regular: "Sono regular", ruim: "Sono ruim", sem_dados: "Sem dados" }[n.level] || n.level)}</span>
        ${fmtH(n.duration_h)} dormindo${n.start ? ` · ${esc(n.start)}${n.end ? `–${esc(n.end)}` : ""}` : ""} <span class="muted small">(ideal ${nf(h.need[0])}–${nf(h.need[1])} h)</span></p>
      ${stagesBar(n)}
      <ul class="small plain">${(n.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      ${extra ? `<p class="small muted">${extra}</p>` : ""}
      ${(n.tips || []).map((t) => `<p class="small rec">${esc(t)}</p>`).join("")}</div>` : ""}
    ${h.areas.length ? `<div class="card"><h2>Como você está</h2><div class="stack">
      ${h.areas.map((a) => `<div class="area"><div class="row between gap"><b>${esc(a.title)}</b><span class="pill ${LEVEL_PILL[a.level][1]}">${LEVEL_PILL[a.level][0]}</span></div>
        <div class="small">${esc(a.text)}</div>${a.tip ? `<div class="small muted">${esc(a.tip)}</div>` : ""}</div>`).join("")}
    </div></div>` : ""}
    <div class="card"><h2>Efeitos do seu treino</h2>${(h.effects || []).length ? `<ul class="small">${h.effects.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : `<p class="small muted">Sem dados suficientes ainda.</p>`}
      <p class="small muted">Compara as noites depois de dias de treino pesado com as noites depois de dias leves ou de folga (últimas 8 semanas).</p></div>
    <div class="card"><h2>Últimas 4 semanas</h2>
      ${healthChart(h.series, "sleep_h", { label: "Horas de sono", bars: true, ref: h.need[0], unit: `Tracejado: mínimo recomendado (${nf(h.need[0])} h). Laranja = noite curta.` })}
      ${healthChart(h.series, "sleep_score", { label: "Nota do sono", ref: 80 })}
      ${healthChart(h.series, "hrv", { label: "HRV noturna (ms)", band: h.hrv && h.hrv.low != null && h.hrv.high != null ? [h.hrv.low, h.hrv.high] : null, unit: h.hrv && h.hrv.low != null ? "Faixa verde: o seu normal. Abaixo dela vários dias seguidos = recuperação incompleta." : "" })}
      ${healthChart(h.series, "rhr", { label: "FC de repouso (bpm)", ref: h.rhr ? h.rhr.base : null, color: "var(--bad)", unit: "Subir 5+ bpm acima da sua base (tracejado) costuma indicar fadiga, estresse ou início de doença." })}
      ${healthChart(h.series, "stress", { label: "Estresse médio (0–100)", color: "var(--warn)" })}
    </div>
    ${stepsCardHtml(h.steps)}
    ${Object.keys(wk).length ? `<div class="card"><h2>Indicadores do relógio</h2><div class="grid2">
      ${wk.vo2max ? `<div><div class="kpi">${nf(wk.vo2max, 1)}</div><div class="small muted">VO2máx${wk.vo2max_bike ? ` (bike ${nf(wk.vo2max_bike, 1)})` : ""}</div></div>` : ""}
      ${wk.fitness_age ? `<div><div class="kpi">${nf(wk.fitness_age, 1)}</div><div class="small muted">idade física${wk.chron_age ? ` (real ${nf(wk.chron_age)})` : ""}</div></div>` : ""}
      ${wk.training_status ? `<div><div class="kpi" style="font-size:18px">${esc(STATUS_TXT[wk.training_status] || wk.training_status)}</div><div class="small muted">status de treino</div></div>` : ""}
      ${wk.weight_kg ? `<div><div class="kpi">${nf(wk.weight_kg, 1)} kg</div><div class="small muted">peso${wk.body_fat ? ` · ${nf(wk.body_fat, 1)}% gordura` : ""}</div></div>` : ""}
    </div></div>` : ""}`;
  }
  const rb = $("#h-refresh");
  if (rb) rb.onclick = async () => { rb.disabled = true; try { await api("/api/health/refresh", { method: "POST" }); toast("Dados do relógio atualizados"); viewSaude(); } catch (e) { rb.disabled = false; toast(e.message, 5000); } };
  const sc = $("#steps-card");
  if (sc) sc.onclick = async (ev) => {
    const b = ev.target.closest("[data-stag]"); if (!b || b.disabled) return;
    b.disabled = true;
    try { toast(stepsTagToast(await answerSteps(b.dataset.stag)), 5000); viewSaude(); } catch (e) { b.disabled = false; toast(e.message, 5000); }
  };
}

/* -------------------------------------------------------------- PERFIL -- */
// o mínimo para o app montar um treino que faça sentido
const PROFILE_REQ = [["name", "nome"], ["age", "idade"], ["weight_kg", "peso"], ["sex", "sexo"], ["goal", "objetivo"], ["training_months", "tempo de treino"], ["available_days", "dias"]];
function profileMissing(p) {
  p = p || {};
  return PROFILE_REQ.filter(([k]) => k === "available_days" ? !(p[k] || []).length : k === "name" ? !p.name || p.name === "Eu" : p[k] == null || p[k] === "");
}
async function viewPerfil(openSec) {
  const p = await api("/api/profile");
  state.profile = p;
  const ul = Object.fromEntries((p.limitations || []).map((l) => [l.key, l]));
  const custom = (p.limitations || []).find((l) => l.key === "personalizado") || {};
  const g = await api("/api/garmin/status");
  const num = (k, label, extra = "") => `<div class="field"><label>${label}</label><input type="number" data-p="${k}" value="${esc(p[k] ?? "")}" ${extra}></div>`;
  state.me = await api("/api/auth/me").catch(() => state.me);
  const [acctHtml, coachHtml, devHtml, partnerHtml] = [await accountCard(), await coachMineCard(), await devicesCard(), await partnerMineCard()];
  const canBackup = !(state.me && state.me.logged_in && !state.me.user.is_admin);
  const miss = profileMissing(p);
  const filled = PROFILE_REQ.length - miss.length;
  const firstName = String(p.name || "").trim();
  const sect = (id, icon, title, sub, body, open = false) => `<details class="sect" id="sec-${id}" ${open || openSec === id ? "open" : ""}>
    <summary><span class="sect-ico" aria-hidden="true">${icon}</span><span class="grow"><b>${title}</b><span class="small muted">${sub}</span></span><span class="chev" aria-hidden="true">›</span></summary>
    <div class="sect-body">${body}</div></details>`;
  const GOAL_TXT = { hipertrofia: "Hipertrofia", forca: "Força", emagrecimento: "Emagrecimento", condicionamento: "Condicionamento", saude: "Saúde geral", hibrido: "Híbrido" };
  const nDays = (p.available_days || []).length;
  const lims = (p.limitations || []).filter((l) => l.key !== "personalizado");
  view.innerHTML = `
  <section class="prof-head">
    <span class="avatar xl" aria-hidden="true">${esc(firstName && firstName !== "Eu" ? firstName.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() : "?")}</span>
    <div class="grow"><b class="prof-name">${esc(firstName && firstName !== "Eu" ? firstName : "Seu perfil")}</b>
      <span class="small muted">${[GOAL_TXT[p.goal], nDays ? `${nDays} dias` : "", p.session_minutes ? `${p.session_minutes} min` : ""].filter(Boolean).join(" · ") || "Complete para o app montar seu treino"}</span>
      <div class="meter" style="margin-top:8px"><span style="width:${Math.round(100 * filled / PROFILE_REQ.length)}%"></span></div>
      <span class="small ${miss.length ? "muted" : "ok-txt"}">${miss.length ? `Falta: ${miss.map((m) => m[1]).join(", ")}` : "Perfil completo ✔"}</span></div>
  </section>
  ${installCardHtml("perfil")}
  ${state.me && state.me.user && state.me.user.is_admin ? `<button class="link-card sect dev-link" id="fam-open" type="button"><span class="sect-ico" aria-hidden="true">👨‍👩‍👧</span><span class="grow"><b>Chamar a família</b><span class="small muted">mensagem pronta no WhatsApp: cada um cria a conta sozinho</span></span><span class="chev" aria-hidden="true">›</span></button>` : ""}
  ${state.me && state.me.logged_in ? `<button class="link-card sect dev-link" data-feedback type="button"><span class="sect-ico" aria-hidden="true">💬</span><span class="grow"><b>Mandar sugestão ou problema</b><span class="small muted">o que achou, o que faltou, o que deu errado</span></span><span class="chev" aria-hidden="true">›</span></button>` : ""}
  ${state.me && state.me.user && state.me.user.is_admin ? `<a class="link-card sect dev-link" href="#dev"><span class="sect-ico" aria-hidden="true">🛠️</span><span class="grow"><b>Área do desenvolvedor</b><span class="small muted" id="dev-count">fotos de aparelhos para aprovar</span></span><span class="chev" aria-hidden="true">›</span></a>` : ""}
  ${sect("dados", "👤", "Seus dados", [p.age ? `${p.age} anos` : "", p.weight_kg ? `${p.weight_kg} kg` : "", p.height_cm ? `${p.height_cm} cm` : ""].filter(Boolean).join(" · ") || "nome, idade, peso, altura, FC",
    `<div class="grid2">
      <div class="field"><label>Nome</label><input data-p="name" value="${esc(p.name && p.name !== "Eu" ? p.name : "")}" placeholder="Como quer ser chamado" autocomplete="given-name"></div>
      <div class="field"><label>Sexo</label><select data-p="sex"><option value=""></option><option value="M" ${p.sex === "M" ? "selected" : ""}>Masculino</option><option value="F" ${p.sex === "F" ? "selected" : ""}>Feminino</option></select></div>
      ${num("age", `Idade${(p._from_garmin || []).includes("age") ? ' <span class="muted">(do Garmin)</span>' : ""}`, 'inputmode="numeric"')}
      ${num("weight_kg", `Peso (kg)${(p._from_garmin || []).includes("weight_kg") ? ' <span class="muted">(do Garmin)</span>' : ""}`, 'step="0.1" inputmode="decimal"')}
      ${num("height_cm", "Altura (cm)", 'inputmode="numeric"')}
      ${num("hr_rest", "FC de repouso", 'inputmode="numeric" placeholder="do relógio"')}
      ${num("hr_max", `FC máx. medida <span class="muted">(calculada: ${p.hr_max_calc ?? "—"})</span>`, 'inputmode="numeric" placeholder="se souber"')}
    </div>
    <p class="small muted">Idade e peso calculam suas zonas de frequência cardíaca e as cargas iniciais. Com o Garmin conectado, eles vêm do relógio.</p>`, miss.some((m) => ["age", "weight_kg", "sex", "name"].includes(m[0])))}
  ${sect("treino", "🏋️", "Treino e rotina", [GOAL_TXT[p.goal], nDays ? `${nDays} dias/semana` : "", p.session_minutes ? `${p.session_minutes} min` : ""].filter(Boolean).join(" · ") || "objetivo, dias, tempo, academia",
    `<div class="grid2">
      <div class="field"><label>Objetivo</label><select data-p="goal">${[["hipertrofia", "Hipertrofia"], ["forca", "Força"], ["emagrecimento", "Emagrecimento"], ["condicionamento", "Condicionamento"], ["saude", "Saúde geral"], ["hibrido", "Híbrido (força + resistência)"]].map(([v, t]) => `<option value="${v}" ${p.goal === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Há quanto tempo treina</label><select data-p="training_months"><option value="">—</option>${MONTH_OPTS.map(([v, t]) => `<option value="${v}" ${+p.training_months === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      ${num("session_minutes", "Minutos por treino", 'min="20" max="180" step="5" inputmode="numeric"')}
    </div>
    <label>Dias que pode treinar</label>
    <div class="chips">${["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"].map((w, i) => `<label class="chip"><input type="checkbox" data-wd="${i}" ${(p.available_days || []).includes(i) ? "checked" : ""}> ${w}</label>`).join("")}</div>
    <div class="field" style="margin-top:12px"><label for="gym-type">Tipo de academia</label><select id="gym-type" data-p="gym_type">${[["completa", "Completa (rede grande: máquinas, polias, barras)"], ["premium", "Premium / de elite (máquinas articuladas modernas)"], ["basica", "Básica / de bairro / do prédio"]].map(([v, t]) => `<option value="${v}" ${(p.gym_type || "completa") === v ? "selected" : ""}>${t}</option>`).join("")}</select>
      <p class="small muted" style="margin:4px 0 0">Premium: o treino começa pelas máquinas que seguem a curva de força e protegem as articulações (pêndulo, V-squat, remada DY, extensora reclinada). Ao mudar, gere o treino de novo.</p></div>
    <label style="margin-top:12px">O que tem na sua academia</label>
    <div class="chips">${[["barbell", "Barra"], ["dumbbell", "Halteres"], ["machine", "Máquinas"], ["cable", "Polia/cabo"], ["kettlebells", "Kettlebell"], ["bands", "Elástico"], ["e-z curl bar", "Barra W"]].map(([k, t]) => `<label class="chip"><input type="checkbox" data-eq="${k}" ${!(p.equipment || []).length || p.equipment.includes(k) ? "checked" : ""}> ${t}</label>`).join("")}</div>
    <label style="margin-top:12px">Cardio que você gosta de usar</label>
    <div class="chips" id="cm-list"></div>
    <h3 style="margin-top:16px">Durante o treino</h3>
    <div class="grid2">
      <div class="field"><label>Descanso entre séries</label><select data-p="rest_pref">${[["padrao", "Padrão (pela ciência)"], ["curto", "Mais curto (−25%)"], ["longo", "Mais longo (+25%)"]].map(([v, t]) => `<option value="${v}" ${(p.rest_pref || "padrao") === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>Séries de aquecimento do exercício</label><select data-p="warmup_sets">${[["auto", "Automáticas (recomendado)"], ["off", "Desligadas"]].map(([v, t]) => `<option value="${v}" ${(p.warmup_sets || "auto") === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="field"><label>No relógio, exercícios por tempo</label><select data-p="watch_timed">${[["manual", "Só avançam quando eu apertar"], ["auto", "Avançam sozinhos no fim do tempo"]].map(([v, t]) => `<option value="${v}" ${(p.watch_timed || "manual") === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    </div>
    <p class="small muted">Padrão: ~2–2,5 min nos exercícios principais pesados, 1,5–2 min nos secundários e 1 min nos isoladores — a partir de ~90 s o ganho de massa não aumenta, e no app você pode encerrar antes em “Estou pronto”. Vale na hora para o app e para o próximo envio ao relógio.</p>
    <details class="adv"><summary class="small">Ajustes avançados</summary>
      <div class="grid2">${num("cardio_target_min", "Meta de cardio (min/semana)", 'min="0" step="10" placeholder="pelo objetivo"')}
        ${num("volume_factor", "Ajuste de volume (×)", 'step="0.05" min="0.5" max="1.5"')}</div>
      <p class="small muted">“Ajuste de volume” multiplica as séries por músculo: reduza se não estiver recuperando, aumente se estagnou recuperando bem.</p></details>
    <button class="btn ghost block" id="prof-regen" type="button" style="margin-top:12px">🔄 Gerar meu treino de novo</button>
    <p class="small muted">Mudou algo aqui? Salve e gere de novo: você vê a prévia antes de trocar. Histórico, cargas, aparelhos fotografados e as trocas “usar sempre” continuam.</p>`, miss.some((m) => ["training_months", "available_days", "goal"].includes(m[0])))}
  ${sect("foco", "🎯", "Foco: músculo ou aptidão", p.focus ? esc(p.focus.label || "foco ativo") : "melhorar algo específico (opcional)", `<div id="focus-box"><div class="sk"><span class="sk-line"></span></div></div>`)}
  ${sect("limites", "🩹", "Limitações e lesões", lims.length ? lims.map((l) => (state.meta.limitations.find((x) => x.id === l.key) || {}).label || l.key).join(", ") : "nenhuma marcada",
    `<p class="small muted">O app marca exercícios como <span class="pill warn">cuidado</span> ou <span class="pill bad">evitar</span>, troca os que não servem e limita a zona do cardio. Não substitui orientação de um profissional.</p>
    <ul class="list">${state.meta.limitations.map((l) => `<li><label class="row gap" style="color:var(--ink);font-size:15px;margin:0"><input type="checkbox" data-lim="${l.id}" ${ul[l.id] ? "checked" : ""}> <span class="grow">${esc(l.label)}</span>
      <select data-sev="${l.id}" style="width:auto"><option value="evitar" ${(ul[l.id] || {}).severity !== "cuidado" ? "selected" : ""}>significativa</option><option value="cuidado" ${(ul[l.id] || {}).severity === "cuidado" ? "selected" : ""}>leve</option></select></label>
      <div class="small muted">${esc(l.note)}</div></li>`).join("")}</ul>
    <div class="field"><label>Outros exercícios a evitar (separe por vírgula)</label><input id="custom-avoid" value="${esc(custom.custom_avoid || "")}" placeholder="ex.: leg press, burpee, levantamento terra"></div>`)}
  ${sect("pers", "🧬", "Personalização detalhada", persSummary(p), `<div id="pers-box"></div>`)}
  ${sect("esportes", "🏃", "Esportes e provas", Object.entries((p.endurance || {}).sports || {}).filter(([, c]) => c.enabled).map(([k]) => SPORT_WORD[k] || k).join(", ") || "corrida, bike, natação…",
    `<p class="small">Corrida, ciclismo, natação, remo, caminhada e trilha: meta de volume ou prova com tempo alvo, zonas por ritmo, potência ou FC.</p>
    <a class="btn ghost block" href="#esportes">Configurar esportes e provas</a>`)}
  ${sect("academia", "🏟️", "Minha academia", "fotos e regulagem dos aparelhos que você usa", `<div id="mygym-box"><div class="sk"><span class="sk-line"></span></div></div>`)}
  ${sect("relogios", "⌚", "Relógios", g.connected ? `Garmin conectado${g.display_name ? "" : ""}` : g.needs_reconnect ? "Garmin: reconectar" : "Garmin, Apple Watch, Amazfit, Xiaomi, Samsung…",
    `<h3>Garmin</h3>
    ${!g.library_installed ? `<p class="pill bad">Biblioteca garminconnect não instalada no servidor: <code>pip install "garminconnect>=0.3.20"</code></p>` : ""}
    ${g.storage_error ? `<p class="pill bad">${esc(g.storage_error)}</p>` : ""}
    ${g.connected ? `<p><span class="pill ok">conectado</span> ${esc(g.display_name || "")}</p>
      ${syncInfo(g.sync)}
      <div class="row gap wrap"><button class="btn sm" id="g-sync">Sincronizar agora</button><button class="btn sm ghost" id="g-acts">Escolher treinos para importar</button><button class="btn sm danger" id="g-logout">Desconectar</button></div><div id="g-acts-box"></div>
      <p class="small muted">Sincroniza sozinho todo dia à noite e quando você abre o app (se a última tiver mais de 6 h): traz musculação com séries/reps/carga e cardio de academia.</p>`
      : `${g.needs_reconnect ? `<div class="rec atencao small"><b>Reconecte o Garmin.</b> O login expirou ou foi revogado pela Garmin; a sincronização automática está parada até você entrar de novo.</div>` : ""}
      <p class="small muted">Login feito uma vez: o app guarda só os tokens (criptografados no banco), que se renovam sozinhos. Sua senha não é armazenada.</p>
      <div class="grid2"><div class="field"><label>E-mail Garmin</label><input id="g-email" type="email" autocomplete="username"></div>
      <div class="field"><label>Senha</label><input id="g-pass" type="password" autocomplete="current-password"></div></div>
      <div id="g-mfa" ${g.mfa_pending ? "" : "hidden"} class="field"><label>Código de verificação (e-mail/app)</label><input id="g-code" inputmode="numeric" autocomplete="one-time-code"></div>
      <button class="btn" id="g-login">${g.mfa_pending ? "Confirmar código" : "Conectar"}</button>${g.sync && g.sync.last_error && !g.needs_reconnect ? `<p class="small muted">${esc(g.sync.last_error)}</p>` : ""}`}
    <div class="hr"></div>
    <details><summary class="small">Como funciona o envio para o Garmin?</summary><div class="small">
      <p><b>1. Arquivo .FIT (offline):</b> em cada treino da planilha, “Baixar .FIT”. Conecte o relógio no USB e copie para <code>GARMIN/NewFiles</code>. O relógio importa ao desconectar e mostra em Treinos. Gerado com o SDK oficial da Garmin.</p>
      <p><b>2. Garmin Connect (direto):</b> “Enviar ao Garmin Connect” cria o treino na sua biblioteca (mesmo formato do editor web) e agenda na data escolhida; o relógio recebe na próxima sincronização. Usa a API não-oficial (biblioteca <code>garminconnect</code>), pois a API oficial de treinos é restrita a empresas parceiras.</p>
      <p><b>3. Volta do relógio:</b> a sincronização automática (todo dia à noite, ao abrir o app ou em “Sincronizar agora”) traz séries/reps/carga registradas no relógio para o histórico de cargas e a análise muscular. Corridas e pedais na rua ficam de fora.</p></div></details>
  
    ${devHtml}
    ${g.connected ? "" : `<button class="act" id="watch-skip" style="margin-top:10px">Não uso relógio por enquanto</button>`}`, g.needs_reconnect || openSec === "relogios")}
  ${partnerHtml ? sect("parceiro", "🤝", "Treinar com alguém", "parceiro de treino e placar da semana", partnerHtml) : ""}
  ${coachHtml ? sect("coach", "🧑‍🏫", "Coach", "quem acompanha seus treinos", coachHtml) : ""}
  ${sect("conta", "🔒", "Conta e segurança", state.me && state.me.logged_in ? esc(state.me.user.email) : "criar login", `${acctHtml}
    ${canBackup ? `<h3>Backup${state.me && state.me.logged_in ? " (todas as contas)" : " dos seus dados"}</h3>
    <p class="small muted">Perfil, planilhas, treinos registrados e agenda em um arquivo .json.</p>
    <div class="row gap wrap"><a class="btn sm ghost" href="/api/backup">Baixar backup</a>
      <label class="btn sm ghost" style="margin:0">Restaurar backup<input type="file" id="restore" accept=".json,application/json" hidden></label></div>` : ""}`)}
  <div class="savebar"><button class="btn block" id="save-profile">Salvar perfil</button></div>`;
  if (openSec) { const el = $(`#sec-${openSec}`); if (el) { el.open = true; setTimeout(() => el.scrollIntoView({ block: "start" }), 50); } }
  // "Salvar" só aparece com uma seção de dados aberta ou algo alterado
  let dirty = false;
  const updSave = () => { const bar = $(".savebar"); if (bar) bar.hidden = !(dirty || ["dados", "treino", "limites"].some((id) => ($(`#sec-${id}`) || {}).open)); };
  $$(".sect").forEach((d) => d.addEventListener("toggle", updSave));
  const own = (e) => e.target.closest && e.target.closest("#focus-box, #mygym-box, #pers-box");  // têm botão próprio de salvar
  persForm(p);
  view.addEventListener("input", (e) => { if (own(e)) return; dirty = true; updSave(); });
  view.addEventListener("change", (e) => { if (own(e)) return; dirty = true; updSave(); });
  updSave();
  api("/api/generator/options").then((o) => {
    const el = $("#cm-list"); if (!el) return;
    el.innerHTML = o.cardio_machines.map((m) => `<label class="chip"><input type="checkbox" data-cm="${m.id}" ${(p.cardio_machines || []).includes(m.id) ? "checked" : ""}> ${esc(m.name)}</label>`).join("");
  });
  $("#save-profile").onclick = async () => {
    const body = {};
    $$("[data-p]").forEach((el) => { const v = el.value; body[el.dataset.p] = (el.type === "number" || el.dataset.p === "training_months") ? (v === "" ? null : +v) : v; });
    body.available_days = $$("[data-wd]").filter((c) => c.checked).map((c) => +c.dataset.wd);
    if (body.available_days.length) body.days_per_week = body.available_days.length;
    const eq = $$("[data-eq]"); body.equipment = eq.every((c) => c.checked) ? [] : eq.filter((c) => c.checked).map((c) => c.dataset.eq);
    body.cardio_machines = $$("[data-cm]").filter((c) => c.checked).map((c) => c.dataset.cm);
    body.limitations = $$("[data-lim]").filter((c) => c.checked).map((c) => ({ key: c.dataset.lim, severity: $(`[data-sev="${c.dataset.lim}"]`).value }));
    const ca = $("#custom-avoid").value.trim();
    if (ca) body.limitations.push({ key: "personalizado", label: "Restrição pessoal", severity: "evitar", custom_avoid: ca });
    state.profile = await api("/api/profile", { method: "PUT", body });
    state.meta = await api("/api/meta");
    store("gg_lim_seen", 1);
    toast("Perfil salvo ✔");
    const head = $(".prof-head"); if (head) { const m = profileMissing(state.profile); head.querySelector(".meter span").style.width = `${Math.round(100 * (PROFILE_REQ.length - m.length) / PROFILE_REQ.length)}%`; }
  };
  if ($("#restore")) $("#restore").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const r = await api("/api/backup", { method: "POST", body: JSON.parse(await f.text()) });
      state.meta = null; toast(`Restaurado: ${r.sessions || 0} treinos, ${r.plans || 0} planilhas`); route();
    } catch (err) { toast(err.message, 5000); }
  };
  bindAccount();
  const ws = $("#watch-skip"); if (ws) ws.onclick = () => { store("gg_watch_skip", 1); toast("Tudo bem: dá para conectar depois em Perfil → Relógios"); location.hash = "#hoje"; };
  const gl = $("#g-login");
  if (gl) gl.onclick = async () => {
    gl.disabled = true;
    try {
      if (!$("#g-mfa").hidden) await api("/api/garmin/mfa", { method: "POST", body: { code: $("#g-code").value } });
      else {
        const r = await api("/api/garmin/login", { method: "POST", body: { email: $("#g-email").value, password: $("#g-pass").value } });
        if (r.mfa_required) { $("#g-mfa").hidden = false; gl.textContent = "Confirmar código"; toast("Digite o código MFA enviado pela Garmin"); return; }
      }
      toast("Conectado ao Garmin Connect"); viewPerfil(); updateGarminPill();
    } catch (e) { toast(e.message, 6000); } finally { gl.disabled = false; }
  };
  const gs = $("#g-sync");
  if (gs) gs.onclick = async () => {
    gs.disabled = true; gs.textContent = "Sincronizando…";
    try { const r = await syncAll(""); toast(syncToast(r) || (r.ok ? "Nada novo no Garmin" : r.error), 5000); viewPerfil(); updateGarminPill(); }
    catch (e) { toast(e.message, 6000); gs.disabled = false; gs.textContent = "Sincronizar agora"; }
  };
  const lo = $("#g-logout"); if (lo) lo.onclick = async () => { await api("/api/garmin/logout", { method: "POST" }); viewPerfil(); updateGarminPill(); };
  const ga = $("#g-acts");
  if (ga) ga.onclick = async () => {
    const box = $("#g-acts-box"); box.innerHTML = `<p class="small muted">Carregando…</p>`;
    try {
      const acts = await api("/api/garmin/activities?days=21");
      box.innerHTML = `<ul class="list small">${acts.map((a) => `<li class="row between gap"><span><b>${esc(a.name)}</b> <span class="muted">${esc(a.type)} · ${fmtDate(a.start)} · ${a.duration_min} min</span></span><button class="btn sm ghost" data-imp="${a.activity_id}">Importar</button></li>`).join("") || "<li>Nenhuma atividade.</li>"}</ul>`;
      box.onclick = async (e) => {
        const id = e.target.dataset.imp; if (!id) return;
        try { await api(`/api/garmin/import/${id}`, { method: "POST" }); e.target.textContent = "Importado ✔"; e.target.disabled = true; } catch (err) { toast(err.message); }
      };
    } catch (e) { box.innerHTML = `<p class="pill bad">${esc(e.message)}</p>`; }
  };
  const rg = $("#prof-regen"); if (rg) rg.onclick = () => openGenerator();
  const fo = $("#fam-open"); if (fo) fo.onclick = () => openFamily();
  loadFocusBox(p);
  loadMyGym();
  if (state.me && state.me.user && state.me.user.is_admin) api("/api/dev/queue").then((q) => { const el = $("#dev-count"); if (el) el.textContent = q.pending.length ? `${q.pending.length} ${q.pending.length === 1 ? "foto" : "fotos"} para aprovar` : "nenhuma foto na fila · adicionar aparelhos"; }).catch(() => {});
}

function syncInfo(sy) {
  if (!sy || !sy.last_run) return `<p class="small muted">Ainda não sincronizou.</p>`;
  const when = new Date(sy.last_run).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  const how = { cron: "automática (noite)", auto: "ao abrir o app", manual: "manual" }[sy.trigger] || "";
  const imp = sy.last_imported || [];
  return `<p class="small">Última sincronização: <b>${esc(when)}</b> ${how ? `<span class="muted">· ${esc(how)}</span>` : ""}
    ${sy.last_ok ? `<span class="pill ok small">ok</span>` : `<span class="pill warn small">falhou</span>`}
    ${imp.length ? `<br>Trouxe: ${imp.map((i) => `${esc(i.name || "atividade")} (${ddmm(i.date)})`).join(", ")}` : ""}
    ${sy.last_error ? `<br><span class="muted">${esc(sy.last_error)}</span>` : ""}</p>`;
}
function syncToast(r) {
  if (!r || !r.imported || !r.imported.length) return "";
  return `Garmin: ${r.imported.length} treino${r.imported.length > 1 ? "s" : ""} importado${r.imported.length > 1 ? "s" : ""} (${r.imported.map((i) => (i.name || "atividade") + (i.merged ? " — juntado ao treino que você registrou" : "")).join(", ")})`;
}
async function updateGarminPill() {
  try {
    const g = await api("/api/garmin/status"); state.garmin = g; const el = $("#garmin-pill");
    const [txt, cls] = g.connected ? ["Garmin: conectado", "ok"] : g.needs_reconnect ? ["Garmin: reconectar", "warn"] : ["Garmin: offline", "muted"];
    el.textContent = txt; el.className = `pill ${cls}`;
    if (g.needs_reconnect) { el.onclick = () => { location.hash = "#perfil"; }; el.style.cursor = "pointer"; }
    return g;
  } catch (_) { return null; }
}
// cada chamada importa até 15 atividades dentro do prazo do servidor: repete enquanto sobrar fila
async function syncAll(qs) {
  let r = await api(`/api/garmin/sync${qs}`, { method: "POST" });
  const imported = [...(r.imported || [])];
  for (let i = 0; i < 4 && r && r.ok && r.pending > 0; i++) {
    r = await api("/api/garmin/sync", { method: "POST" });
    imported.push(...(r.imported || []));
  }
  return { ...r, imported, ran: r.ran !== false };
}
async function autoSync() {
  try {
    const r = await syncAll("?auto=true");
    const msg = syncToast(r);
    if (msg) { toast(`${msg} 🎉`, 6000); if (["analise", "hoje", "evolucao"].includes((location.hash.slice(1) || "hoje").split("/")[0])) route(); }
    if (r && r.ran) updateGarminPill();
  } catch (_) { /* silencioso: o usuário pode sincronizar no Perfil */ }
}

/* --------------------------------------------------------------- router -- */
// uma tela por vez: trocar de tela cancela as leituras da anterior e espera ela sair, para nunca aparecer
// a tela velha por cima da nova (4G lento de academia)
function route() {
  // mesma tela e uma janela aberta (ex.: opções de "Não vou treinar"): a atualização espera a janela fechar
  if (!$("#modal").hidden && location.hash === route.lastHash) { route.again = true; return route.chain || Promise.resolve(); }
  if (route.ctl) route.ctl.abort();
  const ctl = route.ctl = new AbortController();
  route.chain = (route.chain || Promise.resolve()).then(() => (ctl.signal.aborted ? null : doRoute(ctl))).catch(() => {});
  return route.chain;
}
async function doRoute(ctl) {
  route.lastHash = location.hash; route.again = false;
  const [name, a, b] = (location.hash.slice(1) || "hoje").split("/");
  closeModal.hook = null;                 // ...e quem abriu a janela não é avisado: a tela dele já não é a que está na frente
  if (!$("#modal").hidden) closeModal();  // trocar de tela fecha a janela aberta (não fica por cima da tela nova)
  if (name !== "treinar") fxLeave();
  $$(".tabbar a").forEach((el) => el.classList.toggle("active", el.dataset.tab === ({ plano: "planilhas", exercicios: "planilhas", aparelhos: "planilhas", evolucao: "analise", saude: "analise", esportes: "perfil", coach: "perfil", dev: "perfil", bemvindo: "hoje" }[name] || name)));
  const TITLES = { planilhas: "Planos", exercicios: "Planos", aparelhos: "Planos", plano: "Planilha", treinar: "Treinar", analise: "Progresso", evolucao: "Evolução", saude: "Saúde e sono", perfil: "Perfil", esportes: "Esportes", coach: "Coach", dev: "Desenvolvedor" };
  const pt = $("#page-title"); pt.textContent = TITLES[name] || ""; pt.hidden = !TITLES[name] || (name === "treinar" && a);
  const sn = $("#subnav"); sn.hidden = !["planilhas", "exercicios", "aparelhos"].includes(name);
  $$("#subnav a").forEach((x) => x.classList.toggle("on", x.dataset.sub === name));
  const av = $("#avatar"); const nm = state.me && state.me.user && state.me.user.name;
  if (av) av.innerHTML = nm && nm !== "Eu" ? esc(nm.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase())
    : `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>`;
  coachChrome();
  view.oninput = view.onchange = view.onclick = null;
  prefetchStop();
  const seq = route.seq = (route.seq || 0) + 1;
  const live = ["treinar", "plano"].includes(name);  // telas com edição em andamento: sempre direto da rede
  view.dataset.route = ""; view.setAttribute("aria-busy", "true");
  const skel = setTimeout(() => { if (route.seq === seq) view.innerHTML = skeletonHtml(name); }, 90);
  SWR.mode = live ? null : "cache"; SWR.used = new Set();
  try {
    await loadMedia();
    if (!state.meta) {
      [state.meta, state.profile] = await Promise.all([api("/api/meta"), api("/api/profile")]);
      if (state.meta.ephemeral_storage && !$("#eph")) {
        document.querySelector(".topbar").insertAdjacentHTML("afterend", `<div id="eph" class="rec atencao small" style="margin:8px 16px 0">Versão de teste online: os dados podem ser apagados pelo servidor. Use <a href="#perfil">Perfil → Backup</a> para salvar e restaurar.</div>`);
      }
    }
    const routes = { hoje: () => viewHoje(a), planilhas: viewPlanilhas, plano: () => viewPlano(a), treinar: () => viewTreinar(a, b), analise: viewAnalise, evolucao: () => viewEvolucao(decodeURIComponent(a || "")), esportes: viewEsportes, saude: viewSaude, exercicios: viewExercicios, aparelhos: () => viewAparelhos(a), perfil: () => viewPerfil(a), coach: viewCoach, dev: viewDev, bemvindo: viewBemVindo };
    const render = async () => {
      await (routes[name] || viewHoje)();
      if (!view.onclick) view.onclick = (e) => { const id = e.target.dataset && e.target.dataset.ex; if (id) showExercise(id); };
    };
    await render();
    clearTimeout(skel); SWR.mode = null;
    view.dataset.route = name; view.setAttribute("aria-busy", "false");
    netBanner();
    view.classList.remove("enter"); void view.offsetWidth; view.classList.add("enter");
    window.scrollTo(0, 0);
    if (SWR.used.size) refreshInBackground(seq, [...SWR.used], render);
    else prefetchSoon();
    return;
  } catch (e) {
    clearTimeout(skel); SWR.mode = null;
    if (route.seq !== seq || ctl.signal.aborted || (e && e.name === "AbortError")) return;
    view.dataset.route = name; view.setAttribute("aria-busy", "false");
    view.innerHTML = e.offline
      ? `<div class="card"><h2>Sem internet</h2><p>${esc(e.message)}</p><p class="small muted">Um treino em andamento continua salvo no celular (aba Treinar).</p><button class="btn" id="retry">Tentar de novo</button></div>`
      : `<div class="card"><p class="pill bad">Erro: ${esc(e.message)}</p><p><button class="btn ghost" id="retry">Tentar de novo</button></p></div>`;
    const rb = $("#retry"); if (rb) rb.onclick = route;
  }
  window.scrollTo(0, 0);
}
// a tela abriu com dados guardados: busca os atuais e só redesenha se algo mudou (e se a pessoa não começou a digitar)
async function refreshInBackground(seq, paths, render) {
  let typed = false;
  const mark = () => { typed = true; };
  view.addEventListener("input", mark, { once: true });
  const act = actAs(); const k = (p) => `${act ? act.id : "me"}|${p}`;
  const before = paths.map((p) => JSON.stringify(SWR.mem.get(k(p))));
  try { await Promise.all(paths.map((p) => api(p).catch(() => null))); } catch (_) { return; }
  view.removeEventListener("input", mark);
  const changed = paths.some((p, i) => JSON.stringify(SWR.mem.get(k(p))) !== before[i]);
  if (!changed || typed || route.seq !== seq || !$("#modal").hidden) return prefetchSoon();
  const y = window.scrollY;
  SWR.mode = "cache"; SWR.used = new Set();
  try { await render(); } finally { SWR.mode = null; }
  window.scrollTo(0, y);
  prefetchSoon();
}
// com o app parado, deixa as outras abas prontas para abrir na hora
const PREFETCH = ["/api/schedule", "/api/readiness", "/api/report/weekly", "/api/sessions?limit=30", "/api/history", "/api/load?days=90", "/api/plans", "/api/profile"];
function prefetchSoon() {
  const c = navigator.connection || {};
  if (prefetchSoon.done || !navigator.onLine || c.saveData || /(^|-)2g|3g/.test(c.effectiveType || "")) return;
  clearTimeout(prefetchSoon.t);
  prefetchSoon.t = setTimeout(async () => {
    const ctl = prefetchSoon.ctl = new AbortController(); const seq = route.seq;
    for (const p of PREFETCH) {
      if (ctl.signal.aborted || route.seq !== seq) return;
      const act = actAs(); if (SWR.mem.has(`${act ? act.id : "me"}|${p}`)) continue;
      const t0 = performance.now();
      await api(p, { signal: ctl.signal }).catch(() => null);
      if (performance.now() - t0 > 1500) return;  // rede lenta: para e deixa a banda para o que a pessoa abrir
    }
    if (!ctl.signal.aborted && route.seq === seq) prefetchSoon.done = true;
  }, 2500);
}
function prefetchStop() { clearTimeout(prefetchSoon.t); if (prefetchSoon.ctl) prefetchSoon.ctl.abort(); }
function skeletonHtml(name) {
  const line = (w) => `<span class="sk-line" style="width:${w}%"></span>`;
  const card = (h) => `<div class="sk-card" style="height:${h}px"></div>`;
  if (name === "hoje" || !name) return `<div class="sk">${line(40)}${card(64)}${card(150)}${card(220)}</div>`;
  return `<div class="sk">${line(50)}${card(120)}${card(200)}${card(160)}</div>`;
}
window.addEventListener("hashchange", route);
// leituras canceladas por troca de tela não são erro
window.addEventListener("unhandledrejection", (e) => { if (e.reason && e.reason.name === "AbortError") e.preventDefault(); });
async function flushOutbox() {
  if (flushOutbox.busy) return;  // "online" pode disparar mais de uma vez: um envio por vez
  const box = store("gg_outbox") || []; if (!box.length) return;
  flushOutbox.busy = true;
  try { await sendOutbox(box); } finally { flushOutbox.busy = false; }
}
async function sendOutbox(box) {
  const left = [];
  const conflicts = store("gg_outbox_conflict") || [];
  for (const p of box) {
    try { await api("/api/sessions", { method: "POST", body: p }); } catch (e) {
      if (e.offline) left.push(p);
      // já existe esse treino no servidor (registrado de novo em casa, por exemplo): a pessoa escolhe qual fica
      else if (e.status === 409 && e.body && e.body.existing_id && !conflicts.some((c) => c.payload.client_id === p.client_id))
        conflicts.push({ payload: p, existing: e.body.existing, existing_id: e.body.existing_id });
    }
  }
  store("gg_outbox", left.length ? left : null);
  store("gg_outbox_conflict", conflicts.length ? conflicts : null);
  const sent = box.length - left.length - conflicts.length;
  if (conflicts.length) toast("Um treino guardado sem internet já estava registrado de outro jeito: escolha no Hoje qual fica.", 7000);
  if (sent) { toast(`${sent} treino${sent > 1 ? "s" : ""} guardado${sent > 1 ? "s" : ""} sem internet enviado${sent > 1 ? "s" : ""} ✔`); if (!location.hash || location.hash.startsWith("#hoje")) route(); }
}
window.addEventListener("online", flushOutbox);
// sem internet: a tela abre com o que está guardado no celular, com um aviso claro no topo
function netBanner() {
  const off = navigator.onLine === false; const b = $("#view .net-off");
  if (off && !b) view.insertAdjacentHTML("afterbegin", `<div class="net-off" role="status">📡 Sem internet: mostrando o que já estava salvo no celular. Treinos feitos agora são enviados quando a conexão voltar.</div>`);
  if (!off && b) b.remove();
}
window.addEventListener("offline", netBanner);
window.addEventListener("online", netBanner);
(async () => {
  const conv = location.hash.match(/^#convite\/([\w-]+)/);
  let me = null;
  try { me = await api("/api/auth/me"); } catch (_) { /* offline: segue com o que houver */ }
  state.me = me;
  if (conv && !(me && me.logged_in)) return showAuth("convite", conv[1]);
  if (location.hash.startsWith("#criar") && me && !me.logged_in && !me.setup_needed) return showAuth("criar");
  if (me && !me.logged_in && !me.setup_needed) return showAuth("login");
  startApp();
})();
function startApp() {
  document.body.classList.remove("auth-mode");
  flushOutbox();
  route();
  updateGarminPill().then((g) => { if (g && g.connected) autoSync(); });
}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

/* ---- instalar na tela inicial: Android/Chrome usa o instalador nativo; iPhone mostra o passo a passo ---- */
let INSTALL_EVT = null;
const isStandalone = () => (window.matchMedia && matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); INSTALL_EVT = e; $$("[data-install]").forEach((b) => (b.hidden = false)); });
window.addEventListener("appinstalled", () => { INSTALL_EVT = null; $$(".install-card").forEach((c) => c.remove()); toast("HAX instalado na tela inicial 🎉"); });
function installCardHtml(kind = "hoje") {
  if (isStandalone()) return "";
  if (kind === "hoje") { try { const t = +localStorage.getItem("gg_install_later") || 0; if (Date.now() - t < 14 * 864e5) return ""; } catch (_) { /* sem storage */ } }
  return `<section class="install-card ${kind}"><img src="/static/icon-192.png" alt="" class="ic-logo">
    <span class="grow"><b>Instalar o HAX no celular</b><span class="small muted">Ícone na tela inicial, abre em tela cheia e mais rápido na academia.</span></span>
    <span class="ic-btns"><button class="btn sm" data-install>📲 Instalar</button>${kind === "hoje" ? `<button class="act" data-install-later aria-label="Agora não">Agora não</button>` : ""}</span></section>`;
}
async function doInstall() {
  if (INSTALL_EVT) {
    const ev = INSTALL_EVT; INSTALL_EVT = null;
    ev.prompt();
    try { const r = await ev.userChoice; if (r && r.outcome === "accepted") return; } catch (_) { /* navegador antigo */ }
    return;
  }
  const ios = isIOS(); const safari = ios && !/crios|fxios|edgios|gsa/i.test(navigator.userAgent);
  openModal(`<h2>📲 Instalar o HAX</h2>${ios ? `
    <ol class="inst-steps">${safari ? "" : `<li>Se não aparecer a opção abaixo, abra <b>${esc(location.host)}</b> no <b>Safari</b>.</li>`}
      <li>Toque em <b>Compartilhar</b> <span class="inst-ico">⬆︎</span> (na barra de baixo do Safari${safari ? "" : " ou no menu do navegador"}).</li>
      <li>Role e toque em <b>Adicionar à Tela de Início</b> <span class="inst-ico">⊞</span>.</li>
      <li>Confirme em <b>Adicionar</b>. O ícone do HAX aparece junto dos seus apps.</li>
      <li>Abra pelo ícone e <b>entre com o mesmo e-mail e senha</b> (no iPhone o app instalado pede login uma vez).</li></ol>`
    : `<ol class="inst-steps"><li>Toque no menu <span class="inst-ico">⋮</span> do navegador (canto de cima).</li>
      <li>Escolha <b>Instalar app</b> ou <b>Adicionar à tela inicial</b>.</li>
      <li>Confirme. O ícone do HAX aparece junto dos seus apps.</li></ol>
    <p class="small muted">No Chrome do Android o botão “Instalar” abre o instalador direto; se ele não abriu, use o menu como acima.</p>`}
    <p class="small muted">Instalado, o HAX abre em tela cheia (sem a barra do navegador), mantém o login e funciona mesmo com sinal fraco na academia.</p>`);
}
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-install]")) { e.preventDefault(); return doInstall(); }
  if (e.target.closest("[data-feedback]")) { e.preventDefault(); return openFeedback(); }
  if (e.target.closest("[data-install-later]")) { try { localStorage.setItem("gg_install_later", String(Date.now())); } catch (_) { /* */ } const c = e.target.closest(".install-card"); if (c) c.remove(); }
});
// App que estava aberto em segundo plano: ao voltar depois de alguns minutos, confere se há versão nova
// (e recarrega), registra a abertura, atualiza a tela e sincroniza o relógio — como se tivesse aberto de novo.
const MY_VERSION = ((document.querySelector('script[src*="app.js"]') || {}).src || "").split("v=")[1] || "";
let hiddenAt = 0;
document.addEventListener("visibilitychange", async () => {
  if (document.hidden) { hiddenAt = Date.now(); return; }
  if (!hiddenAt || Date.now() - hiddenAt < 3 * 60 * 1000) return;
  hiddenAt = 0;
  try {
    const { v } = await (await fetch("/api/public/version", { cache: "no-store" })).json();
    if (v && MY_VERSION && v !== MY_VERSION && !store("gg_session")) {
      toast("Nova versão do app: atualizando…");
      if (navigator.serviceWorker && navigator.serviceWorker.getRegistration) { const r = await navigator.serviceWorker.getRegistration(); if (r) r.update().catch(() => {}); }
      setTimeout(() => location.reload(), 600);
      return;
    }
  } catch (_) { /* sem rede: segue com o que tem */ }
  try { state.me = await api("/api/auth/me"); } catch (_) { /* */ }
  if (state.me && state.me.logged_in === false && !state.me.setup_needed) return showAuth("login");
  const h = location.hash.split("/")[0];
  if (!["#treinar", "#plano"].includes(h)) route();
  updateGarminPill().then((g) => { if (g && g.connected) autoSync(); });
});
