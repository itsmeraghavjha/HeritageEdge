(function () {
  'use strict';
  const HE = window.HE;
  const $ = (id) => document.getElementById(id);

  let status = null;
  let prevJobState = null;
  let pollTimer = null;
  let sqlMessage = null;                 // result of the last "Test connection"
  const ticked = new Set();              // base months the user has ticked
  let monthsKey = '';                    // which month list the checkboxes were built from

  /* ---------- small helpers ---------- */
  function fmtSize(bytes) {
    if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(1) + ' GB';
    if (bytes >= 1024 ** 2) return (bytes / 1024 ** 2).toFixed(1) + ' MB';
    return Math.max(1, Math.round(bytes / 1024)) + ' KB';
  }
  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  }
  function monthLabel(m) {
    return new Date(m + 'T00:00:00').toLocaleString('en-IN', { month: 'short', year: 'numeric' });
  }
  function monthsRange(months) {
    if (!months || !months.length) return '';
    return months.length === 1 ? monthLabel(months[0]) : `${monthLabel(months[0])} to ${monthLabel(months[months.length - 1])}`;
  }
  function setNote(el, text, kind) {
    el.textContent = text || '';
    el.className = 'note' + (kind ? ' ' + kind : '');
  }
  function setState(el, text, kind) {
    el.textContent = text;
    el.className = 'state' + (kind ? ' ' + kind : '');
  }
  async function api(url, body) {
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
      let data = {};
      try { data = await r.json(); } catch (e) { /* not JSON */ }
      return { ok: r.ok, status: r.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: { error: 'Could not reach the app. Check that it is still running.' } };
    }
  }

  /* ---------- rendering ---------- */
  function render() {
    const { data, uploads, billing, sql, job } = status;
    const running = job.state === 'running';

    const pill = $('modePill');
    pill.textContent = data.title;
    pill.className = 'pill' + (data.mode === 'actual' ? ' is-actual' : '');

    // job banner
    const banner = $('jobBanner');
    banner.hidden = job.state === 'idle';
    banner.className = 'banner' + (job.state === 'failed' ? ' is-error' : job.state === 'done' ? ' is-done' : ' is-live');
    $('jobBar').hidden = !running;
    $('jobBar').firstElementChild.style.width = Math.round(job.fraction * 100) + '%';
    if (running) $('jobText').textContent = job.message;
    else if (job.state === 'failed') $('jobText').textContent = job.error || 'That step failed.';
    else if (job.state === 'done') $('jobText').textContent = job.name === 'build' ? 'The dataset is built and in use.' : 'Billing volumes are saved.';

    // volumes
    setState($('volState'), billing ? 'Ready' : 'Not pulled yet', billing ? 'ok' : '');
    $('volInfo').textContent = billing
      ? `${billing.rows.toLocaleString('en-IN')} rows covering ${monthsRange(billing.months)}. Pulled ${fmtTime(billing.pulled_at)}.`
      : 'Monthly packs and invoice value by customer, sales office and SKU, read straight from the billing tables.';
    if (!$('startDate').value) $('startDate').value = sql.default_start;
    if (sql.missing.length) {
      setNote($('sqlNote'), `SQL is not set up. Add ${sql.missing.join(', ')} to the .env file in the project folder, then restart the app.`, 'is-warn');
    } else if (sqlMessage) {
      setNote($('sqlNote'), sqlMessage.message, sqlMessage.ok ? 'is-ok' : 'is-error');
    } else {
      setNote($('sqlNote'), '');
    }
    $('testSql').disabled = running;
    $('pullSql').disabled = running || sql.missing.length > 0;

    // uploads
    [['offer', 'offerFile'], ['mis', 'misFile']].forEach(([kind, input]) => {
      const info = uploads[kind];
      setState($(kind + 'State'), info ? 'Uploaded' : 'Needed', info ? 'ok' : '');
      $(kind + 'Name').textContent = info ? `${info.name} (${fmtSize(info.size)}), uploaded ${fmtTime(info.uploaded_at)}` : '';
      $(input).disabled = running;
    });

    renderMonths();

    // build
    const missing = [];
    if (!billing) missing.push('billing volumes from SQL');
    if (!uploads.offer) missing.push('the offer file');
    if (!uploads.mis) missing.push('the MIS workbook');
    if (billing && ticked.size === 0) missing.push('at least one base month');
    $('buildBtn').disabled = running || missing.length > 0;
    setState($('buildState'), data.mode === 'actual' ? `Built ${fmtTime(data.built_at)}` : 'Not built yet', data.mode === 'actual' ? 'ok' : '');
    if (job.state === 'failed' && job.name === 'build') setNote($('buildNote'), job.error, 'is-error');
    else setNote($('buildNote'), missing.length ? `Still needed: ${missing.join(', ')}.` : '');

    renderResult(data);
  }

  function renderMonths() {
    const { data, billing, suggested_months: suggested } = status;
    const list = billing ? billing.months : [];
    const key = list.join(',');
    if (key === monthsKey) return;       // keep the user's ticks while the page refreshes
    monthsKey = key;
    ticked.clear();
    const built = (data.mode === 'actual' ? data.base_months || [] : []).filter((m) => list.includes(m));
    (built.length ? built : suggested).forEach((m) => ticked.add(m));
    $('months').innerHTML = '<span class="months-label">Base months</span>' + (list.length
      ? list.map((m) => `<label class="month"><input type="checkbox" value="${m}"${ticked.has(m) ? ' checked' : ''}>
          <span>${HE.esc(monthLabel(m))}</span></label>`).join('')
      : '<span class="sub">Pull billing volumes first. The months available will appear here.</span>');
  }

  const QUALITY = [
    ['billing_revenue_in_offer_scope_pct', 'Billing revenue covered by the offer file', 1, null],
    ['mf_ecom_revenue_in_offer_scope_pct', 'Modern Formats and E-Commerce revenue covered', 1, null],
    ['billing_revenue_outside_mis_groups_pct', 'Revenue left out: customer group not in the MIS', 2, 0.5],
    ['billing_revenue_with_unreadable_pack_size_pct', 'Revenue left out: pack size could not be read', 2, 2],
    ['revenue_with_price_gap_clipped_pct', 'Revenue where billed price is far from list price', 2, 1],
  ];

  function renderResult(data) {
    const panel = $('resultPanel');
    if (data.mode !== 'actual' || !data.rows) { panel.hidden = true; return; }
    panel.hidden = false;
    $('builtSub').textContent = `Built ${fmtTime(data.built_at)} from ${monthsRange(data.base_months)}`;

    const facts = [
      ['Lines', data.rows.toLocaleString('en-IN'), ''],
      ['Net revenue a month', `${HE.RUPEE || '₹'}${data.net_revenue_cr.toLocaleString('en-IN')} Cr`, ''],
    ];
    const q = data.quality || {};
    QUALITY.forEach(([k, label, , warnAbove]) => {
      if (q[k] === undefined) return;
      facts.push([label, `${q[k]}%`, warnAbove !== null && q[k] > warnAbove ? 'is-warn' : '']);
    });
    const src = q.cogs_source_share || {};
    if (src['sku x office'] !== undefined) {
      facts.push(['COGS found for the exact SKU and sales office', `${(src['sku x office'] * 100).toFixed(1)}% of lines`, '']);
    }
    $('facts').innerHTML = facts.map(([label, value, cls]) =>
      `<div class="fact ${cls}"><dt>${HE.esc(label)}</dt><dd>${HE.esc(value)}</dd></div>`).join('');

    const pct1 = (v) => (v * 100).toFixed(1) + '%';
    const rows = (data.reconciliation || []).map((g) => {
      const lines = g.lines.map((l) => {
        const gap = (l.model_pct - l.mis_pct) * 100;
        return `<tr><th scope="row">${HE.esc(l.label)}</th><td>${pct1(l.model_pct)}</td><td>${pct1(l.mis_pct)}</td>
          <td class="${Math.abs(gap) >= 1 ? 'flag' : ''}">${(gap > 0 ? '+' : gap < 0 ? HE.MINUS : '') + Math.abs(gap).toFixed(1)} pp</td></tr>`;
      }).join('');
      const mg = (g.model_margin_pct - g.mis_margin_pct) * 100;
      return `<tr class="group"><th colspan="4" scope="colgroup">${HE.esc(g.group === 'All' ? 'All customers in scope' : g.group)}</th></tr>${lines}
        <tr class="strong"><th scope="row">Margin after these costs</th><td>${pct1(g.model_margin_pct)}</td><td>${pct1(g.mis_margin_pct)}</td>
        <td class="${Math.abs(mg) >= 1 ? 'flag' : ''}">${(mg > 0 ? '+' : mg < 0 ? HE.MINUS : '') + Math.abs(mg).toFixed(1)} pp</td></tr>`;
    }).join('');
    $('reconTable').innerHTML = `<thead><tr><th scope="col">Cost line</th><th scope="col">In HeritageEdge</th>
      <th scope="col">In the MIS</th><th scope="col">Gap</th></tr></thead><tbody>${rows}</tbody>`;
  }

  /* ---------- status polling ---------- */
  async function poll() {
    clearTimeout(pollTimer);
    try {
      const r = await fetch('/api/data/status');
      if (r.status === 401) { location.href = '/login'; return; }
      status = await r.json();
    } catch (e) {
      setNote($('buildNote'), 'Could not reach the app. Check that it is still running.', 'is-error');
      pollTimer = setTimeout(poll, 4000);
      return;
    }
    const state = status.job.state;
    if (prevJobState === 'running' && state === 'done' && status.job.name === 'build') {
      location.reload();                 // the sidebar shows which data is in use, so refresh it
      return;
    }
    prevJobState = state;
    render();
    if (state === 'running') pollTimer = setTimeout(poll, 1500);
  }

  /* ---------- actions ---------- */
  function upload(kind, file) {
    const note = $(kind + 'Note');
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/data/upload/' + kind);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) setNote(note, `Uploading ${Math.round((e.loaded / e.total) * 100)}%`);
      };
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
        if (xhr.status >= 200 && xhr.status < 300) setNote(note, '');
        else setNote(note, body.error || `The upload failed (status ${xhr.status}).`, 'is-error');
        resolve();
      };
      xhr.onerror = () => { setNote(note, 'The upload failed. Check that the app is still running.', 'is-error'); resolve(); };
      const form = new FormData();
      form.append('file', file);
      xhr.send(form);
    }).then(poll);
  }

  ['offer', 'mis'].forEach((kind) => {
    $(kind + 'File').addEventListener('change', (e) => {
      const file = e.target.files[0];
      e.target.value = '';
      if (file) upload(kind, file);
    });
  });

  $('testSql').addEventListener('click', async () => {
    setNote($('sqlNote'), 'Testing the connection');
    const r = await api('/api/data/sql/test');
    sqlMessage = { ok: !!r.data.ok, message: r.data.message || r.data.error || 'The test failed.' };
    render();
  });

  $('pullSql').addEventListener('click', async () => {
    sqlMessage = null;
    const r = await api('/api/data/sql/pull', { start: $('startDate').value });
    if (!r.ok) { setNote($('sqlNote'), r.data.error || 'Could not start the pull.', 'is-error'); return; }
    prevJobState = 'running';
    poll();
  });

  $('months').addEventListener('change', (e) => {
    const box = e.target.closest('input[type=checkbox]');
    if (!box) return;
    if (box.checked) ticked.add(box.value); else ticked.delete(box.value);
    render();
  });

  $('buildBtn').addEventListener('click', async () => {
    const r = await api('/api/data/build', { months: [...ticked].sort() });
    if (!r.ok) { setNote($('buildNote'), r.data.error || 'Could not start the build.', 'is-error'); return; }
    prevJobState = 'running';
    poll();
  });

  poll();
})();
