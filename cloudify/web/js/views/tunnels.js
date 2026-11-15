import { api } from '../api.js';
import { $, $$, confirmDialog, html, icon, mount, statusPill, timeAgo, toast, withButton } from '../ui.js';
import { accountLabel, addRouteDialog, createTunnelDialog, dnsSummary, dnsWarning, editRouteDialog, replicaWarning } from './dialogs.js';

// DNS check results per tunnel (kept across redraws until the next check)
const dnsChecks = new Map();
const DNS_LABELS = {
  ok: ['pill-running', 'DNS ok'], resolves: ['pill-running', 'DNS resolves'],
  missing: ['pill-danger', 'DNS record missing'], 'other-target': ['pill-warning', 'DNS points elsewhere'],
  unknown: ['pill-stopped', 'DNS unknown'],
};

function dnsBadge(t, domain) {
  const record = (dnsChecks.get(t.name) || []).find((r) => r.hostname === domain);
  if (!record) return '';
  const [cls, label] = DNS_LABELS[record.status] || DNS_LABELS.unknown;
  const how = record.method === 'api' ? 'checked with the Cloudflare API' : 'public DNS lookup (add an API token on Domains for an exact check)';
  return html`<span class="pill ${cls}" title="${record.detail || how}" data-dns-status="${record.status}">${label}</span>
    ${record.status === 'missing' && !t.replica ? html`<button class="btn btn-sm" data-dns-repair>Recreate</button>` : ''}
    ${record.status === 'other-target' && !t.replica ? html`<button class="btn btn-sm" data-dns-repair data-overwrite>Re-point</button>` : ''}`;
}

function machinesBadge(t) {
  if (!Array.isArray(t.connectors)) return '';
  const n = t.connectors.length;
  return html`<span class="pill ${n > 1 ? 'pill-running' : 'pill-warning'} pill-plain" title="${
    t.connectors.map((c) => c.origin_ip).filter(Boolean).join(', ')}">${icon('server', 'icon-xs')} ${n} machine${n === 1 ? '' : 's'}</span>`;
}

function tunnelCard(t, store) {
  const running = (t.status || '').startsWith('running');
  const routes = t.routes || [];
  const replica = t.replica;
  return html`
    <div class="card tunnel-card" data-tunnel="${t.name}">
      <div class="top">
        <div class="title">
          <h3>${t.name} ${statusPill(t.status)}
            ${replica ? html`<span class="pill pill-plain pill-info" title="Imported ${timeAgo(replica.imported_at)}">Replica of ${replica.source || 'another machine'}</span>` : ''}
            ${machinesBadge(t)}
            ${t.exists === false ? html`<span class="pill pill-warning" title="Not found in your connected Cloudflare accounts">Missing in Cloudflare</span>`
              : t.exists === null ? html`<span class="pill" title="Cloudflare couldn't be asked: offline, or the tunnel's account isn't connected on this machine">Unknown in Cloudflare</span>` : ''}</h3>
          <div class="subtle">${store.accounts.length > 1 && t.account_id ? html`${icon('globe', 'icon-xs')} ${accountLabel(store, t.account_id)} · ` : ''}${
            t.id ? html`<span class="mono">${t.id}</span>` : 'No tunnel ID'}${t.created_at ? ` · created ${timeAgo(t.created_at)}` : ''}</div>
        </div>
        <div class="actions">
          ${running
            ? html`<button class="btn btn-sm" data-action="stop">${icon('stop')} Stop</button>`
            : html`<button class="btn btn-sm" data-action="start" ${routes.length ? '' : html`disabled title="Add a route first"`}>${icon('play')} Start</button>`}
          ${replica ? '' : html`<button class="btn btn-sm" data-action="route">${icon('plus')} Add route</button>`}
          ${replica || !routes.length ? '' : html`<a class="btn btn-sm" href="#/redundancy?tunnel=${encodeURIComponent(t.name)}" title="Run this tunnel on more machines">${icon('layers')} Replicate</a>`}
          <button class="btn btn-sm btn-ghost btn-icon btn-danger" data-action="delete" title="${replica ? 'Remove replica' : 'Delete tunnel'}" aria-label="${replica ? 'Remove replica' : 'Delete tunnel'}">${icon('trash')}</button>
        </div>
      </div>
      ${t.replicas_outdated ? html`<div class="notice">${icon('alert')}
        Routes changed since this tunnel was exported ${timeAgo(t.exported.at)}. Other machines still serve the old routes:
        <a href="#/redundancy?tunnel=${encodeURIComponent(t.name)}">export again</a> and import it there.</div>` : ''}
      ${routes.length ? html`
        <div class="routes">${routes.map((r) => html`
          <div class="route-row" data-domain="${r.domain}">
            <a class="domain" href="https://${r.domain.replace(/^\*\./, '')}" target="_blank" rel="noopener">${r.domain}</a>
            <span class="arrow">→</span>
            <span class="service" title="${r.service}">${r.service}</span>
            ${dnsBadge(t, r.domain)}
            ${replica ? '' : html`
              <button class="btn btn-ghost btn-sm btn-icon" data-edit-route title="Edit route" aria-label="Edit route ${r.domain}">${icon('edit')}</button>
              <button class="btn btn-ghost btn-sm btn-icon" data-remove-route title="Remove route" aria-label="Remove route ${r.domain}">${icon('x')}</button>`}
          </div>`)}
        </div>`
      : html`<div class="no-routes">No routes yet. Add one to publish a local service on your domain.</div>`}
      <div class="footer">
        <label class="switch"><input type="checkbox" data-autostart ${t.auto_start ? 'checked' : ''}><span class="track"></span>
          <span>Start on boot</span></label>
        <div class="row" style="flex:0;align-items:center">
          <span class="subtle" style="white-space:nowrap">${replica ? 'Routes are managed on the primary machine · ' : ''}${routes.length} route${routes.length === 1 ? '' : 's'}</span>
          ${routes.length ? html`<button class="btn btn-sm" data-check-dns title="Check that each hostname's DNS record exists and points here">${icon('globe')} Check DNS</button>` : ''}
        </div>
      </div>
    </div>`;
}

export default function tunnels(page, { store, openMenu }) {
  const draw = () => {
    mount(page, html`
      <div class="page-header">
        <div>
          <button class="btn btn-ghost btn-icon menu-btn" data-menu aria-label="Menu">${icon('menu')}</button>
          <div class="eyebrow">Network</div>
          <h1>Tunnels</h1>
          <p class="muted">Each tunnel connects this machine to Cloudflare and can serve many hostnames.</p>
        </div>
        <div class="row" style="flex:0">
          <button class="btn btn-icon" data-refresh title="Refresh" aria-label="Refresh">${icon('refresh')}</button>
          <button class="btn btn-primary" data-create>${icon('plus')} New tunnel</button>
        </div>
      </div>

      ${!store.loaded ? html`<div class="tunnel-list">${[1, 2].map(() => html`<div class="skeleton" style="height:150px"></div>`)}</div>`
        : store.tunnels.length ? html`<div class="tunnel-list">${store.tunnels.map((t) => tunnelCard(t, store))}</div>`
        : html`<div class="card empty"><div class="icon-wrap">${icon('tunnel')}</div><h3>No tunnels yet</h3>
            <p>Create a tunnel, then add routes like <span class="mono">app.example.com → localhost:3000</span>.</p>
            <button class="btn btn-primary" data-create>${icon('plus')} New tunnel</button></div>`}

      ${store.unmanaged.length ? html`
        <div class="section">
          <div class="section-title"><div><h2>Other tunnels in your Cloudflare accounts</h2>
            <p class="subtle">Created outside Cloudify. Adopt one to manage it here.</p></div></div>
          <div class="card">${store.unmanaged.map((t) => html`
            <div class="list-item"><div class="grow"><strong>${t.name}</strong><div class="subtle mono">${t.id}</div></div>
              <button class="btn btn-sm" data-adopt="${t.name}" data-account="${t.account_id || ''}">Adopt</button></div>`)}
          </div>
        </div>` : ''}`);
    bind();
  };

  const tunnelOf = (el) => el.closest('[data-tunnel]').dataset.tunnel;
  const find = (name) => store.tunnels.find((t) => t.name === name);

  function bind() {
    $('[data-menu]', page).addEventListener('click', openMenu);
    $$('[data-create]', page).forEach((b) => b.addEventListener('click', () => createTunnelDialog(store)));
    $('[data-refresh]', page).addEventListener('click', (e) => withButton(e.currentTarget, () => store.refresh()));

    $$('[data-action]', page).forEach((button) => button.addEventListener('click', () => {
      const name = tunnelOf(button);
      const action = button.dataset.action;
      if (action === 'route') return addRouteDialog(store, name);
      if (action === 'delete') {
        const replica = find(name)?.replica;
        return confirmDialog({
          title: replica ? `Remove the replica of "${name}"?` : `Delete "${name}"?`,
          message: replica
            ? 'This machine stops serving the tunnel. It keeps running on its other machines and stays in Cloudflare.'
            : `The tunnel is stopped and deleted from Cloudflare along with its routes and auto-start. ${
              dnsWarning(store, (find(name)?.routes || []).map((r) => r.domain))}`,
          confirmLabel: replica ? 'Remove replica' : 'Delete tunnel',
          action: async () => {
            const result = await api.deleteTunnel(name);
            toast([replica ? `Replica of "${name}" removed` : `Tunnel "${name}" deleted`, dnsSummary(result.dns)]
              .filter(Boolean).join('\n'));
            await store.refresh();
          },
        });
      }
      return withButton(button, async () => {
        if (action === 'start') { await api.startTunnel(name); toast(`Tunnel "${name}" started`); }
        if (action === 'stop') { await api.stopTunnel(name); toast(`Tunnel "${name}" stopped`); }
        await store.refresh();
      });
    }));

    $$('[data-autostart]', page).forEach((input) => input.addEventListener('change', async () => {
      const name = tunnelOf(input);
      input.disabled = true;
      try {
        if (input.checked) { await api.enableAutostart(name); toast(`"${name}" will start on boot`); }
        else { await api.disableAutostart(name); toast(`Auto-start disabled for "${name}"`); }
      } catch (err) {
        input.checked = !input.checked;
        toast(err.message, 'error');
      } finally {
        input.disabled = false;
        store.refresh().catch(() => {});
      }
    }));

    $$('[data-edit-route]', page).forEach((button) => button.addEventListener('click', () => {
      const name = tunnelOf(button);
      const domain = button.closest('[data-domain]').dataset.domain;
      editRouteDialog(store, name, find(name).routes.find((r) => r.domain === domain));
    }));

    $$('[data-remove-route]', page).forEach((button) => button.addEventListener('click', () => {
      const name = tunnelOf(button);
      const domain = button.closest('[data-domain]').dataset.domain;
      confirmDialog({
        title: 'Remove route?',
        message: `${domain} will stop being served by "${name}". ${dnsWarning(store, [domain])}${replicaWarning(find(name))}`,
        confirmLabel: 'Remove route',
        action: async () => {
          const result = await api.removeRoute(name, domain);
          toast([`Route ${domain} removed`, dnsSummary(result.dns)].filter(Boolean).join('\n'));
          await store.refresh();
        },
      });
    }));

    $$('[data-check-dns]', page).forEach((button) => button.addEventListener('click', () => withButton(button, async () => {
      const name = tunnelOf(button);
      const { records } = await api.checkDns(name);
      dnsChecks.set(name, records);
      const broken = records.filter((r) => ['missing', 'other-target'].includes(r.status));
      toast(broken.length ? `${broken.length} DNS problem${broken.length > 1 ? 's' : ''} on "${name}"` : `DNS for "${name}" looks good`,
        broken.length ? 'error' : 'success');
      draw();
    })));

    $$('[data-dns-repair]', page).forEach((button) => button.addEventListener('click', () => {
      const name = tunnelOf(button);
      const domain = button.closest('[data-domain]').dataset.domain;
      const overwrite = button.hasAttribute('data-overwrite');
      const repair = async () => {
        await api.repairDns(name, domain, overwrite);
        toast(`DNS record for ${domain} now points at "${name}"`);
        dnsChecks.set(name, (await api.checkDns(name)).records);
        draw();
      };
      if (!overwrite) return withButton(button, repair);
      return confirmDialog({
        title: `Re-point ${domain}?`,
        message: `Its DNS record currently points somewhere else. Replacing it sends ${domain} to tunnel "${name}" instead.`,
        confirmLabel: 'Re-point DNS',
        action: repair,
      });
    }));

    $$('[data-adopt]', page).forEach((button) => button.addEventListener('click', () =>
      withButton(button, async () => {
        await api.adoptTunnel(button.dataset.adopt, button.dataset.account);
        toast(`Now managing "${button.dataset.adopt}"`);
        await store.refresh();
      })));
  }

  draw();
  return store.subscribe(draw);
}
