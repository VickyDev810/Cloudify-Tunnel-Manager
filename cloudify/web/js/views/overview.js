import { $, html, icon, mount, statusPill, formDialog, toast } from '../ui.js';
import { addRouteDialog, createTunnelDialog } from './dialogs.js';
import { cloudflareConnect } from './setup.js';
import { renderTopology } from './topology.js';

export function connectCloudflareDialog(store, { title = 'Connect Cloudflare', requireDomain = false } = {}) {
  let stop = () => {};
  const dialog = formDialog({
    title,
    submitLabel: 'Close',
    body: html`<div id="cfConnect"></div>`,
    onSubmit: async () => stop(),
    onOpen(d) {
      $('.modal-footer', d).classList.add('hidden');
      stop = cloudflareConnect($('#cfConnect', d), {
        requireDomain,
        onDone: () => {
          stop(); d.close(); d.remove();
          toast('Cloudflare domain connected');
          store.refreshInfo(); store.refreshAccounts(); store.refresh();
        },
      });
    },
  });
  dialog.addEventListener('close', () => stop());
}

export default function overview(page, { store, openMenu }) {
  let stopTopology = () => {};

  const draw = () => {
    const running = store.tunnels.filter((t) => (t.status || '').startsWith('running')).length;
    const replicated = store.tunnels.filter((t) => t.exported || t.replica).length;
    const info = store.info;
    const cf = info?.cloudflared;
    const v = (value) => (store.loaded ? value : '–');

    mount(page, html`
      <div class="page-header">
        <div>
          <button class="btn btn-ghost btn-icon menu-btn" data-menu aria-label="Menu">${icon('menu')}</button>
          <div class="eyebrow">Network</div>
          <h1>Overview</h1>
          <p>Traffic from Cloudflare's edge to the services on this machine.</p>
        </div>
        <div class="row" style="flex:0">
          <button class="btn" data-add-route>${icon('plus')} Add route</button>
          <button class="btn btn-primary" data-create>${icon('plus')} New tunnel</button>
        </div>
      </div>

      ${info && !info.cloudflare_logged_in ? html`
        <div class="banner banner-info">${icon('info')}
          <div class="grow"><strong>Connect a Cloudflare domain</strong>
            <span class="muted"> to create tunnels and DNS records.</span></div>
          <a class="btn btn-primary btn-sm" href="#/domains">Connect</a>
        </div>` : ''}
      ${cf?.update_available ? html`
        <div class="banner banner-warning">${icon('download')}
          <div class="grow"><strong>cloudflared ${cf.latest} is available</strong>
            <span class="muted"> · you have ${cf.version}</span></div>
          <a class="btn btn-sm" href="#/settings">Update</a>
        </div>` : ''}

      <div class="stats">
        <div class="stat"><div class="label">${icon('bolt')}Tunnels online</div>
          <div class="value">${v(running)}<small> / ${v(store.tunnels.length)}</small></div></div>
        <div class="stat"><div class="label">${icon('route')}Hostnames</div><div class="value">${v(store.routes.length)}</div></div>
        <div class="stat"><div class="label">${icon('globe')}Domains</div><div class="value">${store.connections.length}</div></div>
        <div class="stat"><div class="label">${icon('layers')}Replicated</div><div class="value">${v(replicated)}</div></div>
      </div>

      <div class="card section">
        <div class="card-header"><div><h2>${icon('route')} Network map</h2></div>
          <span class="subtle"><span class="dot ok" style="width:6px;height:6px;margin-right:6px"></span>live tunnel</span></div>
        <div id="topology"></div>
      </div>

      <div class="grid-2 section">
        <div class="card">
          <div class="card-header"><h2>${icon('tunnel')} Tunnels</h2><a href="#/tunnels" class="subtle">View all →</a></div>
          ${!store.loaded ? html`<div class="card-body"><div class="skeleton" style="height:80px"></div></div>`
            : store.tunnels.length ? store.tunnels.slice(0, 6).map((t) => html`
              <div class="list-item">
                <div class="grow"><strong>${t.name}</strong>
                  <div class="subtle">${(t.routes || []).length} hostname${(t.routes || []).length === 1 ? '' : 's'}${
                    t.auto_start ? ' · starts on boot' : ''}${t.replica ? ' · replica' : ''}</div></div>
                ${statusPill(t.status)}
              </div>`)
            : html`<div class="empty"><div class="icon-wrap">${icon('tunnel')}</div><h3>No tunnels yet</h3>
                <p>A tunnel securely connects this machine to Cloudflare, with no open ports.</p>
                <button class="btn btn-primary" data-create>${icon('plus')} Create your first tunnel</button></div>`}
        </div>

        <div class="card">
          <div class="card-header"><h2>${icon('server')} This machine</h2><a href="#/settings" class="subtle">Settings →</a></div>
          <div class="card-body">
            ${info ? html`
              <dl class="kv">
                <dt>Cloudflare</dt><dd>${info.cloudflare_logged_in
                  ? html`<span class="pill pill-running">Connected · ${store.connections.length || 1} domain${store.connections.length === 1 ? '' : 's'}</span>`
                  : html`<span class="pill pill-warning">Not connected</span>`}</dd>
                <dt>cloudflared</dt><dd>${cf?.installed ? html`<span class="mono">${cf.version}</span>
                  ${cf.update_available ? html` <span class="tag">update ${cf.latest}</span>` : html` <span class="subtle">· up to date</span>`}`
                  : html`<span class="pill pill-warning">Not installed</span>`}</dd>
                <dt>Auto-start</dt><dd class="mono">${info.autostart_backend || 'not supported'}</dd>
                <dt>Platform</dt><dd class="mono">${info.platform}</dd>
                <dt>Cloudify</dt><dd class="mono">v${info.cloudify_version}</dd>
              </dl>` : html`<div class="skeleton" style="height:120px"></div>`}
          </div>
        </div>
      </div>`);

    stopTopology();
    stopTopology = renderTopology($('#topology', page), store);
    page.querySelectorAll('[data-create]').forEach((b) => b.addEventListener('click', () => createTunnelDialog(store)));
    $('[data-add-route]', page).addEventListener('click', () => addRouteDialog(store));
    $('[data-menu]', page).addEventListener('click', openMenu);
  };
  draw();
  const unsubscribe = store.subscribe(draw);
  return () => { unsubscribe(); stopTopology(); };
}
