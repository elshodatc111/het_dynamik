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
window.dropzone = function (exts) {
  // exts: ruxsat etilgan kengaytmalar, masalan 'xlsx|xlsm' (standart — 'pdf')
  var re = new RegExp('\\.(' + (exts || 'pdf') + ')$', 'i');
  return {
    over: false, files: [], skipped: 0,
    pick(list) {
      var dt = new DataTransfer(), all = Array.from(list);
      all.forEach(function (f) { if (re.test(f.name)) dt.items.add(f); });
      this.skipped = all.length - dt.files.length;
      this.$refs.input.files = dt.files;
      this.files = Array.from(dt.files);
    },
    size(f) { return (f.size / 1048576).toFixed(1) + ' MB'; }
  };
};


/* Bir nechta x-data obyektini getter larini saqlagan holda birlashtiradi (oddiy {...a, ...b} getter ni muzlatib qo‘yadi) */
window.mix = function () {
  var out = {};
  Array.from(arguments).forEach(function (o) { Object.defineProperties(out, Object.getOwnPropertyDescriptors(o)); });
  return out;
};

/* Jonli progress: forma yuboriladi (yuklash foizi ko‘rinadi), so‘ng server bosqichlari kuzatiladi.
   Server ishni orqa fonda bajaradi; bu yerda faqat yengil so‘rov (JSON) har ~1 soniyada yuboriladi. */
window.jobMixin = function (opts) {
  opts = opts || {};
  function mb(n) { return (n / 1048576).toFixed(n > 10485760 ? 0 : 1); }
  return {
    phase: 'idle',            // idle | upload | work | done | error
    upPct: 0, upText: '', pct: 0, steps: [], step: 0, label: '', error: '',
    hasUpload: false, miss: 0, timer: null,
    init() { if (opts.attach) this.watch(opts.attach); },
    get shown() { return (this.hasUpload ? ['Faylni serverga yuklash'] : []).concat(this.steps.map(function (s) { return s[0]; })); },
    get cur() { return this.phase === 'upload' ? 0 : this.step + (this.hasUpload ? 1 : 0); },
    get barPct() { return this.phase === 'upload' ? this.upPct : this.pct; },
    reset() { clearTimeout(this.timer); this.phase = 'idle'; this.error = ''; this.pct = 0; this.upPct = 0; },
    fail(msg) { clearTimeout(this.timer); this.phase = 'error'; this.error = msg; },
    submit(ev) {
      var self = this, form = ev.target, fd = new FormData(form);
      if (ev.submitter && ev.submitter.name) fd.append(ev.submitter.name, ev.submitter.value);
      var hasFiles = Array.from(fd.values()).some(function (v) { return v instanceof File && v.size > 0; });
      this.hasUpload = hasFiles; this.steps = []; this.step = 0; this.label = ''; this.error = '';
      this.phase = hasFiles ? 'upload' : 'work'; this.upPct = 0; this.pct = 0;
      var t0 = Date.now(), xhr = new XMLHttpRequest();
      xhr.open('POST', form.getAttribute('action') || location.href);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        self.upPct = Math.round(e.loaded / e.total * 100);
        var sec = Math.max((Date.now() - t0) / 1000, 0.2);
        self.upText = mb(e.loaded) + ' / ' + mb(e.total) + ' MB · ' + (e.loaded / 1048576 / sec).toFixed(1) + ' MB/s';
      };
      xhr.upload.onload = function () { self.upPct = 100; self.phase = 'work'; self.label = 'Server qabul qilmoqda…'; };
      xhr.onerror = function () { self.fail('Server bilan aloqa uzildi. Internetni tekshirib, qaytadan urinib ko‘ring.'); };
      xhr.onload = function () {
        var d = null;
        try { d = JSON.parse(xhr.responseText); } catch (e) { /* HTML javob */ }
        if (!d) return self.fail(xhr.status === 413 ? 'Fayl serverga sig‘maydigan darajada katta.' : 'Server kutilmagan javob qaytardi (HTTP ' + xhr.status + ').');
        if (d.error || !d.status_url) return self.fail(d.error || 'Jarayonni boshlab bo‘lmadi.');
        self.watch(d.status_url);
      };
      xhr.send(fd);
    },
    watch(url) {
      var self = this; this.phase = 'work'; this.miss = 0;
      var tick = function () {
        fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, cache: 'no-store', credentials: 'same-origin' })
          .then(function (r) { if (r.status === 404) throw new Error('404'); return r.json(); })
          .then(function (d) {
            self.miss = 0; self.steps = d.steps || []; self.step = d.step || 0; self.pct = d.pct || 0; self.label = d.label || '';
            if (d.status === 'done') { self.phase = 'done'; self.pct = 100; self.step = self.steps.length; if (d.redirect) location.href = d.redirect; return; }
            if (d.status === 'error') return self.fail(d.error || 'Jarayon xato bilan tugadi.');
            self.timer = setTimeout(tick, document.hidden ? 3000 : 900);
          })
          .catch(function (e) {
            if (String(e.message) === '404') return self.fail('Jarayon topilmadi (muddati o‘tgan bo‘lishi mumkin).');
            if (++self.miss > 15) return self.fail('Server bilan aloqa uzildi.');
            self.timer = setTimeout(tick, 2000);
          });
      };
      tick();
    }
  };
};


/* Menyu bo‘limlarini yig‘ish/ochish (holat brauzerda saqlanadi; faol bo‘lim doim ochiq) */
window.navGroup = function (title, hasActive) {
  var KEY = 'navOpen';
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } }
  var saved = load();
  return {
    open: hasActive ? true : (saved[title] === undefined ? false : !!saved[title]),
    toggle() {
      this.open = !this.open;
      var st = load(); st[title] = this.open;
      try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {}
    }
  };
};


/* Ilovani o‘rnatish (PWA): o‘rnatilmagan bo‘lsa yuqorida eslatib turadi */
window.installBar = function () {
  var KEY = 'installLater', HOURS = 12;
  var standalone = matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  var ua = navigator.userAgent || '';
  var ios = /iphone|ipad|ipod/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  var deferred = null;
  function snoozed() { try { return Date.now() - Number(localStorage.getItem(KEY) || 0) < HOURS * 3600e3; } catch (e) { return false; } }
  return {
    show: false, can: false, ios: false,
    init() {
      var self = this;
      if (standalone || !window.isSecureContext) return;
      this.ios = ios;
      window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault(); deferred = e; self.can = true; self.ios = false; self.sync(!snoozed());
      });
      window.addEventListener('appinstalled', function () { deferred = null; self.sync(false); try { localStorage.setItem(KEY, String(Date.now() + 3650 * 864e5)); } catch (e) {} });
      if (ios && !snoozed()) this.sync(true);
    },
    sync(v) { this.show = v; document.body.classList.toggle('has-install-bar', v); },
    install() {
      var self = this; if (!deferred) return;
      deferred.prompt();
      deferred.userChoice.then(function (r) { deferred = null; self.can = false; self.sync(false); if (r.outcome !== 'accepted') self.later(); });
    },
    later() { try { localStorage.setItem(KEY, String(Date.now())); } catch (e) {} this.sync(false); }
  };
};
if ('serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', function () { navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(function () {}); });
}
