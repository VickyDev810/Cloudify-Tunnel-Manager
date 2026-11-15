import { api, session } from '../api.js';
import { $, $$, confirmDialog, formDialog, html, icon, mount, timeAgo, toast, withButton } from '../ui.js';

function changePasswordDialog() {
  formDialog({
    title: 'Change password',
    submitLabel: 'Change password',
    body: html`
      <div class="field"><label for="cp0">Current password</label>
        <input class="input" id="cp0" name="current" type="password" autocomplete="current-password" required></div>
      <div class="field"><label for="cp1">New password</label>
        <input class="input" id="cp1" name="next" type="password" autocomplete="new-password" required minlength="6"></div>
      <div class="field"><label for="cp2">Confirm new password</label>
        <input class="input" id="cp2" name="confirm" type="password" autocomplete="new-password" required minlength="6"></div>`,
    async onSubmit(v) {
      if (v.next !== v.confirm) throw new Error('New passwords do not match');
      await api.changePassword(v.current, v.next);
      toast('Password changed. Other sessions were signed out.');
    },
  });
}

function addUserDialog(onDone) {
  formDialog({
    title: 'Add user',
    submitLabel: 'Add user',
    body: html`
      <div class="field"><label for="nu">Username</label>
        <input class="input" id="nu" name="username" required maxlength="64" autocomplete="off"></div>
      <div class="field"><label for="np">Password</label>
        <input class="input" id="np" name="password" type="password" required minlength="6" autocomplete="new-password">
        <span class="hint">Share it with them securely. They can change it after signing in.</span></div>`,
    async onSubmit(v) {
      await api.register(v.username.trim(), v.password);
      toast(`User "${v.username.trim()}" added`);
      onDone();
    },
  });
}

export default function settings(page, { store, openMenu }) {
  let users = null;

  const draw = () => {
    const info = store.info;
    const cf = info?.cloudflared;
    mount(page, html`
      <div class="page-header">
        <div>
          <button class="btn btn-ghost btn-icon menu-btn" data-menu aria-label="Menu">${icon('menu')}</button>
          <div class="eyebrow">System</div>
          <h1>Settings</h1>
          <p class="muted">Accounts, Cloudflare connection and system.</p>
        </div>
      </div>

      <div class="grid-2">
        <div class="card">
          <div class="card-header"><h2>Your account</h2></div>
          <div class="card-body">
            <dl class="kv"><dt>Username</dt><dd>${store.user.username}</dd>
              <dt>Role</dt><dd>${store.user.is_admin ? 'Admin' : 'User'}</dd></dl>
            <button class="btn" style="margin-top:18px" data-change-password>${icon('key')} Change password</button>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><h2>Cloudflare</h2></div>
          <div class="card-body">
            ${info ? html`
              <dl class="kv">
                <dt>Domains</dt><dd>${info.cloudflare_logged_in ? html`<span class="pill pill-running">${store.connections.length || 1} connected</span>`
                  : html`<span class="pill pill-warning">Not connected</span>`}</dd>
                <dt>cloudflared</dt><dd>${cf.installed ? html`<span class="mono">${cf.version}</span>` : 'Not installed'}</dd>
                <dt>Latest release</dt><dd>${cf.latest ? html`<span class="mono">${cf.latest}</span>` : html`<span class="subtle">unknown (offline?)</span>`}</dd>
                <dt>Binary</dt><dd class="mono subtle">${cf.path || '–'}</dd>
              </dl>
              <div class="row" style="margin-top:18px;flex-wrap:wrap">
                <a class="btn ${info.cloudflare_logged_in ? '' : 'btn-primary'}" href="#/domains">${icon('globe')} ${info.cloudflare_logged_in ? 'Manage domains' : 'Connect Cloudflare'}</a>
                ${cf.update_available ? html`<button class="btn btn-primary" data-update>${icon('download')} Update to ${cf.latest}</button>`
                  : cf.installed ? html`<button class="btn" data-check>${icon('refresh')} Check for updates</button>` : ''}
              </div>` : html`<div class="skeleton" style="height:120px"></div>`}
          </div>
        </div>
      </div>

      <div class="card section">
        <div class="card-header"><div><h2>Users</h2><p class="subtle">Everyone here can manage all tunnels.</p></div>
          <button class="btn btn-sm" data-add-user>${icon('plus')} Add user</button></div>
        <div id="userList">${users === null ? html`<div class="card-body"><div class="skeleton" style="height:48px"></div></div>`
          : users.map((u) => html`
            <div class="list-item">
              <div class="avatar">${u.username.slice(0, 1)}</div>
              <div class="grow"><strong>${u.username}</strong>${u.username === store.user.username ? html` <span class="subtle">(you)</span>` : ''}
                <div class="subtle">${u.is_admin ? 'Admin' : 'User'}${u.created_at ? ` · added ${timeAgo(u.created_at)}` : ''}</div></div>
              ${u.username === store.user.username ? ''
                : html`<button class="btn btn-sm btn-ghost btn-danger" data-delete-user="${u.username}">Remove</button>`}
            </div>`)}
        </div>
      </div>

      <div class="card section">
        <div class="card-header"><h2>Server</h2></div>
        <div class="card-body">
          ${info ? html`<dl class="kv">
            <dt>Cloudify</dt><dd class="mono">v${info.cloudify_version}</dd>
            <dt>Platform</dt><dd>${info.platform}</dd>
            <dt>Auto-start via</dt><dd>${info.autostart_backend || 'not supported'}</dd>
            <dt>Config folder</dt><dd class="mono">${info.config_dir}</dd>
            <dt>API docs</dt><dd><a href="/api/docs" target="_blank" rel="noopener">/api/docs</a></dd>
          </dl>` : ''}
          <p class="muted" style="margin-top:18px">Stopping the server closes this dashboard. Running tunnels keep running.</p>
          <button class="btn btn-danger" style="margin-top:12px" data-shutdown>${icon('power')} Stop server</button>
        </div>
      </div>`);
    bind();
  };

  async function loadUsers() {
    try { users = (await api.users()).users; } catch (err) { toast(err.message, 'error'); users = []; }
    draw();
  }

  function bind() {
    $('[data-menu]', page).addEventListener('click', openMenu);
    $('[data-change-password]', page).addEventListener('click', changePasswordDialog);
    $('[data-add-user]', page).addEventListener('click', () => addUserDialog(loadUsers));
    $('[data-check]', page)?.addEventListener('click', (e) => withButton(e.currentTarget, async () => {
      const info = await store.refreshInfo();
      toast(info.cloudflared.update_available ? `cloudflared ${info.cloudflared.latest} is available`
        : 'cloudflared is up to date', 'info');
    }));
    $('[data-update]', page)?.addEventListener('click', (e) => withButton(e.currentTarget, async () => {
      const result = await api.updateCloudflared();
      toast(`${result.message}\nRestart running tunnels to use it.`);
      await store.refreshInfo();
    }));
    $$('[data-delete-user]', page).forEach((b) => b.addEventListener('click', () => confirmDialog({
      title: `Remove ${b.dataset.deleteUser}?`,
      message: 'They will be signed out and can no longer access this dashboard.',
      confirmLabel: 'Remove user',
      action: async () => { await api.deleteUser(b.dataset.deleteUser); toast('User removed'); await loadUsers(); },
    })));
    $('[data-shutdown]', page).addEventListener('click', () => confirmDialog({
      title: 'Stop the Cloudify server?',
      message: 'The dashboard will go offline until you run "cloudify serve" again. Tunnels keep running.',
      confirmLabel: 'Stop server',
      action: async () => {
        await api.shutdown();
        session.token = null;
        document.getElementById('app').innerHTML =
          '<div class="auth-wrap"><div class="card auth-card"><h2>Server stopped</h2><p class="muted" style="margin-top:8px">Run <code>cloudify serve</code> to start it again.</p></div></div>';
      },
    }));
  }

  draw();
  loadUsers();
  return store.subscribe(draw);
}
