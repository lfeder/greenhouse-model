"""
Financial utilities: loan schedules, tax calculations, depreciation.
"""
from load import (
    YEARS, N_YEARS, LOAN_DEFS, PAIDOFF_2026,
    FED_BRACKETS, HI_BRACKETS, FED_NOL,
    DIST_BASE,
)


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
    nol_used = 0
    nol_generated = 0
    if eb_fed >= 0:
        # Profitable year: use NOL to reduce taxable income
        nol_used = min(nol_remaining, eb_fed)
        eb_net_fed = eb_fed - nol_used
    else:
        # Loss year: generate new NOL carryforward
        nol_generated = -eb_fed
        eb_net_fed = 0
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
        "nol_used": nol_used, "nol_generated": nol_generated,
        "nol_remaining": nol_remaining - nol_used + nol_generated,
        "fed_taxable": fed_taxable, "hi_taxable": hi_taxable,
    }

def calc_tax_cash_timing(liabilities):
    """Convert annual tax liability → cash payment timing with component breakdown.

    2026: $0 (no 2025 liability, no safe harbor)
    2027: 2026 final + 2 quarterly estimates for 2027
    2028+: Q4 prior est + settlement + Q1-Q3 current estimates

    Returns (totals, details) where details[i] has the components.
    """
    totals = []
    details = []
    for i, year in enumerate(YEARS):
        prior = liabilities[i - 1] if i > 0 else 0
        two_prior = liabilities[i - 2] if i > 1 else 0
        if year == 2026:
            totals.append(0)
            details.append({"note": "No 2025 liability", "total": 0})
        elif year == 2027:
            final_26 = liabilities[0]
            q_est = liabilities[0] / 4
            total = final_26 + q_est * 3  # 4/15 final + Q1(4/15) + Q2(6/15) + Q3(9/15)
            totals.append(total)
            details.append({"final_prior": final_26, "q_est": q_est, "n_quarters": 3, "total": total})
        else:
            q4 = two_prior / 4
            settle = max(0, prior - two_prior)
            cur_est = prior / 4 * 3
            total = q4 + settle + cur_est
            totals.append(total)
            details.append({"q4_prior_est": q4, "settlement": settle, "cur_est_3q": cur_est, "total": total})
    return totals, details


def compute_depreciation(crops, settings):
    s = settings
    exist_fed = [s["existFedAnnual"] if i < s["existFedYearsLeft"] else 0 for i in range(N_YEARS)]
    exist_state = [s["existStateAnnual"] if YEARS[i] <= s["existStateEndYear"] else 0 for i in range(N_YEARS)]
    bonus_pct = s["fedBonusPct"] / 100
    state_life = s["stateUsefulLife"]

    fed = list(exist_fed)
    state = list(exist_state)
    detail = [{"label": "Existing", "fed": list(exist_fed), "state": list(exist_state)}]

    for crop in crops:
        pis = crop["end_year"]
        capex = crop["capex"]
        bonus = capex * bonus_pct
        sl_fed = (capex - bonus) / 20 if bonus_pct < 1 else 0
        sl_state = capex / state_life
        cf = [0] * N_YEARS
        cs = [0] * N_YEARS

        for i, y in enumerate(YEARS):
            if y == pis:
                fed[i] += bonus
                cf[i] += bonus
            if y >= pis and sl_fed > 0:
                fed[i] += sl_fed
                cf[i] += sl_fed
            if pis <= y < pis + state_life:
                state[i] += sl_state
                cs[i] += sl_state

        detail.append({"label": crop.get("label", f"Phase {pis}"), "capex": capex, "fed": cf, "state": cs})

    return {"fed": fed, "state": state, "detail": detail}
