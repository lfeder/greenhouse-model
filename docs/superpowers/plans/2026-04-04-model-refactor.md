# Model.py Refactor Implementation Plan

**Goal:** Split the 1760-line model.py into 4 focused modules, rename to main.py, delete unused output code.

**Architecture:** Extract bottom-up (leaves first, orchestrator last). server.py changes `import model` → `import main`. Verification after each task: `python -c "from main import run_everything; print('ok')"`.

---

## File Map

| File | Role | Functions |
|------|------|-----------|
| `load.py` | Load constants + build from settings | Constants from JSON, `build_crops`, `build_rev_exp`, `allocate_capital_stack`, `compute_kpis`, `ramp_crop_rev_exp`, `ramp_factor`, `prev_quarter`, `get_end_quarter` |
| `fin_util.py` | Utility math (mechanical calcs) | `_parse_month`, `_monthly_pmt`, `_annual_pmt`, `_compute_monthly_loan`, `_compute_annual_loan`, `compute_loan_schedules`, `calc_tax`, `calc_effective_rate`, `calc_tax_dist`, `calc_tax_cash_timing`, `compute_depreciation` |
| `analysis.py` | Business math (IRR, waterfall, ownership, partner cash) | `calc_irr`, `compute_crop_irrs`, `run_waterfall`, `compute_ownership`, `compute_partner_cash`, `build_equity_draws`, `get_tier1` |
| `main.py` | Orchestration + scenarios | `run_pnl`, `run_everything`, `compute_scenario_summary`, `_run_scenario`, `rev_band_multiple`, `SCENARIOS`, `REV_MULTIPLES` |

**Deleted:** `_build_output_rows`, `write_csv`, `write_gsheet`, gsheet background thread in server.py.

**Renamed:** `crop_annual_rev_exp` → `ramp_crop_rev_exp`, `run_full_model` → `run_pnl`

**Import chain (no cycles):**
```
load.py            (standalone — loads JSON, exports globals + builders)
   ^
   |--- fin_util.py   (imports load)
   |--- analysis.py   (imports load, fin_util)
   |
   main.py  (imports load, fin_util, analysis)
   |
   server.py (imports main — updated from `import model`)
```

---

### Task 1: Create load.py

**Files:**
- Create: `load.py`
- Modify: `model.py` (remove constants + builder functions, add import)

- [ ] **Step 1: Create load.py**

Copy into `load.py`:

From **lines 1-74** (constants):
- `json`, `Path` imports
- Load `constants.json` into `C`
- All derived globals: `YEARS`, `N_YEARS`, `EXISTING_*`, `TIER0`, `TIER1`, `TIER1_DEFAULT`, `DIST_BASE`, `JS_BUYOUT`, `EB_GRANT_PEDD`, `PEDD_INSTRUMENTS`, `FED_NOL`, `FED_BRACKETS`, `HI_BRACKETS`, `LOAN_DEFS`, `DEFAULT_GROWTH`, `DEP_DEFAULTS`, `PAIDOFF_2026`

From **lines 986-1003** (crop constants):
- `CROP_REV_PER_AC`, `CROP_DEFS`, `NEW_E_REV_PER_AC`, `EXISTING_E_BASE_PRICE`

From **lines 620-657** (quarter/ramp helpers):
- `prev_quarter`, `get_end_quarter`, `ramp_factor`
- `crop_annual_rev_exp` → rename to `ramp_crop_rev_exp`

From **lines 1005-1196** (builders):
- `build_crops(s)` — update internal call from `crop_annual_rev_exp` to `ramp_crop_rev_exp`
- `build_rev_exp(s)` — same rename

From **lines 1198-1270** (capital stack):
- `allocate_capital_stack(crop_data, s)`

From **lines 1419-1433** (KPIs):
- `compute_kpis(s, crop_data, debug)`

File header:
```python
"""
Load constants from JSON and build data structures from settings.
"""
import json
from pathlib import Path
```

- [ ] **Step 2: Replace in model.py with import**

Remove all moved code. Add:
```python
from load import *
```

- [ ] **Step 3: Verify**

`python -c "from model import run_everything; print('ok')"`

- [ ] **Step 4: Commit**

```bash
git add load.py model.py
git commit -m "Extract load.py: constants + builders from model.py"
```

---

### Task 2: Create fin_util.py

**Files:**
- Create: `fin_util.py`
- Modify: `model.py` (remove finance functions, add import)

- [ ] **Step 1: Create fin_util.py**

Move from model.py:

From **section 2 — LOANS** (lines 80-172):
- `_parse_month`, `_monthly_pmt`, `_annual_pmt`
- `_compute_monthly_loan`, `_compute_annual_loan`
- `compute_loan_schedules()`

From **section 3 — TAX** (lines 179-271):
- `calc_tax`, `calc_effective_rate`, `calc_tax_dist`, `calc_tax_cash_timing`

From **section 6 — DEPRECIATION** (lines 580-614):
- `compute_depreciation(crops, settings)`

File header:
```python
"""
Financial utilities: loan schedules, tax calculations, depreciation.
"""
from load import (
    YEARS, N_YEARS, LOAN_DEFS, PAIDOFF_2026,
    FED_BRACKETS, HI_BRACKETS, FED_NOL,
    DIST_BASE, DEP_DEFAULTS,
)
```

- [ ] **Step 2: Update model.py**

Remove moved functions. Add:
```python
from fin_util import (
    _annual_pmt, compute_loan_schedules,
    calc_tax, calc_tax_dist, calc_tax_cash_timing,
    compute_depreciation,
)
```

- [ ] **Step 3: Verify**

`python -c "from model import run_everything; print('ok')"`

- [ ] **Step 4: Commit**

```bash
git add fin_util.py model.py
git commit -m "Extract fin_util.py: loans, taxes, depreciation"
```

---

### Task 3: Create analysis.py

**Files:**
- Create: `analysis.py`
- Modify: `model.py` (remove analysis functions, add import)

- [ ] **Step 1: Create analysis.py**

Move from model.py:

From **section 8 — IRR** (lines 663-675):
- `calc_irr(cashflows, guess=0.1)`

From **section 4 — OWNERSHIP** (lines 278-461):
- `get_tier1(year)`
- `build_equity_draws(crop_data)`
- `compute_ownership(settings, cum_pedd_by_year, equity_draws=None, biz_values=None)`

From **section 5 — WATERFALL** (lines 467-573):
- `run_waterfall(distrib_cash, tax_dist, t_bill_rate=0.065)`

From **section 9 — PARTNER CASH** (lines 681-718):
- `compute_partner_cash(model, ownership)`

From **lines 1271-1417** (crop IRRs):
- `compute_crop_irrs(crop_data, s)` — uses `_annual_pmt` from fin_util and `calc_irr` locally

File header:
```python
"""
Business analysis: IRR, ownership dilution, distribution waterfall, partner cash flows.
"""
from load import (
    YEARS, N_YEARS,
    DIST_BASE, JS_BUYOUT, EB_GRANT_PEDD,
    TIER0, TIER1, TIER1_DEFAULT,
    PEDD_INSTRUMENTS,
)
from fin_util import _annual_pmt
```

- [ ] **Step 2: Update model.py**

Remove moved functions. Add:
```python
from analysis import (
    calc_irr, get_tier1, build_equity_draws, compute_ownership,
    run_waterfall, compute_partner_cash, compute_crop_irrs,
)
```

- [ ] **Step 3: Verify**

`python -c "from model import run_everything; print('ok')"`

- [ ] **Step 4: Commit**

```bash
git add analysis.py model.py
git commit -m "Extract analysis.py: IRR, waterfall, ownership, partner cash"
```

---

### Task 4: Rename model.py → main.py, delete unused code, update server.py

**Files:**
- Rename: `model.py` → `main.py`
- Modify: `main.py` (remove output code, rename `run_full_model` → `run_pnl`, clean up)
- Modify: `server.py` (update imports, remove gsheet thread)

- [ ] **Step 1: Delete output code from model.py**

Remove entirely:
- `_build_output_rows` (~105 lines)
- `write_csv` (~10 lines)
- `write_gsheet` (~80 lines)
- The `write_csv(result)` call inside `run_everything`
- The `csv` import (no longer needed)

- [ ] **Step 2: Rename `run_full_model` → `run_pnl`**

Update the function definition and the two call sites in `run_everything`.

- [ ] **Step 3: Rename model.py → main.py**

```bash
git mv model.py main.py
```

- [ ] **Step 4: Update server.py**

- Change `import model` → `import main`
- Change `model.run_everything(settings)` → `main.run_everything(settings)`
- Remove `model.write_gsheet(data)` call
- Remove entire gsheet background thread (`_gsheet_data`, `_gsheet_dirty`, `_gsheet_lock`, `_gsheet_worker`, `_queue_gsheet`, thread start)

- [ ] **Step 5: Clean up main.py**

- Update module docstring
- Remove orphaned imports
- Update section headers
- Ensure imports from load, fin_util, analysis are correct

- [ ] **Step 6: Verify**

```bash
python -c "from main import run_everything; print('ok')"
python -c "import main; print(dir(main))"
wc -l load.py fin_util.py analysis.py main.py
```

Expected: main.py ~400 lines, total across all files ≈ original minus deleted output code.

- [ ] **Step 7: Verify server starts**

```bash
python server.py
```

- [ ] **Step 8: Commit**

```bash
git add main.py server.py
git rm model.py
git commit -m "Rename model.py → main.py, delete CSV/gsheet output, clean up server.py"
```
