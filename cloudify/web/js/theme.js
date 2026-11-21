// Apply the saved theme before first paint (dark by default; the theme button remembers the choice).
// A separate file rather than an inline script, so the page's Content-Security-Policy can allow
// scripts from this origin only.
document.documentElement.dataset.theme = localStorage.getItem('cloudify.theme') || 'dark';
