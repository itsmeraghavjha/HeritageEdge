/* Scenario summary: what a saved scenario changes and what it does to the P&L, for people who did not make it. */
(function () {
  const $ = (id) => document.getElementById(id);
  const sid = Number($('sum').dataset.id);
  const esc = HE.esc;
  const money = (v) => HE.lakh(v);
  const moneyD = (v, good) => `<span class="${HE.tone(v, good, 500)}">${HE.lakhSigned(v)}</span>`;
  const dateText = (ts) => new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
  const num = (v) => Math.round(Math.abs(v)).toLocaleString('en-IN');
  const signedNum = (v) => (Math.abs(v) < 0.5 ? '0' : (v > 0 ? '+' : HE.MINUS) + num(v));
  const unitOf = (u) => (u === '%' ? '%' : ' pp');
  const leverVal = (v, unit) => `${v > 0 ? '+' : v < 0 ? HE.MINUS : ''}${String(+Math.abs(v).toFixed(2))}${unitOf(unit)}`;

  function cell(line) {
    const good = !line.cost;
    if (line.kind === 'pct') {
      const d = line.sim - line.base;
      return `<td>${HE.pct(line.base)}</td><td>${HE.pct(line.sim)}</td>
        <td><span class="${HE.tone(d, good, 0.0005)}">${HE.pp(d)}</span></td><td></td>`;
    }
    const f = line.kind === 'lpd' ? (v) => Math.round(v).toLocaleString('en-IN') : money;
    const d = line.sim - line.base;
    const r = line.base ? d / Math.abs(line.base) : 0;
    const dText = line.kind === 'lpd' ? signedNum(d) : HE.lakhSigned(d);
    return `<td>${f(line.base)}</td><td>${f(line.sim)}</td>
      <td><span class="${HE.tone(d, good, line.kind === 'lpd' ? 0.5 : 500)}">${dText}</span></td>
      <td><span class="${HE.tone(d, good, line.kind === 'lpd' ? 0.5 : 500)}">${HE.relText(r)}</span></td>`;
  }

  function kpis(d) {
    const by = Object.fromEntries(d.lines.map((l) => [l.key, l]));
    const pick = [['vol_l', 'Volume per day', true], ['net', 'Net revenue', true], ['cm', 'Contribution (MC)', true], ['nm', 'Net margin', true]]
      .filter(([k]) => by[k]);
    const cards = pick.map(([k, label]) => {
      const l = by[k];
      const dv = l.sim - l.base;
      const shown = k === 'vol_l' ? `${Math.round(l.sim).toLocaleString('en-IN')} LPD` : money(l.sim);
      const delta = k === 'vol_l' ? `${signedNum(dv)} LPD` : HE.lakhSigned(dv);
      return `<div class="sum-kpi"><span>${label}</span><b>${shown}</b>
        <small class="${HE.tone(dv, true, k === 'vol_l' ? 0.5 : 500)}">${delta} (${HE.relText(l.base ? dv / Math.abs(l.base) : 0)})</small></div>`;
    });
    const cm = by.cm_pct;
    if (cm) {
      const dv = cm.sim - cm.base;
      cards.splice(3, 0, `<div class="sum-kpi"><span>MC %</span><b>${HE.pct(cm.sim)}</b>
        <small class="${HE.tone(dv, true, 0.0005)}">${HE.pp(dv)}</small></div>`);
    }
    return cards.join('');
  }

  function changed(d) {
    const parts = [];
    if (d.levers.length) {
      parts.push(`<h3>Set for every row in scope</h3><ul class="sum-list">${d.levers.map((l) =>
        `<li>${esc(l.label)}: <b>${leverVal(l.value, l.unit)}</b></li>`).join('')}</ul>`);
    }
    if (d.row_levers.length) {
      parts.push(`<h3>Set row by row</h3>
        <p class="sum-foot">${d.rows_changed.toLocaleString('en-IN')} of ${d.rows_in_scope.toLocaleString('en-IN')} rows have their own change (a row is one chain, customer group, state, city, sales office and SKU).</p>
        <table class="data sum-table"><thead><tr><th>Lever</th><th>Rows affected</th><th>Smallest change</th><th>Largest change</th></tr></thead><tbody>${d.row_levers.map((r) =>
          `<tr><th scope="row">${esc(r.label)}</th><td>${r.rows.toLocaleString('en-IN')}</td><td>${leverVal(r.low, r.unit)}</td><td>${leverVal(r.high, r.unit)}</td></tr>`).join('')}</tbody></table>`);
    }
    if (!d.levers.length && !d.row_levers.length) parts.push('<p class="sum-foot">This scenario changes nothing: it matches the baseline.</p>');
    parts.push(`<h3>Applies to</h3><p>${d.scope.length ? d.scope.map((s) =>
      `${esc(s.dimension)}: ${esc(s.names.slice(0, 6).join(', '))}${s.names.length > 6 ? ` and ${s.names.length - 6} more` : ''}`).join('; ')
      : 'Every row'} <span class="sum-foot">(${d.rows_in_scope.toLocaleString('en-IN')} of ${d.rows_total.toLocaleString('en-IN')} rows)</span></p>`);
    return parts.join('');
  }

  function bridge(d) {
    const steps = d.bridge;
    const maxAbs = Math.max(...steps.map((s) => Math.abs(s.value)), 1);
    $('sumBridge').innerHTML = `<thead><tr><th>Step</th><th>Contribution</th><th></th></tr></thead><tbody>${steps.map((s) => {
      const total = s.kind === 'total';
      const w = Math.round(Math.abs(s.value) / maxAbs * 100);
      return `<tr class="${total ? 'is-total' : ''}"><th scope="row">${esc(s.label)}</th>
        <td>${total ? money(s.value) : moneyD(s.value, true)}</td>
        <td class="sum-barcell"><span class="sum-bar ${total ? 'is-total' : s.value >= 0 ? 'is-up' : 'is-down'}" style="width:${w}%"></span></td></tr>`;
    }).join('')}</tbody>`;
  }

  function where(d) {
    const sv = (v) => `<span class="${HE.tone(v, true, 0.5)}">${signedNum(v)}</span>`;
    $('sumWhere').innerHTML = d.where.filter((g) => g.items.length).map((g) => `
      <div class="sum-whereblock"><h3>By ${esc(g.dimension.toLowerCase())}<small> (${g.items.length} affected, biggest contribution change first)</small></h3>
      <div class="table-scroll"><table class="data sum-table"><thead><tr><th>${esc(g.dimension)}</th><th>Rows affected</th><th>Volume per day (LPD)</th><th>Net revenue</th><th>Contribution</th><th>MC %</th></tr></thead><tbody>${g.items.map((r) =>
        `<tr><th scope="row">${esc(r.name)}</th><td>${r.rows_affected.toLocaleString('en-IN')}</td><td>${sv(r.d_vol)}</td>
          <td>${moneyD(r.d_net, true)}</td><td>${moneyD(r.d_cm, true)}</td>
          <td>${HE.pct(r.cm_pct)} <small class="${HE.tone(r.d_cm_pct, true, 0.0005)}">${HE.pp(r.d_cm_pct)}</small></td></tr>`).join('')}
        <tr class="is-total"><th scope="row">Total</th><td>${g.totals.rows_affected.toLocaleString('en-IN')}</td><td>${sv(g.totals.d_vol)}</td>
          <td>${moneyD(g.totals.d_net, true)}</td><td>${moneyD(g.totals.d_cm, true)}</td><td></td></tr></tbody></table></div></div>`).join('')
      || '<p class="sum-foot">Nothing moves.</p>';
  }

  function topRows(d) {
    $('sumTopSec').hidden = !d.top_rows.length;
    $('sumTopNote').textContent = d.rows_changed > d.top_shown ? `The ${d.top_shown} of ${d.rows_changed.toLocaleString('en-IN')} changed rows with the biggest contribution change. Every changed row is in the Excel download.` : '';
    const f = (step) => `${esc(step.label)}: ${(+step.base.toFixed(2)).toLocaleString('en-IN')} to ${(+step.scenario.toFixed(2)).toLocaleString('en-IN')}`;
    $('sumTop').innerHTML = `<thead><tr><th>Chain / state / SKU</th><th>What changed (baseline to scenario)</th><th>Net revenue</th><th>Contribution</th></tr></thead><tbody>${d.top_rows.map((r) =>
      `<tr><th scope="row">${esc(r.name)}</th><td class="sum-left">${r.changes.map(f).join('<br>') || '–'}</td><td>${moneyD(r.d_net, true)}</td><td>${moneyD(r.d_cm, true)}</td></tr>`).join('')}</tbody>`;
  }

  function render(d) {
    const sc = d.scenario;
    document.title = `${sc.name} | Scenario summary`;
    $('sumName').textContent = sc.name;
    $('sumMeta').textContent = `Saved by ${sc.mine ? 'you' : (sc.owner || 'someone')} on ${dateText(sc.updated)}. Baseline: ${d.period}.`;
    $('sumBy').textContent = sc.owner || '';
    $('sumPrinted').textContent = `Printed ${new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}`;
    $('sumPurpose').hidden = !sc.note;
    $('sumPurposeText').textContent = sc.note || '';
    $('sumKpis').innerHTML = kpis(d);
    $('sumChanged').innerHTML = changed(d);
    $('sumPnl').innerHTML = `<thead><tr><th>${esc(HE.lakhUnit(d.meta))}</th><th>Baseline</th><th>Scenario</th><th>Change</th><th>Change %</th></tr></thead><tbody>${d.lines.map((l) =>
      `<tr class="${['net', 'cm', 'nm'].includes(l.key) ? 'is-total' : ''}"><th scope="row">${esc(l.label)}</th>${cell(l)}</tr>`).join('')}</tbody>`;
    $('sumUnit').textContent = d.limited ? 'Your view stops at contribution.' : '';
    bridge(d);
    where(d);
    topRows(d);
    $('sumNote').textContent = `Worked out from ${d.period.toLowerCase()} when this page was opened (${new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })}), so it changes if the data is rebuilt. Costs are the cost rates of that data; volume is as entered in the scenario.`;
    $('sumBody').hidden = false;
  }

  $('sumPrint').addEventListener('click', () => window.print());
  $('sumXlsx').addEventListener('click', async () => {
    const btn = $('sumXlsx');
    btn.disabled = true;
    try { await HE.download(`/api/scenarios/${sid}/changes`, {}, 'heritageedge-scenario-changes.xlsx', 'changed rows'); }
    catch (e) { /* the progress box reports the failure */ } finally { btn.disabled = false; }
  });
  $('sumLink').addEventListener('click', async () => {
    const link = window.location.origin + '/scenarios/' + sid;
    try { await navigator.clipboard.writeText(link); $('sumLink').textContent = 'Link copied'; }
    catch (e) { window.prompt('Copy this link', link); }
    setTimeout(() => { $('sumLink').textContent = 'Copy link'; }, 2500);
  });

  fetch(`/api/scenarios/${sid}/summary`).then(async (r) => {
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { $('sumName').textContent = 'Scenario summary'; $('sumError').textContent = d.error || 'The summary could not be loaded.'; $('sumError').hidden = false; return; }
    render(d);
  }).catch(() => { $('sumError').textContent = 'The summary could not be loaded. Reload the page.'; $('sumError').hidden = false; });
})();
