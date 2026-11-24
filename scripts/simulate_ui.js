/* Simulação das telas e do uso no celular (Playwright).

   Uso:  node scripts/simulate_ui.js [--shots DIR] [--quick]

   Para cada persona (novo, musculacao, hibrido, extremo — bancos criados por scripts/ui_seed.py)
   sobe o app localmente e percorre todas as telas em vários aparelhos (iPhone SE, iPhone 14,
   Pixel 7, Galaxy Z Fold capa, iPad Mini, iPhone SE deitado), em tema claro e escuro.

   Confere em cada tela:
     • erros de JavaScript, erros no console e requisições com falha;
     • rolagem lateral / elementos saindo da tela;
     • alvos de toque pequenos (< 36 px) e campos com fonte < 16 px (o iPhone dá zoom ao tocar);
     • texto minúsculo (< 11 px) e contraste baixo (< 4,5:1, ou 3:1 em texto grande);
     • conteúdo escondido atrás da barra de abas; tela em branco ou travada em "Carregando…";
     • tempo até renderizar.
   E fluxos de uso: gerar treino (novo usuário), ver treino, mover, "não vou treinar" e desfazer,
   treinar (preencher séries, cronômetro de descanso, salvar), perfil, esportes, busca de
   exercícios, modal em tela pequena, voltar do navegador e uso sem internet (service worker).
*/
"use strict";
const { chromium, devices } = require("playwright");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
const SHOTS = args.includes("--shots") ? args[args.indexOf("--shots") + 1] : null;
const QUICK = args.includes("--quick");
const TMP = fs.mkdtempSync(path.join(require("os").tmpdir(), "gg-ui-"));

const DEVICES = [
  ["iPhone SE", devices["iPhone SE"]],
  ["iPhone 14", devices["iPhone 14"]],
  ["Pixel 7", devices["Pixel 7"]],
  ["Fold capa", devices["Galaxy Z Fold 6 Cover"] || { viewport: { width: 280, height: 653 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 }],
  ["iPad Mini", devices["iPad Mini"]],
  ["iPhone SE deitado", devices["iPhone SE landscape"]],
].filter((d) => !QUICK || ["iPhone SE", "Pixel 7"].includes(d[0]));
const THEMES = QUICK ? ["light"] : ["light", "dark"];
const PERSONAS = (process.env.GG_PERSONAS || "novo,musculacao,hibrido,extremo").split(",");

const ISSUES = new Map();
const STATS = { render_ms: [] };
function issue(kind, ex) {
  if (!ISSUES.has(kind)) ISSUES.set(kind, { n: 0, ex: [] });
  const it = ISSUES.get(kind); it.n++;
  const s = typeof ex === "string" ? ex : JSON.stringify(ex);
  if (it.ex.length < 5 && !it.ex.includes(s)) it.ex.push(s);
}

async function startServer(persona, port) {
  const db = path.join(TMP, `${persona}.sqlite3`);
  const env = { ...process.env, GYMGARMIN_DB: db, GYMGARMIN_HOME: TMP, GARMIN_TOKEN_KEY: "ui-sim-key" };
  delete env.DATABASE_URL; delete env.VERCEL;
  execFileSync("python3", [path.join(ROOT, "scripts/ui_seed.py"), persona, db], { env, cwd: ROOT, stdio: "pipe" });
  const p = spawn("python3", ["-m", "uvicorn", "gymgarmin.app:app", "--port", String(port), "--log-level", "warning"], { env, cwd: ROOT, stdio: ["ignore", "pipe", "pipe"] });
  p.stderr.on("data", (d) => { const t = String(d); if (/Traceback|Error/.test(t)) issue("erro no servidor", t.slice(-300)); });
  for (let i = 0; i < 80; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/api/meta`); if (r.ok) return p; } catch (_) { /* subindo */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("servidor não subiu");
}

// ---------------------------------------------------------------- checagens na página
async function audit(page, where) {
  const r = await page.evaluate(() => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const desc = (el) => {
      let s = el.tagName.toLowerCase();
      if (el.id) s += "#" + el.id;
      else if (el.className && typeof el.className === "string") s += "." + el.className.trim().split(/\s+/).slice(0, 2).join(".");
      const t = (el.textContent || el.value || "").trim().replace(/\s+/g, " ").slice(0, 40);
      return t ? `${s} «${t}»` : s;
    };
    const visible = (el) => {
      const cs = getComputedStyle(el);
      if (cs.visibility === "hidden" || cs.display === "none" || +cs.opacity === 0) return false;
      const rc = el.getBoundingClientRect();
      return rc.width > 0 && rc.height > 0 && !el.closest("[hidden]") && !el.closest("details:not([open]) > :not(summary)");
    };
    const clippedX = (el) => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) { if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) return true; } return false; };
    const out = { hscroll: Math.max(document.documentElement.scrollWidth - vw, Math.round(vw - (window.visualViewport ? visualViewport.width : vw))), offscreen: [], small: [], zoom: [], tiny: [], contrast: [], blank: false, loading: false, behindTab: false };
    const all = [...document.querySelectorAll("body *")].filter(visible);
    for (const el of all) {
      const rc = el.getBoundingClientRect();
      if ((rc.right > vw + 1 || rc.left < -1) && !clippedX(el) && !el.closest(".modal[hidden]")) {
        const pr = el.parentElement && el.parentElement.getBoundingClientRect();
        if (!pr || !(pr.right > vw + 1 || pr.left < -1)) out.offscreen.push(`${desc(el)} (${Math.round(rc.left)}…${Math.round(rc.right)} de ${vw})`);
      }
      const tag = el.tagName;
      const interactive = /^(A|BUTTON|SELECT|SUMMARY|INPUT|TEXTAREA)$/.test(tag) || el.matches("label.chip, [data-ex], [role=button]");
      if (interactive && !(tag === "INPUT" && /checkbox|radio|hidden/.test(el.type))) {
        // [data-ex] dentro de uma linha inteira tocável (mesmo data-ex) vale pela linha
        let host = el;
        if (el.dataset && el.dataset.ex) for (let p = el.parentElement; p; p = p.parentElement) if (p.dataset && p.dataset.ex === el.dataset.ex) host = p;
        if (host !== el) { const hr = host.getBoundingClientRect(); if (hr.height >= 36) continue; }
        const inline = (tag === "A" || el.dataset.ex !== undefined) && getComputedStyle(el).display === "inline" && el.closest("p, li, .small");
        if (!inline && (rc.height < 36 || rc.width < 36)) out.small.push(`${desc(el)} ${Math.round(rc.width)}×${Math.round(rc.height)}`);
      }
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag) && !/checkbox|radio|hidden|range/.test(el.type || "")) {
        const fs = parseFloat(getComputedStyle(el).fontSize);
        if (fs < 16) out.zoom.push(`${desc(el)} ${fs}px`);
      }
      // texto direto do elemento
      const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (own) {
        const cs = getComputedStyle(el);
        const fs = parseFloat(cs.fontSize);
        if (fs < 11) out.tiny.push(`${desc(el)} ${fs}px`);
        // contraste: cor do texto × primeiro fundo opaco
        const rgb = (s) => (s.match(/[\d.]+/g) || []).map(Number);
        let bg = null;
        for (let p = el; p; p = p.parentElement) { const c = rgb(getComputedStyle(p).backgroundColor); if (c.length >= 3 && (c[3] === undefined || c[3] > 0.5)) { bg = c; break; } }
        if (!bg) bg = rgb(getComputedStyle(document.body).backgroundColor);
        const fg = rgb(cs.color);
        const lum = (c) => { const v = c.slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
        if (fg.length >= 3 && bg && bg.length >= 3) {
          const a = fg[3] === undefined ? 1 : fg[3];
          const mix = fg.slice(0, 3).map((x, i) => x * a + bg[i] * (1 - a));
          const L1 = lum(mix), L2 = lum(bg);
          const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
          const big = fs >= 18.66 || (fs >= 14 && +cs.fontWeight >= 700);
          if (ratio < (big ? 3 : 4.5) && !el.disabled && !el.closest("[disabled]")) out.contrast.push(`${desc(el)} ${ratio.toFixed(2)}:1`);
        }
      }
    }
    // sobreposição: texto/campo/botão em cima de outro (fora os fixos: abas, cronômetro, aviso, modal)
    // fotos dos exercícios (.demo) são mídia de fundo: a legenda por cima é proposital
    const fixedish = (el) => el.closest(".tabbar, #timer, #toast, .topbar, .modal, .savebar, .fx-go, .fx-skip, .fx-dock") || (el.tagName === "IMG" && el.closest(".demo, .hero"));
    const leaves = all.filter((el) => !fixedish(el) && (/^(INPUT|SELECT|TEXTAREA|BUTTON|IMG)$/.test(el.tagName) ||
      ([...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && !el.querySelector("input,select,button,textarea"))));
    // texto em linha que quebra: usa as caixas de cada linha (getClientRects), não o retângulo total
    const rects = leaves.map((el) => [...el.getClientRects()].filter((r) => r.width > 0 && r.height > 0));
    out.overlap = [];
    outer: for (let i = 0; i < leaves.length && out.overlap.length < 5; i++) for (let j = i + 1; j < leaves.length; j++) {
      if (leaves[i].contains(leaves[j]) || leaves[j].contains(leaves[i])) continue;
      for (const a of rects[i]) for (const b of rects[j]) {
        const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (w <= 2 || h <= 2) continue;
        const area = w * h, small = Math.min(a.width * a.height, b.width * b.height);
        if (area > 40 && area > 0.25 * small) { out.overlap.push(`${desc(leaves[i])} × ${desc(leaves[j])}`); continue outer; }
      }
    }
    // texto de gráfico (SVG) pequeno de verdade na tela
    out.svgTiny = [...document.querySelectorAll("#view svg text")].filter(visible).map((t) => t.getBoundingClientRect().height).filter((h) => h < 10).length;
    const v = document.getElementById("view");
    out.blank = !v || v.innerText.trim().length < 10;
    out.loading = !!v && /^(Carregando|Montando)…?$/.test(v.innerText.trim());
    const tab = document.querySelector(".tabbar"); const tr = tab && tab.getBoundingClientRect();
    const padB = parseFloat(getComputedStyle(document.body).paddingBottom);
    out.behindTab = !!tr && tr.height > padB + 1;
    out.tabH = tr ? tr.height : 0;
    return out;
  });
  if (r.hscroll > 1) issue("rolagem lateral na página", `${where}: +${r.hscroll}px`);
  r.offscreen.slice(0, 3).forEach((x) => issue("elemento saindo da tela", `${where}: ${x}`));
  if (r.small.length) issue("alvo de toque pequeno (< 36 px)", `${where}: ${r.small.length}× ex. ${r.small.slice(0, 3).join(" | ")}`);
  if (r.zoom.length) issue("campo com fonte < 16 px (iPhone dá zoom ao tocar)", `${where}: ${r.zoom.length}× ex. ${r.zoom.slice(0, 2).join(" | ")}`);
  if (r.tiny.length) issue("texto < 11 px", `${where}: ${r.tiny.slice(0, 3).join(" | ")}`);
  if (r.contrast.length) issue("contraste baixo", `${where}: ${r.contrast.length}× ex. ${r.contrast.slice(0, 3).join(" | ")}`);
  if (r.blank) issue("tela em branco", where);
  if (r.overlap.length) issue("elementos sobrepostos", `${where}: ${r.overlap.slice(0, 2).join(" | ")}`);
  if (r.svgTiny) issue("texto de gráfico < 10 px na tela", `${where}: ${r.svgTiny}×`);
  if (r.loading) issue("travou em Carregando…", where);
  if (r.behindTab) issue("conteúdo escondido atrás da barra de abas", `${where}: barra ${r.tabH}px`);
  return r;
}

async function go(page, hash, where) {
  const t0 = Date.now();
  await page.evaluate((h) => { location.hash = h; }, hash);
  await page.waitForTimeout(80);
  // espera a tela nova terminar (o app marca #view[data-route]); evita contar a tela anterior como carregada
  const want = (hash.slice(1) || "hoje").split("/")[0];
  await page.waitForFunction((w) => document.getElementById("view").dataset.route === w || document.body.classList.contains("auth-mode"), want, { timeout: 8000 }).catch(() => {});
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForFunction(() => { const v = document.getElementById("view"); return v && v.innerText.trim().length > 10 && !/^Carregando/.test(v.innerText.trim()); }, null, { timeout: 8000 }).catch(() => {});
  STATS.render_ms.push(Date.now() - t0);
  await page.waitForTimeout(150);
  return audit(page, where);
}

function overlap(a, b) { return a && b && a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height; }

// ---------------------------------------------------------------- fluxos
async function flows(page, base, persona, tag) {
  const W = (s) => `${tag} ${s}`;
  const click = async (sel, what) => {
    const el = page.locator(sel).first();
    if (!(await el.count())) { issue("botão esperado não encontrado", W(`${what} (${sel})`)); return false; }
    await el.scrollIntoViewIfNeeded().catch(() => {});
    try { await el.click({ timeout: 4000 }); } catch (e) { issue("não deu para tocar no botão", W(`${what}: ${String(e.message).replace(/\s+/g, " ").slice(-600)}`)); return false; }
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(250);
    return true;
  };
  const modalCheck = async (what) => {
    const m = await page.evaluate(() => {
      const c = document.querySelector("#modal:not([hidden]) .modal-card"); if (!c) return null;
      const r = c.getBoundingClientRect(); const x = c.querySelector(".modal-close").getBoundingClientRect();
      return { h: r.height, top: r.top, bottom: r.bottom, vh: innerHeight, vw: innerWidth, left: r.left, right: r.right, scroll: c.scrollHeight > c.clientHeight + 2,
        overflowY: getComputedStyle(c).overflowY, closeVisible: x.top >= 0 && x.bottom <= innerHeight, closeSize: [x.width, x.height] };
    });
    if (!m) { issue("modal não abriu", W(what)); return; }
    if (m.bottom > m.vh + 1 && !/(auto|scroll)/.test(m.overflowY)) issue("modal maior que a tela sem rolagem", W(`${what}: ${Math.round(m.h)}px de ${m.vh}`));
    if (m.left < -1 || m.right > m.vw + 1) issue("modal saindo da tela na largura", W(what));
    if (!m.closeVisible) issue("botão fechar do modal fora da tela", W(what));
    if (m.closeSize[0] < 36 || m.closeSize[1] < 36) issue("botão fechar do modal pequeno", W(`${what}: ${m.closeSize.map(Math.round).join("×")}`));
    await audit(page, W(`modal ${what}`));
  };

  if (persona === "novo") {
    await go(page, "#hoje", W("hoje (novo)"));
    // perfil vazio: as 7 perguntas do bem-vindo montam o treino
    if (await page.locator("#wiz-open").count()) {
      await click("#wiz-open", "Responder 7 perguntas");
      await page.waitForSelector(".wz", { timeout: 8000 }).catch(() => issue("bem-vindo não abriu", W("novo")));
      await page.fill("#wz-name", "Novo"); await click("[data-wk=sex][data-wv=M]", "sexo");
      await page.fill("#wz-age", "40"); await page.fill("#wz-w", "85");
      await audit(page, W("bem-vindo: sobre você"));
      await click("#wz-next", "Continuar");
      await click("[data-wk=goal][data-wv=hipertrofia]", "objetivo"); await page.waitForTimeout(300);
      await click('[data-wk=training_months][data-wv="0"]', "experiência"); await page.waitForTimeout(300);
      for (const d of [0, 2, 4]) await click(`[data-wk=day][data-wv="${d}"]`, "dia");
      await audit(page, W("bem-vindo: dias"));
      await click("#wz-next", "Continuar");
      await click("[data-wk=place][data-wv=academia]", "onde"); await page.waitForTimeout(300);
      await click("[data-wk=none]", "sem lesão"); await click("#wz-next", "Continuar");
      await click('[data-wk=run][data-wv="0"]', "corrida"); await page.waitForTimeout(300);
      await audit(page, W("bem-vindo: resumo"));
      await click("#wz-next", "Montar meu treino");
      await page.waitForFunction(() => /Semana de/.test(document.getElementById("view").innerText), null, { timeout: 15000 }).catch(() => issue("bem-vindo não montou a semana", W("novo")));
      await audit(page, W("hoje depois do bem-vindo"));
      return;
    }
    await page.evaluate(() => { const d = document.querySelector("#gen-open") && document.querySelector("#gen-open").closest("details"); if (d) d.open = true; });
    if (await click("#gen-open", "Gerar meu treino")) {
      await modalCheck("gerador");
      await click("#gm-prev", "Pré-visualizar");
      await page.waitForSelector("#gm-apply:not([hidden])", { timeout: 15000 }).catch(() => issue("pré-visualização não terminou", W("gerador")));
      await modalCheck("gerador com prévia");
      await click("#gm-apply", "Usar este treino");
      await page.waitForSelector("#gm-see", { timeout: 15000 }).catch(() => issue("janela 'treino novo pronto' não abriu", W("gerador")));
      await modalCheck("treino novo pronto");
      await click("#gm-see", "Ver minha semana");
      await page.waitForTimeout(600);
      const ok = await page.evaluate(() => /Semana de/.test(document.getElementById("view").innerText));
      if (!ok) issue("gerar treino não montou a semana", W("novo"));
      await audit(page, W("hoje depois de gerar"));
    }
    return;
  }

  // Hoje: ver treino, mover, não vou treinar + desfazer
  await go(page, "#hoje", W("hoje"));
  // semana em tira: toca nos dias até o cartão do dia mostrar o botão procurado
  const openDay = async (sel) => { if (await page.locator(`#day-panel ${sel}`).count()) return; const n = await page.locator(".wd").count(); for (let i = 0; i < n; i++) { await page.locator(".wd").nth(i).click(); await page.waitForTimeout(80); if (await page.locator(`#day-panel ${sel}`).count()) return; } };
  const sum = page.locator("#day-panel details.sub > summary").first();
  if (await sum.count()) { await sum.scrollIntoViewIfNeeded(); await sum.click(); await page.waitForTimeout(100); await audit(page, W("hoje com treino aberto")); }
  await openDay("[data-move]");
  if (await page.locator("#day-panel [data-move]").count()) {
    // Mover fica no menu "⋯ Mais" do cartão do treino
    const more = page.locator("#day-panel .tc-more > summary").first();
    if (await more.count()) { await more.scrollIntoViewIfNeeded(); await more.click(); await page.waitForTimeout(100); await audit(page, W("hoje com o menu Mais aberto")); }
    await click("#day-panel [data-move]", "Mover");
    await modalCheck("mover");
    await page.click(".modal-close").catch(() => issue("não fechou o modal", W("mover")));
  }
  await openDay("[data-skip]");
  if (await page.locator("#day-panel [data-skip]").count()) {
    await click("#day-panel [data-skip]", "Não vou treinar");
    await page.waitForSelector("#sk-go", { timeout: 4000 }).catch(() => issue("opções de 'Não vou treinar' não abriram", W("hoje")));
    await modalCheck("não vou treinar: opções");
    const nOpt = await page.locator(".opt").count();
    if (nOpt < 1) issue("'Não vou treinar' sem opções", W("hoje"));
    if ((await page.locator(".opt.on").count()) !== 1) issue("'Não vou treinar' sem uma opção recomendada marcada", W("hoje"));
    // mover: escolher um dia mostra os avisos e libera o confirmar
    const mv = page.locator(".opt[data-opt='mover']:not([disabled])");
    if (await mv.count()) {
      await mv.click(); await page.waitForTimeout(80);
      if (!(await page.locator(".mv-day").count())) issue("'Fazer em outro dia' sem lista de dias", W("hoje"));
      await modalCheck("não vou treinar: dias para mover");
      await page.locator(".opt[data-opt='seguir']").click(); await page.waitForTimeout(80);
    }
    await click("#sk-go", "Confirmar (não vou treinar)");
    await page.waitForSelector("#ok-close", { timeout: 4000 }).catch(() => issue("resultado do pulo não apareceu", W("hoje")));
    await modalCheck("reajuste");
    await click("#ok-close", "Ok do reajuste");
    if (await page.locator("[data-undo]").count()) { await openDay("[data-undo]"); } if (await page.locator("[data-undo]").count()) await click("#day-panel [data-undo]", "Desfazer ajuste");
  }
  if (await page.locator("#rd-apply").count()) await audit(page, W("cartão de prontidão"));
  // atualizar os próximos treinos: botão visível em Hoje, janela com o que muda e confirmação
  if (await page.locator("#rebuild-open, #rebuild-go").count()) {
    if (await page.locator("#rebuild-open").count()) {
      await click("#rebuild-open", "Atualizar os próximos treinos");
      await modalCheck("atualizar os próximos treinos");
      await click("#rb-go", "Atualizar agora");
    } else await click("#rebuild-go", "Atualizar (cartão semana desatualizada)");
    if (await page.locator("#rebuild-go").count()) issue("semana continua desatualizada depois de atualizar", W("hoje"));
  } else if (persona !== "novo") issue("Hoje: sem botão de atualizar os próximos treinos", W("hoje"));

  // Treinar a partir da agenda
  const sch = await (await fetch(`${base}/api/schedule`)).json();
  const e = sch.entries.find((x) => x.status === "planejado" && x.kind === "forca") || sch.entries.find((x) => x.status === "planejado");
  if (e) {
    await go(page, `#treinar/agenda/${e.id}`, W(`treinar ${e.kind}`));
    // treino guiado (padrão): um exercício por vez, descanso cronometrado
    if (await page.locator(".fx-top").count()) {
      for (let k = 0; k < 14 && !(await page.locator("[data-fx='set']").count()) && (await page.locator("[data-fx='done']").count()); k++) {
        await page.locator("[data-fx='done']").first().click(); await page.waitForTimeout(80);
      }
      if (await page.locator("[data-fx='set']").count()) {
        await audit(page, W("treino guiado: exercício"));
        const go_ = await page.locator("[data-fx='set']").boundingBox(); const tb = await page.locator(".tabbar").boundingBox();
        if (go_ && tb && (go_.y + go_.height > tb.y + 1 || go_.y < 0)) issue("treino guiado: botão da série fora da tela", W(`y=${Math.round(go_.y)}`));
        await click("[data-st='reps:1']", "+1 repetição");
        await click("[data-fx='set']", "série feita (guiado)");
        if (!(await page.locator(".fx-restbar #fx-rclock").count())) issue("treino guiado: descanso não começou", W("treinar"));
        else {
          await audit(page, W("treino guiado: descanso"));
          // a série recém-feita continua editável com o cronômetro correndo
          await page.evaluate(() => { const d = document.querySelector(".fx-just-wrap"); if (d) d.open = true; });
          const just = page.locator(".fx-just [data-lst='load_kg:2.5']");
          if (!(await just.count())) issue("descanso: não dá para ajustar a série que acabou de fazer", W("treinar"));
          else {
            const before = await page.locator(".fx-just [data-lv='load_kg']").inputValue();
            await just.click(); await page.waitForTimeout(60);
            const after = await page.locator(".fx-just [data-lv='load_kg']").inputValue();
            if (before === after) issue("descanso: +2,5 kg na série feita não mudou o valor", W(`${before} → ${after}`));
            await page.locator(".fx-just [data-lv='reps'], .fx-just [data-lv='duration_s']").first().fill("9");
            if (!(await page.locator(".fx-restbar #fx-rclock").count())) issue("descanso: editar a série apagou o cronômetro", W("treinar"));
            const saved = await page.evaluate(() => { const s = JSON.parse(localStorage.getItem("gg_session") || "{}"); const L = s.lastSet; if (!L) return null; const it = s.items[L.i]; return (L.w ? it.warmup : it.sets)[L.k]; });
            if (!saved || +saved.load_kg !== +after) issue("descanso: ajuste da série feita não foi guardado", W(JSON.stringify(saved)));
          }
          await click("[data-fx='r+15']", "+15 s"); await click("[data-fx='r-skip']", "Pular descanso (guiado)");
        }
        // aparelho ocupado: as trocas não podem repetir nada do treino; "adiantar" leva a outro exercício e volta
        if (await page.locator("[data-fx='swap']").count()) {
          await click("[data-fx='swap']", "Trocar (aparelho ocupado)");
          await page.waitForSelector("#sw-list:not(.sk)", { timeout: 5000 }).catch(() => issue("troca: lista não carregou", W("treinar")));
          await audit(page, W("troca: aparelho ocupado"));
          const bad = await page.evaluate(() => {
            const s = JSON.parse(localStorage.getItem("gg_session") || "{}");
            const ids = new Set((s.items || []).map((x) => x.exercise_id));
            return [...document.querySelectorAll("[data-sw]")].map((b) => b.dataset.sw).filter((x) => ids.has(x));
          });
          if (bad.length) issue("troca: oferece exercício que já está no treino", W(bad.join(",")));
          const cur = await page.locator(".fx-count").textContent().catch(() => "");
          if (await page.locator("[data-adv]").count()) {
            await page.locator("[data-adv]").first().click(); await page.waitForTimeout(150);
            const now = await page.locator(".fx-count").textContent().catch(() => "");
            if (!(await page.locator("[data-fx='set']").count())) issue("adiantar: não abriu o outro exercício", W(`${cur} → ${now}`));
            await audit(page, W("treino guiado: exercício adiantado"));
          } else await page.evaluate(() => closeModal());
          await page.evaluate(() => { const b = document.querySelector("[data-fx='list']"); if (b) b.scrollIntoView({ block: "center" }); });
        }
        // 🎵 música: abre a janela do momento (sem Spotify conectado: abrir no Spotify)
        if (await page.locator("[data-fx='music']").count()) {
          await click("[data-fx='music']", "Música (treino guiado)");
          await page.waitForTimeout(400);
          if (!(await page.locator("#modal:not([hidden]) h2").count())) issue("música: janela não abriu", W("treinar"));
          else await modalCheck("música no treino");
          await page.evaluate(() => closeModal());
        } else issue("treino guiado: sem botão de música", W("treinar"));
      } else if (e.kind === "forca") issue("treino guiado: sem exercício com séries", W("treinar"));
      await click("[data-fx='list']", "Ver lista");
    } else issue("treino guiado não abriu", W("treinar"));
    const inp = page.locator("tr:not(.done) input[data-s='reps']").first();
    if (await inp.count()) {
      await inp.scrollIntoViewIfNeeded();
      await inp.fill("10");
      await page.locator("input[data-s='load_kg']").first().fill("42.5");
      await click("button[data-done]:not(.on)", "marcar série feita");
      const t = await page.evaluate(() => { const el = document.getElementById("timer"); if (!el || el.hidden) return null; const r = el.getBoundingClientRect(); const tb = document.querySelector(".tabbar").getBoundingClientRect();
        return { timer: { x: r.x, y: r.y, width: r.width, height: r.height }, tab: { x: tb.x, y: tb.y, width: tb.width, height: tb.height }, vh: innerHeight }; });
      if (!t) issue("cronômetro de descanso não apareceu", W("treinar"));
      else {
        if (overlap(t.timer, t.tab)) issue("cronômetro cobre a barra de abas", W(`treinar: timer y=${Math.round(t.timer.y)} h=${Math.round(t.timer.height)}, abas y=${Math.round(t.tab.y)}`));
        if (t.timer.y + t.timer.height > t.vh + 1) issue("cronômetro sai da tela", W("treinar"));
        // o cronômetro não pode esconder o próximo campo a preencher
        const nxt = await page.locator("input[data-s='reps']").nth(1).boundingBox().catch(() => null);
        if (nxt && overlap(t.timer, nxt)) issue("cronômetro cobre o campo da próxima série", W("treinar"));
        await audit(page, W("treinar com cronômetro"));
        await click("[data-timer='stop']", "Pular descanso");
      }
    } else if (e.kind === "resistencia" && !(await page.locator("[data-cdone]").count())) issue("treino de resistência sem campos para registrar", W("treinar"));
    page.once("dialog", (d) => d.accept());
    if (persona === "musculacao" && tag.includes("iPhone SE")) {
      // academia sem sinal na hora de salvar
      const before = (await (await fetch(`${base}/api/sessions?limit=2000`)).json()).length;
      await page.context().setOffline(true);
      await click("#s-finish", "Finalizar e salvar (sem internet)");
      await page.waitForTimeout(400);
      const st = await page.evaluate(() => ({ toast: (document.getElementById("toast") || {}).innerText || "", box: JSON.parse(localStorage.getItem("gg_outbox") || "[]").length }));
      if (!st.box) issue("sem internet: treino perdido ao salvar", W(st.toast));
      if (!/internet/i.test(st.toast)) issue("sem internet ao salvar: sem aviso", W(st.toast));
      await page.context().setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await page.waitForTimeout(1200);
      const after = (await (await fetch(`${base}/api/sessions?limit=2000`)).json()).length;
      const left = await page.evaluate(() => JSON.parse(localStorage.getItem("gg_outbox") || "[]").length);
      if (after !== before + 1 || left) issue("treino guardado sem internet não foi enviado (ou duplicou)", W(`antes ${before}, depois ${after}, na fila ${left}`));
    } else await click("#s-finish", "Finalizar e salvar");
    await page.waitForTimeout(500);
    await audit(page, W("depois de salvar o treino"));
    // postar nas redes: o estúdio abre, desenha o cartão em todos os modelos e formatos sem erro
    { const eff0 = page.locator("#modal:not([hidden]) [data-eff='7']"); if (await eff0.count()) { await eff0.click(); await page.waitForTimeout(300); } }
    if (await page.locator("[data-studio]").count()) {
      await click("[data-studio]", "Postar nas redes");
      await page.waitForSelector("#sc-cv", { timeout: 6000 }).catch(() => issue("postar nas redes: cartão não abriu", W("salvo")));
      await modalCheck("postar nas redes");
      for (const t of await page.$$eval("[data-tpl]", (x) => x.map((b) => b.dataset.tpl))) {
        await page.click(`[data-tpl="${t}"]`).catch(() => issue("postar nas redes: modelo não tocável", W(t)));
        const blank = await page.$eval("#sc-cv", (c) => { const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < d.length; i += 4000) if (d[i]) n++; return n < 5; });
        if (blank) issue("postar nas redes: cartão em branco", W(t));
      }
      await page.click("[data-fmt='quad']").catch(() => {});
      await page.evaluate(() => closeModal());
    } else if (await page.locator(".saved-card").count()) issue("depois de salvar: sem botão de postar nas redes", W("salvo"));
  }

  // Treino livre: montar, tirar o exercício escolhido errado, registrar e ver o que contou
  await go(page, "#treinar/livre", W("treino livre"));
  // já havia outro treino em andamento neste aparelho: o app não passa por cima; a pessoa escolhe "Treino livre"
  if (!(await page.locator("#s-add").count()) && (await page.locator("#free").count())) await click("#free", "Treino livre");
  if (await page.locator("#s-add").count()) {
    const addOne = async (nth) => {
      await click("#s-add", "+ Adicionar exercício");
      await page.waitForSelector("#pk-list [data-pick]", { timeout: 4000 }).catch(() => issue("seletor de exercício vazio", W("treino livre")));
      await modalCheck("treino livre: escolher exercício");
      await page.locator("#pk-list [data-pick]").nth(nth).click(); await page.waitForTimeout(150);
    };
    await addOne(0); await addOne(1);
    let n = await page.locator("[data-dropitem]").count();
    if (n !== 2) issue("treino livre: exercício adicionado sem botão de tirar", W(`${n} botões`));
    else {
      await audit(page, W("treino livre com exercícios"));
      await click("[data-dropitem]", "Tirar do treino (escolhi errado)");
      n = await page.locator("[data-dropitem]").count();
      if (n !== 1) issue("treino livre: não tirou o exercício errado", W(`${n} restantes`));
    }
    // modo lista: a carga digitada na 1ª série vale para as seguintes ainda vazias
    const l1 = page.locator("input[data-s='load_kg']").first();
    if (await l1.count()) {
      await l1.fill("50"); await l1.press("Tab"); await page.waitForTimeout(80);
      const l2 = await page.locator("input[data-s='load_kg']").nth(1).inputValue();
      if (l2 !== "50") issue("treino livre (lista): a carga da 1ª série não passou para a 2ª", W(`2ª série = "${l2}"`));
    }
    // modo guiado: também dá para tirar
    await click("#to-focus", "Treinar um exercício por vez");
    if (!(await page.locator("[data-fx='drop']").count())) issue("modo guiado: sem botão de tirar o exercício", W("treino livre"));
    await audit(page, W("treino livre guiado"));
    // toca "feita" em todas as séries: o descanso com a série editável aparece em TODAS, inclusive na última do exercício
    let nSets = 0;
    for (let k = 0; k < 8 && (await page.locator("[data-fx='set']").count()); k++) {
      await click("[data-fx='set']", "série feita (livre)"); nSets++;
      // descanso = barra no topo; a série recém-feita é ajustada em "Ajustar a série que você acabou de fazer"
      await page.evaluate(() => { const d = document.querySelector(".fx-just-wrap"); if (d) d.open = true; });
      if (!(await page.locator(".fx-just [data-lv='load_kg']").count())) { issue("treino livre guiado: depois de marcar a série não dá para ajustar carga/repetições", W(`série ${nSets}`)); break; }
      if (!(await page.locator(".fx-restbar #fx-rclock").count())) issue("treino livre guiado: sem cronômetro depois da série", W(`série ${nSets}`));
      await page.locator(".fx-just [data-lst='load_kg:2.5']").click(); await page.waitForTimeout(40);
      if (k === 0) await audit(page, W("treino livre guiado: descanso"));
      await click("[data-fx='r-skip']", "Estou pronto / Terminei");
    }
    if (nSets < 2) issue("treino livre guiado: não percorreu as séries", W(`${nSets} séries`));
    const kept = await page.evaluate(() => { const d = JSON.parse(localStorage.getItem("gg_session") || "{}"); const it = (d.items || [])[0] || {}; return (it.sets || []).map((x) => x.load_kg); });
    if (kept.some((v) => v == null)) issue("treino livre guiado: série marcada ficou sem carga depois do ajuste no descanso", W(JSON.stringify(kept)));
    await click("[data-fx='list']", "Ver lista");
    await click("#s-finish", "Finalizar treino livre");
    await page.waitForSelector(".saved-card", { timeout: 5000 }).catch(() => issue("treino livre: tela de resumo não apareceu", W("treino livre")));
    // logo depois de salvar o app pergunta "quão puxado foi": responde como a pessoa faria
    await page.waitForTimeout(900);
    const eff = page.locator("#modal:not([hidden]) [data-eff='7']");
    if (await eff.count()) { await audit(page, W("nota de esforço")); await eff.click(); await page.waitForTimeout(300); }
    await audit(page, W("treino livre salvo"));
    const q = page.locator(".saved-card [data-match]").first();
    if (await q.count()) { await q.scrollIntoViewIfNeeded(); await q.click(); await page.waitForTimeout(300); await audit(page, W("treino livre: respondeu se foi o treino do dia")); }
    await go(page, "#hoje", W("hoje depois do treino livre"));
    const sch2 = await (await fetch(`${base}/api/schedule`)).json();
    if (((sch2.goals || {}).muscles || []).length && !(await page.locator(".miss-week").count())) issue("Hoje sem o cartão 'O que falta esta semana'", W("hoje"));
  } else issue("treino livre não abriu", W(await page.evaluate(() => { const d = JSON.parse(localStorage.getItem("gg_session") || "null"); return `rascunho: ${d ? `${d.day_name} sched=${d.schedule_id} plan=${d.plan_id}` : "nenhum"} · tela: ${document.getElementById("view").innerText.slice(0, 80).replace(/\s+/g, " ")}`; })));

  // Perfil: editar e salvar
  await go(page, "#perfil", W("perfil"));
  await page.evaluate(() => { const d = document.querySelector("#sec-dados"); if (d) d.open = true; });
  await page.waitForTimeout(100);
  const save = page.locator("#save-profile");
  if (await save.count()) { await save.scrollIntoViewIfNeeded(); await save.click(); await page.waitForTimeout(300);
    const t = await page.evaluate(() => { const t = document.getElementById("toast"); return t && !t.hidden ? t.innerText : null; });
    if (!t) issue("salvar perfil sem confirmação visível", W("perfil"));
    else if (/erro|falha|invalid/i.test(t)) issue("salvar perfil deu erro", W(t));
    const tb = await page.evaluate(() => { const t = document.getElementById("toast"); if (!t || t.hidden) return null; const r = t.getBoundingClientRect(); const a = document.querySelector(".tabbar").getBoundingClientRect(); return [r.y, r.height, a.y]; });
    if (tb && tb[0] + tb[1] > tb[2] + 1) issue("aviso (toast) atrás da barra de abas", W(`perfil: ${tb.map(Math.round)}`));
  }
  // Esportes
  await go(page, "#esportes", W("esportes"));
  // Exercícios: busca
  await go(page, "#exercicios", W("exercícios"));
  const q = page.locator("#view input[type='search'], #view input").first();
  if (await q.count()) { await q.fill("supino"); await page.waitForTimeout(700); const n = await page.locator("#view [data-ex]").count(); if (!n) issue("busca por 'supino' sem resultado", W("exercícios"));
    else { await page.locator("#view [data-ex]").first().click(); await page.waitForTimeout(500); await modalCheck("detalhe do exercício"); await page.click(".modal-close").catch(() => {}); } }
  // voltar do navegador
  await page.goBack(); await page.waitForTimeout(400);
  const back = await page.evaluate(() => location.hash);
  if (!back || back === "#exercicios") issue("voltar do navegador não mudou de tela", W(back));
}

async function offline(context, page, base, tag) {
  await page.goto(`${base}/#hoje`); await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(800); // service worker instala
  const sw = await page.evaluate(async () => !!(navigator.serviceWorker && (await navigator.serviceWorker.getRegistration())));
  if (!sw) issue("service worker não registrado (app não abre sem internet)", tag);
  await context.setOffline(true);
  try {
    await page.reload({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(1200);
    const s = await page.evaluate(() => ({ top: !!document.querySelector(".topbar"), text: (document.getElementById("view") || {}).innerText || "" }));
    if (!s.top) issue("sem internet: app não abre", tag);
    else if (!s.text.trim()) issue("sem internet: tela em branco (sem aviso)", tag);
    else if (!/internet|conex|offline|rede/i.test(s.text)) issue("sem internet: sem aviso claro", `${tag}: «${s.text.trim().slice(0, 80)}»`);
  } finally { await context.setOffline(false); }
}

(async () => {
  const browser = await chromium.launch();
  let port = 8811;
  for (const persona of PERSONAS) {
    const srv = await startServer(persona, ++port);
    const base = `http://127.0.0.1:${port}`;
    try {
      const plans = await (await fetch(`${base}/api/plans`)).json();
      const hist = await (await fetch(`${base}/api/history`)).json().catch(() => ({ exercises: [] }));
      const routes = ["#hoje", "#planilhas", "#treinar", "#analise", "#evolucao", "#saude", "#esportes", "#exercicios", "#aparelhos", "#perfil", "#perfil/academia", "#perfil/foco", "#dev", "#bemestar", "#bemestar/yoga_estresse", "#social", "#especiais", "#especiais/arnold_golden_six", "#musica"];
      if (plans.length) routes.push(`#plano/${plans[0].id}`, `#treinar/${plans[0].id}/0`);
      if ((hist.exercises || []).length) routes.push(`#evolucao/${encodeURIComponent(hist.exercises[0].exercise_id)}`);
      for (const [dname, dev] of DEVICES) {
        for (const theme of THEMES) {
          const context = await browser.newContext({ ...dev, colorScheme: theme, locale: "pt-BR", timezoneId: "America/Sao_Paulo", serviceWorkers: "allow" });
          const page = await context.newPage();
          const tag = `[${persona} · ${dname} · ${theme}]`;
          page.on("pageerror", (e) => issue("erro de JavaScript", `${tag} ${String(e.message).slice(0, 120)} @ ${String(e.stack || "").split("\n").slice(1, 3).join(" ").replace(/http:\/\/127.0.0.1:\d+/g, "").slice(0, 200)}`));
          page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) issue("erro no console", `${tag} ${m.text().slice(0, 200)}`); });
          page.on("response", (r) => { if (r.status() >= 500 || (r.status() >= 400 && ![401, 404, 409, 501].includes(r.status()))) issue(`requisição com erro ${r.status()}`, `${tag} ${r.url().replace(base, "")}`); });
          const slow = dname === "Pixel 7" && theme === "dark";  // 4G fraco de academia: 300 ms de latência, ~1,6 Mbit/s
          if (slow) {
            const cdp = await context.newCDPSession(page);
            await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 300, downloadThroughput: 200 * 1024, uploadThroughput: 100 * 1024 });
          }
          await page.goto(`${base}/`); await page.waitForLoadState("networkidle").catch(() => {});
          for (const h of routes) {
            const t0 = Date.now();
            await go(page, h, `${tag} ${h}`);
            if (slow) { const ms = Date.now() - t0; (STATS.slow_ms = STATS.slow_ms || []).push(ms); if (ms > 4000) issue("tela lenta no 4G fraco (> 4 s)", `${tag} ${h}: ${ms} ms`); }
            if (SHOTS && (dname === "iPhone SE" || dname === "Fold capa")) {
              fs.mkdirSync(SHOTS, { recursive: true });
              await page.screenshot({ path: path.join(SHOTS, `${persona}-${dname.replace(/\s/g, "")}-${theme}-${h.replace(/[#/%]/g, "_")}.png`), fullPage: true });
            }
          }
          if (theme === "light" && !dname.includes("iPad")) {
            await flows(page, base, persona, tag).catch((e) => issue("fluxo interrompido", `${tag} ${String(e.message).replace(/\s+/g, " ").slice(-700)}`));
            if (persona === "musculacao" && dname === "iPhone SE") await offline(context, page, base, tag);
          }
          await context.close();
          // estado limpo para o próximo aparelho (fluxos mexem no banco)
        }
        if (persona !== "novo") { /* mesmo banco: fluxos acumulam, como um usuário real */ }
      }
    } finally { srv.kill(); }
  }
  await browser.close();
  console.log(`\n=== telas: ${PERSONAS.length} personas × ${DEVICES.length} aparelhos × ${THEMES.length} temas ===`);
  const sorted = [...ISSUES.entries()].sort((a, b) => b[1].n - a[1].n);
  for (const [k, v] of sorted) { console.log(`${String(v.n).padStart(5)}  ${k}`); v.ex.forEach((x) => console.log(`         ${x.slice(0, 260)}`)); }
  if (STATS.slow_ms) { const q = STATS.slow_ms.sort((a, b) => a - b); console.log(`--- 4G fraco: mediana ${q[q.length >> 1]} ms · máx ${q[q.length - 1]} ms`); }
  const s = STATS.render_ms.sort((a, b) => a - b);
  console.log(`\n--- tempo até renderizar a tela: mediana ${s[s.length >> 1]} ms · p90 ${s[Math.floor(s.length * 0.9)]} ms · máx ${s[s.length - 1]} ms · n ${s.length}`);
})();
