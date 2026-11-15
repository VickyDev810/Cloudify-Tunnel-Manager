// Dialogs used from several pages.
import { api } from '../api.js';
import { $, formDialog, html, mount, toast, zoneOf } from '../ui.js';

/** Will removing `hostname` delete its DNS record automatically? */
export function dnsCleanupFor(store, hostname) {
  const zone = zoneOf(hostname, store.domains());
  return Boolean(zone && store.connections.find((c) => c.domain === zone)?.dns_cleanup);
}

/** Sentence for a confirm dialog about what happens to the DNS records. */
export function dnsWarning(store, hostnames) {
  const automatic = hostnames.filter((h) => dnsCleanupFor(store, h));
  const manual = hostnames.filter((h) => !dnsCleanupFor(store, h));
  return [
    automatic.length ? `The DNS record${automatic.length > 1 ? 's' : ''} for ${automatic.join(', ')} will be deleted too.` : '',
    manual.length ? `Delete the DNS record${manual.length > 1 ? 's' : ''} for ${manual.join(', ')} in the Cloudflare dashboard afterwards `
      + '(or turn on automatic DNS cleanup on the Domains page).' : '',
  ].filter(Boolean).join(' ');
}

/** Warning when the tunnel also runs on other machines (they keep their copy of the routes). */
export function replicaWarning(tunnel) {
  if (!tunnel?.exported || tunnel.replica) return '';
  return ' This tunnel also runs on other machines: they keep serving the old routes until you export it again'
    + ' (Redundancy → Share) and import the new file there.';
}

/** Toast text summarising what happened to DNS records (from the API's "dns" notes). */
export function dnsSummary(notes = []) {
  return notes.map((n) => ({
    deleted: `DNS record for ${n.hostname} deleted`,
    missing: `No DNS record found for ${n.hostname}`,
    'other-target': `DNS record for ${n.hostname} points elsewhere, left unchanged`,
  }[n.outcome] || `Delete the DNS record for ${n.hostname} in the Cloudflare dashboard`)).join('\n');
}

/** "example.com, shop.net" for an account, falling back to a shortened ID. */
export function accountLabel(store, accountId) {
  const domains = store.domains(accountId);
  return domains.length ? domains.join(', ') : `Account ${String(accountId || '').slice(0, 8) || 'unknown'}`;
}

export function createTunnelDialog(store) {
  const accounts = store.accounts.length ? store.accounts : [];
  formDialog({
    title: 'Create tunnel',
    submitLabel: 'Create tunnel',
    body: html`
      <div class="field">
        <label for="tn">Tunnel name</label>
        <input class="input" id="tn" name="name" required maxlength="63"
               pattern="[A-Za-z0-9][A-Za-z0-9_.\\-]*" placeholder="home-server">
        <span class="hint">Letters, numbers, "-", "_" or ".". One tunnel can serve many domains.</span>
      </div>
      ${accounts.length > 1 ? html`
        <div class="field">
          <label for="ta">Cloudflare account</label>
          <select class="input" id="ta" name="account">
            ${accounts.map((a) => html`<option value="${a.account_id}">${accountLabel(store, a.account_id)}</option>`)}
          </select>
          <span class="hint">The tunnel can serve hostnames on any domain of this account.</span>
        </div>` : ''}
      <label class="switch"><input type="checkbox" name="autostart" checked><span class="track"></span>
        Start automatically when this machine boots</label>`,
    async onSubmit(values) {
      await api.createTunnel(values.name.trim(), values.autostart === 'on', values.account || null);
      toast(`Tunnel "${values.name.trim()}" created`);
      await store.refresh();
    },
  });
}

/**
 * Hostname picker: subdomain + one of the tunnel's connected domains.
 * Falls back to a free-text hostname when no domain names are known.
 */
function hostnameFields(store, tunnel, hostname = '') {
  const domains = store.domains(tunnel?.account_id);
  // A hostname on a domain that isn't connected (adopted tunnels, disconnected domains) is
  // edited as plain text: picking a domain from the list would silently move it elsewhere.
  if (!domains.length || (hostname && !zoneOf(hostname, domains))) {
    return html`<div class="field">
      <label for="rd">Public hostname</label>
      <div class="input-group"><span class="addon">https://</span>
        <input class="input" id="rd" name="hostname" required placeholder="app.example.com" value="${hostname}"></div>
      <span class="hint">Must be on a domain in your Cloudflare account. A DNS record is created for you.</span>
    </div>`;
  }
  const zone = zoneOf(hostname, domains) || domains[0];
  const sub = hostname && zoneOf(hostname, domains) ? hostname.slice(0, -zone.length).replace(/\.$/, '') : '';
  return html`<div class="field">
    <label for="rs">Public hostname</label>
    <div class="input-group">
      <span class="addon">https://</span>
      <input class="input" id="rs" name="sub" placeholder="app" value="${sub}" aria-label="Subdomain" style="min-width:0">
      <span class="addon" style="border-left:0;border-right:0">.</span>
      <select class="input" name="zone" aria-label="Domain" style="flex:0 1 auto;width:auto">
        ${domains.map((d) => html`<option value="${d}" ${d === zone ? 'selected' : ''}>${d}</option>`)}
      </select>
    </div>
    <span class="hint">Leave the subdomain empty for the domain itself, or use * for a wildcard. A DNS record is created for you.</span>
  </div>`;
}

function hostnameFrom(values) {
  if (values.hostname !== undefined) return values.hostname.trim();
  const sub = (values.sub || '').trim().replace(/\.$/, '');
  return sub ? `${sub}.${values.zone}` : values.zone;
}

function serviceFields(host = 'localhost', port = '') {
  return html`<div class="row">
    <div class="field">
      <label for="rh">Service host</label>
      <div class="input-group"><span class="addon">http://</span>
        <input class="input" id="rh" name="host" required value="${host}"></div>
      <span class="hint">localhost, an IP, or a Docker container name</span>
    </div>
    <div class="field narrow">
      <label for="rp">Port</label>
      <input class="input" id="rp" name="port" type="number" min="1" max="65535" required placeholder="3000" value="${port}">
    </div>
  </div>`;
}

export function addRouteDialog(store, tunnelName = null) {
  const tunnels = store.tunnels.filter((t) => !t.replica);
  if (!tunnels.length) {
    toast(store.tunnels.length ? 'Replica tunnels are edited on their primary machine' : 'Create a tunnel first', 'info');
    return;
  }
  const initial = tunnels.find((t) => t.name === tunnelName) || tunnels[0];
  formDialog({
    title: 'Add route',
    submitLabel: 'Add route',
    body: html`
      <div class="field">
        <label for="rt">Tunnel</label>
        <select class="input" id="rt" name="tunnel" required>
          ${tunnels.map((t) => html`<option value="${t.name}" ${t.name === initial.name ? 'selected' : ''}>${t.name}${
            store.accounts.length > 1 ? ` · ${accountLabel(store, t.account_id)}` : ''}</option>`)}
        </select>
      </div>
      <div id="hostFields">${hostnameFields(store, initial)}</div>
      ${serviceFields()}`,
    onOpen(dialog) {
      // The domain list depends on the tunnel's Cloudflare account
      $('#rt', dialog).addEventListener('change', (e) => {
        mount($('#hostFields', dialog), hostnameFields(store, tunnels.find((t) => t.name === e.target.value)));
      });
    },
    async onSubmit(values) {
      const hostname = hostnameFrom(values);
      await api.addRoute(values.tunnel, hostname, values.host.trim(), Number(values.port));
      toast(`Route added: ${hostname}`);
      await store.refresh();
    },
  });
}

export function editRouteDialog(store, tunnelName, route) {
  const tunnel = store.tunnels.find((t) => t.name === tunnelName);
  const service = (route.service || '').replace(/^https?:\/\//, '');
  const cut = service.lastIndexOf(':');
  const [host, port] = cut > 0 ? [service.slice(0, cut), service.slice(cut + 1)] : [service, ''];
  formDialog({
    title: 'Edit route',
    submitLabel: 'Save changes',
    body: html`
      <p class="subtle" style="margin-bottom:14px">Tunnel <strong>${tunnelName}</strong></p>
      ${hostnameFields(store, tunnel, route.domain)}
      ${serviceFields(host, port)}`,
    async onSubmit(values) {
      const hostname = hostnameFrom(values);
      // Unchanged service host and port are not sent, so the server keeps the service exactly
      // as it is (https://, a path, ...) when only the hostname changes.
      const sameService = values.host.trim() === host && String(values.port) === String(port);
      const result = await api.editRoute(tunnelName, route.domain, hostname,
        sameService ? null : values.host.trim(), sameService ? null : Number(values.port));
      toast(hostname !== route.domain
        ? [`Route moved to ${hostname}`, dnsSummary(result.dns)].filter(Boolean).join('\n')
        : 'Route updated');
      await store.refresh();
    },
  });
}
