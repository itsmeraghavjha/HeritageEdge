(function () {
  'use strict';
  const HE = window.HE;
  const $ = (id) => document.getElementById(id);

  let meta = null, items = [], who = 'all', query = '', sortKey = 'updated', sortDir = 'desc', confirmId = null, renameId = null;
  const limited = !$('scnWho');                      // a KAM sees only their own, so there is no Everyone's / Mine switch

  const dateText = (ts) => new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  const days = () => (meta && meta.days) || 30;
  const lpdChange = (r) => (r.d_litres + r.d_kg) / days();
  const lpdText = (v) => (Math.abs(v) < 0.5 ? 'No change' : `${v > 0 ? '+' : HE.MINUS}${Math.round(Math.abs(v)).toLocaleString('en-IN')} LPD`);

  function describe(s) {
    const lev = HE.changedLevers(s.levers || {}).map((l) => `${l.label} ${l.text}`);
    const rows = HE.adjustCount((s.levers && s.levers.adjust) || {});
    if (rows) lev.push(`${rows} row${rows > 1 ? 's' : ''} adjusted on their own`);
    return { levers: lev.join('; ') || 'No lever changed', scope: HE.describeSelection(s.scope || {}, meta.dims) || 'Everything' };
  }

  function sortValue(s, key) {
    if (key === 'name') return s.name.toLowerCase();
    if (key === 'owner') return (s.owner || '').toLowerCase();
    if (key === 'updated') return s.updated;
    const r = s.result;
    return r ? r[key] : -Infinity;               // scenarios without a result sink to the bottom
  }

  function visible() {
    const q = query.trim().toLowerCase();
    const list = items.filter((s) => (who === 'all' || s.mine)
      && (!q || `${s.name} ${s.owner} ${s._d.levers} ${s._d.scope}`.toLowerCase().includes(q)));
    const dir = sortDir === 'asc' ? 1 : -1;
    list.sort((a, b) => {
      const x = sortValue(a, sortKey), y = sortValue(b, sortKey);
      return (x < y ? -1 : x > y ? 1 : 0) * dir || a.name.localeCompare(b.name);
    });
    return list;
  }

  const money = (d) => `<span class="${HE.tone(d, true, 500)}">${HE.lakhSigned(d)}</span>`;

  function row(s) {
    const r = s.result;
    const actions = confirmId === s.id
      ? `<span class="scn-confirm">Delete this scenario?
           <button type="button" class="link-btn danger" data-act="delete-yes" data-id="${s.id}">Delete</button>
           <button type="button" class="link-btn" data-act="delete-no">Keep</button></span>`
      : `<a class="link-btn" href="/?scenario=${s.id}">Open and edit</a>
         <button type="button" class="link-btn" data-act="copy" data-id="${s.id}">Make a copy</button>
         ${s.can_change ? `<button type="button" class="link-btn" data-act="rename" data-id="${s.id}">Rename</button>
         <button type="button" class="link-btn" data-act="delete" data-id="${s.id}">Delete</button>` : ''}`;
    return `<tr>
      <th scope="row" class="scn-name"><span>${HE.esc(s.name)}</span>
        <small>${HE.esc(s._d.levers)}</small><small>Applies to: ${HE.esc(s._d.scope)}</small></th>
      <td>${s.mine ? 'You' : HE.esc(s.owner || '–')}</td>
      <td>${dateText(s.updated)}</td>
      <td>${r ? money(r.d_net) : '–'}</td>
      <td>${r ? money(r.d_cm) : '–'}</td>
      <td>${r ? `${HE.pct(r.cm_pct)} <small class="${HE.tone(r.d_cm_pct, true, 0.0005)}">${HE.pp(r.d_cm_pct)}</small>` : '–'}</td>
      <td>${r ? `<span class="${HE.tone(lpdChange(r), true, 0.5)}">${lpdText(lpdChange(r))}</span>` : '–'}</td>
      <td class="scn-actions">${actions}</td></tr>`;
  }

  function render() {
    const list = visible();
    const head = [['name', 'Scenario'], ['owner', 'Saved by'], ['updated', 'Last changed'],
      ['d_net', 'Net revenue change'], ['d_cm', 'MC change'], ['cm_pct', 'MC % in scenario'], [null, 'Volume change'], [null, '']];
    $('scnTable').innerHTML = `<thead><tr>${head.map(([k, label]) => k
      ? `<th scope="col" aria-sort="${k === sortKey ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}">
           <button type="button" class="sort-btn" data-sort="${k}">${label}</button></th>`
      : `<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${list.map(row).join('')}</tbody>`;
    $('scnCount').textContent = items.length ? `Showing ${list.length} of ${items.length}` : '';
    const empty = $('scnEmpty');
    empty.hidden = list.length > 0;
    if (!list.length) {
      empty.textContent = !items.length
        ? 'No saved scenarios yet. Set the levers on the Promo simulator and press Save scenario.'
        : (who === 'mine' && !items.some((s) => s.mine) ? 'You have not saved any scenarios yet.' : 'No scenario matches your search.');
    }
    $('scnSort').value = `${sortKey}:${sortDir}`;
    if (![...$('scnSort').options].some((o) => o.value === $('scnSort').value)) $('scnSort').selectedIndex = -1;
  }

  async function load() {
    const res = await (await fetch('/api/scenarios')).json();
    items = res.scenarios.map((s) => Object.assign(s, { _d: describe(s) }));
    render();
  }

  async function call(url, method, body) {
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const out = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(out.error || 'Something went wrong. Try again.');
    return out;
  }

  /* ---------- clicks ---------- */
  $('scnTable').addEventListener('click', async (e) => {
    const sortBtn = e.target.closest('[data-sort]');
    if (sortBtn) {
      const k = sortBtn.dataset.sort;
      sortDir = k === sortKey ? (sortDir === 'asc' ? 'desc' : 'asc') : (k === 'name' || k === 'owner' ? 'asc' : 'desc');
      sortKey = k;
      return render();
    }
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const id = Number(btn.dataset.id);
    try {
      if (btn.dataset.act === 'delete') { confirmId = id; render(); }
      else if (btn.dataset.act === 'delete-no') { confirmId = null; render(); }
      else if (btn.dataset.act === 'delete-yes') { await call(`/api/scenarios/${confirmId}`, 'DELETE'); confirmId = null; await load(); }
      else if (btn.dataset.act === 'copy') { await call(`/api/scenarios/${id}/duplicate`, 'POST'); await load(); }
      else if (btn.dataset.act === 'rename') {
        renameId = id;
        $('renameInput').value = (items.find((s) => s.id === id) || {}).name || '';
        $('renameError').textContent = '';
        if ($('renameDialog').showModal) $('renameDialog').showModal();
        $('renameInput').select();
      }
    } catch (err) { window.alert(err.message); confirmId = null; render(); }
  });

  $('renameCancel').addEventListener('click', () => $('renameDialog').close());
  $('renameForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      await call(`/api/scenarios/${renameId}/rename`, 'POST', { name: $('renameInput').value });
      $('renameDialog').close();
      await load();
    } catch (err) { $('renameError').textContent = err.message; }
  });

  $('scnSearch').addEventListener('input', (e) => { query = e.target.value; render(); });
  $('scnSort').addEventListener('change', (e) => { [sortKey, sortDir] = e.target.value.split(':'); render(); });
  if (!limited) {
    $('scnWho').addEventListener('click', (e) => {
      const b = e.target.closest('[data-who]');
      if (!b) return;
      who = b.dataset.who;
      $('scnWho').querySelectorAll('[data-who]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      render();
    });
  }

  /* ---------- scenarios saved in this browser the old way ---------- */
  function offerImport() {
    let local = [];
    try { local = HE.loadSaved(); } catch (e) { local = []; }
    if (!local.length) return;
    $('importText').textContent = `${local.length} scenario${local.length > 1 ? 's are' : ' is'} saved only in this browser. Add ${local.length > 1 ? 'them' : 'it'} to your account so ${local.length > 1 ? 'they' : 'it'} show here and on your other devices.`;
    $('importBanner').hidden = false;
    $('importBtn').onclick = async () => {
      $('importBtn').disabled = true;
      try {
        const out = await call('/api/scenarios/import', 'POST', { items: local });
        try { window.localStorage.setItem('heritageedge:saved:imported', JSON.stringify(local)); HE.storeSaved([]); } catch (e) { /* storage blocked */ }
        $('importBanner').hidden = true;
        await load();
        if (out.skipped) window.alert(`${out.skipped} could not be added.`);
      } catch (err) { $('importBtn').disabled = false; window.alert(err.message); }
    };
  }

  fetch('/api/meta').then((r) => r.json()).then((m) => { meta = m; HE.showPeriod(m); $('scnUnit').textContent = HE.lakhUnit(m).toLowerCase(); offerImport(); return load(); })
    .catch(() => { $('scnEmpty').hidden = false; $('scnEmpty').textContent = 'Could not load the scenarios. Reload the page.'; });
})();
