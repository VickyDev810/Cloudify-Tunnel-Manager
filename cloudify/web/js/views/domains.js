import { api } from '../api.js';
import { $$, confirmDialog, formDialog, html, icon, mount, timeAgo, toast } from '../ui.js';
import { connectCloudflareDialog } from './overview.js';

function setDomainDialog(store, connection) {
  formDialog({
    title: 'Set domain name',
    submitLabel: 'Save',
    body: html`
      <p style="margin-bottom:14px">This connection was made by an earlier version, so its domain name isn't known.
        Enter the domain you authorized for it.</p>
      <div class="field"><label for="dn">Domain</label>
        <input class="input" id="dn" name="domain" required placeholder="example.com"></div>`,
    async onSubmit(values) {
      await api.setConnectionDomain(connection.id, values.domain.trim());
      toast('Domain saved');
      await store.refreshAccounts();
    },
  });
}

function tokenDialog(store, connection) {
  formDialog({
    title: `Automatic DNS cleanup · ${connection.domain}`,
    submitLabel: 'Save token',
    body: html`
      <p style="margin-bottom:12px">cloudflared can create DNS records but not delete them. With a Cloudflare API token,
        Cloudify deletes a hostname's record when you remove its route or tunnel. Only records that point at that
        tunnel are touched.</p>
      <ol class="steps-list token-steps" style="margin:0 0 16px">
        <li>Open <a href="https://dash.cloudflare.com/profile/api-tokens" target="_blank" rel="noopener">dash.cloudflare.com → My Profile → API Tokens</a>
          and click <strong>Create Token</strong>.</li>
        <li>Next to <strong>Edit zone DNS</strong>, click <strong>Use template</strong>.</li>
        <li>Permissions: keep <span class="mono">Zone · DNS · Edit</span> (nothing else is needed).</li>
        <li>Zone Resources: <span class="mono">Include · Specific zone · ${connection.domain}</span>.</li>
        <li>Optional: limit Client IP Address Filtering to this server's IP and set a TTL.</li>
        <li><strong>Continue to summary</strong> → <strong>Create Token</strong>, then copy it. Cloudflare shows it only once.</li>
        <li>Paste it below. Cloudify checks it with Cloudflare before saving.</li>
      </ol>
      <div class="field"><label for="apiToken">API token</label>
        <input class="input mono" id="apiToken" name="token" type="password" required autocomplete="off"></div>
      ${connection.dns_cleanup ? html`<button type="button" class="btn btn-sm btn-ghost btn-danger" data-remove-token>Turn off and forget the token</button>` : ''}`,
    onOpen(dialog) {
      dialog.querySelector('[data-remove-token]')?.addEventListener('click', async () => {
        await api.setConnectionToken(connection.id, '');
        toast(`Automatic DNS cleanup turned off for ${connection.domain}`);
        dialog.close(); dialog.remove();
        store.refreshAccounts();
      });
    },
    async onSubmit(values) {
      await api.setConnectionToken(connection.id, values.token.trim());
      toast(`Automatic DNS cleanup enabled for ${connection.domain}`);
      await store.refreshAccounts();
    },
  });
}

function removeDialog(store, connection) {
  const remove = async (force) => {
    await api.removeConnection(connection.id, force);
    toast(`Disconnected ${connection.domain || 'domain'}`);
    await Promise.all([store.refreshAccounts(), store.refreshInfo()]);
  };
  confirmDialog({
    title: `Disconnect ${connection.domain || 'this domain'}?`,
    message: 'Cloudify forgets this domain\'s certificate. Tunnels and DNS records stay as they are, but you can\'t add routes on this domain until you connect it again.',
    confirmLabel: 'Disconnect',
    action: async () => {
      try {
        await remove(false);
      } catch (err) {
        if (err.status !== 409) throw err;
        // Last domain of an account that still has tunnels: confirm again
        setTimeout(() => confirmDialog({
          title: 'Disconnect the whole account?',
          message: err.message,
          confirmLabel: 'Disconnect anyway',
          action: () => remove(true),
        }));
      }
    },
  });
}

export default function domains(page, { store, openMenu }) {
  const draw = () => {
    const accounts = store.accounts;
    mount(page, html`
      <div class="page-header">
        <div>
          <button class="btn btn-ghost btn-icon menu-btn" data-menu aria-label="Menu">${icon('menu')}</button>
          <div class="eyebrow">Network</div>
          <h1>Domains</h1>
          <p class="muted">Domains you've authorized, grouped by Cloudflare account. Tunnels can route any domain of their account.</p>
        </div>
        <button class="btn btn-primary" data-connect>${icon('plus')} Connect domain</button>
      </div>

      ${!accounts.length ? html`
        <div class="card empty"><div class="icon-wrap">${icon('globe')}</div><h3>No Cloudflare domain connected</h3>
          <p>Connect a domain to create tunnels and DNS records on it. You can connect domains from several accounts.</p>
          <button class="btn btn-primary" data-connect>${icon('link')} Connect domain</button></div>`
      : accounts.map((account, index) => {
        const connections = store.connections.filter((c) => c.account_id === account.account_id);
        return html`
          <div class="card section" data-account="${account.account_id}">
            <div class="card-header">
              <div><h2>Cloudflare account ${accounts.length > 1 ? index + 1 : ''}</h2>
                <div class="subtle mono">${account.account_id || 'unknown account'}</div></div>
              <span class="subtle">${account.tunnels.length} tunnel${account.tunnels.length === 1 ? '' : 's'}${
                account.tunnels.length ? `: ${account.tunnels.join(', ')}` : ''}</span>
            </div>
            ${connections.map((c) => html`
              <div class="list-item" data-connection="${c.id}">
                <div class="icon-wrap-sm">${icon('globe')}</div>
                <div class="grow">
                  <strong>${c.domain || 'Unknown domain'}</strong>
                  <div class="subtle">${c.routes} route${c.routes === 1 ? '' : 's'} · connected ${timeAgo(c.added_at)}</div>
                </div>
                ${c.domain ? html`<button class="btn btn-sm" data-dns-token title="Delete DNS records automatically">
                  <span class="dot ${c.dns_cleanup ? 'ok' : ''}" style="animation:none"></span>
                  DNS cleanup ${c.dns_cleanup ? 'on' : 'off'}</button>` : ''}
                ${c.domain ? '' : html`<button class="btn btn-sm" data-set-domain>Set domain</button>`}
                <button class="btn btn-sm btn-ghost btn-danger" data-remove>Disconnect</button>
              </div>`)}
          </div>`;
      })}

      <div class="card section"><div class="card-body">
        <h3 style="margin-bottom:6px">How domains work</h3>
        <p class="muted">Each Cloudflare login authorizes one domain. To route <span class="mono">app.shop.net</span>
          through a tunnel, connect <span class="mono">shop.net</span> here first. Domains in another Cloudflare account
          get their own account section, and new tunnels ask which account to use.</p>
      </div></div>`);

    $$('[data-menu]', page).forEach((b) => b.addEventListener('click', openMenu));
    $$('[data-connect]', page).forEach((b) => b.addEventListener('click', () =>
      connectCloudflareDialog(store, { title: 'Connect a domain', requireDomain: store.connections.length > 0 })));
    const connectionOf = (el) => store.connections.find((c) => c.id === el.closest('[data-connection]').dataset.connection);
    $$('[data-set-domain]', page).forEach((b) => b.addEventListener('click', () => setDomainDialog(store, connectionOf(b))));
    $$('[data-remove]', page).forEach((b) => b.addEventListener('click', () => removeDialog(store, connectionOf(b))));
    $$('[data-dns-token]', page).forEach((b) => b.addEventListener('click', () => tokenDialog(store, connectionOf(b))));
  };
  draw();
  store.refreshAccounts().catch((err) => toast(err.message, 'error'));
  return store.subscribe(draw);
}
