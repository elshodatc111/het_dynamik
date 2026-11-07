/* Dinamika — umumiy skriptlar */
function toggleTheme() {
  var cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  var next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  try { localStorage.setItem('theme', next); } catch (e) {}
  document.dispatchEvent(new CustomEvent('themechange', { detail: next }));
  syncThemeIcons();
}
function syncThemeIcons() {
  var cur = document.documentElement.getAttribute('data-theme');
  document.querySelectorAll('.theme-toggle').forEach(function (el) {
    el.style.display = el.dataset.show === cur ? 'inline-flex' : 'none';
  });
}
document.addEventListener('DOMContentLoaded', syncThemeIcons);

/* Raqamlarni chiroyli ko‘rsatish (grafiklar uchun) */
window.fmtNum = function (v, kind) {
  if (v === null || v === undefined) return '—';
  var n = Number(v);
  var s = n.toLocaleString('en-US', { maximumFractionDigits: kind === 'pct' ? 1 : 0 }).replace(/,/g, ' ');
  return kind === 'pct' ? s + '%' : s;
};
window.chartTheme = function () {
  var dark = document.documentElement.getAttribute('data-theme') === 'dark';
  return {
    mode: dark ? 'dark' : 'light',
    fg: dark ? '#9aa5c4' : '#64748b',
    grid: dark ? '#222b49' : '#e8ebf2'
  };
};

/* Drag & drop yuklash */
window.dropzone = function () {
  return {
    over: false, files: [],
    pick(list) {
      var dt = new DataTransfer();
      Array.from(list).forEach(function (f) { if (/\.pdf$/i.test(f.name)) dt.items.add(f); });
      this.$refs.input.files = dt.files;
      this.files = Array.from(dt.files);
    },
    size(f) { return (f.size / 1048576).toFixed(1) + ' MB'; }
  };
};
