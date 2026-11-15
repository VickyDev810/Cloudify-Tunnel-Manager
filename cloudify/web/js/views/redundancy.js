// Redundancy: run tunnels on several machines (Cloudflare replicas).
import { api } from '../api.js';
import { $, $$, confirmDialog, downloadJson, html, icon, mount, statusPill, timeAgo, toast, withButton, zoneOf } from '../ui.js';

export default function redundancy(page, { store, openMenu }) {
  const preselect = new URLSearchParams(location.hash.split('?')[1] || '').get('tunnel');
  const selected = new Set();          // hostnames
  let step = 'select';                 // select -> split -> passphrase -> done
  let plan = null;
  let exportTunnels = [];
  let doneInfo = null;
  let pendingBundle = null;

  const primaries = () => store.tunnels.filter((t) => !t.replica && (t.routes || []).length);
  const routesOf = (t) => (t.routes || []).map((r) => r.domain);
  if (preselect) {
    const t = store.tunnels.find((x) => x.name === preselect);
    if (t) routesOf(t).forEach((h) => selected.add(h));
  }

  // ------------------------------------------------------------- layout

  const hasShared = () => store.tunnels.some((t) => t.exported || t.replica);
  let tab = preselect ? 'share' : (hasShared() ? 'status' : 'share');
  let tabChosen = Boolean(preselect) || store.loaded;  // default decided once tunnels are known

  mount(page, html`
    <div class="page-header">
      <div>
        <button class="btn btn-ghost btn-icon menu-btn" data-menu aria-label="Menu">${icon('menu')}</button>
        <div class="eyebrow">Reliability</div>
        <h1>Redundancy</h1>
        <p>Run the same tunnel on several machines. Cloudflare spreads traffic across them and
          stops sending requests to a machine that goes down.</p>
      </div>
    </div>
    <div class="tabs" role="tablist">
      <button role="tab" data-tab="status">Status</button>
      <button role="tab" data-tab="share">Share</button>
      <button role="tab" data-tab="import">Import</button>
    </div>
    <div data-panel="status"><div id="status"></div></div>
    <div data-panel="share" class="card"><div class="card-header"><div><h2>${icon('upload')} Share from this machine</h2>
      <p class="subtle">Pick tunnels, whole domains or single hostnames. Then import the file on another machine.</p></div></div>
      <div class="card-body" id="wizard"></div></div>
    <div data-panel="import" class="card"><div class="card-header"><div><h2>${icon('download')} Import on this machine</h2>
      <p class="subtle">Run tunnels exported from another machine here as well. No Cloudflare login needed.</p></div></div>
      <div class="card-body" id="importer"></div></div>`);
  $('[data-menu]', page).addEventListener('click', openMenu);

  function showTab(name) {
    tab = name;
    $$('[data-tab]', page).forEach((b) => {
      b.classList.toggle('active', b.dataset.tab === name);
      b.setAttribute('aria-selected', b.dataset.tab === name);
    });
    $$('[data-panel]', page).forEach((p) => p.classList.toggle('hidden', p.dataset.panel !== name));
    // No automatic polling (each check calls the Cloudflare API); use Refresh.
    if (name === 'status' && !status) loadStatus();
  }
  $$('[data-tab]', page).forEach((b) => b.addEventListener('click', () => { tabChosen = true; showTab(b.dataset.tab); }));

  // ------------------------------------------------------------- status dashboard

  let status = null;

  async function loadStatus() {
    try {
      status = await api.replicaStatus();
    } catch (err) {
      status = { error: err.message, tunnels: [] };
    }
    if (page.isConnected) drawStatus();
  }

  const healthBar = (n) => html`<span class="health-bar" title="${n} of 4 edge connections">${
    [0, 1, 2, 3].map((i) => html`<span class="${i < n ? 'on' : ''}"></span>`)}</span>`;

  function machineRows(t) {
    if (t.machines === null) {
      const local = t.local;
      const state = local
        ? (local.ready ? `connected to Cloudflare · ${local.connections} edge connections` : 'not connected to Cloudflare yet')
        : (t.status.startsWith('running') ? 'running (restart the tunnel to report its health)' : 'tunnel stopped');
      return html`<div class="card-body">
        <div class="replica-summary" style="margin-bottom:10px">
          <span class="dot ${local?.ready ? 'ok' : 'bad'}"></span>
          <strong>This machine</strong><span class="muted">${state}</span>
          ${local ? healthBar(local.connections) : ''}
        </div>
        <p class="subtle">To see every machine serving this tunnel, connect its Cloudflare domain on this machine (Domains page),
          or check the Redundancy page on the primary machine.</p>
      </div>`;
    }
    if (!t.machines.length) {
      return html`<div class="card-body"><div class="replica-summary"><span class="dot bad"></span>
        <strong>No machine is connected.</strong><span class="muted">Requests to ${t.routes.join(', ')} are failing.</span></div></div>`;
    }
    return html`<table class="table">
      <thead><tr><th>Machine</th><th>Edge connections</th><th class="hide-sm">Data centers</th>
        <th class="hide-sm">cloudflared</th><th>Connected</th></tr></thead>
      <tbody>${t.machines.map((m) => html`<tr data-machine="${m.id}">
        <td><div class="machine-name"><span class="dot ${m.edge_connections ? 'ok' : 'warn'}"></span>
          <span class="mono">${m.origin_ip || 'unknown IP'}</span>${m.this_machine ? html`<span class="tag tag-accent">this machine</span>` : ''}</div></td>
        <td>${healthBar(m.edge_connections)}<span class="mono subtle">${m.edge_connections}/4</span></td>
        <td class="hide-sm"><div class="colo-list">${m.edge_locations.map((c) => html`<span class="tag">${c.toUpperCase()}</span>`)}</div></td>
        <td class="hide-sm mono subtle">${m.version || ''}${m.arch ? ` · ${m.arch}` : ''}</td>
        <td class="subtle">${m.run_at ? timeAgo(m.run_at) : '–'}</td>
      </tr>`)}</tbody></table>`;
  }

  function drawStatus() {
    const box = $('#status', page);
    if (!status) {
      mount(box, html`<div class="skeleton" style="height:160px"></div>`);
      return;
    }
    if (!status.tunnels.length) {
      mount(box, html`<div class="card empty"><div class="icon-wrap">${icon('layers')}</div>
        <h3>No tunnels are shared with other machines yet</h3>
        <p>${status.error || 'Share a tunnel and import it on a second machine. Cloudflare then balances traffic between them and fails over automatically.'}</p>
        <button class="btn btn-primary" data-go-share>${icon('upload')} Share a tunnel</button></div>`);
      $('[data-go-share]', page).addEventListener('click', () => showTab('share'));
      return;
    }
    mount(box, html`
      <div class="replica-summary" style="margin:-6px 0 14px">
        <span class="subtle">From Cloudflare · checked ${timeAgo(status.checked_at)}</span>
        <button class="btn btn-sm" data-refresh-status>${icon('refresh')} Refresh</button>
      </div>
      ${status.tunnels.map((t) => {
        const connected = Array.isArray(t.machines) ? t.machines.filter((m) => m.edge_connections > 0).length : null;
        const redundant = connected !== null && connected > 1;
        return html`<div class="card replica-card" data-replica-tunnel="${t.name}">
          <div class="card-header">
            <div class="replica-summary">
              <h2>${t.name}</h2>
              <span class="tag ${t.role === 'primary' ? 'tag-accent' : ''}">${
                t.role === 'primary' ? 'primary' : `replica of ${t.replica?.source || 'another machine'}`}</span>
              ${connected === null ? statusPill(t.status)
                : html`<span class="pill ${redundant ? 'pill-running' : 'pill-warning'}" data-connected="${connected}">${
                  connected} machine${connected === 1 ? '' : 's'} connected${redundant ? ' · redundant' : ' · no failover'}</span>`}
              <span class="subtle mono">${t.routes.join(', ')}</span>
            </div>
            ${t.role === 'primary'
              ? html`<button class="btn btn-sm" data-reexport="${t.name}">${icon('upload')} ${t.outdated ? 'Export again' : 'Export'}</button>`
              : html`<button class="btn btn-sm btn-ghost btn-danger" data-remove-replica="${t.name}">Remove replica</button>`}
          </div>
          ${t.outdated ? html`<div class="notice">${icon('alert')}<div>Routes changed since the last export ${timeAgo(t.exported.at)}.
            Other machines still serve the old routes until you export again and import the new file there.</div></div>` : ''}
          ${machineRows(t)}
        </div>`;
      })}`);
    $('[data-refresh-status]', page)?.addEventListener('click', (e) => withButton(e.currentTarget, loadStatus));
    $$('[data-reexport]', page).forEach((b) => b.addEventListener('click', () => {
      selected.clear();
      routesOf(store.tunnels.find((t) => t.name === b.dataset.reexport)).forEach((h) => selected.add(h));
      step = 'select';
      drawWizard();
      showTab('share');
    }));
    $$('[data-remove-replica]', page).forEach((b) => b.addEventListener('click', () => confirmDialog({
      title: `Remove the replica of "${b.dataset.removeReplica}"?`,
      message: 'This machine stops serving the tunnel. It keeps running on its other machines and stays in Cloudflare.',
      confirmLabel: 'Remove replica',
      action: async () => {
        await api.deleteTunnel(b.dataset.removeReplica);
        toast('Replica removed');
        await store.refresh();
        loadStatus();
      },
    })));
  }

  // ------------------------------------------------------------- export wizard

  function drawSelect() {
    const tunnels = primaries();
    if (!store.loaded) return mount($('#wizard', page), html`<div class="skeleton" style="height:120px"></div>`);
    if (!tunnels.length) {
      return mount($('#wizard', page), html`<p class="muted">No tunnels with routes on this machine yet.
        Replicas can't be re-shared; export from the primary machine.</p>`);
    }
    const domains = store.domains();
    const zones = [...new Set(tunnels.flatMap(routesOf).map((h) => zoneOf(h, domains)).filter(Boolean))];
    mount($('#wizard', page), html`
      ${zones.length > 1 ? html`<div class="chips">Select domain:
        ${zones.map((z) => html`<button class="chip" data-zone="${z}">${icon('globe', 'icon-xs')} ${z}</button>`)}</div>` : ''}
      <div class="tree">${tunnels.map((t) => {
        const hosts = routesOf(t);
        const all = hosts.every((h) => selected.has(h));
        const some = hosts.some((h) => selected.has(h));
        return html`<div class="tree-group">
          <label class="check tree-parent"><input type="checkbox" data-tunnel-check="${t.name}" ${all ? 'checked' : ''}
            ${some && !all ? 'data-indeterminate' : ''}><span><strong>${t.name}</strong>
            <span class="subtle">· whole tunnel · ${hosts.length} hostname${hosts.length === 1 ? '' : 's'}</span></span></label>
          ${hosts.map((h) => html`<label class="check tree-child"><input type="checkbox" data-host="${h}" ${selected.has(h) ? 'checked' : ''}>
            <span class="mono">${h}</span></label>`)}
        </div>`;
      })}</div>
      <div class="row" style="margin-top:16px;align-items:center">
        <span class="subtle">${selected.size} hostname${selected.size === 1 ? '' : 's'} selected</span>
        <button class="btn btn-primary" data-next ${selected.size ? '' : 'disabled'} style="flex:0">Continue</button>
      </div>`);
    $$('[data-indeterminate]', page).forEach((el) => { el.indeterminate = true; });
    $$('[data-host]', page).forEach((box) => box.addEventListener('change', () => {
      box.checked ? selected.add(box.dataset.host) : selected.delete(box.dataset.host);
      drawSelect();
    }));
    $$('[data-tunnel-check]', page).forEach((box) => box.addEventListener('change', () => {
      const t = store.tunnels.find((x) => x.name === box.dataset.tunnelCheck);
      routesOf(t).forEach((h) => (box.checked ? selected.add(h) : selected.delete(h)));
      drawSelect();
    }));
    $$('[data-zone]', page).forEach((chip) => chip.addEventListener('click', () => {
      tunnels.flatMap(routesOf).filter((h) => zoneOf(h, domains) === chip.dataset.zone).forEach((h) => selected.add(h));
      drawSelect();
    }));
    $('[data-next]', page)?.addEventListener('click', (e) => withButton(e.currentTarget, async () => {
      // Whole tunnels when all their hostnames are selected, otherwise individual hostnames
      const whole = tunnels.filter((t) => routesOf(t).every((h) => selected.has(h))).map((t) => t.name);
      const partialHosts = [...selected].filter((h) => !whole.some((n) => routesOf(store.tunnels.find((t) => t.name === n)).includes(h)));
      plan = await api.replicaPlan(whole, partialHosts);
      exportTunnels = [...plan.whole];
      step = Object.keys(plan.partial).length ? 'split' : 'passphrase';
      drawWizard();
    }));
  }

  function drawSplit() {
    const entries = Object.entries(plan.partial);
    mount($('#wizard', page), html`
      <div class="notice notice-info">${icon('info')}<div>Every machine running a tunnel serves <em>all</em> of its hostnames.
        To replicate only some of them, Cloudify moves them into their own tunnel first and points their DNS records at it.
        The hostnames stay online while this happens.</div></div>
      ${entries.map(([tunnel, entry]) => html`
        <div class="split-item" data-split="${tunnel}">
          <p><strong>${entry.selected.join(', ')}</strong> ${entry.selected.length === 1 ? 'shares' : 'share'} tunnel <strong>${tunnel}</strong> with
            <span class="mono">${entry.others.join(', ')}</span>.</p>
          <div class="field" style="margin:10px 0 0"><label>New tunnel for the selected hostnames</label>
            <input class="input" data-new-name value="${tunnel}-ha" pattern="[A-Za-z0-9][A-Za-z0-9_.\\-]*" maxlength="63"></div>
        </div>`)}
      <div class="row" style="margin-top:16px">
        <button class="btn" data-back style="flex:0">Back</button>
        <button class="btn btn-primary" data-split-go>Move hostnames & continue</button>
      </div>`);
    $('[data-back]', page).addEventListener('click', () => { step = 'select'; drawWizard(); });
    $('[data-split-go]', page).addEventListener('click', (e) => withButton(e.currentTarget, async () => {
      for (const [tunnel, entry] of entries) {
        const name = $(`[data-split="${CSS.escape(tunnel)}"] [data-new-name]`, page).value.trim();
        await api.replicaSplit(tunnel, entry.selected, name);
        exportTunnels.push(name);
        toast(`Moved ${entry.selected.join(', ')} to tunnel "${name}"`);
      }
      await store.refresh();
      step = 'passphrase';
      drawWizard();
    }));
  }

  function drawPassphrase() {
    mount($('#wizard', page), html`
      <p style="margin-bottom:12px">Exporting ${exportTunnels.map((n, i) => html`${i ? ', ' : ''}<strong>${n}</strong>`)}.</p>
      <form id="exportForm">
        <div class="form-error hidden" id="exportError"></div>
        <div class="row">
          <div class="field"><label for="pp1">Passphrase</label>
            <input class="input" id="pp1" type="password" minlength="8" required autocomplete="new-password"></div>
          <div class="field"><label for="pp2">Confirm</label>
            <input class="input" id="pp2" type="password" minlength="8" required autocomplete="new-password"></div>
        </div>
        <p class="subtle" style="margin:-6px 0 14px">The file contains the tunnel's secret and is encrypted with this passphrase.
          You'll need it on the other machine.</p>
        <div class="row">
          <button type="button" class="btn" data-back style="flex:0">Back</button>
          <button type="submit" class="btn btn-primary">${icon('download')} Download replica file</button>
        </div>
      </form>`);
    $('[data-back]', page).addEventListener('click', () => { step = 'select'; drawWizard(); });
    $('#pp1', page).focus();
    $('#exportForm', page).addEventListener('submit', async (e) => {
      e.preventDefault();
      const error = $('#exportError', page);
      const button = $('button[type=submit]', e.target);
      error.classList.add('hidden');
      if ($('#pp1', page).value !== $('#pp2', page).value) {
        error.textContent = 'Passphrases do not match';
        error.classList.remove('hidden');
        return;
      }
      button.classList.add('loading');
      try {
        const bundle = await api.replicaExport(exportTunnels, $('#pp1', page).value);
        const filename = `cloudify-replica-${exportTunnels.join('-')}-${bundle.created_at.slice(0, 10)}.json`;
        downloadJson(filename, bundle);
        doneInfo = { filename, bundle };
        step = 'done';
        drawWizard();
        store.refresh().catch(() => {});
      } catch (err) {
        error.textContent = err.message;
        error.classList.remove('hidden');
      } finally {
        button.classList.remove('loading');
      }
    });
  }

  function drawDone() {
    const hosts = doneInfo.bundle.tunnels.flatMap((t) => t.hostnames);
    const services = [...new Set(store.tunnels.filter((t) => exportTunnels.includes(t.name))
      .flatMap((t) => t.routes.map((r) => r.service)))];
    mount($('#wizard', page), html`
      <div style="text-align:center"><div class="success-mark">${icon('check')}</div>
        <h3>Downloaded ${doneInfo.filename}</h3></div>
      <ol class="steps-list">
        <li>Make sure the same services run on the other machine: <span class="mono">${services.join(', ')}</span></li>
        <li>On the other machine open Cloudify → <strong>Redundancy</strong> → <strong>Import</strong>, choose the file and enter the passphrase.
          <div class="subtle">Or run <span class="mono">cloudify replica import ${doneInfo.filename}</span></div></li>
        <li>Cloudflare then serves <span class="mono">${hosts.join(', ')}</span> from both machines.</li>
      </ol>
      <button class="btn btn-block" data-again>Share something else</button>`);
    $('[data-again]', page).addEventListener('click', () => { selected.clear(); step = 'select'; drawWizard(); });
  }

  function drawWizard() {
    ({ select: drawSelect, split: drawSplit, passphrase: drawPassphrase, done: drawDone })[step]();
  }

  // ------------------------------------------------------------- import

  function drawImporter() {
    mount($('#importer', page), html`
      <form id="importForm">
        <div class="form-error hidden" id="importError"></div>
        <div class="field"><label for="bundleFile">Replica file</label>
          <input class="input file-input" id="bundleFile" type="file" accept=".json,application/json" required></div>
        <div id="bundleSummary"></div>
        <div class="field"><label for="ipp">Passphrase</label>
          <input class="input" id="ipp" type="password" required autocomplete="off"></div>
        <label class="switch" style="margin-bottom:16px"><input type="checkbox" id="importAutostart" checked><span class="track"></span>
          Start on boot</label>
        <button type="submit" class="btn btn-primary btn-block">${icon('server')} Import and start</button>
      </form>`);
    $('#bundleFile', page).addEventListener('change', async (e) => {
      pendingBundle = null;
      const file = e.target.files[0];
      const summary = $('#bundleSummary', page);
      try {
        pendingBundle = JSON.parse(await file.text());
        if (pendingBundle.format !== 'cloudify-replica-bundle') throw new Error();
        mount(summary, html`<div class="notice notice-info">${icon('info')}<div>From <strong>${pendingBundle.source}</strong>,
          ${timeAgo(pendingBundle.created_at)}:${pendingBundle.tunnels.map((t) => html`<div><strong>${t.name}</strong>
          <span class="mono subtle">${t.hostnames.join(', ')}</span></div>`)}</div></div>`);
      } catch {
        pendingBundle = null;
        mount(summary, html`<div class="form-error">That file is not a Cloudify replica file.</div>`);
      }
    });
    $('#importForm', page).addEventListener('submit', async (e) => {
      e.preventDefault();
      const error = $('#importError', page);
      const button = $('button[type=submit]', e.target);
      error.classList.add('hidden');
      if (!pendingBundle) {
        error.textContent = 'Choose a replica file first';
        error.classList.remove('hidden');
        return;
      }
      button.classList.add('loading');
      try {
        const result = await api.replicaImport(pendingBundle, $('#ipp', page).value, $('#importAutostart', page).checked);
        toast(`This machine now also runs ${result.tunnels.join(', ')}`);
        status = null;
        pendingBundle = null;
        drawImporter();
        await store.refresh();
      } catch (err) {
        error.textContent = err.message;
        error.classList.remove('hidden');
      } finally {
        button.classList.remove('loading');
      }
    });
  }

  drawWizard();
  drawImporter();
  showTab(tab);
  // The selection step follows live data (unless the user is interacting with it)
  const unsubscribe = store.subscribe(() => {
    if (!tabChosen && store.loaded) {
      tabChosen = true;
      if (hasShared() && tab !== 'status') showTab('status');
    }
    if (step === 'select' && !page.contains(document.activeElement)) drawSelect();
  });
  return unsubscribe;
}
