import { api, session } from '../api.js';
import { $, html, icon, mount } from '../ui.js';

export function renderLogin(root, { message, onLogin }) {
  mount(root, html`
    <div class="auth-wrap">
      <div class="card auth-card">
        <div class="brand"><span class="brand-mark">${icon('mark')}</span>Cloudify</div>
        <p class="lead">Sign in to manage your tunnels</p>
        <form id="loginForm">
          <div class="form-error ${message ? '' : 'hidden'}" id="loginError">${message || ''}</div>
          <div class="field">
            <label for="username">Username</label>
            <input class="input" id="username" name="username" autocomplete="username" required>
          </div>
          <div class="field">
            <label for="password">Password</label>
            <input class="input" id="password" name="password" type="password" autocomplete="current-password" required>
          </div>
          <button class="btn btn-primary btn-block" type="submit">Sign in</button>
        </form>
        <p class="subtle" style="text-align:center;margin-top:18px">
          Need an account? Ask an existing user to add you under Settings → Users.
        </p>
      </div>
    </div>`);

  const form = $('#loginForm');
  $('#username').focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const button = $('button[type=submit]', form);
    const error = $('#loginError');
    button.classList.add('loading');
    error.classList.add('hidden');
    try {
      const { access_token } = await api.login(form.username.value.trim(), form.password.value);
      session.token = access_token;
      await onLogin();
    } catch (err) {
      error.textContent = err.message;
      error.classList.remove('hidden');
      form.password.select();
    } finally {
      button.classList.remove('loading');
    }
  });
}
