"""
Greenhouse Expansion Financial Model v2
All calculations in one readable Python file.
Constants from constants.json, slider values from settings.json.
"""

import json
import csv
from pathlib import Path

# ============================================================
# 1. LOAD CONSTANTS
# ============================================================

_DIR = Path(__file__).parent
with open(_DIR / "constants.json") as f:
    C = json.load(f)

YEARS = C["years"]
N_YEARS = len(YEARS)

# Existing ops
BASE_REV_PER_AC = C["existing_ops"]["base_rev_per_ac"]
EXISTING_K_REV = C["existing_ops"]["keiki_rev"]
EXISTING_J_REV = C["existing_ops"]["japanese_rev"]
EXISTING_E_REV = C["existing_ops"]["english_rev"]
EXISTING_L_REV = C["existing_ops"]["lettuce_rev"]
EXISTING_EXP_RATIO = C["existing_ops"]["exp_ratio"]
PAIDOFF_2026 = C["existing_ops"]["paidoff_2026"]

# Tiers
TIER0 = C["tiers"]["tier0"]
TIER1 = {int(k): v for k, v in C["tiers"]["tier1"].items() if k != "default"}
TIER1_DEFAULT = C["tiers"]["tier1"]["default"]

# Distribution base (2026 starting ownership)
DIST_BASE = C["distribution_base"]["2026"]

# JS buyout: JJB buys 3%/yr from JS
JS_BUYOUT = C["js_buyout"]

# EB grants
EB_GRANT_PEDD = C["eb_grant_pedd"]
# EB expansion grant is slider-driven (in settings.json)

# PE/DD
PEDD_INSTRUMENTS = C["pedd_instruments"]

# Tax
FED_NOL = C["tax"]["fed_nol"]
FED_BRACKETS = [(b[0] if b[0] is not None else float("inf"), b[1]) for b in C["tax"]["fed_brackets"]]
HI_BRACKETS  = [(b[0] if b[0] is not None else float("inf"), b[1]) for b in C["tax"]["hi_brackets"]]

# Loans
LOAN_DEFS = C["loans"]

# Default growth for existing ops
DEFAULT_GROWTH = C["existing_ops"]["default_growth"]

# Depreciation defaults
DEP_DEFAULTS = C["depreciation_defaults"]


# ============================================================
# 2. LOANS — compute from parameters
# ============================================================

def _parse_month(s):
    """'2024-03' → (2024, 3)"""
    y, m = s.split("-")
    return int(y), int(m)

def _monthly_pmt(principal, annual_rate, n_months):
    r = annual_rate / 12
    if r == 0:
        return principal / n_months
    return principal * (r * (1 + r)**n_months) / ((1 + r)**n_months - 1)

def _annual_pmt(principal, annual_rate, n_years):
    r = annual_rate
    if r == 0:
        return principal / n_years
    return principal * (r * (1 + r)**n_years) / ((1 + r)**n_years - 1)

def _compute_monthly_loan(loan):
    """Build month-by-month schedule, aggregate to calendar years."""
    start_y, start_m = _parse_month(loan["start_month"])
    orig = loan["orig_bal"]
    rate = loan["rate"]
    io_mo = loan.get("io_months", 0)
    amort_mo = loan["amort_months"]
    pmt = _monthly_pmt(orig, rate, amort_mo)
    r = rate / 12

    # Generate monthly entries: (cal_year, cal_month, interest, principal)
    bal = orig
    months = []
    for m in range(io_mo + amort_mo):
        cal_m = (start_m - 1 + m) % 12 + 1
        cal_y = start_y + (start_m - 1 + m) // 12
        if bal <= 0.01:
            break
        interest = bal * r
        principal = 0 if m < io_mo else min(pmt - interest, bal)
        months.append((cal_y, cal_m, interest, principal))
        bal -= principal

    # Aggregate to calendar years
    # Include pre-YEARS principal in cumulative total
    cum_prin = sum(p for cy, _, _, p in months if cy < YEARS[0])
    result = []
    for y in YEARS:
        yr = [(i, p) for cy, _, i, p in months if cy == y]
        yr_int = sum(i for i, _ in yr)
        yr_prin = sum(p for _, p in yr)
        cum_prin += yr_prin
        end_bal = max(0, orig - cum_prin)
        result.append({"interest": yr_int, "principal": yr_prin, "end_bal": end_bal})
    return result

def _compute_annual_loan(loan):
    """Annual amortizing loan."""
    orig = loan["orig_bal"]
    rate = loan["rate"]
    n_yr = loan["amort_years"]
    start_y, _ = _parse_month(loan["start_month"])
    pmt = _annual_pmt(orig, rate, n_yr)

    result = []
    bal = orig
    for y in YEARS:
        idx = y - start_y
        if idx < 0 or idx >= n_yr or bal <= 0.01:
            result.append({"interest": 0, "principal": 0, "end_bal": max(0, bal)})
            continue
        interest = bal * rate
        principal = min(pmt - interest, bal)
        bal -= principal
        result.append({"interest": interest, "principal": principal, "end_bal": max(0, bal)})
    return result

def compute_loan_schedules():
    """Compute all loan schedules + paid-off residuals → totals."""
    data = {}
    for loan in LOAN_DEFS:
        name = loan["name"]
        if loan["frequency"] == "monthly":
            data[name] = _compute_monthly_loan(loan)
        elif loan["frequency"] == "annual":
            data[name] = _compute_annual_loan(loan)

    total_int = [sum(data[n][i]["interest"] for n in data) for i in range(N_YEARS)]
    total_prin = [sum(data[n][i]["principal"] for n in data) for i in range(N_YEARS)]

    # Add paid-off residuals to 2026
    total_int[0] += PAIDOFF_2026["interest"]
    total_prin[0] += PAIDOFF_2026["principal"]

    total_ds = [i + p for i, p in zip(total_int, total_prin)]
    return {"data": data, "total_int": total_int, "total_prin": total_prin, "total_ds": total_ds}


# ============================================================
# 3. TAX — bracket-based calculation
# ============================================================

def calc_tax(taxable_income, brackets):
    """Compute tax from brackets."""
    if taxable_income <= 0:
        return 0
    tax = 0
    prev = 0
    for limit, rate in brackets:
        s = min(taxable_income, limit) - prev
        if s <= 0:
            break
        tax += s * rate
        prev = limit
    return tax

def calc_effective_rate(income, brackets):
    if income <= 0:
        return 0
    return calc_tax(income, brackets) / income

def calc_tax_dist(taxable_inc, fed_dep, state_dep, ownership, nol_remaining):
    """Entity-level tax distribution for one year.

    1. Fed taxable = operating taxable - fed depreciation
    2. EB's share = fed_taxable × EB%
    3. Subtract EB's NOL
    4. EB tax via brackets
    5. Gross up: entity dist = EB tax / EB%
    6. Same for Hawaii
    """
    eb_pct = ownership["EB"] / 100

    # Federal
    fed_taxable = taxable_inc - fed_dep
    eb_fed = fed_taxable * eb_pct
    nol_used = min(nol_remaining, max(0, eb_fed))
    eb_net_fed = max(0, eb_fed - nol_used)
    fed_tax = calc_tax(eb_net_fed, FED_BRACKETS)
    fed_dist = fed_tax / eb_pct if eb_pct > 0 else 0

    # Hawaii
    hi_taxable = taxable_inc - state_dep
    eb_hi = max(0, hi_taxable * eb_pct)
    hi_tax = calc_tax(eb_hi, HI_BRACKETS)
    hi_dist = hi_tax / eb_pct if eb_pct > 0 else 0

    return {
        "fed_dist": fed_dist, "hi_dist": hi_dist, "total": fed_dist + hi_dist,
        "fed_tax": fed_tax, "hi_tax": hi_tax,
        "eb_fed": eb_fed, "eb_net_fed": eb_net_fed, "eb_hi": eb_hi,
        "nol_used": nol_used, "nol_remaining": nol_remaining - nol_used,
        "fed_taxable": fed_taxable, "hi_taxable": hi_taxable,
    }

def calc_tax_cash_timing(liabilities):
    """Convert annual tax liability → cash payment timing.

    2026: $0 (no 2025 liability, no safe harbor)
    2027: 2026 final + 2 quarterly estimates for 2027
    2028+: Q4 prior est + settlement + Q1-Q3 current estimates
    """
    result = []
    for i, year in enumerate(YEARS):
        prior = liabilities[i - 1] if i > 0 else 0
        two_prior = liabilities[i - 2] if i > 1 else 0
        if year == 2026:
            result.append(0)
        elif year == 2027:
            result.append(liabilities[0] + liabilities[0] / 4 * 2)
        else:
            q4 = two_prior / 4
            settle = max(0, prior - two_prior)
            cur_est = prior / 4 * 3
            result.append(q4 + settle + cur_est)
    return result


# ============================================================
# 4. OWNERSHIP — distribution percentages over time
# ============================================================

def get_tier1(year):
    return TIER1.get(year, TIER1_DEFAULT)

def compute_ownership(settings, cum_pedd_by_year):
    """Compute ownership % for each year.

    Drivers:
    1. Start: distribution_base 2026
    2. JS buyout: JJB buys 3%/yr from JS in 2027-2030
    3. EB grant (PEDD): +0.5% per $500K repaid, 0.25% from JS + 0.25% from JJB
    4. EB grant (expansion): slider % from JS, between start/stop years
    5. JJB equity investment dilution (for expansion scenarios)
    """
    jjb = DIST_BASE["JJB"]
    eb = DIST_BASE["EB"]
    js = DIST_BASE["JS"]

    buyout_years = JS_BUYOUT["years"]
    buyout_pct = JS_BUYOUT["pct_per_year"]

    exp_grant_pct = settings.get("ebExpansionGrantPct", 0)
    exp_grant_start = settings.get("ebExpansionGrantStartYear", 2027)
    exp_grant_end = settings.get("ebExpansionGrantEndYear", 2035)

    pedd_grant_earned = 0  # cumulative % granted from PEDD
    result = []

    for i, year in enumerate(YEARS):
        # JS buyout: JJB buys from JS
        if year in buyout_years:
            js -= buyout_pct
            jjb += buyout_pct

        # EB grant (PEDD): based on cumulative PE/DD repaid
        cum_pedd = cum_pedd_by_year[i] if i < len(cum_pedd_by_year) else 0
        target_grant = min(EB_GRANT_PEDD["max_pct"], int(cum_pedd / 500_000) * EB_GRANT_PEDD["per_500k"])
        new_grant = target_grant - pedd_grant_earned
        if new_grant > 0:
            eb += new_grant
            js -= new_grant * EB_GRANT_PEDD["source_split"]["JS"] / (EB_GRANT_PEDD["source_split"]["JS"] + EB_GRANT_PEDD["source_split"]["JJB"])
            jjb -= new_grant * EB_GRANT_PEDD["source_split"]["JJB"] / (EB_GRANT_PEDD["source_split"]["JS"] + EB_GRANT_PEDD["source_split"]["JJB"])
            pedd_grant_earned = target_grant

        # EB grant (expansion): slider-driven, from JS only
        if exp_grant_pct > 0 and exp_grant_start <= year <= exp_grant_end:
            eb += exp_grant_pct
            js -= exp_grant_pct

        result.append({"year": year, "JJB": round(jjb, 2), "EB": round(eb, 2), "JS": round(js, 2)})

    return result


# ============================================================
# 5. PE/DD WATERFALL
# ============================================================

def run_waterfall(distrib_cash, tax_dist, t_bill_rate=0.065):
    """Tax → Tier 0 → Tier 1 → PE/DD → Tier 2.

    T1 shortfall becomes new deferred distribution.
    PE/DD order: PE3 → PE1 → PE2 → DD → T1Short
    Interest accrues at start of year, principal paid first.
    """
    instruments = []
    for p in PEDD_INSTRUMENTS:
        rate = t_bill_rate + p.get("rate_spread", 0) if p.get("rate_type") == "tbill" else p.get("rate", 0)
        instruments.append({"name": p["name"], "label": p["label"], "remaining": p["balance"], "int_owed": p.get("accrued_int", 0), "rate": rate})
    # T1 shortfall bucket
    instruments.append({"name": "T1Short", "label": "T1 Shortfall DD", "remaining": 0, "int_owed": 0, "rate": 0})

    detail = {inst["name"]: {"prin_paid": [], "int_paid": [], "end_bal": [], "int_owed": []} for inst in instruments}
    pedd_payments = []
    tier0_actual = []
    tier1_actual = []
    tier1_shortfall = []
    tier2 = []
    cum_pedd = 0
    cum_pedd_by_year = []

    for i, year in enumerate(YEARS):
        t1_full = get_tier1(year)
        remaining = distrib_cash[i] - tax_dist[i]

        # Tier 0
        t0 = min(TIER0, max(0, remaining))
        remaining -= t0
        tier0_actual.append(t0)

        # Tier 1
        t1 = min(t1_full, max(0, remaining))
        remaining -= t1
        tier1_actual.append(t1)
        shortfall = t1_full - t1
        tier1_shortfall.append(shortfall)
        if shortfall > 0:
            instruments[-1]["remaining"] += shortfall

        # Accrue interest at start of year
        for inst in instruments:
            if inst["remaining"] > 0 and inst["rate"] > 0:
                inst["int_owed"] += inst["remaining"] * inst["rate"]

        # Pay PE/DD in order
        pedd_year = 0
        yr_prin = {inst["name"]: 0 for inst in instruments}
        yr_int = {inst["name"]: 0 for inst in instruments}

        for inst in instruments:
            if remaining <= 0:
                break
            if inst["remaining"] > 0:
                pay = min(remaining, inst["remaining"])
                inst["remaining"] -= pay
                remaining -= pay
                pedd_year += pay
                cum_pedd += pay
                yr_prin[inst["name"]] += pay
            if inst["remaining"] <= 0 and inst["int_owed"] > 0 and remaining > 0:
                pay = min(remaining, inst["int_owed"])
                inst["int_owed"] -= pay
                remaining -= pay
                pedd_year += pay
                cum_pedd += pay
                yr_int[inst["name"]] += pay

        for inst in instruments:
            detail[inst["name"]]["prin_paid"].append(yr_prin[inst["name"]])
            detail[inst["name"]]["int_paid"].append(yr_int[inst["name"]])
            detail[inst["name"]]["end_bal"].append(inst["remaining"])
            detail[inst["name"]]["int_owed"].append(inst["int_owed"])

        pedd_payments.append(pedd_year)
        tier2.append(max(0, remaining))
        cum_pedd_by_year.append(cum_pedd)

    return {
        "detail": detail, "pedd_payments": pedd_payments,
        "tier0_actual": tier0_actual, "tier1_actual": tier1_actual,
        "tier1_shortfall": tier1_shortfall, "tier2": tier2,
        "cum_pedd": cum_pedd, "cum_pedd_by_year": cum_pedd_by_year,
    }


# ============================================================
# 6. DEPRECIATION
# ============================================================

def compute_depreciation(crops, settings):
    s = settings
    fed = [s.get("existFedAnnual", 200000) if i < s.get("existFedYearsLeft", 8) else 0 for i in range(N_YEARS)]
    state = [s.get("existStateAnnual", 1000000) if YEARS[i] <= s.get("existStateEndYear", 2029) else 0 for i in range(N_YEARS)]
    bonus_pct = s.get("fedBonusPct", 100) / 100
    state_life = s.get("stateUsefulLife", 10)

    for crop in crops:
        pis = crop["end_year"]
        capex = crop["capex"]
        bonus = capex * bonus_pct
        sl_fed = (capex - bonus) / 20 if bonus_pct < 1 else 0
        sl_state = capex / state_life

        for i, y in enumerate(YEARS):
            if y == pis:
                fed[i] += bonus
            if y >= pis and sl_fed > 0:
                fed[i] += sl_fed
            if pis <= y < pis + state_life:
                state[i] += sl_state

    return {"fed": fed, "state": state}


# ============================================================
# 7. CROP REVENUE & EXPENSE
# ============================================================

def get_end_quarter(start_q, months):
    sy = start_q // 10
    sq = start_q % 10
    eq = sq + (months + 2) // 3
    ey = sy
    while eq > 4:
        eq -= 4
        ey += 1
    return ey * 10 + eq

def ramp_factor(prod_yr, ramp_years):
    if ramp_years == 0: return 1.0
    if ramp_years == 1: return 0.70 if prod_yr == 1 else 1.0
    if ramp_years == 2: return {1: 0.50, 2: 0.85}.get(prod_yr, 1.0)
    return {1: 0.40, 2: 0.65, 3: 0.85}.get(prod_yr, 1.0)

def crop_annual_rev_exp(base_rev, base_exp, end_q, ramp_years, rev_inf, cost_inf):
    end_year = end_q // 10
    end_qtr = end_q % 10
    rev, exp = [], []
    for y in YEARS:
        if y < end_year:
            rev.append(0); exp.append(0); continue
        frac = (4 - end_qtr + 1) / 4 if y == end_year else 1
        prod_yr = 1 if y == end_year else y - end_year + 1
        ramp = ramp_factor(prod_yr, ramp_years)
        n = y - 2026
        rev.append(base_rev * ramp * frac * (1 + rev_inf / 100) ** n)
        exp.append(base_exp * ramp * frac * (1 + cost_inf / 100) ** n)
    return rev, exp


# ============================================================
# 8. IRR
# ============================================================

def calc_irr(cashflows, guess=0.1):
    rate = guess
    for _ in range(200):
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))
        dnpv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cashflows))
        if abs(npv) < 0.01:
            return rate
        if abs(dnpv) < 1e-10:
            break
        rate -= npv / dnpv
        rate = max(-0.5, min(10, rate))
    return rate


# ============================================================
# 9. PARTNER CASH
# ============================================================

def compute_partner_cash(model, ownership):
    """Per-partner annual distributions."""
    partners = {}
    for p in ["EB", "JS", "JJB"]:
        own = [o[p] for o in ownership]
        rows = {}
        rows["tax_dist"] = [model["tax_cash_dist"][i] * own[i] / 100 for i in range(N_YEARS)]
        if p == "EB":
            rows["tier_0"] = list(model["tier0_actual"])
        rows["tier_1"] = [model["tier1_actual"][i] * own[i] / 100 for i in range(N_YEARS)]
        if p in ("JS", "JJB"):
            rows["pedd"] = [v * 0.5 for v in model["pedd_payments"]]
        rows["tier_2"] = [model["tier2"][i] * own[i] / 100 for i in range(N_YEARS)]

        # JS buyout payments
        if p in ("JS", "JJB"):
            eq = []
            for y in YEARS:
                sy = str(y)
                if y in JS_BUYOUT["years"] and sy in JS_BUYOUT["valuations"]:
                    pmt = JS_BUYOUT["valuations"][sy] * JS_BUYOUT["pct_per_year"] / 100
                    eq.append(pmt if p == "JS" else -pmt)
                else:
                    eq.append(0)
            rows["equity_buy_sell"] = eq

        # Total
        total = []
        for i in range(N_YEARS):
            t = rows["tax_dist"][i] + rows["tier_1"][i] + rows["tier_2"][i]
            if p == "EB": t += rows["tier_0"][i]
            if p in ("JS", "JJB"): t += rows["pedd"][i] + rows["equity_buy_sell"][i]
            total.append(t)
        rows["total"] = total
        partners[p] = rows

    return partners


# ============================================================
# 10. FULL MODEL
# ============================================================

def run_full_model(rev, exp, settings, dep_crops=None):
    """Run everything. Returns all intermediate values."""
    s = settings
    loans = compute_loan_schedules()
    dep = compute_depreciation(dep_crops or [], s)
    t_bill_rate = s.get("tBillRate", 4) / 100

    op_inc = [r - e for r, e in zip(rev, exp)]
    capex_res = [r * 0.02 for r in rev]
    taxable_inc = [o - li for o, li in zip(op_inc, loans["total_int"])]

    # Tax liability per year
    nol_left = FED_NOL
    tax_liab = []
    tax_detail = []
    for i in range(N_YEARS):
        own = {"EB": DIST_BASE["EB"], "JS": DIST_BASE["JS"], "JJB": DIST_BASE["JJB"]}  # simplified for tax calc
        td = calc_tax_dist(taxable_inc[i], dep["fed"][i], dep["state"][i], own, nol_left)
        nol_left = td["nol_remaining"]
        tax_liab.append(td["total"])
        tax_detail.append(td)

    tax_cash = calc_tax_cash_timing(tax_liab)
    distrib_cash = [o - ds - cr for o, ds, cr in zip(op_inc, loans["total_ds"], capex_res)]

    # Waterfall
    wf = run_waterfall(distrib_cash, tax_cash, t_bill_rate)

    # Ownership (needs cum_pedd_by_year from waterfall)
    ownership = compute_ownership(s, wf["cum_pedd_by_year"])

    # Partner cash
    partners = compute_partner_cash({**wf, "tax_cash_dist": tax_cash}, ownership)

    # EB grant summary
    pedd_grant = min(EB_GRANT_PEDD["max_pct"], int(wf["cum_pedd"] / 500_000) * EB_GRANT_PEDD["per_500k"])

    return {
        "years": YEARS, "rev": rev, "exp": exp,
        "op_inc": op_inc, "capex_res": capex_res,
        "taxable_inc": taxable_inc, "tax_liab": tax_liab, "tax_cash": tax_cash,
        "distrib_cash": distrib_cash, "tax_detail": tax_detail,
        "loans": loans, "dep": dep,
        "ownership": ownership, "partners": partners,
        "pedd_grant": pedd_grant,
        **wf,
    }


# ============================================================
# 11. CSV OUTPUT
# ============================================================

def _build_output_rows(data):
    """Build all output rows from run_everything() result. Used by CSV and gsheet."""
    hdr = [""] + [str(y) for y in YEARS]
    R = lambda label, arr: [label] + [round(v) for v in arr]
    rows = []

    rows.append(["P&L"]); rows.append(hdr)
    for name, vals in data.get("rev_rows", []):
        rows.append(R(name, vals))
    rows.append(R("Total Revenue", data["rev"]))
    rows.append(R("Total Expenses", data["exp"]))
    rows.append(R("Operating Income", data["op_inc"]))
    rows.append([])

    rows.append(["EXISTING DEBT SERVICE"]); rows.append(hdr)
    if "loans" in data:
        for name, sched in data["loans"]["data"].items():
            rows.append(R(f"{name} Int", [s["interest"] for s in sched]))
            rows.append(R(f"{name} Prin", [s["principal"] for s in sched]))
        rows.append(R("Total Existing Int", data["loans"]["total_int"]))
        rows.append(R("Total Existing Prin", data["loans"]["total_prin"]))
        rows.append(R("Total Existing DS", data["loans"]["total_ds"]))
    rows.append([])

    rows.append(["EXPANSION DEBT SERVICE"]); rows.append(hdr)
    if "expansion_int" in data:
        rows.append(R("Expansion Interest", data["expansion_int"]))
        rows.append(R("Expansion Principal", data["expansion_prin"]))
        rows.append(R("Expansion DS", data["expansion_ds"]))
    rows.append([])

    rows.append(["DEPRECIATION"]); rows.append(hdr)
    rows.append(R("Fed Depreciation", data["dep"]["fed"]))
    rows.append(R("State Depreciation", data["dep"]["state"]))
    rows.append([])

    rows.append(["TAX DETAIL"]); rows.append(hdr)
    rows.append(R("Taxable Inc", data["taxable_inc"]))
    rows.append(R("Fed Depr", data["dep"]["fed"]))
    rows.append(R("Fed Taxable", [data["taxable_inc"][i] - data["dep"]["fed"][i] for i in range(N_YEARS)]))
    rows.append(R("State Depr", data["dep"]["state"]))
    rows.append(R("HI Taxable", [data["taxable_inc"][i] - data["dep"]["state"][i] for i in range(N_YEARS)]))
    rows.append(R("EB Fed Taxable", [td["eb_fed"] for td in data["tax_detail"]]))
    rows.append(R("NOL Used", [td["nol_used"] for td in data["tax_detail"]]))
    rows.append(R("EB Net Fed", [td["eb_net_fed"] for td in data["tax_detail"]]))
    rows.append(R("Fed Tax (EB)", [td["fed_tax"] for td in data["tax_detail"]]))
    rows.append(R("Fed Dist", [td["fed_dist"] for td in data["tax_detail"]]))
    rows.append(R("HI Tax (EB)", [td["hi_tax"] for td in data["tax_detail"]]))
    rows.append(R("HI Dist", [td["hi_dist"] for td in data["tax_detail"]]))
    rows.append(R("Tax Liability", data["tax_liab"]))
    rows.append(R("Tax Cash Dist", data["tax_cash"]))
    rows.append([])

    rows.append(R("Capex Reserve", data["capex_res"]))
    rows.append(R("Distributable Cash", data["distrib_cash"]))
    rows.append([])

    rows.append(["WATERFALL"]); rows.append(hdr)
    rows.append(R("Distributable Cash", data["distrib_cash"]))
    rows.append(R("Tax Dist", data["tax_cash"]))
    rows.append(R("Tier 0", data["tier0_actual"]))
    rows.append(R("Tier 1", data["tier1_actual"]))
    rows.append(R("T1 Shortfall", data["tier1_shortfall"]))
    rows.append(R("PE/DD Payments", data["pedd_payments"]))
    rows.append(R("Tier 2", data["tier2"]))
    rows.append([])

    rows.append(["PE/DD DETAIL"]); rows.append(hdr)
    for name in [p["name"] for p in PEDD_INSTRUMENTS] + ["T1Short"]:
        if name in data["detail"]:
            d = data["detail"][name]
            rows.append(R(f"{name} Prin", d["prin_paid"]))
            rows.append(R(f"{name} Int", d["int_paid"]))
            rows.append(R(f"{name} Bal", d["end_bal"]))
    rows.append([])

    rows.append(["OWNERSHIP %"]); rows.append(hdr)
    for p in ["JJB", "EB", "JS"]:
        rows.append([p] + [round(o[p], 1) for o in data["ownership"]])
    rows.append([])

    rows.append(["PARTNER CASH"]); rows.append(hdr)
    for p in ["EB", "JS", "JJB"]:
        for rn, rd in data["partners"][p].items():
            rows.append(R(f"{p} {rn}", rd))
        rows.append([])

    rows.append(["EB PEDD Grant %", data.get("pedd_grant", 0)])

    # Crop IRR summary
    if "crop_irrs" in data:
        rows.append([])
        rows.append(["CROP IRR"])
        rows.append(["Crop", "CapEx", "Equity", "IRR", "Unlevered"])
        for c in data["crop_irrs"]:
            rows.append([c["label"], round(c["capex"]), round(c["equity"]), c["irr"], c.get("unlev", "")])

    return rows


def write_csv(data, path="output.csv"):
    p = _DIR / path
    rows = _build_output_rows(data)
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)
    return str(p)


# ============================================================
# 12. GOOGLE SHEETS OUTPUT
# ============================================================

GSHEET_ID = "1jDsN-P4M5rNt0LkWrhtaJkvju_ADRW17bKH8HR1nPOk"
GSHEET_KEY = _DIR / "gsheet-key.json"

def write_gsheet(data):
    """Write full model output to Google Sheet with multiple tabs."""
    try:
        import gspread
    except ImportError:
        return
    if not GSHEET_KEY.exists():
        return

    gc = gspread.service_account(filename=str(GSHEET_KEY))
    sh = gc.open_by_key(GSHEET_ID)

    def _tab(name):
        try:
            return sh.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            return sh.add_worksheet(title=name, rows=300, cols=20)

    hdr = [""] + [str(y) for y in YEARS]
    R = lambda label, arr: [label] + [round(v) for v in arr]

    # Main tab: uses shared _build_output_rows
    ws = sh.sheet1
    ws.clear()
    ws.update(range_name="A1", values=_build_output_rows(data))

    # Debt tab
    debt_rows = [["EXISTING LOANS"], hdr]
    if "loans" in data:
        for name, sched in data["loans"]["data"].items():
            debt_rows.append(R(f"{name} Int", [s["interest"] for s in sched]))
            debt_rows.append(R(f"{name} Prin", [s["principal"] for s in sched]))
            debt_rows.append(R(f"{name} Bal", [s["end_bal"] for s in sched]))
            debt_rows.append([])
    if "expansion_int" in data:
        debt_rows.append(["EXPANSION LOANS"]); debt_rows.append(hdr)
        debt_rows.append(R("Expansion Interest", data["expansion_int"]))
        debt_rows.append(R("Expansion Principal", data["expansion_prin"]))
        debt_rows.append(R("Expansion DS", data["expansion_ds"]))
        debt_rows.append([])
    debt_rows.append(["PE/DD DETAIL"]); debt_rows.append(hdr)
    for name in [p["name"] for p in PEDD_INSTRUMENTS] + ["T1Short"]:
        if name in data.get("detail", {}):
            d = data["detail"][name]
            debt_rows.append(R(f"{name} Prin", d["prin_paid"]))
            debt_rows.append(R(f"{name} Int", d["int_paid"]))
            debt_rows.append(R(f"{name} Bal", d["end_bal"]))
            debt_rows.append(R(f"{name} Int Owed", d["int_owed"]))
    dws = _tab("Debt")
    dws.clear()
    dws.update(range_name="A1", values=debt_rows)

    # Depreciation tab
    dep_rows = [["DEPRECIATION"], hdr, R("Fed", data["dep"]["fed"]), R("State", data["dep"]["state"])]
    dpws = _tab("Depreciation")
    dpws.clear()
    dpws.update(range_name="A1", values=dep_rows)

    # Constants tab
    def _flat(obj, prefix=""):
        rows = []
        if isinstance(obj, dict):
            for k, v in obj.items(): rows.extend(_flat(v, f"{prefix}{k}."))
        elif isinstance(obj, list):
            for i, v in enumerate(obj): rows.extend(_flat(v, f"{prefix}[{i}]."))
        else: rows.append([prefix.rstrip("."), str(obj)])
        return rows
    cws = _tab("Constants")
    cws.clear()
    cws.update(range_name="A1", values=[["Key", "Value"]] + _flat(C))

    # Settings tab
    sws = _tab("Settings")
    sws.clear()
    try:
        with open(_DIR / "settings.json") as f: sdata = json.load(f)
        sws.update(range_name="A1", values=[["Key", "Value"]] + [[k, str(v)] for k, v in sdata.items()])
    except Exception: pass


# ============================================================
# 13. BUILD CROPS FROM SETTINGS
# ============================================================

CROP_DEFS = [
    ("K",  "Keiki",          "newKAcres",  "cukeExpPct",    "cucumberCostPerAc", 1.0,   None),
    ("J",  "Japanese",       "newJAcres",  "cukeExpPct",    "cucumberCostPerAc", 1.0,   None),
    ("E",  "English",        "newEAcres",  "cukeExpPct",    "cucumberCostPerAc", None,   "englishRevPct"),
    ("L",  "Lettuce",        "newLAcres",  "lettuceExpPct", "lettuceCostPerAc",  None,   None),
    ("T",  "Tomato (Build)", "newTAcres",  "tomatoExpPct",  "tomatoCostPerAc",   None,   "tomatoRevPct"),
]

def build_crops(s):
    """Build crop list from slider settings."""
    crops = []
    for key, label, acres_key, exp_key, cost_key, fixed_mult, mult_key in CROP_DEFS:
        acres = s.get(acres_key, 0)
        if acres <= 0:
            continue
        if key == "L":
            base_rev = s.get("lettuceLbs", 600000) * s.get("lettucePrice", 7.0)
        else:
            mult = s.get(mult_key, (fixed_mult or 1.0) * 100) / 100 if mult_key else (fixed_mult or 1.0)
            base_rev = acres * BASE_REV_PER_AC * mult
        exp_pct = s.get(exp_key, 70) / 100
        start_q = s.get(f"{key.lower()}StartQ", 20271)
        build_mo = s.get(f"{key.lower()}BuildMonths", 12)
        ramp_yr = s.get(f"{key.lower()}RampYears", 2)
        end_q = get_end_quarter(start_q, build_mo)
        crops.append({
            "key": key, "label": label, "acres": acres,
            "base_rev": base_rev, "base_exp": base_rev * exp_pct,
            "capex": acres * s.get(cost_key, 1000000),
            "start_q": start_q, "end_q": end_q,
            "build_months": build_mo, "ramp_years": ramp_yr,
            "end_year": end_q // 10,
        })
    # Tomato purchase (always)
    t_mult = s.get("tomatoRevPct", 120) / 100
    tb_rev = 6 * BASE_REV_PER_AC * t_mult
    tb_exp_pct = s.get("tomatoExpPct", 70) / 100
    tb_start_q = s.get("tStartQ", 20281)
    tb_build = s.get("tbBuildMonths", 6)
    tb_end_q = get_end_quarter(tb_start_q, tb_build)
    crops.append({
        "key": "TB", "label": "Tomato (Purchased)", "acres": 6,
        "base_rev": tb_rev, "base_exp": tb_rev * tb_exp_pct,
        "capex": s.get("tomatoPurchasePrice", 8000000),
        "start_q": tb_start_q, "end_q": tb_end_q,
        "build_months": tb_build, "ramp_years": s.get("tbRampYears", 1),
        "end_year": tb_end_q // 10, "is_buy": True,
    })
    return crops


def build_rev_exp(s):
    """Build per-component revenue/expense arrays from settings."""
    ri = s.get("revInflation", 4)
    ci = s.get("costInflation", 3)
    debug = s.get("debug", False)

    # Ordered lists: [[name, data], ...] — order matters for display
    rev_rows = []
    exp_rows = []
    rev = [0.0] * N_YEARS
    exp = [0.0] * N_YEARS

    # Existing ops — K, J, E, L (in this order)
    for label, base in [("Existing Keiki", EXISTING_K_REV), ("Existing Japanese", EXISTING_J_REV), ("Existing English", EXISTING_E_REV)]:
        r = [base * (1 + ri / 100) ** (y - 2026) for y in YEARS]
        e = [base * EXISTING_EXP_RATIO * (1 + ci / 100) ** (y - 2026) for y in YEARS]
        for i in range(N_YEARS):
            rev[i] += r[i]; exp[i] += e[i]
        rev_rows.append([label, r]); exp_rows.append([label, e])

    # Existing lettuce
    el_rev = [0.0] * N_YEARS; el_exp = [0.0] * N_YEARS
    for i, y in enumerate(YEARS):
        n = y - 2026
        rm = (1 + ri / 100) ** n; cm = (1 + ci / 100) ** n
        l_base = EXISTING_L_REV if y <= 2026 else s.get("lettuceLbs", 600000) * s.get("lettucePrice", 7.0)
        el_rev[i] = l_base * rm
        el_exp[i] = l_base * (EXISTING_EXP_RATIO if y <= 2026 else s.get("lettuceExpPct", 70) / 100) * cm
        rev[i] += el_rev[i]; exp[i] += el_exp[i]
    rev_rows.append(["Existing Lettuce", el_rev]); exp_rows.append(["Existing Lettuce", el_exp])

    # New crops — K, J, E, L, then T (matching CROP_DEFS order, TB excluded from table)
    crop_data = [] if debug else build_crops(s)
    dep_crops = []
    for crop in crop_data:
        cr, ce = crop_annual_rev_exp(crop["base_rev"], crop["base_exp"], crop["end_q"], crop["ramp_years"], ri, ci)
        if not crop.get("is_buy"):
            for i in range(N_YEARS):
                rev[i] += cr[i]; exp[i] += ce[i]
            rev_rows.append([crop["label"], cr]); exp_rows.append([crop["label"], ce])
            dep_crops.append({"end_year": crop["end_year"], "capex": crop["capex"]})

    return rev, exp, rev_rows, exp_rows, crop_data, dep_crops


def compute_crop_irrs(crop_data, s):
    """Compute per-crop IRR and expansion debt service."""
    ri = s.get("revInflation", 4)
    ci = s.get("costInflation", 3)
    financing = s.get("financingPct", 65) / 100
    rate = s.get("interestRate", 7) / 100
    term = s.get("loanTermYears", 10)

    crop_irrs = []
    expansion_int = [0.0] * N_YEARS
    expansion_prin = [0.0] * N_YEARS

    for crop in crop_data:
        cr, ce = crop_annual_rev_exp(crop["base_rev"], crop["base_exp"], crop["end_q"], crop["ramp_years"], ri, ci)
        if crop.get("is_buy"):
            loan_base = min(s.get("tomatoHardAssets", 5000000), crop["capex"])
        else:
            loan_base = crop["capex"]
        loan_amt = loan_base * financing
        equity = crop["capex"] - loan_amt
        annual_ds = _annual_pmt(loan_amt, rate, term) if loan_amt > 0 else 0

        cfs = []
        start_year = crop["start_q"] // 10
        end_year = crop["end_q"] // 10
        bal = loan_amt
        for i, y in enumerate(YEARS):
            cf = cr[i] - ce[i]
            io_end_y = end_year + (1 if (crop["end_q"] % 10) > 2 else 0)
            yr_int = yr_prin = 0
            if start_year <= y <= start_year + term - 1 and bal > 0:
                yr_int = bal * rate
                if y >= io_end_y:
                    yr_prin = min(annual_ds - yr_int, bal)
                    bal -= yr_prin
                cf -= yr_int + yr_prin
            if not crop.get("is_buy"):
                expansion_int[i] += yr_int
                expansion_prin[i] += yr_prin
            if y == start_year:
                cf -= equity
            cfs.append(cf)

        irr = calc_irr(cfs) * 100 if any(c != 0 for c in cfs) else 0

        # Cash-on-cash: steady-state annual (op inc - debt svc) / equity
        base_op_inc = crop["base_rev"] - crop["base_exp"]
        unlev = (base_op_inc / crop["capex"] * 100) if crop["capex"] > 0 else 0

        crop_irrs.append({
            "key": crop["key"], "label": crop["label"],
            "capex": crop["capex"], "equity": equity,
            "irr": round(irr, 1), "unlev": round(unlev, 1),
            "unlev_detail": {"base_rev": round(crop["base_rev"]), "base_exp": round(crop["base_exp"]),
                             "op_inc": round(base_op_inc), "capex": round(crop["capex"])},
            "cashflows": [round(c) for c in cfs],
        })

    # Total IRR (built crops only, excludes TB)
    built_cfs = [0] * N_YEARS
    for ci_data in crop_irrs:
        if ci_data["key"] != "TB":
            for i in range(N_YEARS):
                built_cfs[i] += ci_data["cashflows"][i]
    total_irr = calc_irr(built_cfs) * 100 if any(c != 0 for c in built_cfs) else 0

    return crop_irrs, round(total_irr, 1), expansion_int, expansion_prin


def compute_kpis(s, crop_data, debug):
    """Compute KPI card values."""
    crop_capex = sum(c["capex"] for c in crop_data if not c.get("is_buy"))
    shared_capex = (s.get("landCostPerAc", 125000) * 20 + s.get("packhouseSF", 30000) * s.get("packhouseCostPerSF", 100) + s.get("housingPeople", 25) * 50000) if not debug else 0
    return {"total_capex": crop_capex, "shared_capex": shared_capex, "financing_pct": s.get("financingPct", 65)}


# ============================================================
# 14. RUN EVERYTHING (single entry point for server)
# ============================================================

def run_everything(s):
    """Single entry point: settings → all computed data for HTML."""
    rev, exp, rev_rows, exp_rows, crop_data, dep_crops = build_rev_exp(s)
    result = run_full_model(rev, exp, s, dep_crops)
    crop_irrs, total_irr, expansion_int, expansion_prin = compute_crop_irrs(crop_data, s)
    kpis = compute_kpis(s, crop_data, s.get("debug", False))

    write_csv(result)

    return {
        **{k: v for k, v in result.items() if k != "loans"},
        "loan_data": {n: [{"interest": d["interest"], "principal": d["principal"], "end_bal": d["end_bal"]} for d in sched] for n, sched in result["loans"]["data"].items()},
        "loan_total_int": result["loans"]["total_int"],
        "loan_total_prin": result["loans"]["total_prin"],
        "loan_total_ds": result["loans"]["total_ds"],
        "crop_irrs": crop_irrs,
        "total_irr": total_irr,
        "crops": [{"key": c["key"], "label": c["label"], "acres": c["acres"], "capex": c["capex"]} for c in crop_data],
        "rev_rows": rev_rows, "exp_rows": exp_rows,
        "expansion_int": expansion_int, "expansion_prin": expansion_prin,
        "expansion_ds": [expansion_int[i] + expansion_prin[i] for i in range(N_YEARS)],
        **kpis,
    }


# ============================================================
# STANDALONE TEST
# ============================================================

if __name__ == "__main__":
    settings = json.load(open(_DIR / "settings.json"))

    # Existing ops only (debug mode)
    base_rev = EXISTING_K_REV + EXISTING_J_REV + EXISTING_E_REV + EXISTING_L_REV
    base_exp = base_rev * EXISTING_EXP_RATIO
    g = DEFAULT_GROWTH
    rev = [base_rev * (1 + g)**i for i in range(N_YEARS)]
    exp = [base_exp * (1 + g)**i for i in range(N_YEARS)]

    model = run_full_model(rev, exp, settings)
    csv_path = write_csv(model)

    print(f"Output: {csv_path}\n")
    print("Year   OpInc      DistCash   TaxLiab    TaxCash    T0      T1      PEDD    T2")
    for i, y in enumerate(YEARS):
        m = model
        print(f"{y}  {m['op_inc'][i]/1e3:>8.0f}K  {m['distrib_cash'][i]/1e3:>8.0f}K  "
              f"{m['tax_liab'][i]/1e3:>7.0f}K  {m['tax_cash'][i]/1e3:>7.0f}K  "
              f"{m['tier0_actual'][i]/1e3:>5.0f}K  {m['tier1_actual'][i]/1e3:>5.0f}K  "
              f"{m['pedd_payments'][i]/1e3:>5.0f}K  {m['tier2'][i]/1e3:>5.0f}K")

    print(f"\nEB PEDD Grant: +{model['pedd_grant']:.1f}%")
    print("\nOwnership:")
    for o in model["ownership"]:
        print(f"  {o['year']}: JJB {o['JJB']:.1f}%  EB {o['EB']:.1f}%  JS {o['JS']:.1f}%")

    print("\nPartner Totals:")
    for p in ["EB", "JS", "JJB"]:
        totals = model["partners"][p]["total"]
        print(f"  {p}: {', '.join(f'{t/1e3:.0f}K' for t in totals)}")

    # Verify FCL against Excel
    print("\nFCL Schedule (verify against Excel):")
    for i, y in enumerate(YEARS):
        d = model["loans"]["data"]["FCL (Lettuce)"][i]
        if d["interest"] > 0:
            print(f"  {y}: Int {d['interest']:>10,.0f}  Prin {d['principal']:>10,.0f}  Bal {d['end_bal']:>10,.0f}")
