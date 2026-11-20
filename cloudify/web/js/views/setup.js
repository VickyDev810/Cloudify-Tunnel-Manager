import { api, session } from '../api.js';
import { $, html, icon, mount } from '../ui.js';

/**
 * "Connect Cloudflare" flow: runs `cloudflared tunnel login` on the server and
 * shows the authorization link. Reused by the setup wizard and Settings.
 * Returns a cleanup function that stops polling.
 */
export function cloudflareConnect(container, { onDone, onSkip, skipLabel = 'Skip for now', requireDomain = false }) {
  let timer = null;
  let domain = '';
  const stop = () => clearInterval(timer);

  const renderStart = (error) => mount(container, html`
    ${error ? html`<div class="form-error">${error}</div>` : ''}
    <p class="muted">Cloudify needs permission to create tunnels and DNS records for your domain.
      This runs <code>cloudflared tunnel login</code> and gives you a link to authorize it.
      Each login authorizes one domain; connect more later on the Domains page, even from other Cloudflare accounts.</p>
    <div class="field" style="margin-top:16px">
      <label for="cfDomain">Domain to authorize${requireDomain ? '' : html` <span class="subtle">(recommended)</span>`}</label>
      <input class="input" id="cfDomain" placeholder="example.com" value="${domain}" autocomplete="off" ${requireDomain ? 'required' : ''}>
      <span class="hint">Pick this same domain on the Cloudflare page.</span>
    </div>
    <div class="row" style="margin-top:8px">
      ${onSkip ? html`<button class="btn" data-skip>${skipLabel}</button>` : ''}
      <button class="btn btn-primary" data-start>${icon('link')} Connect Cloudflare</button>
    </div>`);

  const renderWaiting = (url) => mount(container, html`
    ${url ? html`
      <p class="muted">Open this link, choose <strong>${domain || 'your domain'}</strong>, and click <strong>Authorize</strong>:</p>
      <div class="auth-link">${url}</div>
      <a class="btn btn-primary btn-block" href="${url}" target="_blank" rel="noopener">${icon('external')} Open Cloudflare</a>
      <div class="wait"><span class="spinner"></span>Waiting for you to authorize in Cloudflare…</div>`
    : html`<div class="wait"><span class="spinner"></span>Starting cloudflared…</div>`}
    ${onSkip ? html`<button class="btn btn-ghost btn-block" data-skip>${skipLabel}</button>` : ''}`);

  const bind = () => {
    $('[data-start]', container)?.addEventListener('click', start);
    $('[data-skip]', container)?.addEventListener('click', () => { stop(); onSkip(); });
  };

  async function poll() {
    if (!container.isConnected) { stop(); return; }  // dialog/page was closed
    try {
      const s = await api.loginStatus();
      if (s.status === 'completed') { stop(); onDone(); return; }
      if (s.status === 'error' || s.status?.startsWith('error_exit') || s.status === 'manually_stopped') {
        stop(); renderStart(s.message || 'Cloudflare login failed. Please try again.'); bind(); return;
      }
      if (s.url && !$('.auth-link', container)) { renderWaiting(s.url); bind(); }
    } catch { /* keep polling */ }
  }

  async function start() {
    const input = $('#cfDomain', container);
    domain = (input?.value || '').trim().toLowerCase();
    if (requireDomain && !domain) { input.reportValidity(); return; }
    renderWaiting(null); bind();
    try {
      await (domain ? api.connectDomain(domain) : api.startCloudflareLogin());
      timer = setInterval(poll, 1500);
      poll();
    } catch (err) {
      renderStart(err.message); bind();
    }
  }

  renderStart(); bind();
  return stop;
}

export function renderSetup(root, { setup, onFinish }) {
  const steps = ['Create account', 'Connect Cloudflare', 'Done'];

  const frame = (step, content) => mount(root, html`
    <div class="auth-wrap">
      <div class="card auth-card wide">
        <div class="brand"><span class="brand-mark">${icon('mark')}</span>Cloudify</div>
        <p class="lead">Let's get your tunnel manager ready</p>
        <div class="steps">${steps.map((label, i) => html`
          <div class="step ${i === step ? 'active' : i < step ? 'done' : ''}"><div class="bar"></div>${i + 1}. ${label}</div>`)}
        </div>
        <div id="stepBody">${content}</div>
      </div>
    </div>`);

  function stepAccount() {
    frame(0, html`
      <form id="adminForm">
        <div class="form-error hidden" id="adminError"></div>
        <div class="field">
          <label for="scode">Setup code</label>
          <input class="input" id="scode" name="setupCode" autocomplete="off" spellcheck="false" required
                 maxlength="64" placeholder="XXXXX-XXXXX">
          <span class="hint">Shown in the terminal where <code>cloudify serve</code> runs
            (Docker: <code>docker logs cloudify</code>). It proves you run this server.</span>
        </div>
        <div class="field">
          <label for="su">Admin username</label>
          <input class="input" id="su" name="username" autocomplete="username" required maxlength="64">
        </div>
        <div class="row">
          <div class="field">
            <label for="sp">Password</label>
            <input class="input" id="sp" name="password" type="password" autocomplete="new-password" required minlength="6">
          </div>
          <div class="field">
            <label for="sp2">Confirm password</label>
            <input class="input" id="sp2" name="confirm" type="password" autocomplete="new-password" required minlength="6">
          </div>
        </div>
        <p class="subtle" style="margin:-4px 0 18px">This account protects the dashboard. You can add more users later.</p>
        <button class="btn btn-primary btn-block" type="submit">Create account</button>
      </form>`);
    $('#scode').focus();
    const form = $('#adminForm');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const error = $('#adminError');
      const button = $('button[type=submit]', form);
      error.classList.add('hidden');
      if (form.password.value !== form.confirm.value) {
        error.textContent = 'Passwords do not match';
        error.classList.remove('hidden');
        return;
      }
      button.classList.add('loading');
      try {
        const username = form.username.value.trim();
        await api.register(username, form.password.value, form.setupCode.value.trim());
        session.token = (await api.login(username, form.password.value)).access_token;
        setup.cloudflare_logged_in ? stepDone() : stepCloudflare();
      } catch (err) {
        error.textContent = err.message;
        error.classList.remove('hidden');
      } finally {
        button.classList.remove('loading');
      }
    });
  }

  function stepCloudflare() {
    frame(1, html`<div id="cf"></div>`);
    if (!setup.cloudflared_installed) {
      mount($('#cf'), html`
        <div class="banner banner-warning">${icon('alert')}<div class="grow">
          <strong>cloudflared is not installed on this machine.</strong><br>
          <span class="muted">Install it, then connect Cloudflare from Settings.</span></div></div>
        <button class="btn btn-primary btn-block" id="cont">Continue</button>`);
      $('#cont').addEventListener('click', stepDone);
      return;
    }
    cloudflareConnect($('#cf'), { onDone: stepDone, onSkip: stepDone });
  }

  function stepDone() {
    frame(2, html`
      <div style="text-align:center">
        <div class="success-mark">${icon('check')}</div>
        <h2>You're all set</h2>
        <p class="muted" style="margin:6px 0 22px">Create your first tunnel and point a domain at a local service.</p>
        <button class="btn btn-primary btn-block" id="go">Open dashboard</button>
      </div>`);
    $('#go').addEventListener('click', () => { location.hash = '#/tunnels'; onFinish(); });
  }

  stepAccount();
}
