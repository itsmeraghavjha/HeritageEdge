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
          <div class="lever" data-key="${l.key}">
            <div class="lever-top">
              <label for="num-${l.key}">${HE.esc(l.label)}</label>
              <span class="lever-input">
                <input type="number" id="num-${l.key}" min="${l.min}" max="${l.max}" step="${l.step}" inputmode="decimal">
                <span class="unit">${HE.esc(l.unit.trim())}</span>
              </span>
            </div>
            <input type="range" id="rng-${l.key}" aria-label="${HE.esc(l.label)}"
                   min="${l.min}" max="${l.max}" step="${l.step}">
            <p class="hint">${HE.esc(l.hint)}</p>
          </div>`).join('')}
      </fieldset>`).join('');

    HE.FLAT.forEach((l) => {
      const num = $('num-' + l.key), rng = $('rng-' + l.key);
      const set = (raw) => {
        let v = parseFloat(raw);
        if (Number.isNaN(v)) v = HE.DEFAULTS[l.key];
        v = Math.min(l.max, Math.max(l.min, v));
        state.levers[l.key] = v;
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

  /* ---------- scope chips ---------- */
  function renderScope() {
    const section = (title, key, items) => `
      <div class="chip-group">
        <p class="chip-title">${title}</p>
        <div class="chips" data-scope="${key}">
          ${items.map((it) => `<button type="button" class="chip" data-id="${it.id}"
              aria-pressed="${state.scope[key].includes(it.id)}">${HE.esc(it.name)}</button>`).join('')}
        </div>
      </div>`;
    $('scopeBody').innerHTML =
      section('Chains', 'chains', meta.chains) +
      section('States', 'states', meta.states) +
      section('SKUs', 'skus', meta.skus) +
      '<button type="button" class="link-btn" id="scopeClear">Clear selection</button>';

    $('scopeBody').addEventListener('click', (e) => {
      const chip = e.target.closest('.chip');
      if (chip) {
        const key = chip.parentElement.dataset.scope;
        const list = state.scope[key];
        const i = list.indexOf(chip.dataset.id);
        if (i >= 0) list.splice(i, 1); else list.push(chip.dataset.id);
        chip.setAttribute('aria-pressed', String(i < 0));
        changed();
      } else if (e.target.id === 'scopeClear') {
        state.scope = HE.emptyScope();
        $('scopeBody').querySelectorAll('.chip').forEach((c) => c.setAttribute('aria-pressed', 'false'));
        changed();
      }
    });
    updateScopeSummary();
  }

  function updateScopeSummary(res) {
    const names = (key, items, all) => {
      const ids = state.scope[key];
      if (!ids.length) return all;
      const n = items.filter((i) => ids.includes(i.id)).map((i) => i.name);
      return n.length > 2 ? n.length + ' ' + all.replace('All ', '') : n.join(', ');
    };
    let text = [
      names('chains', meta.chains, 'All chains'),
      names('states', meta.states, 'all states'),
      names('skus', meta.skus, 'all SKUs'),
    ].join(', ');
    if (res && res.rows_in_scope !== res.rows_total) {
      text += ` (${res.rows_in_scope} of ${res.rows_total} lines)`;
    }
    $('scopeSummary').textContent = text;
  }

  /* ---------- results ---------- */
  function kpi(label, value, deltaText, deltaTone, sub) {
    return `<div class="kpi">
      <p class="kpi-label">${label}</p>
      <p class="kpi-value">${value}</p>
      <p class="kpi-delta ${deltaTone}">${deltaText}</p>
      <p class="kpi-sub">${sub}</p>
    </div>`;
  }

  function renderKpis(r) {
    const b = r.base, s = r.sim;
    const dNet = s.net - b.net, dCm = s.cm - b.cm, dUnits = s.units - b.units;
    $('kpis').innerHTML =
      kpi('Net revenue', HE.inr(s.net),
          `${HE.inrSigned(dNet)} (${HE.relText(HE.rel(b.net, s.net))})`, HE.tone(dNet, true, 500),
          `Baseline ${HE.inr(b.net)}`) +
      kpi('Gross margin', HE.pct(s.gm_pct),
          HE.pp(s.gm_pct - b.gm_pct), HE.tone(s.gm_pct - b.gm_pct, true, 0.0005),
          `${HE.inr(s.gm)}, baseline ${HE.pct(b.gm_pct)}`) +
      kpi('Contribution', HE.inr(s.cm),
          `${HE.inrSigned(dCm)} (${HE.relText(HE.rel(b.cm, s.cm))})`, HE.tone(dCm, true, 500),
          `${HE.pct(s.cm_pct)} of net revenue, baseline ${HE.pct(b.cm_pct)}`) +
      kpi('Volume in units', (s.units / 1e5).toFixed(1) + ' lakh',
          HE.relText(HE.rel(b.units, s.units)), HE.tone(dUnits, true, 1),
          `Baseline ${(b.units / 1e5).toFixed(1)} lakh units`);
  }

  function renderBridge(steps) {
    const W = 760, H = 320, padL = 16, padR = 16, top = 34, bottom = 56;
    const plotH = H - top - bottom;
    const n = steps.length;
    const slot = (W - padL - padR) / n;
    const barW = Math.min(76, slot * 0.64);

    let run = steps[0].value;
    const levels = [run];
    steps.forEach((s, i) => {
      if (s.kind === 'delta') { run += s.value; levels.push(run); }
      else if (i > 0) levels.push(s.value);
    });
    const hi = Math.max(...levels), lo = Math.min(...levels);
    let span = hi - lo;
    if (span < hi * 0.05) span = hi * 0.05;
    let axisMin = lo - span;
    const truncated = axisMin > 0;
    if (!truncated) axisMin = 0;
    const axisMax = hi + span * 0.3;
    const y = (v) => top + plotH - ((v - axisMin) / (axisMax - axisMin)) * plotH;
    const floor = top + plotH;

    let cursor = steps[0].value;
    let links = '';
    let bars = '';
    steps.forEach((s, i) => {
      const x = padL + slot * i + (slot - barW) / 2;
      const cx = x + barW / 2;
      let y1, y2, cls, label;
      if (s.kind === 'total') {
        y1 = y(s.value); y2 = floor;
        cls = i === 0 ? 'bar-base' : 'bar-scn';
        label = HE.inr(s.value);
        cursor = s.value;
      } else {
        const from = cursor, to = cursor + s.value;
        y1 = Math.min(y(from), y(to)); y2 = Math.max(y(from), y(to));
        const flat = Math.abs(s.value) < 500;
        cls = flat ? 'bar-zero' : s.value > 0 ? 'bar-up' : 'bar-down';
        label = flat ? 'No change' : HE.inrSigned(s.value);
        cursor = to;
      }
      if (i < n - 1) {
        links += `<line class="bridge-link" x1="${x + barW}" x2="${x + slot}" y1="${y(cursor)}" y2="${y(cursor)}"/>`;
      }
      const h = Math.max(2, y2 - y1);
      bars += `<rect class="${cls}" x="${x}" y="${y1}" width="${barW}" height="${h}" rx="2"/>`;
      bars += `<text class="bar-value ${cls}-t" x="${cx}" y="${y1 - 8}" text-anchor="middle">${label}</text>`;
      bars += `<text class="bar-label" x="${cx}" y="${H - bottom + 22}" text-anchor="middle">${HE.esc(s.label)}</text>`;
    });
    const parts = `<line class="axis" x1="${padL}" x2="${W - padR}" y1="${floor}" y2="${floor}"/>${links}${bars}`;

    const first = steps[0].value, last = steps[n - 1].value;
    $('bridge').innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Contribution moves from ${HE.inr(first)} to ${HE.inr(last)}">${parts}</svg>`;
    $('bridgeNote').textContent = truncated
      ? `Bars start at ${HE.inr(axisMin)} so small changes stay visible.`
      : '';
  }

  const SUMMARY_ROWS = [
    ['Volume (’000 units)', 'units', 'k', true],
    ['Gross sales', 'gross', 'l', true],
    ['Promo', 'promo', 'l', false],
    ['Rebates', 'rebates', 'l', false],
    ['Net revenue', 'net', 'l', true, 'strong'],
    ['COGS', 'cogs', 'l', false],
    ['Gross margin', 'gm', 'l', true, 'strong'],
    ['Logistics', 'logistics', 'l', false],
    ['Sales and distribution', 'sd', 'l', false],
    ['Contribution', 'cm', 'l', true, 'strong'],
    ['Gross margin %', 'gm_pct', 'p', true],
    ['Contribution %', 'cm_pct', 'p', true],
  ];

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

  function renderBreakdown(r) {
    const rows = r.breakdown[activeDim].map((n) => {
      const dNet = n.sim.net - n.base.net, dCm = n.sim.cm - n.base.cm;
      return `<tr><th scope="row">${HE.esc(n.label)}</th>
        <td>${HE.lakh(n.sim.net)}</td>
        <td class="${HE.tone(dNet, true, 500)}">${HE.lakhSigned(dNet)}</td>
        <td>${HE.lakh(n.sim.cm)}</td>
        <td class="${HE.tone(dCm, true, 500)}">${HE.lakhSigned(dCm)}</td>
        <td>${HE.pct(n.sim.cm_pct)}</td></tr>`;
    }).join('');
    $('breakdown').innerHTML = `<thead><tr><th scope="col">${activeDim === 'sku' ? 'SKU' : activeDim === 'state' ? 'State' : 'Chain'}</th>
      <th scope="col">Net revenue</th><th scope="col">Change</th>
      <th scope="col">Contribution</th><th scope="col">Change</th><th scope="col">CM %</th></tr></thead>
      <tbody>${rows}</tbody>`;
  }

  function render(r) {
    latest = r;
    renderKpis(r);
    renderBridge(r.bridge);
    renderSummary(r);
    renderBreakdown(r);
    updateScopeSummary(r);
  }

  /* ---------- data flow ---------- */
  function showError() {
    $('kpis').innerHTML = '<p class="error">Couldn’t load the numbers. Check that the server is running, then reload the page.</p>';
  }

  async function refresh() {
    const id = ++requestId;
    try {
      const res = await (await HE.postJson('/api/simulate', { levers: state.levers, scope: state.scope })).json();
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

  function applyScenario(levers, scope) {
    state.levers = Object.assign({}, HE.DEFAULTS, levers || {});
    if (scope) state.scope = Object.assign(HE.emptyScope(), scope);
    syncAllLevers();
    renderScope();
    changed();
  }

  $('loadSelect').addEventListener('change', (e) => {
    const v = e.target.value;
    if (v.startsWith('p:')) {
      const p = HE.PRESETS.find((x) => x.id === v.slice(2));
      if (p) applyScenario(p.levers, null);
    } else if (v.startsWith('s:')) {
      const s = HE.loadSaved()[Number(v.slice(2))];
      if (s) applyScenario(s.levers, s.scope);
    }
    e.target.value = '';
  });

  $('resetBtn').addEventListener('click', () => applyScenario({}, HE.emptyScope()));

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
    list.push({ name, levers: Object.assign({}, state.levers), scope: JSON.parse(JSON.stringify(state.scope)) });
    HE.storeSaved(list);
    renderLoadSelect();
  }

  document.querySelectorAll('.tabs [role=tab]').forEach((tab) => {
    tab.addEventListener('click', () => {
      activeDim = tab.dataset.dim;
      document.querySelectorAll('.tabs [role=tab]').forEach((t) =>
        t.setAttribute('aria-selected', String(t === tab)));
      if (latest) renderBreakdown(latest);
    });
  });

  /* ---------- start ---------- */
  fetch('/api/meta').then((r) => r.json()).then((m) => {
    meta = m;
    renderLevers();
    renderScope();
    renderLoadSelect();
    refresh();
  }).catch(showError);
})();
