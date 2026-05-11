// Shared utilities for all dashboard pages

// --- TABLE DISPLAY ---
let _DN = 2; // trailing years to hide from tables/chart
function sl(arr) { return _DN ? arr.slice(0, -_DN) : arr; }
function yh(Y) { return sl(Y).map(y => `<th>'${String(y).slice(2)}</th>`).join(''); }
function sr(label, Y) { return `<tr class="section-header"><td colspan="${sl(Y).length + 1}">${label}</td></tr>`; }
function tr(label, data, fn, cls) {
  fn = fn || fCM;
  const d = _DN ? data.slice(0, -_DN) : data;
  return `<tr${cls ? ` class="${cls}"` : ''}><td>${label}</td>${d.map(v => `<td>${fn(v)}</td>`).join('')}</tr>`;
}

// --- FORMATTERS ---
function fK(n) {
  if (n == null || isNaN(n)) return '\u2014';
  const abs = Math.abs(n), sign = n < 0 ? '\u2212' : '';
  if (abs >= 1e6) return sign + (abs / 1e6).toFixed(1) + 'M';
  if (abs >= 1e3) return sign + Math.round(abs / 1e3).toLocaleString() + 'K';
  return sign + Math.round(abs).toLocaleString();
}
function fCK(n) {
  if (Math.abs(n) < 50) return '\u2014';
  const k = Math.round(Math.abs(n) / 1e3);
  return (n < 0 ? '\u2212' : '') + k.toLocaleString();
}
function fCM(n) {
  if (Math.abs(n) < 500) return '\u2014';
  return (n < 0 ? '\u2212' : '') + (Math.abs(n) / 1e6).toFixed(1);
}

// --- SLIDER BUILDER ---
const _sliderOpts = {};

function sliderBg(pct) {
  const s = getComputedStyle(document.documentElement);
  return `linear-gradient(to right,${s.getPropertyValue('--slider-fill').trim()} ${pct}%,${s.getPropertyValue('--slider-track').trim()} ${pct}%)`;
}

function makeSlider(key, opts) {
  _sliderOpts[key] = opts;
  const val = settings[key] ?? opts.def;
  const pct = ((val - opts.min) / (opts.max - opts.min)) * 100;
  const sub = opts.sub ? `<div class="slider-sublabel">${opts.sub(val)}</div>` : '';
  return `<div class="slider-group">
    <div class="slider-header">
      <div style="flex:1;padding-right:10px"><div class="slider-label">${opts.label}</div>${sub}</div>
      <span class="slider-value" id="sv-${key}">${opts.fmt(val)}</span>
    </div>
    <input type="range" id="sl-${key}" min="${opts.min}" max="${opts.max}" step="${opts.step}" value="${val}"
      style="background:${sliderBg(pct)}"
      oninput="onSlider('${key}',this)">
  </div>`;
}

function onSlider(key, el) {
  const opts = _sliderOpts[key];
  const val = Number(el.value);
  const pct = ((val - opts.min) / (opts.max - opts.min)) * 100;
  el.style.background = sliderBg(pct);
  document.getElementById('sv-' + key).textContent = opts.fmt(val);
  updateSetting(key, val);
}

// --- THEME ---
const THEME_KEY = 'gh-expansion-theme';

function toggleTheme() {
  document.body.classList.toggle('light');
  const isLight = document.body.classList.contains('light');
  document.getElementById('themeToggle').textContent = isLight ? 'dark' : 'light';
  localStorage.setItem(THEME_KEY, isLight ? 'light' : 'dark');
}

function initTheme() {
  if (localStorage.getItem(THEME_KEY) === 'light') {
    document.body.classList.add('light');
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = 'dark';
  }
}

// --- NAVIGATION ---
function buildNav(activePage) {
  const pages = [
    {href: 'index.html', label: 'Expansion'},
    {href: 'notes.html', label: 'Notes'},
    {href: 'debt.html', label: 'Debt'},
    {href: 'depreciation.html', label: 'Depreciation'},
    {href: 'cukes.html', label: 'Cukes'},
  ];
  const links = pages.map(p => {
    const active = p.href === activePage;
    const style = `color:var(--${active ? 'accent' : 'text-muted'});text-decoration:none;text-transform:uppercase;letter-spacing:0.1em${active ? ';font-weight:700' : ''}`;
    return `<a href="${p.href}" style="${style}">${p.label}</a>`;
  }).join('\n        ');
  return links;
}
