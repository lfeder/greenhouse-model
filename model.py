"""
Greenhouse Expansion Financial Model
All calculations in one readable Python file.
Constants loaded from constants.json (shared with HTML).
"""

import json
from pathlib import Path

# ============================================================
# 1. LOAD CONSTANTS FROM SHARED JSON
# ============================================================

_CONST_PATH = Path(__file__).parent / "constants.json"
with open(_CONST_PATH) as f:
    C = json.load(f)

YEARS = C["years"]

# Existing ops
BASE_REV_PER_AC = C["existing_ops"]["base_rev_per_ac"]
EXISTING_KJ_REV = C["existing_ops"]["kj_rev"]
EXISTING_L_REV = C["existing_ops"]["lettuce_rev"]
EXISTING_EXP_RATIO = C["existing_ops"]["exp_ratio"]

# Existing debt
EXISTING_DEBT_LETTUCE = C["existing_debt"]["lettuce_annual"]
EXISTING_DEBT_LETTUCE_END = C["existing_debt"]["lettuce_end_year"]
EXISTING_DEBT_LAND = C["existing_debt"]["land_annual"]
EXISTING_DEBT_LAND_END = C["existing_debt"]["land_end_year"]

# Tiers
TIER0 = C["tiers"]["tier0"]
TIER1_SCHEDULE = {int(k): v for k, v in C["tiers"]["tier1"].items() if k != "default"}
TIER1_DEFAULT = C["tiers"]["tier1"]["default"]

# Ownership
OWNERSHIP_BASE = {int(k): v for k, v in C["ownership_base"].items()}
VESTING_DELTAS = C["vesting_deltas"]

# JS buyout
JS_BUYOUT_VAL = {int(k): v for k, v in C["js_buyout"]["valuations"].items()}
JS_ANNUAL_LOSS = C["js_buyout"]["js_annual_loss"]
EB_ANNUAL_GAIN = C["js_buyout"]["eb_annual_gain"]

# PE/DD instruments
PEDD_INSTRUMENTS = C["pedd_instruments"]

# Tax
FED_NOL = C["tax"]["fed_nol"]
FED_BRACKETS = [(b[0] if b[0] is not None else float("inf"), b[1]) for b in C["tax"]["fed_brackets"]]
HI_BRACKETS = [(b[0] if b[0] is not None else float("inf"), b[1]) for b in C["tax"]["hi_brackets"]]

# Loans
_fcl = C["loans"]["fcl"]["schedule"]
FCL_SCHEDULE = {int(k): v for k, v in _fcl.items()}
_jjbdp = C["loans"]["jjbdp"]["schedule"]
JJBDP_SCHEDULE = {int(k): v for k, v in _jjbdp.items()}
BIPAH = C["loans"]["bipah"]
PAIDOFF_2026 = C["loans"]["paidoff_2026"]

# Debug defaults
DEBUG_DEFAULTS = C["debug_defaults"]


# ============================================================
# 2. LOAN CALCULATIONS
# ============================================================

def bipah_monthly_pmt():
    r = BIPAH["rate"] / 12
    n = BIPAH["term_months"]
    return BIPAH["orig_bal"] * (r * (1 + r)**n) / ((1 + r)**n - 1)

def bipah_annual_schedule(year):
    """BIPAH: started Jan 2025, monthly payments."""
    month_start = (year - BIPAH["start_year"]) * 12
    r = BIPAH["rate"] / 12
    pmt = bipah_monthly_pmt()
    # Compute balance at month_start
    bal = BIPAH["orig_bal"]
    for _ in range(max(0, month_start)):
        bal -= (pmt - bal * r)
        if bal <= 0:
            return {"interest": 0, "principal": 0, "end_bal": 0}
    total_int = total_prin = 0
    for m in range(12):
        if month_start + m < 0 or bal <= 0 or month_start + m >= BIPAH["term_months"]:
            break
        int_pmt = bal * r
        prin_pmt = min(pmt - int_pmt, bal)
        total_int += int_pmt
        total_prin += prin_pmt
        bal -= prin_pmt
    return {"interest": total_int, "principal": total_prin, "end_bal": max(0, bal)}

def compute_loan_schedules():
    """Returns per-year interest, principal, end_bal for all loans."""
    loan_data = {}
    for name, sched in [("FCL", FCL_SCHEDULE), ("JJB DP", JJBDP_SCHEDULE)]:
        loan_data[name] = []
        for y in YEARS:
            s = sched.get(y, {"interest": 0, "principal": 0, "end_bal": 0})
            loan_data[name].append(s)
    # BIPAH computed
    loan_data["BIPAH"] = [bipah_annual_schedule(y) for y in YEARS]

    # Totals
    total_int = []
    total_prin = []
    for i, y in enumerate(YEARS):
        ti = sum(loan_data[n][i]["interest"] for n in loan_data)
        tp = sum(loan_data[n][i]["principal"] for n in loan_data)
        if i == 0:  # paid-off residuals
            ti += PAIDOFF_2026["interest"]
            tp += PAIDOFF_2026["principal"]
        total_int.append(ti)
        total_prin.append(tp)
    total_ds = [ti + tp for ti, tp in zip(total_int, total_prin)]
    return {"loan_data": loan_data, "total_int": total_int, "total_prin": total_prin, "total_ds": total_ds}


# ============================================================
# 3. TAX CALCULATIONS
# ============================================================

def calc_tax_from_brackets(taxable_income, brackets):
    if taxable_income <= 0:
        return 0
    tax = 0
    prev = 0
    for limit, rate in brackets:
        slice_amt = min(taxable_income, limit) - prev
        if slice_amt <= 0:
            break
        tax += slice_amt * rate
        prev = limit
    return tax

def calc_effective_rate(taxable_income, brackets):
    if taxable_income <= 0:
        return 0
    return calc_tax_from_brackets(taxable_income, brackets) / taxable_income

def calc_tax_dist_for_year(taxable_inc, fed_dep, state_dep, ownership, fed_nol_remaining):
    """Calculate entity-level tax distribution for one year.

    Steps:
    1. Fed taxable = operating taxable - fed depreciation
    2. EB's share = fed_taxable × EB%
    3. Apply EB's NOL carryforward
    4. EB's tax = bracket calc on net taxable
    5. Gross up: entity dist = EB tax / EB%
    6. Same for Hawaii with state depreciation
    """
    # Federal
    fed_taxable = taxable_inc - fed_dep
    eb_fed_taxable = fed_taxable * ownership["EB"] / 100
    nol_used = min(fed_nol_remaining, max(0, eb_fed_taxable))
    eb_net_fed = max(0, eb_fed_taxable - nol_used)
    fed_tax = calc_tax_from_brackets(eb_net_fed, FED_BRACKETS)
    fed_dist = fed_tax / (ownership["EB"] / 100) if ownership["EB"] > 0 else 0

    # Hawaii
    hi_taxable = taxable_inc - state_dep
    eb_hi_taxable = max(0, hi_taxable * ownership["EB"] / 100)
    hi_tax = calc_tax_from_brackets(eb_hi_taxable, HI_BRACKETS)
    hi_dist = hi_tax / (ownership["EB"] / 100) if ownership["EB"] > 0 else 0

    return {
        "fed_dist": fed_dist, "hi_dist": hi_dist,
        "fed_tax": fed_tax, "hi_tax": hi_tax,
        "eb_net_fed": eb_net_fed, "eb_hi_taxable": eb_hi_taxable,
        "nol_used": nol_used, "fed_taxable": fed_taxable, "hi_taxable": hi_taxable,
        "eb_fed_taxable": eb_fed_taxable,
        "fed_nol_remaining": fed_nol_remaining - nol_used,
    }

def calc_quarterly_tax_timing(total_tax_liab):
    """Convert annual tax liability to cash distribution timing.

    2026: $0 (no 2025 liability)
    2027: 2026 final + 2 quarterly estimates
    2028+: Q4 prior est + settlement + 3 quarterly estimates
    """
    result = []
    for i, year in enumerate(YEARS):
        prior_liab = total_tax_liab[i - 1] if i > 0 else 0
        two_prior_liab = total_tax_liab[i - 2] if i > 1 else 0

        if year == 2026:
            result.append(0)
        elif year == 2027:
            final_26 = total_tax_liab[0]
            q_est = total_tax_liab[0] / 4
            result.append(final_26 + q_est * 2)
        else:
            q4_prior = two_prior_liab / 4
            settle = max(0, prior_liab - two_prior_liab)
            cur_est = prior_liab / 4 * 3
            result.append(q4_prior + settle + cur_est)
    return result


# ============================================================
# 4. OWNERSHIP & DILUTION
# ============================================================

def get_ownership(year):
    if year <= 2029 and year in OWNERSHIP_BASE:
        return dict(OWNERSHIP_BASE[year])
    return dict(OWNERSHIP_BASE[2029])

def compute_ownership_trajectory(equity_by_year, biz_values, js_transfer_pct=0):
    """Compute ownership % year by year with vesting + dilution + transfers.

    - Vesting deltas applied 2027-2029 (contractual)
    - JJB equity investment dilutes at market value
    - Post-2029: JS→EB transfer
    - Percentages lock after each event
    """
    jjb = 42.5
    eb = 26.0
    js = 31.5
    result = []

    for i, year in enumerate(YEARS):
        # Vesting deltas (2027-2029)
        if year in (2027, 2028, 2029):
            jjb += VESTING_DELTAS["JJB"]
            eb += VESTING_DELTAS["EB"]
            js += VESTING_DELTAS["JS"]

        # Post-2029 JS→EB transfer
        if year > 2029 and js_transfer_pct > 0 and js > js_transfer_pct:
            js -= js_transfer_pct
            eb += js_transfer_pct

        # JJB equity investment → dilution at market value
        eq_deploy = equity_by_year.get(year, 0)
        biz_val = biz_values[i] if i < len(biz_values) else 0
        if eq_deploy > 0 and biz_val > 0:
            post_money = biz_val + eq_deploy
            jjb = (jjb / 100 * biz_val + eq_deploy) / post_money * 100
            eb = eb / 100 * biz_val / post_money * 100
            js = js / 100 * biz_val / post_money * 100

        result.append({"year": year, "JJB": jjb, "EB": eb, "JS": js})
    return result


# ============================================================
# 5. PE/DD WATERFALL
# ============================================================

def get_tier1(year):
    return TIER1_SCHEDULE.get(year, TIER1_DEFAULT)

def run_waterfall(distrib_cash, tax_dist, t_bill_rate=0.065):
    """Run the full distribution waterfall.

    Order: Tax → Tier 0 → Tier 1 → PE/DD (PE3→PE1→PE2→DD) → Tier 2
    T1 shortfall becomes new deferred distribution.
    """
    pedd_bal = []
    for p in PEDD_INSTRUMENTS:
        rate = t_bill_rate + p.get("rate_spread", 0) if p.get("rate_type") == "tbill" else p.get("rate", 0)
        pedd_bal.append({
            "name": p["name"], "label": p["label"],
            "remaining": p["balance"], "int_owed": p.get("accrued_int", 0),
            "rate": rate,
        })
    # T1 shortfall deferred distribution
    pedd_bal.append({"name": "T1Short", "label": "T1 Shortfall DD", "remaining": 0, "int_owed": 0, "rate": 0})

    detail = {pe["name"]: {"prin_paid": [], "int_paid": [], "end_bal": [], "int_owed": []} for pe in pedd_bal}
    pedd_payments = []
    tier0_actual = []
    tier1_actual = []
    tier1_shortfall = []
    tier2 = []
    cum_pedd = 0

    for i, year in enumerate(YEARS):
        tier1_full = get_tier1(year)
        remaining = distrib_cash[i] - tax_dist[i]

        # Tier 0
        t0 = min(TIER0, max(0, remaining))
        remaining -= t0
        tier0_actual.append(t0)

        # Tier 1
        t1 = min(tier1_full, max(0, remaining))
        remaining -= t1
        tier1_actual.append(t1)
        shortfall = tier1_full - t1
        tier1_shortfall.append(shortfall)
        if shortfall > 0:
            pedd_bal[-1]["remaining"] += shortfall  # add to T1Short

        # Accrue interest at start of year
        for pe in pedd_bal:
            if pe["remaining"] > 0 and pe["rate"] > 0:
                pe["int_owed"] += pe["remaining"] * pe["rate"]

        # PE/DD payments in order
        pedd_this_year = 0
        year_prin = {pe["name"]: 0 for pe in pedd_bal}
        year_int = {pe["name"]: 0 for pe in pedd_bal}

        for pe in pedd_bal:
            if remaining <= 0:
                break
            # Principal first
            if pe["remaining"] > 0:
                prin_pay = min(remaining, pe["remaining"])
                pe["remaining"] -= prin_pay
                remaining -= prin_pay
                pedd_this_year += prin_pay
                cum_pedd += prin_pay
                year_prin[pe["name"]] += prin_pay
            # Interest after principal done
            if pe["remaining"] <= 0 and pe["int_owed"] > 0 and remaining > 0:
                int_pay = min(remaining, pe["int_owed"])
                pe["int_owed"] -= int_pay
                remaining -= int_pay
                pedd_this_year += int_pay
                cum_pedd += int_pay
                year_int[pe["name"]] += int_pay

        for pe in pedd_bal:
            detail[pe["name"]]["prin_paid"].append(year_prin[pe["name"]])
            detail[pe["name"]]["int_paid"].append(year_int[pe["name"]])
            detail[pe["name"]]["end_bal"].append(pe["remaining"])
            detail[pe["name"]]["int_owed"].append(pe["int_owed"])

        pedd_payments.append(pedd_this_year)
        tier2.append(max(0, remaining))

    return {
        "detail": detail,
        "pedd_payments": pedd_payments,
        "tier0_actual": tier0_actual,
        "tier1_actual": tier1_actual,
        "tier1_shortfall": tier1_shortfall,
        "tier2": tier2,
        "cum_pedd": cum_pedd,
    }


# ============================================================
# 6. DEPRECIATION
# ============================================================

def compute_depreciation(crops, exist_fed_years=8, exist_fed_annual=200_000,
                         exist_state_end=2029, exist_state_annual=1_000_000,
                         fed_bonus_pct=1.0, state_useful_life=10):
    """Compute federal and state depreciation schedules."""
    fed = [exist_fed_annual if i < exist_fed_years else 0 for i in range(len(YEARS))]
    state = [exist_state_annual if YEARS[i] <= exist_state_end else 0 for i in range(len(YEARS))]

    for crop in crops:
        pis_year = crop["end_year"]  # placed-in-service year
        capex = crop["capex"]
        bonus = capex * fed_bonus_pct
        remain = capex - bonus
        sl_annual = remain / 20 if remain > 0 else 0
        state_sl = capex / state_useful_life

        for i, y in enumerate(YEARS):
            if y == pis_year:
                fed[i] += bonus
            if y >= pis_year and sl_annual > 0:
                fed[i] += sl_annual
            if pis_year <= y < pis_year + state_useful_life:
                state[i] += state_sl

    return {"fed": fed, "state": state}


# ============================================================
# 7. CROP REVENUE & EXPENSE MODEL
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

def compute_crop_revenue(crop, rev_inflation, cost_inflation):
    """Compute annual revenue and expense for a crop."""
    end_q = crop["end_q"]
    end_year = end_q // 10
    end_qtr = end_q % 10
    rev_list = []
    exp_list = []

    for y in YEARS:
        if y < end_year:
            rev_list.append(0)
            exp_list.append(0)
            continue

        if y == end_year:
            frac = (4 - end_qtr + 1) / 4
            prod_yr = 1
        else:
            frac = 1
            prod_yr = y - end_year + 1

        ramp = ramp_factor(prod_yr, crop["ramp_years"])
        n = y - 2026
        rev = crop["base_rev"] * ramp * frac * (1 + rev_inflation / 100) ** n
        exp = crop["base_exp"] * ramp * frac * (1 + cost_inflation / 100) ** n
        rev_list.append(rev)
        exp_list.append(exp)

    return rev_list, exp_list


# ============================================================
# 8. IRR (Newton's method)
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
# 9. FULL MODEL
# ============================================================

def run_full_model(rev, exp, loan_int, loan_prin, total_ds, fed_dep, state_dep, t_bill_rate=0.065):
    """Run the complete financial model from revenue through waterfall.

    Returns all intermediate calculations for display.
    """
    op_inc = [r - e for r, e in zip(rev, exp)]
    capex_res = [r * 0.02 for r in rev]
    taxable_inc = [o - li for o, li in zip(op_inc, loan_int)]

    # Tax liability per year
    fed_nol_remaining = FED_NOL
    total_tax_liab = []
    tax_detail = []
    for i in range(len(YEARS)):
        own = get_ownership(YEARS[i])
        td = calc_tax_dist_for_year(taxable_inc[i], fed_dep[i], state_dep[i], own, fed_nol_remaining)
        fed_nol_remaining = td["fed_nol_remaining"]
        total_tax_liab.append(td["fed_dist"] + td["hi_dist"])
        tax_detail.append(td)

    # Quarterly timing
    tax_cash_dist = calc_quarterly_tax_timing(total_tax_liab)

    # Distributable cash
    distrib_cash = [o - ds - cr for o, ds, cr in zip(op_inc, total_ds, capex_res)]

    # Waterfall
    wf = run_waterfall(distrib_cash, tax_cash_dist, t_bill_rate)

    return {
        "years": YEARS,
        "rev": rev, "exp": exp,
        "op_inc": op_inc, "capex_res": capex_res,
        "taxable_inc": taxable_inc,
        "total_tax_liab": total_tax_liab,
        "tax_cash_dist": tax_cash_dist,
        "distrib_cash": distrib_cash,
        "tax_detail": tax_detail,
        "loan_int": loan_int, "loan_prin": loan_prin, "total_ds": total_ds,
        "fed_dep": fed_dep, "state_dep": state_dep,
        **wf,
    }


# ============================================================
# 10. PARTNER CASH
# ============================================================

def compute_partner_cash(model, ownership_trajectory):
    """Compute per-partner annual cash distributions."""
    partners = {}
    for partner in ["EB", "JS", "JJB"]:
        rows = {}
        own_arr = [o[partner] for o in ownership_trajectory]

        tax_share = [model["tax_cash_dist"][i] * own_arr[i] / 100 for i in range(len(YEARS))]
        rows["tax_dist"] = tax_share

        if partner == "EB":
            rows["tier_0"] = list(model["tier0_actual"])

        t1_share = [model["tier1_actual"][i] * own_arr[i] / 100 for i in range(len(YEARS))]
        rows["tier_1"] = t1_share

        if partner in ("JS", "JJB"):
            rows["pedd"] = [p * 0.5 for p in model["pedd_payments"]]

        t2_share = [model["tier2"][i] * own_arr[i] / 100 for i in range(len(YEARS))]
        rows["tier_2"] = t2_share

        if partner in ("JS", "JJB"):
            eq_pay = []
            for y in YEARS:
                if y in JS_BUYOUT_VAL:
                    pmt = JS_BUYOUT_VAL[y] * (JS_ANNUAL_LOSS - EB_ANNUAL_GAIN / 2)
                    eq_pay.append(pmt if partner == "JS" else -pmt)
                else:
                    eq_pay.append(0)
            rows["equity_buy_sell"] = eq_pay

        total = []
        for i in range(len(YEARS)):
            t = tax_share[i] + t1_share[i] + t2_share[i]
            if partner == "EB":
                t += model["tier0_actual"][i]
            if partner in ("JS", "JJB"):
                t += model["pedd_payments"][i] * 0.5
            if YEARS[i] in JS_BUYOUT_VAL:
                pmt = JS_BUYOUT_VAL[YEARS[i]] * (JS_ANNUAL_LOSS - EB_ANNUAL_GAIN / 2)
                if partner == "JS": t += pmt
                elif partner == "JJB": t -= pmt
            total.append(t)
        rows["total"] = total

        partners[partner] = rows

    # EB equity grant
    grant_pct = min(2.0, (model["cum_pedd"] // 500_000) * 0.5)
    return partners, grant_pct


# ============================================================
# QUICK TEST
# ============================================================
if __name__ == "__main__":
    # Debug mode: existing ops only
    rev = [DEBUG_DEFAULTS["rev_2026"] * (1 + DEBUG_DEFAULTS["growth"])**i for i in range(10)]
    exp = [DEBUG_DEFAULTS["exp_2026"] * (1 + DEBUG_DEFAULTS["growth"])**i for i in range(10)]

    loans = compute_loan_schedules()
    dep = compute_depreciation(crops=[])
    model = run_full_model(rev, exp, loans["total_int"], loans["total_prin"], loans["total_ds"], dep["fed"], dep["state"])

    print("Year   OpInc      DistCash   TaxLiab    TaxCash    T0      T1      PEDD    T2")
    for i, y in enumerate(YEARS):
        print(f"{y}  {model['op_inc'][i]/1e3:>8.0f}K  {model['distrib_cash'][i]/1e3:>8.0f}K  "
              f"{model['total_tax_liab'][i]/1e3:>7.0f}K  {model['tax_cash_dist'][i]/1e3:>7.0f}K  "
              f"{model['tier0_actual'][i]/1e3:>5.0f}K  {model['tier1_actual'][i]/1e3:>5.0f}K  "
              f"{model['pedd_payments'][i]/1e3:>5.0f}K  {model['tier2'][i]/1e3:>5.0f}K")

    own = compute_ownership_trajectory({}, [o * 4 for o in model["op_inc"]])
    partners, grant = compute_partner_cash(model, own)
    print(f"\nEB Grant: +{grant:.1f}%")
    for p in ["EB", "JS", "JJB"]:
        totals = partners[p]["total"]
        print(f"{p}: {', '.join(f'{t/1e3:.0f}K' for t in totals)}")
