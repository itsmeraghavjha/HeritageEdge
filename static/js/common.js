(function () {
  'use strict';

  const DEFAULTS = {
    price_change_pct: 0,
    promo_add_pp: 0,
    trade_margin_add_pp: 0,
    rebate_add_pp: 0,
    volume_change_pct: 0,
    cogs_change_pct: 0,
    freight_change_pct: 0,
  };

  const GROUPS = [
    {
      title: 'Price and promotion',
      levers: [
        { key: 'price_change_pct', label: 'MRP change', min: -10, max: 10, step: 0.5, unit: '%', signed: true,
          hint: 'Moves shelf price and invoice price together.' },
        { key: 'promo_add_pp', label: 'Extra promo discount', min: -5, max: 15, step: 0.5, unit: ' pp', signed: true,
          hint: 'Added to each chain’s current promo, as a share of invoice value.' },
      ],
    },
    {
      title: 'Chain terms',
      levers: [
        { key: 'trade_margin_add_pp', label: 'Trade margin change', min: -3, max: 5, step: 0.25, unit: ' pp', signed: true,
          hint: 'Chain margin as a share of MRP. More margin pushes more volume.' },
        { key: 'rebate_add_pp', label: 'Other deductions and display fees', min: -2, max: 4, step: 0.25, unit: ' pp', signed: true,
          hint: 'The gap between list invoice and billed price (credit notes, fees, other schemes), as a share of invoice value. Some of it buys visibility.' },
      ],
    },
    {
      title: 'Volume you expect',
      note: 'Your own view of how much more or less these levers will sell. Applies to the chains, states and SKUs in scope.',
      levers: [
        { key: 'volume_change_pct', label: 'Volume change', min: -20, max: 50, step: 1, unit: '%', signed: true,
          hint: 'The change in packs sold that you expect from the levers above. The Volume check shows what the model suggests and what volume you need to break even.' },
      ],
    },
    {
      title: 'Costs',
      note: 'Costs apply to every chain, state and SKU.',
      levers: [
        { key: 'cogs_change_pct', label: 'COGS change', min: -10, max: 15, step: 0.5, unit: '%', signed: true,
          hint: 'Moves the whole COGS line from the MIS, for example a milk, packing or other input cost change.' },
        { key: 'freight_change_pct', label: 'S&D / freight cost', min: -10, max: 20, step: 0.5, unit: '%', signed: true,
          hint: 'Scales the whole sales, distribution and freight line from the MIS.' },
      ],
    },
  ];
  const FLAT = GROUPS.flatMap((g) => g.levers);

  const PRESETS = [
    { id: 'festive', name: 'Festive display push',
      levers: { promo_add_pp: 4, rebate_add_pp: 1, volume_change_pct: 6 } },
    { id: 'price3', name: 'Price increase of 3%', levers: { price_change_pct: 3 } },
    { id: 'cogs5', name: 'COGS up 5%', levers: { cogs_change_pct: 5 } },
    { id: 'cogs5price2', name: 'COGS up 5%, price up 2%',
      levers: { cogs_change_pct: 5, price_change_pct: 2 } },
    { id: 'margin1', name: 'Give chains 1 pp more margin', levers: { trade_margin_add_pp: 1 } },
  ];

  const STATE_KEY = 'heritageedge:scenario:v2';
  const SAVED_KEY = 'heritageedge:saved:v2';
  // A selection (scope or filter) is {dimension id: [option ids]}. A missing dimension means "all".
  const emptyScope = () => ({});
  const OLD_KEYS = { chains: 'chain', states: 'state', cities: 'city', sos: 'so', families: 'family', skus: 'sku' };

  function read(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ }
  }

  // Older saves used plural keys (chains, states, skus). Convert them and drop empty lists.
  function normalizeSelection(sel) {
    const out = {};
    Object.keys(sel || {}).forEach((k) => {
      const list = Array.isArray(sel[k]) ? sel[k].map(String) : [];
      if (list.length) out[OLD_KEYS[k] || k] = list;
    });
    return out;
  }
  // Drop ids that do not exist in the current data, so a stale choice cannot silently hide every row.
  function pruneSelection(sel, dims) {
    const out = {};
    dims.forEach((d) => {
      const known = new Set(d.options.map((o) => String(o.id)));
      const keep = ((sel || {})[d.id] || []).filter((id) => known.has(String(id)));
      if (keep.length) out[d.id] = keep;
    });
    return out;
  }
  function describeSelection(sel, dims) {
    const parts = [];
    dims.forEach((d) => {
      const ids = (sel || {})[d.id] || [];
      if (!ids.length) return;
      const names = ids.map((id) => (d.options.find((o) => String(o.id) === String(id)) || { name: id }).name);
      parts.push(`${d.name}: ${names.slice(0, 2).join(', ')}${names.length > 2 ? ` and ${names.length - 2} more` : ''}`);
    });
    return parts.join('; ');
  }
  const hasSelection = (sel) => Object.keys(sel || {}).some((k) => (sel[k] || []).length);

  function loadState() {
    const s = read(STATE_KEY, null) || read('heritageedge:scenario:v1', {});
    return {
      levers: Object.assign({}, DEFAULTS, s.levers || {}),
      scope: normalizeSelection(s.scope),
    };
  }
  function saveState(state) { write(STATE_KEY, state); }
  function loadSaved() {
    const list = read(SAVED_KEY, null) || read('heritageedge:saved:v1', []);
    return list.map((x) => Object.assign({}, x, { scope: normalizeSelection(x.scope) }));
  }
  function storeSaved(list) { write(SAVED_KEY, list); }

  /* ---------- searchable multi-select, one per dimension ----------
     cfg: { host, dims: [{id, name, options: [{id, name}]}], holder, key, onChange, mode }
     The selection lives at holder[key] and is replaced (not edited) on every change.
     mode 'popover' draws dropdown buttons; 'inline' draws collapsible rows for narrow panels. */
  const MAX_SHOWN = 400;
  function buildSelectors(cfg) {
    const { host, dims, holder, key, onChange } = cfg;
    const inline = cfg.mode === 'inline';
    const byId = Object.fromEntries(dims.map((d) => [d.id, d]));
    const sel = () => holder[key] || {};

    const panel = (d) => `
      <input type="search" class="text msel-search" placeholder="Search ${esc(d.name.toLowerCase())}" aria-label="Search ${esc(d.name)}">
      <div class="msel-actions">
        <button type="button" class="link-btn" data-act="all">Select shown</button>
        <button type="button" class="link-btn" data-act="none">Clear</button>
      </div>
      <ul class="msel-list"></ul>`;

    host.innerHTML = dims.map((d) => inline
      ? `<details class="isel" data-dim="${d.id}">
           <summary><span class="isel-name">${esc(d.name)}</span><span class="isel-val"></span></summary>
           <div class="isel-body">${panel(d)}</div></details>`
      : `<div class="msel" data-dim="${d.id}">
           <button type="button" class="msel-btn" aria-haspopup="true" aria-expanded="false">${esc(d.name)}<span class="msel-badge" hidden></span></button>
           <div class="msel-pop" hidden>${panel(d)}</div></div>`).join('');

    const roots = [...host.querySelectorAll('[data-dim]')];
    const rootOf = (el) => el.closest('[data-dim]');

    function shown(root) {
      const d = byId[root.dataset.dim];
      const q = root.querySelector('.msel-search').value.trim().toLowerCase();
      return d.options.filter((o) => !q || o.name.toLowerCase().includes(q));
    }
    function paint(root) {
      const d = byId[root.dataset.dim];
      const chosen = new Set((sel()[d.id] || []).map(String));
      const items = shown(root);
      root.querySelector('.msel-list').innerHTML = items.slice(0, MAX_SHOWN).map((o) =>
        `<li><label><input type="checkbox" value="${esc(o.id)}"${chosen.has(String(o.id)) ? ' checked' : ''}>
         <span>${esc(o.name)}</span></label></li>`).join('') +
        (items.length > MAX_SHOWN ? `<li class="msel-empty">Showing the first ${MAX_SHOWN}. Search to narrow the list.</li>` : '') +
        (items.length ? '' : '<li class="msel-empty">No matches</li>');
    }
    function summarize(root) {
      const d = byId[root.dataset.dim];
      const ids = sel()[d.id] || [];
      if (inline) {
        const val = root.querySelector('.isel-val');
        val.textContent = !ids.length ? 'All'
          : ids.length === 1 ? (d.options.find((o) => String(o.id) === String(ids[0])) || { name: ids[0] }).name
            : `${ids.length} selected`;
        val.classList.toggle('has-value', ids.length > 0);
      } else {
        const badge = root.querySelector('.msel-badge');
        badge.textContent = ids.length || '';
        badge.hidden = !ids.length;
        root.querySelector('.msel-btn').classList.toggle('has-value', ids.length > 0);
      }
    }
    function setDim(dim, ids) {
      const next = Object.assign({}, sel());
      if (ids.length) next[dim] = ids; else delete next[dim];
      holder[key] = next;
    }
    function refresh() { roots.forEach((r) => { paint(r); summarize(r); }); }

    host.addEventListener('input', (e) => {
      if (e.target.classList.contains('msel-search')) paint(rootOf(e.target));
    });
    host.addEventListener('change', (e) => {
      const box = e.target.closest('input[type=checkbox]');
      if (!box) return;
      const root = rootOf(box);
      const dim = root.dataset.dim;
      const set = new Set(sel()[dim] || []);
      if (box.checked) set.add(box.value); else set.delete(box.value);
      setDim(dim, [...set]);
      summarize(root);
      onChange();
    });
    host.addEventListener('click', (e) => {
      const root = rootOf(e.target);
      if (!root) return;
      const act = e.target.dataset && e.target.dataset.act;
      if (act) {
        const dim = root.dataset.dim;
        if (act === 'none') setDim(dim, []);
        else setDim(dim, [...new Set([...(sel()[dim] || []), ...shown(root).map((o) => String(o.id))])]);
        paint(root);
        summarize(root);
        onChange();
        return;
      }
      const btn = e.target.closest('.msel-btn');
      if (btn) {
        const pop = root.querySelector('.msel-pop');
        const open = pop.hidden;
        closeAll();
        pop.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
        if (open) root.querySelector('.msel-search').focus();
      }
    });
    function closeAll() {
      host.querySelectorAll('.msel-pop').forEach((p) => { p.hidden = true; });
      host.querySelectorAll('.msel-btn').forEach((b) => b.setAttribute('aria-expanded', 'false'));
    }
    if (!inline) {
      document.addEventListener('click', (e) => { if (!host.contains(e.target)) closeAll(); });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAll(); });
    }
    refresh();
    return { refresh };
  }

  async function postJson(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error('Request failed with status ' + r.status);
    return r;
  }

  /* ---------- formatting ---------- */
  const MINUS = '−';
  const RUPEE = '₹';

  function inr(v) {
    const a = Math.abs(v);
    let t;
    if (a >= 1e7) t = (a / 1e7).toFixed(2) + ' Cr';
    else if (a >= 1e5) t = (a / 1e5).toFixed(1) + ' L';
    else t = Math.round(a).toLocaleString('en-IN');
    return (v < 0 && Math.round(a) !== 0 ? MINUS : '') + RUPEE + t;
  }
  function inrSigned(v) { return (v > 0 && Math.round(v) !== 0 ? '+' : '') + inr(v); }

  function lakh(v, d) {
    d = d === undefined ? 1 : d;
    const x = v / 1e5;
    const s = Math.abs(x).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
    return (x < 0 && Number(s.replace(/,/g, '')) !== 0 ? MINUS : '') + s;
  }
  function lakhSigned(v, d) {
    const s = lakh(v, d);
    return v > 0 && Number(s.replace(/,/g, '')) !== 0 ? '+' + s : s;
  }
  function thousands(v, signed) {
    const x = v / 1e3;
    const s = Math.abs(x).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    if (Number(s.replace(/,/g, '')) === 0) return '0';
    return (x < 0 ? MINUS : signed && x > 0 ? '+' : '') + s;
  }
  function pct(v, d) { return (v * 100).toFixed(d === undefined ? 1 : d) + '%'; }
  function pp(v) {
    const x = v * 100;
    if (Math.abs(x) < 0.05) return '0.0 pp';
    return (x > 0 ? '+' : MINUS) + Math.abs(x).toFixed(1) + ' pp';
  }
  function rel(base, now) { return base ? (now - base) / base : 0; }
  function relText(r) {
    const x = r * 100;
    if (Math.abs(x) < 0.05) return '0.0%';
    return (x > 0 ? '+' : MINUS) + Math.abs(x).toFixed(1) + '%';
  }
  /* Colour for a change: 'up' is good, 'down' is bad, 'flat' is nothing. */
  function tone(delta, goodWhenUp, eps) {
    if (Math.abs(delta) <= (eps || 0)) return 'flat';
    return (delta > 0) === (goodWhenUp !== false) ? 'up' : 'down';
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function leverText(l, v) {
    const n = Number(v);
    const body = String(+n.toFixed(2));
    return (l.signed && n > 0 ? '+' : n < 0 ? MINUS : '') + body.replace('-', '') + l.unit;
  }
  function changedLevers(levers) {
    return FLAT.filter((l) => Number(levers[l.key]) !== DEFAULTS[l.key])
      .map((l) => ({ key: l.key, label: l.label, text: leverText(l, levers[l.key]) }));
  }

  window.HE = {
    DEFAULTS, GROUPS, FLAT, PRESETS,
    loadState, saveState, loadSaved, storeSaved, emptyScope,
    normalizeSelection, pruneSelection, describeSelection, hasSelection, buildSelectors,
    postJson, inr, inrSigned, lakh, lakhSigned, thousands, pct, pp, rel, relText, tone, esc,
    leverText, changedLevers, MINUS,
  };

  /* ---------- collapsible sidebar ---------- */
  (function () {
    const btn = document.getElementById('sideToggle');
    if (!btn) return;
    const root = document.documentElement;
    const links = document.querySelectorAll('.nav a');

    function apply(collapsed, save) {
      root.classList.toggle('side-collapsed', collapsed);
      btn.setAttribute('aria-expanded', String(!collapsed));
      const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
      btn.setAttribute('aria-label', label);
      btn.title = label;
      // Icon-only rail needs tooltips on the links.
      links.forEach((a) => {
        const text = a.querySelector('.nav-label').textContent.trim();
        if (collapsed) a.title = text; else a.removeAttribute('title');
      });
      if (save) {
        try { localStorage.setItem('heritageedge:sidebar', collapsed ? 'collapsed' : 'open'); } catch (e) { /* storage unavailable */ }
        // Let charts redraw at the new width once the layout has settled.
        setTimeout(() => window.dispatchEvent(new Event('resize')), 220);
      }
    }

    apply(root.classList.contains('side-collapsed'), false);
    btn.addEventListener('click', () => apply(!root.classList.contains('side-collapsed'), true));
  })();
})();
