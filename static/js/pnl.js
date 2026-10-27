(function () {
  'use strict';
  const HE = window.HE;
  const $ = (id) => document.getElementById(id);
  const state = HE.loadState();
  const expanded = new Set();
  let mode = 'sim';
  let data = null;
  let meta = null;
  let requestId = 0;

  // [header, key, kind, good when up]
  const COLS = [
    ['Volume', 'units', 'k', true],
    ['Gross sales', 'gross', 'l', true],
    ['Promo', 'promo', 'l', false],
    ['Other deductions', 'rebates', 'l', false],
    ['Net revenue', 'net', 'l', true],
    ['COGS', 'cogs', 'l', false],
    ['Gross margin', 'gm', 'l', true],
    ['GM %', 'gm_pct', 'p', true],
    ['Over/under', 'ou', 'l', false],
    ['S&D / freight', 'logistics', 'l', false],
    ['Contribution', 'cm', 'l', true],
    ['CM %', 'cm_pct', 'p', true],
    ['P&A', 'pa', 'l', false],
    ['Interest', 'interest', 'l', false],
    ['Marketing', 'mktg', 'l', false],
    ['Net margin', 'nm', 'l', true],
    ['NM %', 'nm_pct', 'p', true],
  ];
  const STRONG = new Set(['net', 'gm', 'cm', 'nm']);

  function cell(node, col) {
    const [, key, kind, goodUp] = col;
    const b = node.base[key], s = node.sim[key];
    let text, cls = '';
    if (mode === 'delta') {
      const d = s - b;
      if (kind === 'k') text = HE.thousands(d, true);
      else if (kind === 'l') text = HE.lakhSigned(d);
      else text = HE.pp(d);
      cls = HE.tone(d, goodUp, kind === 'p' ? 0.0005 : kind === 'k' ? 50 : 500);
    } else {
      const v = mode === 'base' ? b : s;
      text = kind === 'k' ? HE.thousands(v) : kind === 'l' ? HE.lakh(v) : HE.pct(v);
      if (kind !== 'p' && v < 0) cls = 'down';
    }
    return `<td class="${cls} ${STRONG.has(key) ? 'strong' : ''}">${text}</td>`;
  }

  function rowHtml(node, depth) {
    const hasKids = !!(node.children && node.children.length);
    const open = expanded.has(node.id);
    const toggle = hasKids
      ? `<button type="button" class="caret" data-id="${HE.esc(node.id)}" aria-expanded="${open}"
           aria-label="${open ? 'Collapse' : 'Expand'} ${HE.esc(node.label)}"></button>`
      : '<span class="caret-space"></span>';
    let html = `<tr class="depth-${Math.min(depth, 3)}${hasKids ? ' has-kids' : ''}">
      <th scope="row"><span class="name" style="padding-left:${depth * 20}px">${toggle}<span>${HE.esc(node.label)}</span></span></th>
      ${COLS.map((c) => cell(node, c)).join('')}</tr>`;
    if (hasKids && open) html += node.children.map((c) => rowHtml(c, depth + 1)).join('');
    return html;
  }

  function dimName(id) {
    const d = meta.dims.find((x) => x.id === id);
    return d ? d.name : id;
  }

  function render() {
    const head = `<thead><tr><th scope="col" class="first">${data.dims.map(dimName).join(', then ')}</th>
      ${COLS.map((c) => `<th scope="col">${c[0]}</th>`).join('')}</tr></thead>`;
    const totalRow = `<tr class="total"><th scope="row"><span class="name">Total</span></th>
      ${COLS.map((c) => cell(data.total, c)).join('')}</tr>`;
    $('pnlTable').innerHTML = head + '<tbody>' + totalRow +
      data.tree.map((n) => rowHtml(n, 0)).join('') + '</tbody>';
    $('tableFoot').textContent = mode === 'delta'
      ? 'Scenario minus baseline. Rupees lakh a month, volume in thousand units, percentages in points.'
      : 'Rupees lakh a month. Volume in thousand units.';
  }

  function allIds(nodes, out) {
    nodes.forEach((n) => { if (n.children) { out.push(n.id); allIds(n.children, out); } });
    return out;
  }

  function banner() {
    const list = HE.changedLevers(state.levers);
    const scope = HE.describeSelection(state.scope, meta.dims);
    if (!list.length) {
      $('banner').className = 'banner';
      $('banner').innerHTML = 'No levers are set, so the scenario matches the baseline. <a href="/">Set levers in the simulator.</a>';
      return;
    }
    $('banner').className = 'banner is-live';
    $('banner').innerHTML = '<strong>Scenario applied:</strong> ' +
      list.map((l) => `${HE.esc(l.label)} ${HE.esc(l.text)}`).join(', ') +
      (scope ? `. Price, promo and chain-term levers apply only to: ${HE.esc(scope)}` : '') + '.';
  }

  function dims() {
    return DIM_IDS.map((id) => $(id).value).filter(Boolean);
  }

  async function load() {
    const id = ++requestId;
    try {
      const res = await (await HE.postJson('/api/pnl', { levers: state.levers, scope: state.scope, dims: dims() })).json();
      if (id !== requestId) return;
      data = res;
      if (!expanded.size) data.tree.forEach((n) => { if (n.children) expanded.add(n.id); });
      render();
    } catch (e) {
      $('pnlTable').innerHTML = '<tbody><tr><td class="error">Couldn’t load the P&amp;L. Check that the server is running, then reload the page.</td></tr></tbody>';
    }
  }

  const DIM_IDS = ['dim0', 'dim1', 'dim2', 'dim3', 'dim4', 'dim5'];
  const DEFAULT_DIMS = ['chain', 'state', 'city', 'sku', '', ''];

  // A level can't repeat one chosen above it, so grey those options out.
  function syncDims() {
    const chosen = new Set();
    DIM_IDS.forEach((id) => {
      const sel = $(id);
      [...sel.options].forEach((o) => { o.disabled = !!o.value && chosen.has(o.value); });
      if (sel.value && chosen.has(sel.value)) sel.value = '';
      if (sel.value) chosen.add(sel.value);
    });
  }

  function initSelects() {
    const opts = (withNone) => (withNone ? '<option value="">None</option>' : '') +
      meta.dims.map((d) => `<option value="${d.id}">${d.name}</option>`).join('');
    $('dimRow').innerHTML = DIM_IDS.map((id, i) => `
      <div class="field"><label for="${id}">${i === 0 ? 'Group by' : 'Then by'}</label>
      <select id="${id}" class="select">${opts(i > 0)}</select></div>`).join('');
    DIM_IDS.forEach((id, i) => { $(id).value = DEFAULT_DIMS[i]; });
    syncDims();
    DIM_IDS.forEach((id) => $(id).addEventListener('change', () => {
      syncDims();
      expanded.clear();
      load();
    }));
  }

  $('pnlTable').addEventListener('click', (e) => {
    const btn = e.target.closest('.caret');
    if (!btn) return;
    const id = btn.dataset.id;
    if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
    render();
    const again = document.querySelector(`.caret[data-id="${CSS.escape(id)}"]`);
    if (again) again.focus();
  });

  document.querySelectorAll('.seg [role=radio]').forEach((b) => b.addEventListener('click', () => {
    mode = b.dataset.mode;
    document.querySelectorAll('.seg [role=radio]').forEach((x) =>
      x.setAttribute('aria-checked', String(x === b)));
    if (data) render();
  }));

  $('expandAll').addEventListener('click', () => {
    if (!data) return;
    allIds(data.tree, []).forEach((id) => expanded.add(id));
    render();
  });
  $('collapseAll').addEventListener('click', () => { expanded.clear(); if (data) render(); });

  $('exportBtn').addEventListener('click', async () => {
    const btn = $('exportBtn');
    btn.disabled = true;
    try {
      const r = await HE.postJson('/api/export', { levers: state.levers, scope: state.scope, dims: dims() });
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = url;
      a.download = 'heritageedge-pnl.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      $('tableFoot').textContent = 'The export failed. Try again in a moment.';
    } finally { btn.disabled = false; }
  });

  fetch('/api/meta').then((r) => r.json()).then((m) => {
    meta = m;
    state.scope = HE.pruneSelection(state.scope, meta.dims);
    banner();
    initSelects();
    load();
  });
})();
