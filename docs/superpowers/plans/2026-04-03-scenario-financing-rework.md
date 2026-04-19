# Scenario Financing Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure scenarios from A/B/C/D to A / B-SBIC / B-3P / C with per-scenario financing modes, annual draw-based dilution, editable JJB caps, and a reorganized scenario comparison card showing uses/sources/ownership.

**Architecture:** Each scenario carries its own financing parameters (mode, JJB cap, SBIC cap/equity%, 3P valuation, 3P-buys-JS toggle). `allocate_capital_stack` gains a new `"sbic_3p"` mode. `compute_ownership` shifts from lump-sum to annual-draw dilution at pre-agreed valuations. The scenario comparison card in partners.html becomes the primary control surface with editable cells. Tomato hard asset slider moves from partners to expansion sidebar.

**Tech Stack:** Python (model.py), vanilla JS/HTML (partners.html, index.html)

---

### Task 1: Update SCENARIOS dict and settings defaults

**Files:**
- Modify: `model.py:1405-1410` (SCENARIOS dict)
- Modify: `settings.json` (add new keys, remove D-specific)

- [ ] **Step 1: Replace SCENARIOS dict in model.py**

```python
SCENARIOS = {
    "A":      {"newKAcres":0, "newJAcres":0, "newEAcres":4.5, "newTAcres":0, "newLAcres":0, "packhouseAcres":0.4, "housingPods":2, "landAcres":0, "debug":False,
               "financingMode":"jjb_only", "jjbEquityCap":10000000, "sbicKicker":0, "sbicEquityPct":0, "sbicCap":0, "thirdPartyBuysJS":False},
    "B_SBIC": {"newKAcres":4, "newJAcres":6, "newEAcres":6, "newTAcres":0, "newLAcres":0, "packhouseAcres":0.6, "housingPods":6, "landAcres":30, "debug":False,
               "financingMode":"sbic", "jjbEquityCap":10000000, "sbicKicker":5, "sbicEquityPct":10, "sbicCap":25000000, "thirdPartyBuysJS":False},
    "B_3P":   {"newKAcres":4, "newJAcres":6, "newEAcres":6, "newTAcres":0, "newLAcres":0, "packhouseAcres":0.6, "housingPods":6, "landAcres":30, "debug":False,
               "financingMode":"3p", "jjbEquityCap":10000000, "sbicKicker":0, "sbicEquityPct":0, "sbicCap":0, "thirdPartyBuysJS":False},
    "C":      {"newKAcres":4, "newJAcres":6, "newEAcres":6, "newTAcres":8, "newLAcres":2.5, "packhouseAcres":1, "housingPods":10, "landAcres":50, "debug":False,
               "financingMode":"sbic_3p", "jjbEquityCap":10000000, "sbicKicker":5, "sbicEquityPct":10, "sbicCap":25000000, "thirdPartyBuysJS":False},
}
```

- [ ] **Step 2: Add new keys to settings.json**

Add these keys (with defaults matching current B scenario):
```json
"sbicEquityPct": 10,
"sbicCap": 25000000
```

- [ ] **Step 3: Commit**

```bash
git add model.py settings.json
git commit -m "Restructure scenarios: A, B-SBIC, B-3P, C with per-scenario financing"
```

---

### Task 2: Update `allocate_capital_stack` for new modes

**Files:**
- Modify: `model.py:1178-1227` (allocate_capital_stack function)

- [ ] **Step 1: Rewrite allocate_capital_stack**

The function needs to handle 4 financing modes. Key change: `"sbic_3p"` mode uses SBIC for mezzanine debt AND equity, plus 3P equity on top. SBIC combined cap = $25M (debt + equity at implied valuation).

```python
def allocate_capital_stack(crop_data, s):
    """Allocate bank/SBIC/3P/JJB across crops.

    Modes:
      jjb_only: bank + JJB equity (no co-investor)
      sbic:     bank + SBIC debt + JJB equity + SBIC equity kicker
      3p:       bank + JJB equity + 3P equity (above JJB cap)
      sbic_3p:  bank + SBIC debt + JJB equity + SBIC equity + 3P equity
    """
    bank_cap = s["bankLoanCap"]
    bank_ltv = s["financingPct"] / 100
    target_ltv = s["targetLtv"] / 100
    jjb_cap = s["jjbEquityCap"]
    mode = s["financingMode"]
    sbic_cap = s.get("sbicCap", 25000000)
    sbic_equity_pct = s.get("sbicEquityPct", 0)

    build_crops = [c for c in crop_data if not c.get("is_buy")]
    total_capex = sum(c["capex"] for c in build_crops)
    total_startup = sum(c.get("startup_capital", 0) for c in build_crops)
    lendable_base = total_capex + total_startup

    # Step 1: bank fills first
    total_bank = min(total_capex * bank_ltv, bank_cap)
    total_sbic_debt = 0
    total_sbic_equity = 0
    total_3p = 0

    if mode == "jjb_only":
        total_jjb = lendable_base - total_bank

    elif mode == "sbic":
        total_debt = lendable_base * target_ltv
        total_bank = min(total_capex * bank_ltv, bank_cap, total_debt)
        total_sbic_debt = min(total_debt - total_bank, sbic_cap)
        total_jjb = lendable_base - total_bank - total_sbic_debt
        # SBIC equity: separate from debt, also under sbic_cap
        # Equity $ implied by: pct / (100 - pct) * pre_money_valuation
        # But for capital stack, SBIC equity comes from JJB giving up ownership, not additional $
        # Track it for ownership calc but don't add to capital stack
        total_sbic_equity = 0  # equity is ownership dilution, not capital

    elif mode == "3p":
        jjb_needed = lendable_base - total_bank
        if jjb_needed <= jjb_cap:
            total_jjb = jjb_needed
        else:
            total_jjb = jjb_cap
            total_3p = jjb_needed - jjb_cap

    elif mode == "sbic_3p":
        total_debt = lendable_base * target_ltv
        total_bank = min(total_capex * bank_ltv, bank_cap, total_debt)
        total_sbic_debt = min(total_debt - total_bank, sbic_cap)
        equity_needed = lendable_base - total_bank - total_sbic_debt
        if equity_needed <= jjb_cap:
            total_jjb = equity_needed
        else:
            total_jjb = jjb_cap
            total_3p = equity_needed - jjb_cap

    else:
        total_jjb = lendable_base - total_bank

    # Distribute pro-rata per crop by capex
    for crop in build_crops:
        capex = crop["capex"]
        pct = capex / total_capex if total_capex > 0 else 0
        crop["bank_loan"] = total_bank * pct
        crop["sbic_loan"] = total_sbic_debt * pct
        crop["jjb_equity"] = total_jjb * pct
        crop["third_party_equity"] = total_3p * pct
```

- [ ] **Step 2: Commit**

```bash
git add model.py
git commit -m "allocate_capital_stack: support jjb_only, sbic, 3p, sbic_3p modes"
```

---

### Task 3: Annual draw-based dilution in `compute_ownership`

**Files:**
- Modify: `model.py:281-440` (compute_ownership function)
- Modify: `model.py:1526-1546` (equity_by_year computation in run_everything)

- [ ] **Step 1: Update equity_by_year to include ALL equity parties (not just JJB)**

In `run_everything` and `_run_scenario`, change the equity_by_year computation to track per-party annual draws:

```python
    # Compute equity deployed per year per party for annual dilution
    equity_draws = {}  # {year: {"jjb": $, "3p": $, "sbic_equity": $}}
    for crop in crop_data:
        if crop.get("is_buy"):
            continue
        start_year = crop["start_q"] // 10
        if start_year not in equity_draws:
            equity_draws[start_year] = {"jjb": 0, "3p": 0}
        equity_draws[start_year]["jjb"] += crop.get("jjb_equity", 0)
        equity_draws[start_year]["3p"] += crop.get("third_party_equity", 0)
        sc = crop.get("startup_capital", 0)
        sc_yr = crop.get("startup_year")
        if sc and sc_yr:
            if sc_yr not in equity_draws:
                equity_draws[sc_yr] = {"jjb": 0, "3p": 0}
            equity_draws[sc_yr]["jjb"] += sc
```

- [ ] **Step 2: Rewrite compute_ownership for annual draw-based dilution**

Key changes:
- Each equity party (JJB, 3P) invests at a pre-agreed buy-in valuation
- Each annual draw dilutes other parties proportionally
- SBIC equity is a one-time kicker (% grant, reverse-solve implied valuation)
- JS→JJB vesting continues; accelerates if 3P buys JS
- EB PEDD grant + expansion promote unchanged

The function signature changes to:
```python
def compute_ownership(settings, cum_pedd_by_year, equity_draws=None, biz_values=None):
```

Where `equity_draws` is `{year: {"jjb": $, "3p": $}}` instead of `{year: total_jjb_$}`.

Dilution logic per annual draw:
```python
# For each year with draws:
for party in ["jjb", "3p"]:
    amount = draws.get(party, 0)
    if amount <= 0:
        continue
    valuation = jjb_buyin_val if party == "jjb" else tp_valuation
    post_money = valuation + amount
    new_pct = amount / post_money * 100
    # Dilute all OTHER parties proportionally
    scale = (100 - new_pct) / 100
    for other in [eb, js, jjb, sbic]:  # adjust all except the investing party
        other *= scale
    investing_party += new_pct
```

Full implementation will preserve all existing detail row tracking (starting %, buyout, dilution, SBIC kicker, PEDD grant, EB promote, ending %).

- [ ] **Step 3: Update `_run_scenario` to pass equity_draws instead of equity_by_year**

Same pattern as run_everything — build equity_draws dict, pass to compute_ownership.

- [ ] **Step 4: Commit**

```bash
git add model.py
git commit -m "Annual draw-based dilution with per-party equity tracking"
```

---

### Task 4: Add bridge and capital data to scenario summary

**Files:**
- Modify: `model.py:1433-1475` (compute_scenario_summary)

- [ ] **Step 1: Compute bridge loan totals per scenario**

After running `_run_scenario`, compute the bridge:
```python
        # Bridge loan (JJB funds EB/JS shortfalls)
        bridge_total = 0
        if not s["debug"]:
            s_noexp = dict(s)
            s_noexp["debug"] = True
            rev_ne, exp_ne, _, _, _, _, _ = build_rev_exp(s_noexp)
            result_ne = run_full_model(rev_ne, exp_ne, s_noexp, [])
            own_ne, _ = compute_ownership(s_noexp, result_ne["cum_pedd_by_year"])
            partners_ne = compute_partner_cash({**result_ne, "tax_cash_dist": result_ne["tax_cash"]}, own_ne)
            for p in ["EB", "JS"]:
                ne_total = partners_ne[p]["total"]
                exp_total = d["partners"][p]["total"]
                for i in range(N_YEARS):
                    gap = ne_total[i] - exp_total[i]
                    if gap > 0:
                        bridge_total += gap
        summary["bridge_total"] = bridge_total
```

- [ ] **Step 2: Add ownership snapshot to summary**

```python
        # Terminal ownership for scenario card
        summary["own_EB"] = own.get("EB", 0)
        summary["own_JS"] = own.get("JS", 0)
        summary["own_JJB"] = own.get("JJB", 0)
        summary["own_SBIC"] = own.get("SBIC", 0)
```

- [ ] **Step 3: Add implied SBIC valuation**

```python
        # SBIC implied valuation (reverse solve from equity %)
        sbic_eq_pct = s.get("sbicEquityPct", 0)
        sbic_debt = kpi["sbic_total"]
        if sbic_eq_pct > 0:
            implied_val = sbic_debt * (100 - sbic_eq_pct) / sbic_eq_pct
            summary["sbic_implied_val"] = implied_val
        else:
            summary["sbic_implied_val"] = 0
```

- [ ] **Step 4: Commit**

```bash
git add model.py
git commit -m "Add bridge, ownership, SBIC valuation to scenario summary"
```

---

### Task 5: Restructure partners.html scenario comparison card

**Files:**
- Modify: `partners.html:222-256` (scenario table rendering)

- [ ] **Step 1: Update scenario keys and add editable cells**

Change `scKeys` from `['A','B','C','D']` to `['A','B_SBIC','B_3P','C']`.

Add column headers with display labels:
```javascript
const scLabels = {A:'A', B_SBIC:'B (SBIC)', B_3P:'B (3P)', C:'C'};
```

- [ ] **Step 2: Add editable JJB cap cells in header area**

Each scenario column gets an editable JJB max field:
```javascript
st += `<tr><td>Max JJB Equity</td>${scKeys.map(k => 
  `<td><input type="text" class="sc-edit" data-sc="${k}" data-key="jjbEquityCap" 
    value="${fK(sc[k].jjb_equity_cap || 10000000)}" 
    onchange="updateScenarioParam('${k}','jjbEquityCap',this.value)"
    style="width:60px;background:var(--panel-dark);color:var(--text);border:1px solid var(--border);border-radius:3px;font-family:inherit;font-size:10px;padding:2px 4px;text-align:right"></td>`
).join('')}</tr>`;
```

- [ ] **Step 3: Build the restructured card sections**

Order:
1. **$ Uses** — Total Uses, Bridge
2. **$ Sources** — Bank, SBIC Loan, 3P Equity, JJB Equity
3. **Ownership** — EB%, JS%, JJB%, SBIC% with show/hide detail toggle
4. **Ownership Detail (hidden)** — JS→JJB vesting, JJB equity dilution, SBIC kicker, 3P investment, PEDD grant to EB, EB expansion promote
5. **Valuation** — Revenue, EBITDA, Multiple, Biz Value
6. **Per-partner** — Avg Dist, Equity Value, SBIC IRR

- [ ] **Step 4: Add 3P-buys-JS toggle per applicable scenario**

For B_3P and C columns, show a small checkbox:
```javascript
if (k === 'B_3P' || k === 'C') {
  // inline toggle
}
```

- [ ] **Step 5: Commit**

```bash
git add partners.html
git commit -m "Restructured scenario card: uses/sources/ownership with editable cells"
```

---

### Task 6: Update index.html scenarios and move tomato hard asset slider

**Files:**
- Modify: `index.html:88-93` (SCENARIOS dict)
- Modify: `index.html:155-178` (buildSidebar — add tomato hard asset slider)
- Modify: `partners.html:205-207` (remove tomato hard asset slider from partners sidebar)

- [ ] **Step 1: Update SCENARIOS in index.html**

Replace A/B/C/D buttons and SCENARIOS dict with A/B/C (B applies to both B_SBIC and B_3P since they share the same acres):
```javascript
const SCENARIOS = {
  A: {newKAcres:0, newJAcres:0, newEAcres:4.5, newTAcres:0, newLAcres:0, packhouseAcres:0.4, housingPods:2, landAcres:0, debug:false, sbicKicker:0},
  B: {newKAcres:4, newJAcres:6, newEAcres:6, newTAcres:0, newLAcres:0, packhouseAcres:0.6, housingPods:6, landAcres:30, debug:false, sbicKicker:5},
  C: {newKAcres:4, newJAcres:6, newEAcres:6, newTAcres:8, newLAcres:2.5, packhouseAcres:1, housingPods:10, landAcres:50, debug:false, sbicKicker:10},
};
```

Remove D button from sidebar.

- [ ] **Step 2: Add tomato hard asset slider to expansion sidebar**

In `buildSidebar()`, add after the Inflation panel:
```javascript
h += `<div class="panel"><div class="panel-title">Tomato (Buy)</div>
  ${S('tomatoHardAssets', {label:'Hard Asset Value',min:2000000,max:10000000,step:1000000,def:5000000,fmt:v=>(v/1e6).toFixed(2)+'M'})}
</div>`;
```

- [ ] **Step 3: Remove tomato hard asset slider from partners.html sidebar**

Delete the "Tomato (Buy) — Financing" panel from `buildSidebar()` in partners.html.

- [ ] **Step 4: Commit**

```bash
git add index.html partners.html
git commit -m "Update expansion scenarios, move tomato hard asset slider to expansion page"
```

---

### Task 7: Update partners.html sidebar (remove scenario-level financing sliders)

**Files:**
- Modify: `partners.html:148-210` (buildSidebar)

- [ ] **Step 1: Clean up partners sidebar**

Since financing params are now per-scenario in the comparison card, remove from sidebar:
- Remove SBIC/3P mode toggle
- Remove SBIC rate/term/kicker sliders (these become scenario-level)
- Remove target LTV slider (still needed globally? Keep for now)
- Remove JJB equity cap slider (now editable per scenario in card)
- Keep: Bank loan sliders (global), EB promote sliders (global), buy-in valuation

- [ ] **Step 2: Commit**

```bash
git add partners.html
git commit -m "Clean up partners sidebar, financing params now per-scenario in card"
```

---

### Task 8: Wire up editable scenario params and recompute

**Files:**
- Modify: `partners.html` (add updateScenarioParam function, SBIC equity % editable, 3P valuation editable)
- Modify: `model.py:1433-1475` (compute_scenario_summary — accept per-scenario overrides)

- [ ] **Step 1: Add scenario param storage and update function**

In partners.html, store per-scenario overrides in a JS object that persists to localStorage:
```javascript
const SC_OVERRIDES_KEY = 'scenario_overrides';
function loadScOverrides() {
  try { return JSON.parse(localStorage.getItem(SC_OVERRIDES_KEY)) || {}; } catch { return {}; }
}
function saveScOverrides(ov) {
  localStorage.setItem(SC_OVERRIDES_KEY, JSON.stringify(ov));
}
function updateScenarioParam(sc, key, rawValue) {
  const ov = loadScOverrides();
  if (!ov[sc]) ov[sc] = {};
  // Parse: strip K/M suffixes, handle numbers
  let val = parseFloat(String(rawValue).replace(/[^0-9.-]/g, ''));
  if (String(rawValue).includes('M')) val *= 1e6;
  if (String(rawValue).includes('K')) val *= 1e3;
  ov[sc][key] = val;
  saveScOverrides(ov);
  compute();  // recompute with new overrides
}
```

- [ ] **Step 2: Pass overrides to backend**

Modify the compute call to include scenario overrides so `compute_scenario_summary` can use them:
```javascript
async function compute(partial) {
  if (partial) Object.assign(settings, partial);
  settings._scenario_overrides = loadScOverrides();
  // ... rest unchanged
}
```

- [ ] **Step 3: Update compute_scenario_summary to apply overrides**

```python
def compute_scenario_summary(base_settings):
    overrides = base_settings.get("_scenario_overrides", {})
    for name, defaults in SCENARIOS.items():
        s = dict(base_settings)
        s.update(defaults)
        if name in overrides:
            s.update(overrides[name])
        d = _run_scenario(s)
        # ... rest of summary computation
```

- [ ] **Step 4: Commit**

```bash
git add partners.html model.py
git commit -m "Wire up editable per-scenario params with localStorage persistence"
```

---

### Task 9: Version stamps and nav consistency

**Files:**
- Modify: `index.html`, `partners.html`, `debt.html`, `depreciation.html`, `employees.html` — version stamps

- [ ] **Step 1: Update all version stamps to 04-03 16:00**

- [ ] **Step 2: Verify nav links include Employees on all pages**

- [ ] **Step 3: Commit**

```bash
git add index.html partners.html debt.html depreciation.html employees.html
git commit -m "Update version stamps"
```
