import { api } from '../api.js';
import { $, $$, confirmDialog, html, icon, mount, statusPill, toast, zoneOf } from '../ui.js';
import { addRouteDialog, dnsSummary, dnsWarning, editRouteDialog, replicaWarning } from './dialogs.js';

const OTHER = '__other__';

export default function routes(page, { store, openMenu }) {
  let query = '';
  let domainFilter = '';

  /** Routes grouped by connected domain (others last). */
  const groups = () => {
    const domains = store.domains();
    const byDomain = new Map(domains.map((d) => [d, []]));
    for (const r of store.routes) {
      if (query && !`${r.domain} ${r.service} ${r.tunnel}`.toLowerCase().includes(query)) continue;
      const zone = zoneOf(r.domain, domains) || OTHER;
      if (domainFilter && zone !== domainFilter) continue;
      if (!byDomain.has(zone)) byDomain.set(zone, []);
      byDomain.get(zone).push(r);
    }
    return [...byDomain.entries()].filter(([zone, list]) => list.length || (!query && !domainFilter && zone !== OTHER));
  };

  const table = (list) => html`<table class="table">
    <thead><tr><th>Hostname</th><th>Service</th><th class="hide-sm">Tunnel</th><th class="hide-sm">Status</th><th></th></tr></thead>
    <tbody>${list.map((r) => html`
      <tr data-domain="${r.domain}" data-tunnel="${r.tunnel}">
        <td><a href="https://${r.domain.replace(/^\*\./, '')}" target="_blank" rel="noopener">${r.domain}</a></td>
        <td class="mono muted">${r.service}</td>
        <td class="hide-sm">${r.tunnel}</td>
        <td class="hide-sm">${statusPill(r.status)}</td>
        <td class="actions">${store.tunnels.find((t) => t.name === r.tunnel)?.replica
          ? html`<span class="subtle">replica</span>`
          : html`<button class="btn btn-sm btn-ghost btn-icon" data-edit title="Edit route" aria-label="Edit route ${r.domain}">${icon('edit')}</button>
                 <button class="btn btn-sm btn-danger" data-remove>Remove</button>`}</td>
      </tr>`)}
    </tbody></table>`;

  const drawRows = () => {
    const container = $('#routeRows', page);
    if (!store.loaded) {
      mount(container, html`<div class="card"><div class="card-body"><div class="skeleton" style="height:120px"></div></div></div>`);
      return;
    }
    if (!store.routes.length) {
      mount(container, html`<div class="card empty"><div class="icon-wrap">${icon('route')}</div><h3>No routes yet</h3>
        <p>A route publishes a local service, e.g. <span class="mono">app.example.com → localhost:3000</span>.</p>
        <button class="btn btn-primary" data-add>${icon('plus')} Add route</button></div>`);
    } else {
      const list = groups();
      mount(container, list.length ? list.map(([zone, items]) => html`
        <div class="card domain-group" data-zone="${zone}">
          <div class="card-header"><h2>${icon('globe')} ${zone === OTHER ? 'Other hostnames' : zone}</h2>
            <span class="subtle">${items.length} route${items.length === 1 ? '' : 's'}</span></div>
          ${items.length ? table(items) : html`<div class="card-body subtle">No routes on this domain yet.</div>`}
        </div>`)
        : html`<div class="card empty"><p>No routes match "${query}".</p></div>`);
    }
    $$('[data-add]', page).forEach((b) => b.addEventListener('click', () => addRouteDialog(store)));
    $$('[data-edit]', page).forEach((button) => button.addEventListener('click', () => {
      const { domain, tunnel } = button.closest('tr').dataset;
      editRouteDialog(store, tunnel, store.routes.find((r) => r.domain === domain && r.tunnel === tunnel));
    }));
    $$('[data-remove]', page).forEach((button) => button.addEventListener('click', () => {
      const { domain, tunnel } = button.closest('tr').dataset;
      confirmDialog({
        title: 'Remove route?',
        message: `${domain} will stop being served by "${tunnel}". ${dnsWarning(store, [domain])}${
          replicaWarning(store.tunnels.find((t) => t.name === tunnel))}`,
        confirmLabel: 'Remove route',
        action: async () => {
          const result = await api.removeRoute(tunnel, domain);
          toast([`Route ${domain} removed`, dnsSummary(result.dns)].filter(Boolean).join('\n'));
          await store.refresh();
        },
      });
    }));
  };

  const drawFilter = () => {
    const select = $('#domainFilter', page);
    const domains = store.domains();
    select.classList.toggle('hidden', domains.length < 2);
    mount(select, html`<option value="">All domains</option>${domains.map((d) =>
      html`<option value="${d}" ${d === domainFilter ? 'selected' : ''}>${d}</option>`)}`);
  };

  mount(page, html`
    <div class="page-header">
      <div>
        <button class="btn btn-ghost btn-icon menu-btn" data-menu aria-label="Menu">${icon('menu')}</button>
        <div class="eyebrow">Network</div>
          <h1>Routes</h1>
        <p class="muted">Public hostnames and the local services they forward to, by domain.</p>
      </div>
      <div class="row" style="flex:0">
        <select class="input hidden" id="domainFilter" aria-label="Filter by domain" style="width:auto"></select>
        <div class="search">${icon('search')}<input class="input" id="routeSearch" placeholder="Filter routes" aria-label="Filter routes"></div>
        <button class="btn btn-primary" id="addRouteBtn">${icon('plus')} Add route</button>
      </div>
    </div>
    <div class="group-list" id="routeRows"></div>`);

  $('[data-menu]', page).addEventListener('click', openMenu);
  $('#addRouteBtn', page).addEventListener('click', () => addRouteDialog(store));
  $('#routeSearch', page).addEventListener('input', (e) => { query = e.target.value.trim().toLowerCase(); drawRows(); });
  $('#domainFilter', page).addEventListener('change', (e) => { domainFilter = e.target.value; drawRows(); });
  const draw = () => { drawFilter(); drawRows(); };
  draw();
  return store.subscribe(draw);
}
