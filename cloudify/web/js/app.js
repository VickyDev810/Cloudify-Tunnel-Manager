// App shell: session bootstrap, sidebar, hash router, shared data store.

import { api, session } from './api.js';
import { $, $$, html, icon, mount, toast } from './ui.js';
import { renderSetup } from './views/setup.js';
import { renderLogin } from './views/login.js';
import overview from './views/overview.js';
import tunnels from './views/tunnels.js';
import routes from './views/routes.js';
import domains from './views/domains.js';
import redundancy from './views/redundancy.js';
import settings from './views/settings.js';

const PAGES = {
  overview: { title: 'Overview', icon: 'home', view: overview, group: 'Network' },
  tunnels: { title: 'Tunnels', icon: 'tunnel', view: tunnels, group: 'Network' },
  routes: { title: 'Routes', icon: 'route', view: routes, group: 'Network' },
  domains: { title: 'Domains', icon: 'globe', view: domains, group: 'Network' },
  redundancy: { title: 'Redundancy', icon: 'layers', view: redundancy, group: 'Reliability' },
  settings: { title: 'Settings', icon: 'settings', view: settings, group: 'System' },
};
const GROUPS = ['Network', 'Reliability', 'System'];

const root = document.getElementById('app');

// ------------------------------------------------------------------- store

export const store = {
  user: null,
  tunnels: [],
  unmanaged: [],
  connections: [],
  accounts: [],
  info: null,
  loaded: false,
  listeners: new Set(),

  refreshedAt: 0,

  async refresh() {
    this.refreshedAt = Date.now();
    const data = await api.tunnels();
    this.tunnels = data.tunnels || [];
    this.unmanaged = data.unmanaged || [];
    this.loaded = true;
    this.emit();
    return this;
  },
  async refreshAccounts() {
    const data = await api.accounts();
    this.connections = data.connections || [];
    this.accounts = data.accounts || [];
    this.emit();
    return data;
  },
  /** Connected domains, optionally only those of one Cloudflare account. */
  domains(accountId) {
    return this.connections.filter((c) => c.domain && (!accountId || c.account_id === accountId)).map((c) => c.domain);
  },
  async refreshInfo() {
    this.info = await api.systemInfo();
    this.emit();
    return this.info;
  },
  emit() { this.listeners.forEach((fn) => fn(this)); },
  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); },
  get routes() {
    return this.tunnels.flatMap((t) => (t.routes || []).map((r) => ({ ...r, tunnel: t.name, status: t.status })));
  },
};

// ------------------------------------------------------------------- theme

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('cloudify.theme', next);
  $$('[data-theme-icon]').forEach((el) => mount(el, icon(next === 'dark' ? 'sun' : 'moon')));
}

// ------------------------------------------------------------------- shell

let cleanup = null;

function renderShell() {
  const themeIcon = document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon';
  mount(root, html`
    <div class="shell" id="shell">
      <aside class="sidebar">
        <div class="brand"><span class="brand-mark">${icon('tunnel')}</span>Cloudify<span class="version" data-version></span></div>
        <nav class="nav">
          ${GROUPS.map((group) => html`
            <div class="nav-label">${group}</div>
            ${Object.entries(PAGES).filter(([, page]) => page.group === group).map(([key, page]) => html`
              <a href="#/${key}" data-nav="${key}">${icon(page.icon)}<span>${page.title}</span>
                ${['tunnels', 'routes', 'domains'].includes(key) ? html`<span class="count" data-count="${key}"></span>` : ''}
              </a>`)}`)}
        </nav>
        <div class="sidebar-status" id="sidebarStatus"></div>
        <div class="sidebar-footer">
          <div class="avatar">${(store.user?.username || '?').slice(0, 1)}</div>
          <div class="who"><strong>${store.user?.username}</strong><span class="subtle">${store.user?.is_admin ? 'Admin' : 'User'}</span></div>
          <button class="btn btn-ghost btn-icon" id="themeBtn" title="Toggle theme" aria-label="Toggle theme"><span data-theme-icon>${icon(themeIcon)}</span></button>
          <button class="btn btn-ghost btn-icon" id="logoutBtn" title="Sign out" aria-label="Sign out">${icon('logout')}</button>
        </div>
      </aside>
      <main class="main"><div class="page" id="page"></div></main>
    </div>`);

  $('#themeBtn').addEventListener('click', toggleTheme);
  $('#logoutBtn').addEventListener('click', logout);
  $$('.nav a').forEach((a) => a.addEventListener('click', () => $('#shell').classList.remove('nav-open')));
  store.subscribe(updateCounts);
}

function updateStatus() {
  const box = $('#sidebarStatus');
  if (!box) return;
  const info = store.info;
  const online = store.tunnels.filter((t) => (t.status || '').startsWith('running')).length;
  const version = $('[data-version]');
  if (version && info) version.textContent = `v${info.cloudify_version}`;
  mount(box, html`
    <div class="line"><span class="dot ${info?.cloudflare_logged_in ? 'ok' : 'warn'}"></span>
      Cloudflare <span class="mono">${info ? (info.cloudflare_logged_in ? 'connected' : 'not connected') : '…'}</span></div>
    <div class="line"><span class="dot ${online ? 'ok' : ''}"></span>
      Tunnels <span class="mono">${store.loaded ? `${online}/${store.tunnels.length} online` : '…'}</span></div>
    <div class="line"><span class="dot ${info?.cloudflared?.update_available ? 'warn' : info?.cloudflared?.installed ? 'ok' : 'bad'}" style="animation:none"></span>
      cloudflared <span class="mono">${info?.cloudflared?.version || '…'}</span></div>`);
}

function updateCounts() {
  updateStatus();
  const tunnelCount = $('[data-count=tunnels]');
  const routeCount = $('[data-count=routes]');
  if (tunnelCount) tunnelCount.textContent = store.loaded ? store.tunnels.length : '';
  if (routeCount) routeCount.textContent = store.loaded ? store.routes.length : '';
  const domainCount = $('[data-count=domains]');
  if (domainCount) domainCount.textContent = store.connections.length || '';
}

function currentPage() {
  const key = location.hash.replace(/^#\/?/, '').split('?')[0];
  return Object.prototype.hasOwnProperty.call(PAGES, key) ? key : 'overview';
}

export function navigate(key) {
  location.hash = `#/${key}`;
}

function route() {
  if (!store.user) return;
  const key = currentPage();
  const page = PAGES[key];
  $$('[data-nav]').forEach((a) => a.classList.toggle('active', a.dataset.nav === key));
  document.title = `${page.title} · Cloudify`;
  cleanup?.();
  // A fresh element per visit: a view that finishes loading after the user has moved on draws
  // into its own detached element instead of over the page that is open now.
  const container = document.createElement('div');
  $('#page').replaceChildren(container);
  cleanup = page.view(container, { store, navigate, openMenu: () => $('#shell').classList.add('nav-open') }) || null;
  if (store.loaded && Date.now() - store.refreshedAt > 30000) store.refresh().catch(() => {});
  window.scrollTo(0, 0);
}

// ------------------------------------------------------------- session flow

async function startApp() {
  store.user = await api.me();
  renderShell();
  route();
  store.refresh().catch((err) => toast(err.message, 'error'));
  store.refreshInfo().catch(() => {});
  store.refreshAccounts().catch(() => {});
  // No background polling: listing tunnels calls the Cloudflare API, so data is
  // refreshed after actions, with the Refresh buttons, or when a page is opened
  // and the data is more than 30 seconds old.
}

async function logout() {
  try { await api.logout(); } catch { /* token may already be invalid */ }
  session.token = null;
  showLogin();
}

function stop() {
  cleanup?.();
  cleanup = null;
  store.user = null;
  store.listeners.clear();
  // Forget the previous session's data so the next sign-in starts clean
  Object.assign(store, { tunnels: [], unmanaged: [], connections: [], accounts: [], info: null,
                         loaded: false, refreshedAt: 0 });
}

function showLogin(message) {
  stop();
  document.getElementById('toasts').replaceChildren();  // don't carry notifications across sessions
  renderLogin(root, { message, onLogin: startApp });
}

session.onExpired = () => { if (store.user) showLogin('Your session expired. Please sign in again.'); };

async function boot() {
  let setup;
  try {
    setup = await api.setupCheck();
  } catch (err) {
    mount(root, html`<div class="auth-wrap"><div class="card auth-card"><h2>Can't reach Cloudify</h2><p class="muted" style="margin-top:8px">${err.message}</p></div></div>`);
    return;
  }
  if (setup.needs_setup) {
    stop();
    renderSetup(root, { setup, onFinish: startApp });
    return;
  }
  if (session.token) {
    try { await startApp(); return; } catch { session.token = null; }
  }
  showLogin();
}

window.addEventListener('hashchange', route);
boot();
