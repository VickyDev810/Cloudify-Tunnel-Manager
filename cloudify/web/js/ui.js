// Shared UI helpers: safe HTML templating, icons, toasts, dialogs.

// ---------------------------------------------------------------- templating

class SafeHtml {
  constructor(value) { this.value = value; }
  toString() { return this.value; }
}

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (c) => ESCAPES[c]);

function render(value) {
  if (value === null || value === undefined || value === false) return '';
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(render).join('');
  return escapeHtml(value);
}

/** Tagged template: every ${value} is HTML-escaped unless it is itself html`` / raw(). */
export function html(strings, ...values) {
  return new SafeHtml(strings.reduce((out, str, i) => out + str + (i < values.length ? render(values[i]) : ''), ''));
}
export const raw = (value) => new SafeHtml(value);

export function mount(element, content) {
  element.innerHTML = render(content);
  return element;
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// --------------------------------------------------------------------- icons

const PATHS = {
  cloud: '<path d="M7 18h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6.2 9.3 4.5 4.5 0 0 0 7 18z"/>',
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9v11h14V9"/>',
  tunnel: '<path d="M4 20V11a8 8 0 0 1 16 0v9"/><path d="M8 20v-7a4 4 0 0 1 8 0v7"/><path d="M2 20h20"/>',
  // the Cloudify mark: a tunnel seen head-on (two open rings) with the light at its end
  mark: '<path class="ring-outer" d="M17.3 6.7A7.5 7.5 0 1 0 17.3 17.3"/><path class="ring-inner" d="M15.18 8.82A4.5 4.5 0 1 0 15.18 15.18"/>'
    + '<circle class="glow" cx="12" cy="12" r="3.2"/><circle class="light" cx="12" cy="12" r="1.5"/>',
  route: '<circle cx="6" cy="19" r="2"/><circle cx="18" cy="5" r="2"/><path d="M8 19h7a3.5 3.5 0 0 0 0-7H9a3.5 3.5 0 0 1 0-7h7"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6M10 14 21 3"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  power: '<path d="M18.4 6.6a9 9 0 1 1-12.8 0"/><path d="M12 2v10"/>',
  bolt: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
  key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3M17 6l3 3"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  server: '<rect x="2" y="3" width="20" height="8" rx="2"/><rect x="2" y="13" width="20" height="8" rx="2"/><path d="M6 7h.01M6 17h.01"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5M12 3v12"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
};

export const icon = (name, cls = '') =>
  raw(`<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name] || ''}</svg>`);

// -------------------------------------------------------------------- toasts

export function toast(message, type = 'success', timeout = 4500) {
  const kind = { success: 'check', error: 'alert', info: 'info' }[type] || 'info';
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.setAttribute('role', type === 'error' ? 'alert' : 'status');
  mount(el, html`${icon(kind)}<div class="msg">${message}</div>`);
  const stack = document.getElementById('toasts');
  stack.appendChild(el);
  while (stack.children.length > 3) stack.firstElementChild.remove();  // keep the header usable
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, type === 'error' ? timeout + 3000 : timeout);
}

// ------------------------------------------------------------------- dialogs

/**
 * Open a modal containing a form.
 *   onSubmit(values, dialog) -> may throw to show an error; resolve to close.
 */
export function formDialog({ title, body, submitLabel = 'Save', danger = false, onSubmit, onOpen }) {
  const dialog = document.createElement('dialog');
  dialog.className = 'modal';
  mount(dialog, html`
    <form method="dialog" novalidate>
      <div class="modal-header">
        <h2>${title}</h2>
        <button type="button" class="btn btn-ghost btn-icon" data-close aria-label="Close">${icon('x')}</button>
      </div>
      <div class="modal-body">
        <div class="form-error hidden" data-error></div>
        ${body}
      </div>
      <div class="modal-footer">
        <button type="button" class="btn" data-close>Cancel</button>
        <button type="submit" class="btn ${danger ? 'btn-danger-solid' : 'btn-primary'}">${submitLabel}</button>
      </div>
    </form>`);
  document.body.appendChild(dialog);

  const form = $('form', dialog);
  const error = $('[data-error]', dialog);
  const submit = $('button[type=submit]', dialog);
  const close = () => { dialog.close(); dialog.remove(); };
  $$('[data-close]', dialog).forEach((b) => b.addEventListener('click', close));
  dialog.addEventListener('cancel', (e) => { e.preventDefault(); if (!submit.classList.contains('loading')) close(); });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (submit.classList.contains('loading')) return;  // Enter pressed again while saving
    if (!form.reportValidity()) return;
    error.classList.add('hidden');
    submit.classList.add('loading');
    try {
      await onSubmit(Object.fromEntries(new FormData(form)), dialog);
      close();
    } catch (err) {
      error.textContent = err.message;
      error.classList.remove('hidden');
    } finally {
      submit.classList.remove('loading');
    }
  });

  dialog.showModal();
  onOpen?.(dialog);
  $('input:not([type=hidden]):not([type=checkbox]), select', dialog)?.focus();
  return dialog;
}

/** Confirmation dialog; `action` runs while the button shows a spinner. */
export function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = true, action }) {
  return formDialog({ title, body: html`<p>${message}</p>`, submitLabel: confirmLabel, danger, onSubmit: action });
}

// ------------------------------------------------------------------- buttons

/** Run an async action while a button shows a spinner; errors become toasts. */
export async function withButton(button, fn) {
  button?.classList.add('loading');
  try {
    return await fn();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    button?.classList.remove('loading');
  }
}

// ------------------------------------------------------------------- formats

export function timeAgo(iso) {
  if (!iso) return '';
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(seconds)) return '';
  const units = [[31536000, 'year'], [2592000, 'month'], [86400, 'day'], [3600, 'hour'], [60, 'minute']];
  for (const [size, name] of units) {
    const n = Math.floor(seconds / size);
    if (n >= 1) return `${n} ${name}${n > 1 ? 's' : ''} ago`;
  }
  return 'just now';
}

export function statusPill(status) {
  const running = (status || '').startsWith('running');
  const detail = (status || '').match(/\((.+)\)/)?.[1];
  return html`<span class="pill ${running ? 'pill-running' : 'pill-stopped'}" title="${status || ''}">
    ${running ? 'Running' : 'Stopped'}${detail ? html`<span class="subtle" style="font-weight:500">· ${detail}</span>` : ''}
  </span>`;
}

/** Domain (zone) a hostname belongs to, among the connected domains. */
export function zoneOf(hostname, domains) {
  const host = (hostname || '').replace(/^\*\./, '').toLowerCase();
  return domains.filter((d) => d && (host === d || host.endsWith(`.${d}`)))
    .sort((a, b) => b.length - a.length)[0] || null;
}

/** Save a JS object as a downloaded JSON file. */
export function downloadJson(filename, data) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
