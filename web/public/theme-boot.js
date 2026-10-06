// Apply the theme before first paint (no flash). A file, not inline, so the CSP can forbid inline scripts.
(function () {
  try {
    var p = localStorage.getItem('tutor.palette') || 'heather';
    var m = localStorage.getItem('tutor.mode') || 'auto';
    var dark = m === 'dark' || (m === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.palette = p;
    if (dark) document.documentElement.dataset.mode = 'dark';
  } catch (e) {}
})();
