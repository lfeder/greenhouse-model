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


def get_tier1(year):
    return TIER1.get(year, TIER1_DEFAULT)


def build_equity_draws(crop_data):
    """Build per-party annual equity draws from crop data.
    Returns {year: {"jjb": $, "3p": $}} for use in compute_ownership.
    """
    draws = {}
    for crop in crop_data:
        if crop.get("is_buy"):
            continue
        start_year = crop["start_q"] // 10
        if start_year not in draws:
            draws[start_year] = {"jjb": 0, "3p": 0}
        draws[start_year]["jjb"] += crop.get("jjb_equity", 0)
        draws[start_year]["3p"] += crop.get("third_party_equity", 0)
        sc = crop.get("startup_capital", 0)
        sc_yr = crop.get("startup_year")
        if sc and sc_yr:
            if sc_yr not in draws:
                draws[sc_yr] = {"jjb": 0, "3p": 0}
            draws[sc_yr]["jjb"] += sc
    return draws


def compute_ownership(settings, cum_pedd_by_year, equity_draws=None, biz_values=None):
    """Compute ownership % for each year with detailed activity log.

    Drivers (in order each year):
    1. Vesting: JJB buys from JS
    2. JJB equity draw → dilutes EB/JS at buyinValuation
    3. 3P equity draw → dilutes EB/JJB at thirdPartyValuation
    4. SBIC kicker (one-time, on first equity deployment year)
    5. EB grant (PEDD)
    6. EB grant (expansion promote)
    """
    jjb = DIST_BASE["JJB"]
    eb = DIST_BASE["EB"]
    js = DIST_BASE["JS"]
    sbic = 0.0
    tp = 0.0

    buyout_years = JS_BUYOUT["years"]
    buyout_pct = JS_BUYOUT["pct_per_year"]

    is_expansion = not settings["debug"]
    fin_mode = settings["financingMode"]
    sbic_kicker = settings["sbicKicker"] if is_expansion and settings["_has_sbic"] and fin_mode in ("sbic", "sbic_3p") else 0
    tp_valuation = settings["thirdPartyValuation"]
    tp_buys_js = settings["thirdPartyBuysJS"] and fin_mode in ("3p", "sbic_3p") and is_expansion
    exp_grant_pct = settings["ebExpansionGrantPct"] if is_expansion else 0
    exp_grant_start = settings["ebExpansionGrantStartYear"]
    exp_grant_end = settings["ebExpansionGrantEndYear"]
    buyin_val = settings["buyinValuation"]

    equity_draws = equity_draws or {}
    biz_values = biz_values or [0] * N_YEARS

    pedd_grant_earned = 0
    sbic_kicker_granted = False
    tp_js_bought = False
    result = []       # [{year, JJB, EB, JS, SBIC, TP}, ...]
    detail_rows = []  # [[label, y0, y1, ...], ...] for display

    # Prepare detail tracking
    start_jjb, start_eb, start_js, start_sbic, start_tp = [], [], [], [], []
    buyout_row = []
    jjb_capital_row, jjb_equity_row = [], []
    tp_capital_row, tp_equity_row = [], []
    sbic_row = []
    pedd_grant_row, exp_grant_row = [], []
    end_jjb, end_eb, end_js, end_sbic, end_tp = [], [], [], [], []

    for i, year in enumerate(YEARS):
        start_jjb.append(jjb); start_eb.append(eb); start_js.append(js)
        start_sbic.append(sbic); start_tp.append(tp)
        draws = equity_draws.get(year, {})

        # 1. Vesting: JJB buys from JS
        buy = 0
        if year in buyout_years and js > buyout_pct:
            js -= buyout_pct
            jjb += buyout_pct
            buy = buyout_pct
        buyout_row.append(buy)

        # 2. JJB equity investment at buyinValuation
        jjb_amount = draws.get("jjb", 0)
        jjb_new_pct = 0
        if jjb_amount > 0 and buyin_val > 0:
            post_money = buyin_val + jjb_amount
            new_pct = jjb_amount / post_money * 100
            scale = 1 - new_pct / 100
            eb *= scale
            js *= scale
            sbic *= scale
            tp *= scale
            jjb = jjb * scale + new_pct
            jjb_new_pct = new_pct
        jjb_capital_row.append(jjb_amount)
        jjb_equity_row.append(jjb_new_pct)

        # 3. 3P equity investment at thirdPartyValuation
        tp_amount = draws.get("3p", 0)
        tp_new_pct = 0
        if tp_amount > 0 and tp_valuation > 0:
            # If 3P buys JS: on first 3P draw, accelerate JJB buyout then 3P buys JS stake
            if tp_buys_js and not tp_js_bought and js > 0:
                js_to_jjb = js - 22.5
                if js_to_jjb > 0:
                    js -= js_to_jjb
                    jjb += js_to_jjb
                tp += js
                js = 0
                tp_js_bought = True

            post_money = tp_valuation + tp_amount
            new_pct = tp_amount / post_money * 100
            scale = 1 - new_pct / 100
            eb *= scale
            jjb *= scale
            js *= scale
            sbic *= scale
            tp = tp * scale + new_pct
            tp_new_pct = new_pct
        tp_capital_row.append(tp_amount)
        tp_equity_row.append(tp_new_pct)

        # 4. SBIC kicker: one-time % grant on first year with any equity draw
        sbic_grant = 0
        any_draw = jjb_amount > 0 or tp_amount > 0
        if sbic_kicker > 0 and any_draw and not sbic_kicker_granted:
            sbic_grant = sbic_kicker
            others = jjb + eb + js
            if others > 0:
                jjb -= sbic_grant * (jjb / others)
                eb -= sbic_grant * (eb / others)
                js -= sbic_grant * (js / others)
            sbic += sbic_grant
            sbic_kicker_granted = True
        sbic_row.append(sbic_grant)

        # 5. EB grant (PEDD)
        cum_pedd = cum_pedd_by_year[i] if i < len(cum_pedd_by_year) else 0
        target_grant = min(EB_GRANT_PEDD["max_pct"], int(cum_pedd / 500_000) * EB_GRANT_PEDD["per_500k"])
        new_grant = target_grant - pedd_grant_earned
        if new_grant > 0:
            eb += new_grant
            js -= new_grant * 0.5   # 50% from JS
            jjb -= new_grant * 0.5  # 50% from JJB
            pedd_grant_earned = target_grant
        pedd_grant_row.append(new_grant)

        # 6. EB grant (expansion promote)
        exp_g = 0
        if exp_grant_pct > 0 and exp_grant_start <= year <= exp_grant_end:
            eb += exp_grant_pct
            js_share = js / (js + jjb) if (js + jjb) > 0 else 0.5
            js -= exp_grant_pct * js_share
            jjb -= exp_grant_pct * (1 - js_share)
            exp_g = exp_grant_pct
        exp_grant_row.append(exp_g)

        end_jjb.append(jjb); end_eb.append(eb); end_js.append(js)
        end_sbic.append(sbic); end_tp.append(tp)
        result.append({"year": year, "JJB": round(jjb, 2), "EB": round(eb, 2), "JS": round(js, 2), "SBIC": round(sbic, 2), "TP": round(tp, 2)})

    # Build detail rows for display
    detail_rows = [
        ["Starting JJB %"] + [round(v, 1) for v in start_jjb],
        ["Starting EB %"] + [round(v, 1) for v in start_eb],
        ["Starting JS %"] + [round(v, 1) for v in start_js],
        ["Starting SBIC %"] + [round(v, 1) for v in start_sbic],
        ["Starting 3P %"] + [round(v, 1) for v in start_tp],
        ["JJB buys from JS"] + [round(v, 1) for v in buyout_row],
        ["JJB Capital ($)"] + [round(v) for v in jjb_capital_row],
        ["JJB Equity Issued %"] + [round(v, 1) for v in jjb_equity_row],
        ["3P Capital ($)"] + [round(v) for v in tp_capital_row],
        ["3P Equity Issued %"] + [round(v, 1) for v in tp_equity_row],
        ["SBIC Kicker %"] + [round(v, 1) for v in sbic_row],
        ["EB grant (PEDD)"] + [round(v, 1) for v in pedd_grant_row],
        ["EB promote"] + [round(v, 1) for v in exp_grant_row],
        ["Ending JJB %"] + [round(v, 1) for v in end_jjb],
        ["Ending EB %"] + [round(v, 1) for v in end_eb],
        ["Ending JS %"] + [round(v, 1) for v in end_js],
        ["Ending SBIC %"] + [round(v, 1) for v in end_sbic],
        ["Ending 3P %"] + [round(v, 1) for v in end_tp],
    ]

    return result, detail_rows


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

    pedd_done = False  # True once all PE/DD fully repaid

    for i, year in enumerate(YEARS):
        t1_full = get_tier1(year)
        remaining = distrib_cash[i] - tax_dist[i]

        # Tier 0
        t0 = min(TIER0, max(0, remaining))
        remaining -= t0
        tier0_actual.append(t0)

        if pedd_done:
            # After PE/DD repaid: all remaining goes to T1 (no T2 split)
            t1 = max(0, remaining)
            remaining = 0
            tier1_actual.append(t1)
            tier1_shortfall.append(0)
            for inst in instruments:
                detail[inst["name"]]["prin_paid"].append(0)
                detail[inst["name"]]["int_paid"].append(0)
                detail[inst["name"]]["end_bal"].append(0)
                detail[inst["name"]]["int_owed"].append(0)
            pedd_payments.append(0)
            tier2.append(0)
            cum_pedd_by_year.append(cum_pedd)
            continue

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

        # Check if PE/DD is now fully repaid — takes effect next year
        if all(inst["remaining"] <= 0 and inst["int_owed"] <= 0 for inst in instruments):
            pedd_done = True

    return {
        "detail": detail, "pedd_payments": pedd_payments,
        "tier0_actual": tier0_actual, "tier1_actual": tier1_actual,
        "tier1_shortfall": tier1_shortfall, "tier2": tier2,
        "cum_pedd": cum_pedd, "cum_pedd_by_year": cum_pedd_by_year,
    }


def compute_partner_cash(model, ownership):
    """Per-partner annual distributions."""
    partners = {}
    for p in ["EB", "JS", "JJB", "SBIC", "TP"]:
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

        # Total (ex-tax: excludes tax distributions)
        total = []
        for i in range(N_YEARS):
            t = rows["tier_1"][i] + rows["tier_2"][i]
            if p == "EB": t += rows["tier_0"][i]
            if p in ("JS", "JJB"): t += rows["pedd"][i] + rows["equity_buy_sell"][i]
            total.append(t)
        rows["total"] = total
        partners[p] = rows

    return partners


def compute_crop_irrs(crop_data, s):
    """Compute per-crop IRR and expansion debt service."""
    from load import ramp_crop_rev_exp

    ri = s["revInflation"]
    ci = s["costInflation"]
    bank_rate = s["bankRate"] / 100
    bank_term = s["bankTerm"]
    sbic_rate = s["sbicRate"] / 100
    sbic_term = s["sbicTerm"]

    def _loan_schedule(loan_amt, rate, term, start_year, io_end_y):
        """Compute yearly int/prin/bal arrays for a single loan."""
        int_row, prin_row, bal_row = [], [], []
        bal = 0
        annual_ds = _annual_pmt(loan_amt, rate, term) if loan_amt > 0 else 0
        for i, y in enumerate(YEARS):
            if y == start_year:
                bal = loan_amt
            yr_int = yr_prin = 0
            if start_year <= y <= start_year + term - 1 and bal > 0:
                yr_int = bal * rate
                if y >= io_end_y:
                    yr_prin = min(annual_ds - yr_int, bal)
                    bal -= yr_prin
            int_row.append(yr_int)
            prin_row.append(yr_prin)
            bal_row.append(max(0, bal))
        return int_row, prin_row, bal_row

    # First pass: compute per-crop cashflows and collect infra cashflows
    crop_cfs_raw = {}   # key → [cfs]
    crop_rev_2035 = {}  # key → 2035 revenue (for pro-rata allocation)
    infra_cfs = [0.0] * N_YEARS  # combined infra cashflows
    infra_capex_total = 0
    expansion_int = [0.0] * N_YEARS
    expansion_prin = [0.0] * N_YEARS
    expansion_loan_detail = {}

    for crop in crop_data:
        cr, ce = ramp_crop_rev_exp(crop["base_rev"], crop["base_exp"], crop["end_q"], crop["ramp_years"], ri, ci)
        start_year = crop["start_q"] // 10
        end_year = crop["end_q"] // 10
        io_end_y = end_year + (1 if (crop["end_q"] % 10) > 2 else 0)

        # Capital stack per crop
        if crop.get("is_buy"):
            # T_BUY: bank only, capped by hard assets
            buy_loan_base = min(s["tomatoHardAssets"], crop["capex"])
            b_amt = buy_loan_base * s["targetLtv"] / 100
            s_amt = 0
            equity = crop["capex"] - b_amt
        else:
            b_amt = crop.get("bank_loan", 0)
            s_amt = crop.get("sbic_loan", 0)
            equity = crop.get("jjb_equity", crop["capex"] - b_amt - s_amt)

        # Compute schedules for each tranche
        b_int, b_prin, b_bal = _loan_schedule(b_amt, bank_rate, bank_term, start_year, io_end_y)
        s_int, s_prin, s_bal = _loan_schedule(s_amt, sbic_rate, sbic_term, start_year, io_end_y)

        # Combined per-crop cashflows
        cfs = []
        crop_int_row = [b_int[i] + s_int[i] for i in range(N_YEARS)]
        crop_prin_row = [b_prin[i] + s_prin[i] for i in range(N_YEARS)]
        crop_bal_row = [b_bal[i] + s_bal[i] for i in range(N_YEARS)]

        sc = crop.get("startup_capital", 0)
        sc_yr = crop.get("startup_year")
        for i, y in enumerate(YEARS):
            cf = cr[i] - ce[i]
            if sc and sc_yr and y == sc_yr:
                cf -= sc * (1 + ci / 100) ** (y - 2026)
            cf -= crop_int_row[i] + crop_prin_row[i]
            if not crop.get("is_buy"):
                expansion_int[i] += crop_int_row[i]
                expansion_prin[i] += crop_prin_row[i]
            if y == start_year:
                cf -= equity
            cfs.append(cf)

        expansion_loan_detail[crop["label"]] = {
            "loan_amt": b_amt + s_amt, "bank_amt": b_amt, "sbic_amt": s_amt,
            "rate": bank_rate, "sbic_rate": sbic_rate, "term": bank_term, "sbic_term": sbic_term,
            "interest": crop_int_row, "principal": crop_prin_row, "balance": crop_bal_row,
            "bank_interest": b_int, "bank_principal": b_prin, "bank_balance": b_bal,
            "sbic_interest": s_int, "sbic_principal": s_prin, "sbic_balance": s_bal,
        }

        if crop.get("is_infra"):
            for i in range(N_YEARS):
                infra_cfs[i] += cfs[i]
            infra_capex_total += crop["capex"]
            continue

        crop_cfs_raw[crop["key"]] = {
            "cfs": cfs, "crop": crop, "equity": equity + sc,
            "cr": cr, "ce": ce,
        }
        # 2035 revenue for pro-rata (last year)
        if not crop.get("is_buy"):
            crop_rev_2035[crop["key"]] = cr[-1]

    # Allocate infra costs pro-rata by 2035 revenue
    total_rev_2035 = sum(crop_rev_2035.values())
    crop_irrs = []
    for key, raw in crop_cfs_raw.items():
        crop = raw["crop"]
        cfs = list(raw["cfs"])
        equity = raw["equity"]

        # Add pro-rata infra share (not for T_BUY)
        infra_share = 0
        if not crop.get("is_buy") and total_rev_2035 > 0:
            pct = crop_rev_2035.get(key, 0) / total_rev_2035
            for i in range(N_YEARS):
                cfs[i] += infra_cfs[i] * pct
            infra_share = infra_capex_total * pct
            equity += infra_share * (1 - s["targetLtv"] / 100)

        irr = calc_irr(cfs) * 100 if any(c != 0 for c in cfs) else 0
        total_capex = crop["capex"] + infra_share
        base_op_inc = crop["base_rev"] - crop["base_exp"]
        unlev = (base_op_inc / total_capex * 100) if total_capex > 0 else 0

        crop_irrs.append({
            "key": crop["key"], "label": crop["label"],
            "capex": total_capex, "equity": equity,
            "irr": round(irr, 1), "unlev": round(unlev, 1),
            "unlev_detail": {"base_rev": round(crop["base_rev"]), "base_exp": round(crop["base_exp"]),
                             "op_inc": round(base_op_inc), "capex": round(total_capex)},
            "cashflows": [round(c) for c in cfs],
        })

    # Total IRR and unleveraged (built crops only, excludes TB)
    built_cfs = [0] * N_YEARS
    total_rev = total_exp = total_capex_built = 0
    for ci_data in crop_irrs:
        if ci_data["key"] != "T_BUY":
            for i in range(N_YEARS):
                built_cfs[i] += ci_data["cashflows"][i]
            total_rev += ci_data["unlev_detail"]["base_rev"]
            total_exp += ci_data["unlev_detail"]["base_exp"]
            total_capex_built += ci_data["unlev_detail"]["capex"]
    total_irr = calc_irr(built_cfs) * 100 if any(c != 0 for c in built_cfs) else 0
    total_unlev = ((total_rev - total_exp) / total_capex_built * 100) if total_capex_built > 0 else 0

    return crop_irrs, round(total_irr, 1), round(total_unlev, 1), expansion_int, expansion_prin, expansion_loan_detail
