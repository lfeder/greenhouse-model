// === SHARED CONSTANTS ===
const YEARS = [];
for (let y = 2026; y <= 2035; y++) YEARS.push(y);

const TIER0 = 300000;
const TIER1_SCHEDULE = { 2026: 500000, 2027: 600000 }; // 2028+ = 750000
function getTier1(year) { return TIER1_SCHEDULE[year] || 750000; }

const OWNERSHIP = {
  2026: { EB: 26.0, JS: 31.5, JJB: 42.5 },
  2027: { EB: 26.5, JS: 28.5, JJB: 45.0 },
  2028: { EB: 27.0, JS: 25.5, JJB: 47.5 },
  2029: { EB: 27.5, JS: 22.5, JJB: 50.0 },
};
function getOwnership(year) {
  // Prefer expansion page ownership (includes dilution from JJB equity investment)
  if (!getOwnership._cache) {
    try { getOwnership._cache = JSON.parse(localStorage.getItem('gh-ownership')); } catch(e) {}
  }
  if (getOwnership._cache && getOwnership._cache[year]) return getOwnership._cache[year];
  return (year <= 2029 && OWNERSHIP[year]) ? OWNERSHIP[year] : OWNERSHIP[2029];
}
// Clear cache on each recalc so it picks up fresh data
function clearOwnershipCache() { getOwnership._cache = null; }

// PE/DD instruments — repayment order: PE3 → PE1 → PE2 → DD
// PE/DD as of Jan 1, 2026 (from Excel Debt Schedule tab)
const PEDD_INSTRUMENTS = [
  { name: 'PE3', label: 'PE Tranche 3', balance: 515000, accruedInt: 147120, rateType: 'tbill', rateSpread: 0.025, order: 1 },
  { name: 'PE1', label: 'PE Tranche 1', balance: 1000000, accruedInt: 0, rate: 0, order: 2 },
  { name: 'PE2', label: 'PE Tranche 2', balance: 500000, accruedInt: 79571, rate: 0.05, order: 3 },
  { name: 'DD',  label: 'Deferred Dist', balance: 380000, accruedInt: 0, rate: 0, order: 4 },
];

// Loan definitions
const LOANS = [
  {
    name: 'FCL (Lettuce)', rate: 0.0712,
    schedule: {
      2026: { interest: 310725, principal: 783267, endBal: 3935227 },
      2027: { interest: 253099, principal: 840893, endBal: 3094334 },
      2028: { interest: 191233, principal: 902759, endBal: 2191576 },
      2029: { interest: 124819, principal: 969173, endBal: 1222402 },
      2030: { interest: 53516, principal: 1040476, endBal: 181926 },
      2031: { interest: 1624, principal: 180708, endBal: 1219 },
    },
    annualSchedule(year) { return this.schedule[year] || { interest: 0, principal: 0, endBal: 0 }; },
    maturity: 'Mar 2031', startBal: 4718495,
  },
  {
    name: 'BIPAH (Property)', origBal: 4665000, rate: 0.065, termMonths: 240, maturity: 'Jan 2045',
    monthlyPmt() {
      const r = this.rate / 12, n = this.termMonths;
      return this.origBal * (r * Math.pow(1 + r, n)) / (Math.pow(1 + r, n) - 1);
    },
    balanceAfter(months) {
      const r = this.rate / 12, pmt = this.monthlyPmt();
      let bal = this.origBal;
      for (let i = 0; i < months; i++) { bal -= (pmt - bal * r); if (bal <= 0) return 0; }
      return bal;
    },
    annualSchedule(year) {
      const monthStart = (year - 2025) * 12;
      const r = this.rate / 12, pmt = this.monthlyPmt();
      let bal = this.balanceAfter(Math.max(0, monthStart));
      let totalInt = 0, totalPrin = 0;
      for (let m = 0; m < 12; m++) {
        const absMonth = monthStart + m;
        if (absMonth < 0 || bal <= 0 || absMonth >= this.termMonths) break;
        const intPmt = bal * r;
        const prinPmt = Math.min(pmt - intPmt, bal);
        totalInt += intPmt; totalPrin += prinPmt; bal -= prinPmt;
      }
      return { interest: totalInt, principal: totalPrin, endBal: Math.max(0, bal) };
    },
    get startBal() { return this.balanceAfter(12); },
  },
  {
    name: 'JJB Downpayment', rate: 0.075, maturity: 'Jan 2030', startBal: 500000,
    schedule: {
      2026: { interest: 37500, principal: 86082, endBal: 413918 },
      2027: { interest: 31044, principal: 92539, endBal: 321379 },
      2028: { interest: 24103, principal: 99479, endBal: 221900 },
      2029: { interest: 16643, principal: 106940, endBal: 114960 },
      2030: { interest: 8622, principal: 114960, endBal: 0 },
    },
    annualSchedule(year) { return this.schedule[year] || { interest: 0, principal: 0, endBal: 0 }; },
  },
];

// Paid-off loan residuals in 2026
const PAIDOFF_2026 = { interest: 2934, principal: 51901 }; // AgCredit + JJB Solar

// === FORMATTERS ===
function fmtK(n) {
  if (n == null || isNaN(n)) return '\u2014';
  const abs = Math.abs(n), sign = n < 0 ? '\u2212' : '';
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}${Math.round(abs / 1e3).toLocaleString()}K`;
  return `${sign}${Math.round(abs).toLocaleString()}`;
}

// Table cell in $K (no suffix, just the number in thousands)
function fmtCellK(n) {
  if (Math.abs(n) < 50) return '\u2014';
  const k = Math.round(Math.abs(n) / 1e3);
  return `${n < 0 ? '\u2212' : ''}${k.toLocaleString()}`;
}

// Table cell in $M
function fmtCellM(n) {
  if (Math.abs(n) < 500) return '\u2014';
  const m = Math.abs(n) / 1e6;
  return `${n < 0 ? '\u2212' : ''}${m.toFixed(1)}`;
}

function fmtM(n) { return `${n < 0 ? '\u2212' : ''}${(Math.abs(n) / 1e6).toFixed(2)}M`; }

// === SLIDER BUILDER ===
function sliderBg(pct) {
  const s = getComputedStyle(document.documentElement);
  const fill = s.getPropertyValue('--slider-fill').trim();
  const track = s.getPropertyValue('--slider-track').trim();
  return `linear-gradient(to right,${fill} 0%,${fill} ${pct}%,${track} ${pct}%,${track} 100%)`;
}

const sliderOpts = {};
function createSlider(containerId, key, state, opts) {
  sliderOpts[key] = opts;
  const container = document.getElementById(containerId);
  if (!container) return;
  const fill = ((state[key] - opts.min) / (opts.max - opts.min)) * 100;
  container.innerHTML = `
    <div class="slider-group">
      <div class="slider-header">
        <div style="flex:1;padding-right:10px">
          <div class="slider-label">${opts.label}</div>
          <div class="slider-sublabel" id="${key}-sublabel">${opts.sublabel ? opts.sublabel() : ''}</div>
        </div>
        <span class="slider-value" id="${key}-display">${opts.format(state[key])}</span>
      </div>
      <input type="range" id="${key}" min="${opts.min}" max="${opts.max}" step="${opts.step}" value="${state[key]}">
    </div>`;
  const input = document.getElementById(key);
  input.style.background = sliderBg(fill);
  input.addEventListener('input', (e) => {
    let val = Number(e.target.value);
    if (opts.snap) val = opts.snap(val);
    state[key] = val;
    input.value = val;
    const pct = ((val - opts.min) / (opts.max - opts.min)) * 100;
    input.style.background = sliderBg(pct);
    document.getElementById(`${key}-display`).textContent = opts.format(val);
    if (opts.sublabel) document.getElementById(`${key}-sublabel`).innerHTML = opts.sublabel();
    if (typeof recalc === 'function') recalc();
  });
}

// === THEME ===
function initTheme() {
  const theme = localStorage.getItem('gh-expansion-theme');
  if (theme === 'light') {
    document.body.classList.add('light');
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = 'dark';
  }
}

function toggleTheme() {
  document.body.classList.toggle('light');
  const isLight = document.body.classList.contains('light');
  const btn = document.getElementById('themeToggle');
  if (btn) btn.textContent = isLight ? 'dark' : 'light';
  localStorage.setItem('gh-expansion-theme', isLight ? 'light' : 'dark');
  // Re-render slider backgrounds
  document.querySelectorAll('input[type=range]').forEach(input => {
    const key = input.id;
    if (sliderOpts[key]) {
      const state = window._pageState;
      if (state && state[key] !== undefined) {
        const pct = ((state[key] - sliderOpts[key].min) / (sliderOpts[key].max - sliderOpts[key].min)) * 100;
        input.style.background = sliderBg(pct);
      }
    }
  });
}

// === SAVE / LOAD ===
function makeSaveLoad(storageKey, state) {
  return {
    save() {
      localStorage.setItem(storageKey, JSON.stringify(state));
      const btn = document.getElementById('saveBtn');
      if (btn) { btn.textContent = 'saved!'; setTimeout(() => { btn.textContent = 'save'; }, 1500); }
    },
    load() {
      try { const s = JSON.parse(localStorage.getItem(storageKey)); if (s) Object.assign(state, s); } catch(e) {}
    },
  };
}

// === TABLE HELPERS ===
function yearHeaders() {
  return YEARS.map(y => `<th>'${String(y).slice(2)}</th>`).join('');
}
function sectionRow(label) {
  return `<tr class="section-header"><td colspan="${YEARS.length + 1}">${label}</td></tr>`;
}
function tableRow(label, data, fmtFn, cls) {
  const fn = fmtFn || fmtCellK;
  return `<tr${cls ? ` class="${cls}"` : ''}><td>${label}</td>${data.map(v => `<td>${fn(v)}</td>`).join('')}</tr>`;
}
function balRow(label, data) {
  return `<tr><td>${label}</td>${data.map(v => `<td>${v > 50 ? Math.round(v / 1e3).toLocaleString() : '\u2014'}</td>`).join('')}</tr>`;
}

// === LOAN HELPERS ===
function computeLoanSchedules() {
  const loanData = {};
  for (const loan of LOANS) {
    loanData[loan.name] = { interest: [], principal: [], endBal: [] };
    for (const year of YEARS) {
      const s = loan.annualSchedule(year);
      loanData[loan.name].interest.push(s.interest);
      loanData[loan.name].principal.push(s.principal);
      loanData[loan.name].endBal.push(s.endBal);
    }
  }
  // Add paid-off residuals to 2026
  const idx = YEARS.indexOf(2026);
  if (idx >= 0) {
    // Create a virtual "Paid-off" entry or add to totals
  }
  const totalInt = YEARS.map((_, i) => LOANS.reduce((s, l) => s + loanData[l.name].interest[i], 0) + (i === 0 ? PAIDOFF_2026.interest : 0));
  const totalPrin = YEARS.map((_, i) => LOANS.reduce((s, l) => s + loanData[l.name].principal[i], 0) + (i === 0 ? PAIDOFF_2026.principal : 0));
  const totalDS = totalInt.map((v, i) => v + totalPrin[i]);
  return { loanData, totalInt, totalPrin, totalDS };
}

// === FULL MODEL: rev/exp → tax → waterfall ===
function runFullModel(opts) {
  // opts: { rev, exp, loanInt, loanPrin, totalDS, fedDep, stateDep, fedTaxRate, hiTaxRate, fedNOL, tBillRate }
  const opInc = opts.rev.map((r, i) => r - opts.exp[i]);
  const capexRes = opts.rev.map(r => r * 0.02);
  const taxableInc = opInc.map((e, i) => e - opts.loanInt[i]);

  // Tax liability per year
  let fedNOLRemaining = opts.fedNOL || 0;
  const totalTaxLiab = [];
  for (let i = 0; i < YEARS.length; i++) {
    const own = getOwnership(YEARS[i]);
    const fedTaxable = taxableInc[i] - (opts.fedDep[i] || 0);
    const ebFedTaxable = fedTaxable * own.EB / 100;
    const nolUsed = Math.min(fedNOLRemaining, Math.max(0, ebFedTaxable));
    fedNOLRemaining -= nolUsed;
    const ebNetFed = Math.max(0, ebFedTaxable - nolUsed);
    const fedTax = ebNetFed * (opts.fedTaxRate || 30) / 100;
    const fedDist = own.EB > 0 ? fedTax / (own.EB / 100) : 0;

    const hiTaxable = taxableInc[i] - (opts.stateDep[i] || 0);
    const ebHiTaxable = hiTaxable * own.EB / 100;
    const hiTax = Math.max(0, ebHiTaxable) * (opts.hiTaxRate || 10) / 100;
    const hiDist = own.EB > 0 ? hiTax / (own.EB / 100) : 0;

    totalTaxLiab.push(fedDist + hiDist);
  }

  // Tax cash distributions (quarterly timing)
  const taxCashDist = YEARS.map((year, i) => {
    const priorLiab = i > 0 ? totalTaxLiab[i-1] : 0;
    const twoPriorLiab = i > 1 ? totalTaxLiab[i-2] : 0;
    if (year === 2026) return 0;
    if (year === 2027) return totalTaxLiab[0] + totalTaxLiab[0] / 4 * 2;
    const q4 = twoPriorLiab / 4;
    const settle = Math.max(0, priorLiab - twoPriorLiab);
    const curEst = priorLiab / 4 * 3;
    return q4 + settle + curEst;
  });

  const distribCash = opInc.map((o, i) => o - opts.totalDS[i] - capexRes[i]);
  const wf = runWaterfall(distribCash, taxCashDist, opts.tBillRate || 0.065);

  return { opInc, capexRes, taxableInc, totalTaxLiab, taxCashDist, distribCash, ...wf };
}

// Get rev/exp from localStorage or debug defaults
function getRevExp(debug) {
  const DEBUG_REV = 14200000, DEBUG_EXP = 9700000, GROWTH = 0.05;
  let expData = null;
  try { expData = JSON.parse(localStorage.getItem('gh-expansion-data')); } catch(e) {}
  return {
    rev: YEARS.map((_, i) => (debug || !expData) ? DEBUG_REV * Math.pow(1 + GROWTH, i) : (expData.totalRev[i] || 0)),
    exp: YEARS.map((_, i) => (debug || !expData) ? DEBUG_EXP * Math.pow(1 + GROWTH, i) : (expData.totalExp[i] || 0)),
  };
}

// Get depreciation from localStorage or debug defaults
function getDepreciation(debug) {
  if (debug) {
    return {
      fedDep: YEARS.map((_, i) => i < 8 ? 200000 : 0),
      stateDep: YEARS.map(y => y <= 2029 ? 1000000 : 0),
    };
  }
  let depData = null;
  try { depData = JSON.parse(localStorage.getItem('gh-depreciation')); } catch(e) {}
  return {
    fedDep: depData ? depData.totalFed : YEARS.map(() => 0),
    stateDep: depData ? depData.totalState : YEARS.map(() => 0),
  };
}

// === WATERFALL SIMULATION ===
function runWaterfall(distribCash, taxDist, tBillRate) {
  const peddBal = PEDD_INSTRUMENTS.map(p => ({ ...p, remaining: p.balance, intOwed: p.accruedInt || 0 }));
  // Add a dynamic "T1 Shortfall" deferred distribution (0% interest, last in order)
  const t1ShortfallPE = { name: 'T1Short', label: 'T1 Shortfall DD', balance: 0, rate: 0, order: 99, remaining: 0, intOwed: 0 };
  peddBal.push(t1ShortfallPE);

  const peddDetail = {};
  for (const pe of peddBal) peddDetail[pe.name] = { prinPaid: [], intPaid: [], endBal: [], intOwed: [] };

  const peddPayments = [], tier2 = [];
  const tier0Actual = [], tier1Actual = [], tier1Shortfall = [];
  let cumPEDD = 0;

  for (let i = 0; i < YEARS.length; i++) {
    const tier1Full = getTier1(YEARS[i]);

    // Available after tax
    let remaining = distribCash[i] - taxDist[i];

    // Tier 0 — always paid (assumed priority)
    const t0 = Math.min(TIER0, Math.max(0, remaining));
    remaining -= t0;
    tier0Actual.push(t0);

    // Tier 1 — pay what we can
    const t1 = Math.min(tier1Full, Math.max(0, remaining));
    remaining -= t1;
    tier1Actual.push(t1);
    const shortfall = tier1Full - t1;
    tier1Shortfall.push(shortfall);

    // Any Tier 1 shortfall becomes new deferred distribution
    if (shortfall > 0) {
      t1ShortfallPE.remaining += shortfall;
    }

    // Accrue interest at start of year on opening balances
    let peddThisYear = 0;
    const yearPrin = {}, yearInt = {};
    for (const pe of peddBal) { yearPrin[pe.name] = 0; yearInt[pe.name] = 0; }

    for (const pe of peddBal) {
      if (pe.remaining > 0) {
        const rate = pe.rateType === 'tbill' ? (tBillRate || 0.065) : (pe.rate || 0);
        pe.intOwed += pe.remaining * rate;
      }
    }

    // Pay PE/DD in order: principal first, then interest after principal done
    for (const pe of peddBal) {
      if (remaining <= 0) break;
      if (pe.remaining > 0) {
        const prinPay = Math.min(remaining, pe.remaining);
        pe.remaining -= prinPay; remaining -= prinPay;
        peddThisYear += prinPay; cumPEDD += prinPay;
        yearPrin[pe.name] += prinPay;
      }
      if (pe.remaining <= 0 && pe.intOwed > 0 && remaining > 0) {
        const intPay = Math.min(remaining, pe.intOwed);
        pe.intOwed -= intPay; remaining -= intPay;
        peddThisYear += intPay; cumPEDD += intPay;
        yearInt[pe.name] += intPay;
      }
    }

    for (const pe of peddBal) {
      peddDetail[pe.name].prinPaid.push(yearPrin[pe.name]);
      peddDetail[pe.name].intPaid.push(yearInt[pe.name]);
      peddDetail[pe.name].endBal.push(pe.remaining);
      peddDetail[pe.name].intOwed.push(pe.intOwed);
    }

    peddPayments.push(peddThisYear);
    tier2.push(Math.max(0, remaining));
  }

  return { peddDetail, peddPayments, tier2, cumPEDD, tier0Actual, tier1Actual, tier1Shortfall };
}
