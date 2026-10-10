(function () {
  'use strict';

  const DEFAULTS = {
    price_change_pct: 0,
    promo_add_pp: 0,
    promo_pass_through_pct: 70,
    trade_margin_add_pp: 0,
    rebate_add_pp: 0,
    elasticity_scale: 1,
    manual_uplift_pct: 0,
    milk_cost_change_pct: 0,
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
        { key: 'promo_pass_through_pct', label: 'Promo passed to shelf price', min: 0, max: 100, step: 5, unit: '%',
          hint: 'The part of the promo that shoppers see and that drives volume.' },
      ],
    },
    {
      title: 'Chain terms',
      levers: [
        { key: 'trade_margin_add_pp', label: 'Trade margin change', min: -3, max: 5, step: 0.25, unit: ' pp', signed: true,
          hint: 'Chain margin as a share of MRP. More margin pushes more volume.' },
        { key: 'rebate_add_pp', label: 'Rebates and display fees', min: -2, max: 4, step: 0.25, unit: ' pp', signed: true,
          hint: 'As a share of invoice value. Buys visibility on shelf.' },
      ],
    },
    {
      title: 'Demand response',
      levers: [
        { key: 'elasticity_scale', label: 'Price sensitivity', min: 0.5, max: 2, step: 0.1, unit: '×', signed: false,
          hint: '1× uses each SKU’s own elasticity. Raise it if shoppers react more.' },
        { key: 'manual_uplift_pct', label: 'Extra volume from visibility', min: -10, max: 30, step: 1, unit: '%', signed: true,
          hint: 'Volume you expect from display or activation, on top of price effects.' },
      ],
    },
    {
      title: 'Costs',
      note: 'Costs apply to every chain, state and SKU.',
      levers: [
        { key: 'milk_cost_change_pct', label: 'Milk procurement cost', min: -10, max: 15, step: 0.5, unit: '%', signed: true,
          hint: 'Flows into COGS in proportion to each SKU’s milk content.' },
        { key: 'freight_change_pct', label: 'Freight cost', min: -10, max: 20, step: 0.5, unit: '%', signed: true,
          hint: 'Applied to logistics cost per unit.' },
      ],
    },
  ];
  const FLAT = GROUPS.flatMap((g) => g.levers);

  const PRESETS = [
    { id: 'festive', name: 'Festive display push',
      levers: { promo_add_pp: 4, promo_pass_through_pct: 80, rebate_add_pp: 1, manual_uplift_pct: 6 } },
    { id: 'price3', name: 'Price increase of 3%', levers: { price_change_pct: 3 } },
    { id: 'milk5', name: 'Milk cost up 5%', levers: { milk_cost_change_pct: 5 } },
    { id: 'milk5price2', name: 'Milk cost up 5%, price up 2%',
      levers: { milk_cost_change_pct: 5, price_change_pct: 2 } },
    { id: 'margin1', name: 'Give chains 1 pp more margin', levers: { trade_margin_add_pp: 1 } },
  ];

  const STATE_KEY = 'heritageedge:scenario:v1';
  const SAVED_KEY = 'heritageedge:saved:v1';
  const emptyScope = () => ({ chains: [], states: [], skus: [] });

  function read(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ }
  }

  function loadState() {
    const s = read(STATE_KEY, {});
    return {
      levers: Object.assign({}, DEFAULTS, s.levers || {}),
      scope: Object.assign(emptyScope(), s.scope || {}),
    };
  }
  function saveState(state) { write(STATE_KEY, state); }
  function loadSaved() { return read(SAVED_KEY, []); }
  function storeSaved(list) { write(SAVED_KEY, list); }

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
