// Small client for the Cloudify JSON API.

const TOKEN_KEY = 'cloudify.token';

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export const session = {
  get token() { return localStorage.getItem(TOKEN_KEY); },
  set token(value) { value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY); },
  onExpired: () => {},
};

function errorMessage(data, status) {
  if (!data) return `Request failed (${status})`;
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map(d => d.msg).join('\n'); // validation errors
  return data.error || `Request failed (${status})`;
}

async function request(method, path, body, { form = false, auth = true } = {}) {
  const headers = {};
  let payload;
  if (form) {
    headers['Content-Type'] = 'application/x-www-form-urlencoded';
    payload = new URLSearchParams(body);
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  if (auth && session.token) headers.Authorization = `Bearer ${session.token}`;

  let response;
  try {
    response = await fetch(path, { method, headers, body: payload });
  } catch {
    throw new ApiError('Cannot reach the Cloudify server. Is it still running?', 0);
  }
  const data = await response.json().catch(() => null);
  if (response.status === 401 && auth && session.token) {
    session.token = null;
    session.onExpired();
  }
  if (!response.ok) throw new ApiError(errorMessage(data, response.status), response.status);
  return data;
}

const get = (path) => request('GET', path);
const post = (path, body = {}) => request('POST', path, body);

export const api = {
  // setup & accounts
  setupCheck: () => request('GET', '/setup/check', undefined, { auth: false }),
  register: (username, password, setupCode) =>
    post('/auth/register', setupCode ? { username, password, setup_code: setupCode } : { username, password }),
  login: (username, password) => request('POST', '/auth/login', { username, password }, { form: true, auth: false }),
  logout: () => post('/auth/logout'),
  me: () => get('/auth/me'),
  changePassword: (current_password, new_password) => post('/auth/change-password', { current_password, new_password }),
  users: () => get('/auth/users'),
  deleteUser: (username) => post('/auth/users/delete', { username }),

  // Cloudflare login
  startCloudflareLogin: () => post('/initial_setup'),
  loginStatus: () => get('/status'),

  // tunnels & routes
  tunnels: () => get('/tunnels'),
  createTunnel: (tunnel_name, autostart, account_id = null) =>
    post('/tunnel/create', { tunnel_name, auto_start_on_create: autostart, confirm_autostart: autostart, account_id }),
  deleteTunnel: (tunnel_name) => post('/tunnel/delete', { tunnel_name, force: true }),
  adoptTunnel: (tunnel_name, account_id) =>
    post('/tunnel/adopt', account_id ? { tunnel_name, account_id } : { tunnel_name }),
  startTunnel: (tunnel_name) => post('/tunnel/start', { tunnel_name }),
  stopTunnel: (tunnel_name) => post('/tunnel/stop', { tunnel_name }),
  enableAutostart: (tunnel_name) => post('/tunnel/autostart/setup', { tunnel_name }),
  disableAutostart: (tunnel_name) => post('/tunnel/autostart/remove', { tunnel_name }),
  addRoute: (tunnel_name, domain, end_user_url, localhost_port) =>
    post('/tunnel/route/add', { tunnel_name, domain, end_user_url, localhost_port }),
  removeRoute: (tunnel_name, domain) => post('/tunnel/route/remove', { tunnel_name, domain }),
  editRoute: (tunnel_name, domain, new_domain, end_user_url, localhost_port) =>
    post('/tunnel/route/edit', { tunnel_name, domain, new_domain, end_user_url, localhost_port }),
  checkDns: (tunnel_name) => get(`/tunnel/dns?tunnel_name=${encodeURIComponent(tunnel_name)}`),
  repairDns: (tunnel_name, domain, overwrite = false) => post('/tunnel/route/dns', { tunnel_name, domain, overwrite }),
  connectors: (tunnel_name) => get(`/tunnel/connectors?tunnel_name=${encodeURIComponent(tunnel_name)}`),

  // Cloudflare accounts & domains
  accounts: () => get('/accounts'),
  connectDomain: (domain) => post('/accounts/connect', { domain }),
  setConnectionDomain: (id, domain) => post('/accounts/domain', { id, domain }),
  removeConnection: (id, force = false) => post('/accounts/remove', { id, force }),

  // replicas
  replicaStatus: () => get('/replicas/status'),
  setConnectionToken: (id, token) => post('/accounts/token', { id, token }),
  replicaPlan: (tunnels, hostnames) => post('/replicas/plan', { tunnels, hostnames }),
  replicaSplit: (tunnel_name, hostnames, new_name) => post('/replicas/split', { tunnel_name, hostnames, new_name }),
  replicaExport: (tunnels, passphrase) => post('/replicas/export', { tunnels, passphrase }),
  replicaImport: (bundle, passphrase, autostart) => post('/replicas/import', { bundle, passphrase, autostart }),

  // system
  systemInfo: () => get('/system/info'),
  updateCloudflared: () => post('/system/cloudflared/update'),
  shutdown: () => post('/shutdown'),
};
