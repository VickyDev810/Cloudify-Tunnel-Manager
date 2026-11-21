// Network map: Cloudflare edge -> public hostnames -> tunnels -> local services.
import { html, icon, mount } from '../ui.js';

const MAX_PER_COLUMN = 10;

function limited(items, render) {
  const shown = items.slice(0, MAX_PER_COLUMN);
  const more = items.length - shown.length;
  return html`${shown.map(render)}${more > 0 ? html`<div class="node more">+${more} more</div>` : ''}`;
}

/** Draw curved links between nodes after layout. */
function drawLinks(grid, svg, links) {
  const box = grid.getBoundingClientRect();
  svg.setAttribute('width', grid.scrollWidth);
  svg.setAttribute('height', grid.scrollHeight);
  const anchor = (id, side) => {
    const el = grid.querySelector(`[data-node="${CSS.escape(id)}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: (side === 'right' ? r.right : r.left) - box.left, y: r.top + r.height / 2 - box.top };
  };
  svg.innerHTML = links.map(([from, to, live]) => {
    const a = anchor(from, 'right');
    const b = anchor(to, 'left');
    if (!a || !b) return '';
    const dx = Math.max(24, (b.x - a.x) / 2);
    return `<path class="${live ? 'live' : ''}" d="M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}"/>`;
  }).join('');
}

export function renderTopology(container, store) {
  const tunnels = store.tunnels.filter((t) => (t.routes || []).length);
  if (!store.loaded) {
    mount(container, html`<div class="topology"><div class="skeleton" style="height:180px"></div></div>`);
    return () => {};
  }
  if (!tunnels.length) {
    mount(container, html`<div class="topology-empty">Your network map appears here once a tunnel has a route:
      Cloudflare → hostname → tunnel → local service.</div>`);
    return () => {};
  }

  const running = (t) => (t.status || '').startsWith('running');
  const hosts = tunnels.flatMap((t) => t.routes.map((r) => ({ host: r.domain, tunnel: t, service: r.service })));
  const services = [];
  for (const h of hosts) {
    const key = `${h.tunnel.name}|${h.service}`;
    if (!services.some((s) => s.key === key)) services.push({ key, service: h.service, tunnel: h.tunnel });
  }
  const machines = (t) => (Array.isArray(t.connectors) ? t.connectors.length : (running(t) ? 1 : 0));

  mount(container, html`
    <div class="topology">
      <div class="topology-grid">
        <svg class="links" aria-hidden="true"></svg>
        <div class="col">
          <div class="col-title">Edge</div>
          <div class="node node-edge" data-node="edge">
            <strong>${icon('cloud', 'icon-xs')} Cloudflare</strong>
            <span class="subtle">Global network · TLS</span>
          </div>
        </div>
        <div class="col">
          <div class="col-title">Hostnames · ${hosts.length}</div>
          ${limited(hosts, (h) => html`<div class="node" data-node="${`h:${h.host}`}" title="${h.host}">
            <span class="mono">${h.host}</span></div>`)}
        </div>
        <div class="col">
          <div class="col-title">Tunnels · ${tunnels.length}</div>
          ${limited(tunnels, (t) => html`<div class="node node-tunnel" data-node="${`t:${t.name}`}">
            <span class="dot ${running(t) ? 'ok' : ''}"></span><strong>${t.name}</strong>
            <span class="meta" title="Machines running this tunnel">${machines(t)}× ${icon('server', 'icon-xs')}</span></div>`)}
        </div>
        <div class="col">
          <div class="col-title">Services</div>
          ${limited(services, (s) => html`<div class="node" data-node="${`s:${s.key}`}" title="${s.service}">
            <span class="mono">${s.service.replace(/^https?:\/\//, '')}</span></div>`)}
        </div>
      </div>
    </div>`);

  const links = [];
  for (const h of hosts) {
    links.push(['edge', `h:${h.host}`, running(h.tunnel)]);
    links.push([`h:${h.host}`, `t:${h.tunnel.name}`, running(h.tunnel)]);
  }
  for (const s of services) links.push([`t:${s.tunnel.name}`, `s:${s.key}`, running(s.tunnel)]);

  const grid = container.querySelector('.topology-grid');
  const svg = container.querySelector('svg.links');
  const redraw = () => drawLinks(grid, svg, links);
  requestAnimationFrame(redraw);
  document.fonts?.ready.then(redraw);
  const observer = new ResizeObserver(redraw);
  observer.observe(grid);
  return () => observer.disconnect();
}
