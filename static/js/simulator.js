(function () {
  'use strict';
  const HE = window.HE;
  const $ = (id) => document.getElementById(id);
  const state = HE.loadState();
  let meta = null;
  let activeDim = 'chain';
  let latest = null;
  let requestId = 0;
  let timer = null;

  /* ---------- levers panel ---------- */
  function fillPct(input) {
    const min = Number(input.min), max = Number(input.max);
    input.style.setProperty('--fill', ((Number(input.value) - min) / (max - min)) * 100 + '%');
  }

  function renderLevers() {
    $('leverGroups').innerHTML = HE.GROUPS.map((g) => `
      <fieldset class="lever-group">
        <legend>${HE.esc(g.title)}</legend>
        ${g.note ? `<p class="group-note">${HE.esc(g.note)}</p>` : ''}
        ${g.levers.map((l) => `
          <div class="lever" data-key="${l.key}" title="${HE.esc(l.hint)}">
            <div class="lever-top">
              <label for="num-${l.key}">${HE.esc(l.label)}</label>
              <span class="lever-input">
                <input type="number" id="num-${l.key}" min="${l.min}" max="${l.max}" step="${l.step}" inputmode="decimal">
                <span class="unit">${HE.esc(l.unit.trim())}</span>
              </span>
            </div>
            <input type="range" id="rng-${l.key}" aria-label="${HE.esc(l.label)}"
                   min="${l.min}" max="${l.max}" step="${l.step}">
            <p class="hint visually-hidden">${HE.esc(l.hint)}</p>
          </div>`).join('')}
      </fieldset>`).join('');

    HE.FLAT.forEach((l) => {
      const num = $('num-' + l.key), rng = $('rng-' + l.key);
      const set = (raw) => {
        let v = parseFloat(raw);
        if (Number.isNaN(v)) v = HE.DEFAULTS[l.key];
        v = Math.min(l.max, Math.max(l.min, v));
        state.levers[l.key] = v;
        if (v !== HE.DEFAULTS[l.key]) HE.trackLever(l.key);
        syncLever(l);
        changed();
      };
      rng.addEventListener('input', () => set(rng.value));
      num.addEventListener('change', () => set(num.value));
      syncLever(l);
    });
  }

  function syncLever(l) {
    const v = state.levers[l.key];
    const num = $('num-' + l.key), rng = $('rng-' + l.key);
    if (document.activeElement !== num) num.value = v;
    rng.value = v;
    fillPct(rng);
    num.closest('.lever').classList.toggle('is-changed', v !== HE.DEFAULTS[l.key]);
  }
  function syncAllLevers() { HE.FLAT.forEach(syncLever); }

  /* ---------- scope: where price, promo and chain terms apply ---------- */
  let selectors = null;

  function renderScope() {
    selectors = HE.buildSelectors({ host: $('scopeDims'), dims: meta.dims, combos: meta.combos, holder: state, key: 'scope',
                                    onChange: changed, mode: 'inline' });
    updateScopeSummary();
  }

  function updateScopeSummary(res) {
    let text = HE.describeSelection(state.scope, meta.dims) || (HE.LIMITED ? 'All my accounts' : 'Everything');
    if (res && res.rows_in_scope !== res.rows_total) {
      text += ` (${res.rows_in_scope.toLocaleString('en-IN')} of ${res.rows_total.toLocaleString('en-IN')} lines)`;
    }
    $('scopeSummary').textContent = text;
    $('scopeClear').hidden = !HE.hasSelection(state.scope);
  }

  $('scopeClear').addEventListener('click', () => {
    state.scope = HE.emptyScope();
    selectors.refresh();
    changed();
  });

  /* ---------- results ---------- */
  // One card = what the scenario gives, how it differs from today in plain words, and today's figure.
  function kpi(label, value, change, today, key, extra) {
    return `<div class="kpi${key ? ' kpi-key' : ''}">
      <p class="kpi-label">${label}</p>
      <p class="kpi-value">${value}</p>
      <p class="kpi-change ${change.tone}">${change.text}</p>
      <p class="kpi-sub">Today: ${today}</p>
      ${extra ? `<p class="kpi-sub">${extra}</p>` : ''}
    </div>`;
  }
  // "₹97.3 L lower (−16.0%)", or "No change" when the difference is too small to matter.
  function change(delta, fmt, tone, relPct) {
    if (tone === 'flat') return { tone, text: 'No change' };
    const word = delta > 0 ? 'higher' : 'lower';
    return { tone, text: `${fmt(Math.abs(delta))} ${word}${relPct == null ? '' : ` (${HE.relText(relPct)})`}` };
  }

  function renderKpis(r) {
    const b = r.base, s = r.sim;
    const dNet = s.net - b.net, dGm = s.gm_pct - b.gm_pct, dCm = s.cm - b.cm, dNm = s.nm - b.nm, dUnits = s.units - b.units;
    const money = (d, v) => change(d, HE.inr, HE.tone(d, true, 500), HE.rel(v, v + d));
    const lakhPacks = (n) => (n / 1e5).toFixed(1) + ' lakh';
    const cards = [
      kpi('Net revenue', HE.inr(s.net), money(dNet, b.net), HE.inr(b.net)),
      kpi('Gross margin', HE.pct(s.gm_pct), change(dGm, (d) => HE.pct(d).replace('%', ' pp'), HE.tone(dGm, true, 0.0005)),
          HE.pct(b.gm_pct)),
      kpi('Contribution', HE.inr(s.cm), money(dCm, b.cm), HE.inr(b.cm), true),
    ];
    if (!HE.LIMITED) cards.push(kpi('Net margin', HE.inr(s.nm), money(dNm, b.nm), HE.inr(b.nm)));
    cards.push(kpi('Volume (packs)', lakhPacks(s.units), change(dUnits, (d) => lakhPacks(d), HE.tone(dUnits, true, 1),
                   HE.rel(b.units, s.units)), lakhPacks(b.units), false, qtyText(s)));
    $('kpis').innerHTML = cards.join('');
  }

  // Litres and kg are kept apart (nothing is converted between them) and shown per day: monthly volume / days.
  function qtyText(m) {
    const days = (meta && meta.days) || 30, parts = [];
    const n = (v) => Math.round(v / days).toLocaleString('en-IN');
    if (m.litres > 0) parts.push(`${n(m.litres)} LPD`);
    if (m.kg > 0) parts.push(`${n(m.kg)} KGPD`);
    return parts.join(' + ');
  }

  const SUMMARY_ALL = [
    ['Volume (’000 packs)', 'units', 'k', true],
    ['Gross sales', 'gross', 'l', true],
    ['Promo', 'promo', 'l', false],
    ['Other deductions', 'rebates', 'l', false],
    ['Net revenue', 'net', 'l', true, 'strong'],
    ['COGS', 'cogs', 'l', false],
    ['Gross margin', 'gm', 'l', true, 'strong'],
    ['Over/under', 'ou', 'l', false],
    ['S&D / freight', 'logistics', 'l', false],
    ['Contribution', 'cm', 'l', true, 'strong'],
    ['P&A', 'pa', 'l', false],
    ['Interest', 'interest', 'l', false],
    ['Marketing', 'mktg', 'l', false],
    ['Net margin', 'nm', 'l', true, 'strong'],
    ['Gross margin %', 'gm_pct', 'p', true],
    ['Contribution %', 'cm_pct', 'p', true],
    ['Net margin %', 'nm_pct', 'p', true],
  ];

  const SUMMARY_ROWS = SUMMARY_ALL.filter((row) => HE.allowedKey(row[1]));

  function renderSummary(r) {
    const rows = SUMMARY_ROWS.map(([label, key, kind, goodUp, strong]) => {
      const b = r.base[key], s = r.sim[key], d = s - b;
      let bt, st, dt, eps;
      if (kind === 'k') { bt = HE.thousands(b); st = HE.thousands(s); dt = HE.thousands(d, true); eps = 50; }
      else if (kind === 'l') { bt = HE.lakh(b); st = HE.lakh(s); dt = HE.lakhSigned(d); eps = 500; }
      else { bt = HE.pct(b); st = HE.pct(s); dt = HE.pp(d); eps = 0.0005; }
      return `<tr class="${strong || ''}"><th scope="row">${label}</th>
        <td>${bt}</td><td>${st}</td><td class="${HE.tone(d, goodUp, eps)}">${dt}</td></tr>`;
    }).join('');
    $('summary').innerHTML = `<thead><tr><th scope="col">Line</th><th scope="col">Baseline</th>
      <th scope="col">Scenario</th><th scope="col">Change</th></tr></thead><tbody>${rows}</tbody>`;
  }

  function dimName(id) {
    const d = meta.dims.find((x) => x.id === id);
    return d ? d.name : id;
  }

  let tabsBuilt = false;
  function buildTabs(keys) {
    $('breakTabs').innerHTML = keys.map((k) =>
      `<button type="button" role="tab" data-dim="${k}" aria-selected="${k === activeDim}">${HE.esc(dimName(k))}</button>`).join('');
    tabsBuilt = true;
  }
  $('breakTabs').addEventListener('click', (e) => {
    const tab = e.target.closest('[role=tab]');
    if (!tab) return;
    activeDim = tab.dataset.dim;
    $('breakTabs').querySelectorAll('[role=tab]').forEach((t) => t.setAttribute('aria-selected', String(t === tab)));
    if (latest) renderBreakdown(latest);
  });

  /* ---------- row adjustments: sliders on a single chain, product category, SKU or state ---------- */
  let openRow = null;                       // { dim, id }: the row whose sliders are open
  const ROW_LEVERS = () => HE.FLAT.filter((l) => HE.ADJUST_KEYS.includes(l.key));
  const rowAdjust = (dim, id) => (state.adjust[dim] || {})[id] || {};
  function setRowAdjust(dim, id, key, v) {
    const cur = Object.assign({}, rowAdjust(dim, id));
    if (v) cur[key] = v; else delete cur[key];
    const by = Object.assign({}, state.adjust[dim]);
    if (Object.keys(cur).length) by[id] = cur; else delete by[id];
    if (Object.keys(by).length) state.adjust[dim] = by; else delete state.adjust[dim];
  }
  function clearRowAdjust(dim, id) {
    ROW_LEVERS().forEach((l) => setRowAdjust(dim, id, l.key, 0));
  }

  function rowVerdict(n) {
    const v = n.volume;
    if (!v) return '';
    const dCm = n.sim.cm - n.base.cm;
    const now = `Contribution ${HE.lakhSigned(dCm)} L against today.`;
    if (v.status === 'neutral') return `${now} Nothing changes what a pack earns here.`;
    if (v.status === 'unreachable') return `${now} Each pack earns nothing or loses money at these terms, so more volume will not pay it back.`;
    if (v.status === 'ahead') return `${now} Already ahead at today’s volume. Volume here could fall by ${HE.pct(-v.required)} before it drops below today.`;
    const has = Math.abs(v.assumed) < 1e-9 ? 'No volume change is set for this row yet.'
      : `Volume here is ${HE.relText(v.assumed)} in this scenario, which ${v.assumed >= v.required - 1e-9 ? 'covers it' : 'falls short'}.`;
    return `${now} Needs ${HE.pct(v.required)} more volume to match today. ${has}`;
  }

  function editorHtml(n) {
    const cur = rowAdjust(activeDim, n.id);
    return `<div class="row-editor" data-dim="${activeDim}" data-id="${HE.esc(n.id)}">
      <p class="row-editor-head">Changes for <strong>${HE.esc(n.label)}</strong> only. They add to the levers on the left.</p>
      <div class="row-levers">${ROW_LEVERS().map((l) => `
        <div class="lever${cur[l.key] ? ' is-changed' : ''}" data-key="${l.key}">
          <div class="lever-top"><label for="rn-${l.key}">${HE.esc(l.label)}</label>
            <span class="lever-input"><input type="number" id="rn-${l.key}" data-key="${l.key}" min="${l.min}" max="${l.max}" step="${l.step}" value="${cur[l.key] || 0}" inputmode="decimal">
            <span class="unit">${HE.esc(l.unit.trim())}</span></span></div>
          <input type="range" id="rr-${l.key}" data-key="${l.key}" aria-label="${HE.esc(l.label)}" min="${l.min}" max="${l.max}" step="${l.step}" value="${cur[l.key] || 0}">
        </div>`).join('')}</div>
      <p class="row-result" data-result>${HE.esc(rowVerdict(n))}</p>
      <div class="row-actions"><button type="button" class="btn btn-small" data-act="reset-row">Reset this row</button>
        <button type="button" class="btn btn-small" data-act="close-row">Done</button></div></div>`;
  }

  const promoPct = (m) => (m.gross ? m.promo / m.gross : 0);
  const qtyOrDash = (m) => qtyText(m) || '–';
  function rowCellList(n) {
    const dNet = n.sim.net - n.base.net, dCm = n.sim.cm - n.base.cm;
    const dVol = n.sim.units - n.base.units, dPromo = promoPct(n.sim) - promoPct(n.base);
    return [['qty-col', qtyOrDash(n.base)], [`qty-col ${HE.tone(dVol, true, 0.5)}`, qtyOrDash(n.sim)],
      ['', HE.pct(promoPct(n.base))], [HE.tone(dPromo, false, 0.0005), HE.pct(promoPct(n.sim))],
      ['', HE.lakh(n.sim.net)], [HE.tone(dNet, true, 500), HE.lakhSigned(dNet)],
      ['', HE.lakh(n.sim.cm)], [HE.tone(dCm, true, 500), HE.lakhSigned(dCm)], ['', HE.pct(n.sim.cm_pct)]];
  }
  const rowCells = (n) => rowCellList(n).map(([cls, html]) => `<td${cls ? ` class="${cls}"` : ''}>${html}</td>`).join('');
  const adjustLabel = (n) => (Object.keys(rowAdjust(activeDim, n.id)).length ? 'Adjusted' : 'Adjust');
  const isOpen = (n) => openRow && openRow.dim === activeDim && openRow.id === n.id;

  function updateRowNote() {
    const count = HE.adjustCount(state.adjust);
    $('rowNote').hidden = !count;
    $('rowNote').firstElementChild.textContent = `${count} row${count > 1 ? 's' : ''} adjusted on their own. They are included in every number on this page.`;
  }

  // MC filter: all rows, only rows whose scenario MC is zero or more, or only rows below zero.
  let mcFilter = 'all';
  const passesMc = (n) => mcFilter === 'all' || (mcFilter === 'pos' ? n.sim.cm >= 0 : n.sim.cm < 0);
  $('mcFilter').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-mc]');
    if (!btn) return;
    mcFilter = btn.dataset.mc;
    $('mcFilter').querySelectorAll('[data-mc]').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
    $('breakdown').dataset.dim = '';          // force a full redraw
    if (latest) renderBreakdown(latest);
  });

  function renderBreakdown(r) {
    const all = r.breakdown[activeDim];
    // The row being adjusted always stays in the table, even if its MC crosses zero while you drag.
    const nodes = all.filter((n) => passesMc(n) || isOpen(n));
    $('mcCount').textContent = mcFilter === 'all' ? '' : `Showing ${nodes.length} of ${all.length} rows`;
    const canAdjust = HE.ADJUST_DIMS.includes(activeDim);
    updateRowNote();
    const table = $('breakdown');
    // While a row's sliders are open the table is updated in place, so a drag is not interrupted.
    if (openRow && openRow.dim === activeDim && table.dataset.dim === activeDim
        && table.querySelector(`.row-editor[data-id="${CSS.escape(openRow.id)}"]`)) {
      const done = nodes.every((n) => {
        const tr = table.querySelector(`tr[data-id="${CSS.escape(n.id)}"]`);
        if (!tr) return false;
        rowCellList(n).forEach(([cls, html], i) => { const td = tr.children[i + 1]; td.className = cls; td.innerHTML = html; });
        tr.querySelector('.adjust-btn').textContent = adjustLabel(n);
        tr.querySelector('.adjust-btn').classList.toggle('is-adjusted', adjustLabel(n) === 'Adjusted');
        if (isOpen(n)) {
          const res = table.querySelector('[data-result]');
          if (res) res.textContent = rowVerdict(n);
        }
        return true;
      });
      if (done) return;
    }
    const rows = nodes.map((n) => `<tr data-id="${HE.esc(n.id)}"><th scope="row">${HE.esc(n.label)}</th>
        ${rowCells(n)}
        <td class="adjust-cell">${canAdjust ? `<button type="button" class="link-btn adjust-btn${adjustLabel(n) === 'Adjusted' ? ' is-adjusted' : ''}" data-act="open-row" aria-expanded="${isOpen(n)}">${adjustLabel(n)}</button>` : ''}</td></tr>
      ${isOpen(n) ? `<tr class="row-edit"><td colspan="11">${editorHtml(n)}</td></tr>` : ''}`).join('');
    table.dataset.dim = activeDim;
    table.innerHTML = `<thead>
      <tr class="group-head"><th></th><th colspan="2" class="grp">Volume per day</th><th colspan="2" class="grp">Promo, % of invoice price</th>
        <th colspan="5" class="grp">Rupees lakh a month</th><th></th></tr>
      <tr><th scope="col">${HE.esc(dimName(activeDim))}</th>
      <th scope="col">Today</th><th scope="col">Scenario</th><th scope="col">Today</th><th scope="col">Scenario</th>
      <th scope="col">Net revenue</th><th scope="col">Change</th>
      <th scope="col">MC</th><th scope="col">Change</th><th scope="col">MC %</th><th scope="col"></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="11" class="empty-note">${mcFilter === 'neg' ? 'No rows with negative MC.' : 'No rows with positive MC.'}</td></tr>`}</tbody>`;
  }

  $('breakdown').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const tr = btn.closest('tr');
    const id = btn.closest('.row-editor') ? btn.closest('.row-editor').dataset.id : tr && tr.dataset.id;
    if (btn.dataset.act === 'open-row') {
      openRow = isOpenId(id) ? null : { dim: activeDim, id };
      renderBreakdown(latest);
    } else if (btn.dataset.act === 'close-row') {
      openRow = null;
      renderBreakdown(latest);
    } else if (btn.dataset.act === 'reset-row') {
      clearRowAdjust(activeDim, id);
      changed();
      $('breakdown').querySelectorAll('.row-editor [data-key]').forEach((el) => { if (el.type) el.value = 0; });
      $('breakdown').querySelectorAll('.row-editor .lever').forEach((el) => el.classList.remove('is-changed'));
      $('breakdown').querySelectorAll('.row-editor input[type=range]').forEach(fillPct);
    }
  });
  const isOpenId = (id) => openRow && openRow.dim === activeDim && openRow.id === id;

  function onRowInput(e) {
    const el = e.target;
    const box = el.closest('.row-editor');
    if (!box || !el.dataset.key) return;
    const l = HE.FLAT.find((x) => x.key === el.dataset.key);
    let v = parseFloat(el.value);
    if (Number.isNaN(v)) v = 0;
    v = Math.min(l.max, Math.max(l.min, v));
    setRowAdjust(box.dataset.dim, box.dataset.id, l.key, v);
    const lever = el.closest('.lever');
    const other = lever.querySelector(el.type === 'range' ? 'input[type=number]' : 'input[type=range]');
    other.value = v;
    if (other.type === 'range') fillPct(other); else fillPct(el);
    lever.classList.toggle('is-changed', v !== 0);
    changed();
  }
  $('breakdown').addEventListener('input', (e) => { if (e.target.type === 'range') onRowInput(e); });
  $('breakdown').addEventListener('change', (e) => { if (e.target.type === 'number') onRowInput(e); });

  $('rowClear').addEventListener('click', () => {
    state.adjust = {};
    openRow = null;
    $('breakdown').dataset.dim = '';
    changed();
  });

  /* ---------- volume check: the volume you expect, what the model suggests, and the breakeven ---------- */
  function render(r) {
    latest = r;
    if (!tabsBuilt) buildTabs(Object.keys(r.breakdown));
    renderKpis(r);
    renderSummary(r);
    renderBreakdown(r);
    updateScopeSummary(r);
    const sel = r.rows_in_scope !== r.rows_total;
    const share = r.whole.units ? r.base.units / r.whole.units : 0;
    $('viewNote').textContent = sel
      ? `Showing your selection only: ${r.rows_in_scope.toLocaleString('en-IN')} of ${r.rows_total.toLocaleString('en-IN')} lines, ${HE.pct(share)} of today’s volume.`
      : (HE.LIMITED ? 'Showing all your accounts. Pick chains, states or SKUs on the left to see just those.'
                    : 'Showing the whole business. Pick chains, states or SKUs on the left to see just those.');
    const rc = HE.adjustCount(state.adjust);
    if (rc) $('viewNote').textContent += ` ${rc} row${rc > 1 ? 's are' : ' is'} adjusted on ${rc > 1 ? 'their' : 'its'} own.`;
  }

  /* ---------- data flow ---------- */
  function showError() {
    $('kpis').innerHTML = '<p class="error">Couldn’t load the numbers. Check that the server is running, then reload the page.</p>';
  }

  async function refresh() {
    const id = ++requestId;
    try {
      const res = await (await HE.postJson('/api/simulate', { levers: HE.leversPayload(state), scope: state.scope })).json();
      if (id === requestId) render(res);
    } catch (e) { if (id === requestId) showError(); }
  }

  function changed() {
    HE.saveState(state);
    updateScopeSummary(latest);
    clearTimeout(timer);
    timer = setTimeout(refresh, 90);
  }

  /* ---------- scenarios ---------- */
  function renderLoadSelect() {
    const saved = HE.loadSaved();
    $('loadSelect').innerHTML =
      '<option value="">Load a scenario</option>' +
      '<optgroup label="Starting points">' +
      HE.PRESETS.map((p) => `<option value="p:${p.id}">${HE.esc(p.name)}</option>`).join('') + '</optgroup>' +
      (saved.length ? '<optgroup label="Your scenarios">' +
        saved.map((s, i) => `<option value="s:${i}">${HE.esc(s.name)}</option>`).join('') + '</optgroup>' : '');
  }

  function applyScenario(levers, scope, adjust) {
    state.levers = Object.assign({}, HE.DEFAULTS, levers || {});
    state.adjust = HE.normalizeAdjust(adjust);
    openRow = null;
    $('breakdown').dataset.dim = '';
    if (scope) state.scope = HE.pruneSelection(scope, meta.dims);
    syncAllLevers();
    selectors.refresh();
    changed();
  }

  $('loadSelect').addEventListener('change', (e) => {
    const v = e.target.value;
    if (v.startsWith('p:')) {
      const p = HE.PRESETS.find((x) => x.id === v.slice(2));
      if (p) applyScenario(p.levers, null, {});
    } else if (v.startsWith('s:')) {
      const s = HE.loadSaved()[Number(v.slice(2))];
      if (s) applyScenario(s.levers, s.scope, s.adjust);
    }
    e.target.value = '';
  });

  $('resetBtn').addEventListener('click', () => applyScenario({}, HE.emptyScope(), {}));

  const dialog = $('saveDialog');
  $('saveBtn').addEventListener('click', () => {
    $('saveName').value = '';
    if (dialog.showModal) dialog.showModal();
    else {
      const name = window.prompt('Scenario name');
      if (name) saveScenario(name);
    }
  });
  $('saveCancel').addEventListener('click', () => dialog.close());
  $('saveForm').addEventListener('submit', () => saveScenario($('saveName').value));

  function saveScenario(name) {
    name = name.trim();
    if (!name) return;
    const list = HE.loadSaved().filter((s) => s.name !== name);
    list.push({ name, levers: Object.assign({}, state.levers), scope: JSON.parse(JSON.stringify(state.scope)),
                adjust: JSON.parse(JSON.stringify(state.adjust)) });
    HE.storeSaved(list);
    renderLoadSelect();
  }

  /* ---------- start ---------- */
  fetch('/api/meta').then((r) => r.json()).then((m) => {
    meta = m;
    state.scope = HE.pruneSelection(state.scope, meta.dims);
    renderLevers();
    renderScope();
    renderLoadSelect();
    refresh();
  }).catch(showError);
})();
