"""
Greenhouse Expansion Financial Model v2
Orchestrator — calls into load, fin_util, analysis modules.
"""
from load import (
    YEARS, N_YEARS, DIST_BASE, FED_NOL, EB_GRANT_PEDD,
    EXISTING_K_ACRES, EXISTING_J_ACRES, EXISTING_E_ACRES, EXISTING_L_ACRES,
    EXISTING_K_REV_PER_AC, EXISTING_J_REV_PER_AC,
    NEW_E_REV_PER_AC, EXISTING_E_BASE_PRICE,
    REV_MULTIPLES,
    build_rev_exp, allocate_capital_stack, compute_kpis,
    ramp_crop_rev_exp, get_end_quarter,
)
from fin_util import (
    compute_loan_schedules, calc_tax_dist, calc_tax_cash_timing,
    compute_depreciation,
)
from analysis import (
    calc_irr, build_equity_draws, compute_ownership,
    run_waterfall, compute_partner_cash, compute_crop_irrs,
)


# ============================================================
# P&L assembly
# ============================================================

def run_pnl(rev, exp, settings, dep_crops=None, expansion_int=None):
    """Assemble P&L from rev/exp arrays. Returns all intermediate values."""
    s = settings
    loans = compute_loan_schedules()
    dep = compute_depreciation(dep_crops or [], s)
    t_bill_rate = s["tBillRate"] / 100
    expansion_int = expansion_int or [0] * N_YEARS

    op_inc = [r - e for r, e in zip(rev, exp)]
    capex_res_pct = s["capexReservePct"] / 100
    capex_res = [r * capex_res_pct for r in rev]
    # Total interest = existing loans + expansion loans
    total_interest = [loans["total_int"][i] + expansion_int[i] for i in range(N_YEARS)]
    taxable_inc = [o - ti for o, ti in zip(op_inc, total_interest)]

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

    tax_cash, tax_cash_detail = calc_tax_cash_timing(tax_liab)
    distrib_cash = [o - ds - cr for o, ds, cr in zip(op_inc, loans["total_ds"], capex_res)]

    # Waterfall
    wf = run_waterfall(distrib_cash, tax_cash, t_bill_rate)

    # Ownership computed in run_everything (needs equity_by_year + biz_values)
    ownership = None
    ownership_detail = None

    # Partner cash deferred to run_everything
    partners = None

    # EB grant summary
    pedd_grant = min(EB_GRANT_PEDD["max_pct"], int(wf["cum_pedd"] / 500_000) * EB_GRANT_PEDD["per_500k"])

    return {
        "years": YEARS, "rev": rev, "exp": exp,
        "op_inc": op_inc, "capex_res": capex_res,
        "taxable_inc": taxable_inc, "total_interest": total_interest,
        "tax_liab": tax_liab, "tax_cash": tax_cash, "tax_cash_detail": tax_cash_detail,
        "distrib_cash": distrib_cash, "tax_detail": tax_detail,
        "loans": loans, "dep": dep,
        "ownership": ownership, "partners": partners,
        "pedd_grant": pedd_grant,
        **wf,
    }


# ============================================================
# Scenarios
# ============================================================

# Direct source caps per scenario column (expansion params come from settings.json).
# SBIC loan and 3P equity are direct amounts; JJB fills the remainder after bank.
FINANCING_SCENARIOS = {
    "A":      {"sbicCap":0,        "tpEquityCap":0,        "sbicEquityPct":0,  "thirdPartyBuysJS":False},
    "B_SBIC": {"sbicCap":9900000,  "tpEquityCap":0,        "sbicEquityPct":5,  "thirdPartyBuysJS":False},
    "B_3P":   {"sbicCap":0,        "tpEquityCap":8800000,  "sbicEquityPct":0,  "thirdPartyBuysJS":False},
    "C_SBIC": {"sbicCap":25000000, "tpEquityCap":3800000,  "sbicEquityPct":10, "thirdPartyBuysJS":False},
    "C_3P":   {"sbicCap":0,        "tpEquityCap":28800000, "sbicEquityPct":0,  "thirdPartyBuysJS":False},
    "D":      {"debug": True},
}

# Map each financing column to its expansion scenario
_EXPANSION_MAP = {"A": "A", "B_SBIC": "B", "B_3P": "B", "C_SBIC": "C", "C_3P": "C", "D": None}

def rev_band_multiple(revenue):
    """Return EBITDA multiple based on revenue band."""
    for cap, mult in REV_MULTIPLES:
        if revenue <= cap:
            return mult
    return REV_MULTIPLES[-1][1]


def compute_scenario_summary(base_settings):
    """Run all 6 scenario columns, return summary for comparison card."""
    # Use last display year (index -3 = 2033 with 10yr model)
    display_idx = N_YEARS - 3  # 2033
    summaries = {}
    exp_scenarios = base_settings.get("expansion_scenarios", {})
    fin_overrides = base_settings.get("financing_overrides", {})
    for name, fin_params in FINANCING_SCENARIOS.items():
        s = dict(base_settings)
        # Apply expansion scenario params
        exp_key = _EXPANSION_MAP.get(name)
        if exp_key and exp_key in exp_scenarios:
            s.update(exp_scenarios[exp_key])
        # Apply financing params
        s.update(fin_params)
        # Apply per-column financing overrides (sbicCap, tpEquityCap, sbicEquityPct)
        if name in fin_overrides:
            s.update(fin_overrides[name])
        d = _run_scenario(s)
        steady_rev = d["rev"][display_idx]
        steady_op = d["op_inc"][display_idx]
        mult = rev_band_multiple(steady_rev)
        biz_val = steady_op * mult
        own = d["ownership"][display_idx]
        partners = d.get("partners", {})
        summary = {"multiple": mult, "rev": steady_rev, "op_inc": steady_op, "biz_val": biz_val}
        for p in ["EB", "JS", "JJB", "SBIC", "TP"]:
            pt = partners.get(p, {}).get("total", [0]*N_YEARS)
            avg_dist = sum(pt[1:display_idx+1]) / max(1, display_idx)
            summary[f"{p}_pct"] = own.get(p, 0)
            summary[f"{p}_dist"] = avg_dist
            summary[f"{p}_val"] = biz_val * own.get(p, 0) / 100

        # SBIC IRR with exit at display year
        sbic_dist = partners.get("SBIC", {}).get("total", [0]*N_YEARS)
        sbic_tax = partners.get("SBIC", {}).get("tax_dist", [0]*N_YEARS)
        eld = d.get("_expansion_loan_detail", {})
        sbic_cfs = [0.0] * N_YEARS
        for i in range(N_YEARS):
            loan_cf = sum(det.get("sbic_interest", [0]*N_YEARS)[i] + det.get("sbic_principal", [0]*N_YEARS)[i] for det in eld.values())
            sbic_cfs[i] = loan_cf + sbic_dist[i] + sbic_tax[i]
        for crop in d.get("_crop_data", []):
            if crop.get("is_buy") or crop.get("sbic_loan", 0) == 0:
                continue
            sy = crop["start_q"] // 10
            if sy in YEARS:
                sbic_cfs[YEARS.index(sy)] -= crop["sbic_loan"]
        sbic_exit = steady_op * mult * own.get("SBIC", 0) / 100
        sbic_cfs[display_idx] += sbic_exit
        summary["sbic_irr"] = round(calc_irr(sbic_cfs) * 100, 1) if any(c != 0 for c in sbic_cfs) else 0

        # Capital uses & sources
        kpi = compute_kpis(s, d["_crop_data"], False)
        summary["total_uses"] = kpi["total_capex"] + kpi["shared_capex"] + kpi["startup_capital"]
        summary["bank_total"] = kpi["bank_total"]
        summary["sbic_loan"] = kpi["sbic_total"]
        summary["tp_equity"] = kpi["tp_equity"]
        summary["jjb_equity"] = kpi["jjb_equity"]

        # Terminal ownership for scenario card
        summary["own_EB"] = own.get("EB", 0)
        summary["own_JS"] = own.get("JS", 0)
        summary["own_JJB"] = own.get("JJB", 0)
        summary["own_SBIC"] = own.get("SBIC", 0)
        summary["own_TP"] = own.get("TP", 0)

        # SBIC implied valuation (reverse solve from equity %)
        sbic_eq_pct = s["sbicEquityPct"]
        if sbic_eq_pct > 0 and kpi["sbic_total"] > 0:
            implied_val = kpi["sbic_total"] * (100 - sbic_eq_pct) / sbic_eq_pct
        else:
            implied_val = 0
        summary["sbic_implied_val"] = implied_val

        # Bridge loan (JJB funds EB/JS cashflow shortfalls during expansion)
        bridge_total = 0
        if not s.get("debug"):
            s_noexp = dict(s)
            s_noexp["debug"] = True
            rev_ne, exp_ne, _, _, _, _, _ = build_rev_exp(s_noexp)
            result_ne = run_pnl(rev_ne, exp_ne, s_noexp, [])
            own_ne, _ = compute_ownership(s_noexp, result_ne["cum_pedd_by_year"])
            partners_ne = compute_partner_cash({**result_ne, "tax_cash_dist": result_ne["tax_cash"]}, own_ne)
            for p in ["EB", "JS"]:
                ne_total = partners_ne[p]["total"]
                exp_total = partners.get(p, {}).get("total", [0] * N_YEARS)
                for i in range(N_YEARS):
                    gap = ne_total[i] - exp_total[i]
                    if gap > 0:
                        bridge_total += gap
        summary["bridge_total"] = bridge_total

        summaries[name] = summary
    return summaries


def _run_scenario(s):
    """Lightweight scenario run — returns just what summary needs."""
    rev, exp, rev_rows, exp_rows, crop_data, dep_crops, total_acres = build_rev_exp(s)
    allocate_capital_stack(crop_data, s)
    crop_irrs, total_irr, total_unlev, expansion_int, expansion_prin, expansion_loan_detail = compute_crop_irrs(crop_data, s)
    result = run_pnl(rev, exp, s, dep_crops, expansion_int)
    equity_draws = build_equity_draws(crop_data)
    year_pe = s["yearPE"]
    biz_values = [result["op_inc"][i] * int(year_pe.get(str(YEARS[i]), 8)) for i in range(N_YEARS)]
    s["_has_sbic"] = any(c.get("sbic_loan", 0) > 0 for c in crop_data if not c.get("is_buy"))
    s["_3p_equity_total"] = sum(c.get("third_party_equity", 0) for c in crop_data if not c.get("is_buy"))
    ownership, _ = compute_ownership(s, result["cum_pedd_by_year"], equity_draws, biz_values)
    tp_buys = s.get("thirdPartyBuysJS", False) and not s["debug"]
    partners = compute_partner_cash({**result, "tax_cash_dist": result["tax_cash"]}, ownership, tp_buys)
    result["ownership"] = ownership
    result["partners"] = partners
    result["_expansion_loan_detail"] = expansion_loan_detail
    result["_crop_data"] = crop_data
    return result


# ============================================================
# Run everything (single entry point for server)
# ============================================================

def run_everything(s):
    """Single entry point: settings → all computed data for HTML."""
    rev, exp, rev_rows, exp_rows, crop_data, dep_crops, total_acres = build_rev_exp(s)

    # Allocate capital stack before computing IRRs
    allocate_capital_stack(crop_data, s)

    # Compute expansion DS BEFORE full model so interest is included in taxable income
    crop_irrs, total_irr, total_unlev, expansion_int, expansion_prin, expansion_loan_detail = compute_crop_irrs(crop_data, s)

    result = run_pnl(rev, exp, s, dep_crops, expansion_int)
    kpis = compute_kpis(s, crop_data, s["debug"])

    # Compute equity draws per year for ownership dilution
    equity_draws = build_equity_draws(crop_data)

    # Business values for dilution pricing (op_inc × default PE)
    year_pe = s["yearPE"]
    biz_values = [result["op_inc"][i] * int(year_pe.get(str(YEARS[i]), 8)) for i in range(N_YEARS)]

    # Ownership with dilution
    s["_has_sbic"] = any(c.get("sbic_loan", 0) > 0 for c in crop_data if not c.get("is_buy"))
    s["_3p_equity_total"] = sum(c.get("third_party_equity", 0) for c in crop_data if not c.get("is_buy"))
    ownership, ownership_detail = compute_ownership(s, result["cum_pedd_by_year"], equity_draws, biz_values)

    # Partner cash (needs ownership)
    tp_buys = s.get("thirdPartyBuysJS", False) and not s["debug"]
    partners = compute_partner_cash({**result, "tax_cash_dist": result["tax_cash"]}, ownership, tp_buys)
    result["ownership"] = ownership
    result["ownership_detail"] = ownership_detail
    result["partners"] = partners
    pedd_grant = min(EB_GRANT_PEDD["max_pct"], int(result["cum_pedd"] / 500_000) * EB_GRANT_PEDD["per_500k"])
    result["pedd_grant"] = pedd_grant

    # No-expansion baseline for comparison (when expansion is on)
    partners_no_exp = None
    if not s["debug"]:
        s_noexp = dict(s)
        s_noexp["debug"] = True
        rev_ne, exp_ne, _, _, _, _, _ = build_rev_exp(s_noexp)
        result_ne = run_pnl(rev_ne, exp_ne, s_noexp, [])
        # Use base ownership (no dilution) for no-expansion
        own_ne, _ = compute_ownership(s_noexp, result_ne["cum_pedd_by_year"], {}, [0]*N_YEARS)
        partners_ne = compute_partner_cash({**result_ne, "tax_cash_dist": result_ne["tax_cash"]}, own_ne)
        partners_no_exp = {p: partners_ne[p]["total"] for p in ["EB", "JS", "JJB", "SBIC", "TP"]}
        partners_no_exp["_op_inc_terminal"] = result_ne["op_inc"][-1]
        partners_no_exp["_ownership"] = {"JJB": own_ne[-1]["JJB"], "EB": own_ne[-1]["EB"], "JS": own_ne[-1]["JS"], "SBIC": own_ne[-1].get("SBIC", 0), "TP": own_ne[-1].get("TP", 0)}

        # JJB bridge loan: fund shortfalls, repay from surpluses
        guarantee = {}
        for p in ["EB", "JS"]:
            advance = [0.0] * N_YEARS
            repay = [0.0] * N_YEARS
            balance = [0.0] * N_YEARS
            bal = 0.0
            for i in range(N_YEARS):
                gap = partners_no_exp[p][i] - partners[p]["total"][i]
                if gap > 0:
                    advance[i] = gap
                    bal += gap
                elif gap < 0 and bal > 0:
                    repay[i] = min(-gap, bal)
                    bal -= repay[i]
                balance[i] = bal
            guarantee[p] = {"advance": advance, "repay": repay, "balance": balance}
            # Add bridge net (advance - repay) into partner total
            for i in range(N_YEARS):
                partners[p]["total"][i] += advance[i] - repay[i]
        partners_no_exp["guarantee"] = guarantee
        partners_no_exp["guarantee_total"] = sum(
            guarantee["EB"]["advance"]) + sum(guarantee["JS"]["advance"])

    # SBIC investor ROI: loan DS + kicker distributions
    sbic_loan_total = sum(c.get("sbic_loan", 0) for c in crop_data if not c.get("is_buy"))
    sbic_cfs = []
    sbic_dist = partners.get("SBIC", {}).get("total", [0]*N_YEARS)
    sbic_tax_dist = partners.get("SBIC", {}).get("tax_dist", [0]*N_YEARS)
    for i in range(N_YEARS):
        # SBIC receives: loan interest + principal + distributions (ex-tax) + tax distributions
        loan_cf = sum(det.get("sbic_interest", [0]*N_YEARS)[i] + det.get("sbic_principal", [0]*N_YEARS)[i]
                      for det in expansion_loan_detail.values())
        cf = loan_cf + sbic_dist[i] + sbic_tax_dist[i]
        # First year of deployment: subtract loan outflow
        if i == 0 or (i > 0 and sbic_cfs[i-1] == 0 and cf > 0):
            pass  # deployment handled below
        sbic_cfs.append(cf)
    # Find deployment year and subtract loan amount
    for crop in crop_data:
        if crop.get("is_buy") or crop.get("sbic_loan", 0) == 0:
            continue
        sy = crop["start_q"] // 10
        idx = YEARS.index(sy) if sy in YEARS else -1
        if idx >= 0:
            sbic_cfs[idx] -= crop["sbic_loan"]
    # Add exit value at display year (SBIC sells kicker stake at biz value)
    exit_idx = N_YEARS - 3
    exit_rev = result["rev"][exit_idx]
    exit_op = result["op_inc"][exit_idx]
    exit_mult = rev_band_multiple(exit_rev)
    sbic_own_pct = result["ownership"][exit_idx].get("SBIC", 0) / 100 if result.get("ownership") else 0
    sbic_exit_value = exit_op * exit_mult * sbic_own_pct
    sbic_cfs[exit_idx] += sbic_exit_value
    sbic_irr = calc_irr(sbic_cfs) * 100 if any(c != 0 for c in sbic_cfs) else 0
    result["sbic_irr"] = round(sbic_irr, 1)
    result["sbic_exit_value"] = round(sbic_exit_value)
    result["sbic_loan_total"] = sbic_loan_total

    # Gantt data for frontend
    existing_ac = {
        "K": EXISTING_K_ACRES, "J": EXISTING_J_ACRES,
        "E": EXISTING_E_ACRES, "L": EXISTING_L_ACRES, "T": 0,
    }
    rev_per_ac = {
        "K": EXISTING_K_REV_PER_AC, "J": EXISTING_J_REV_PER_AC,
        "E": NEW_E_REV_PER_AC * s["englishPricePerLb"] / EXISTING_E_BASE_PRICE,
        "L": round(s["lettuceLbs"] * s["lettucePrice"] / EXISTING_L_ACRES) if EXISTING_L_ACRES else 0,
        "T": s["tomatoBuildRevPerAc"],
    }
    gantt_data = []
    tb_item = None
    for c in crop_data:
        item = {
            "key": c["key"], "label": c["label"],
            "acres_existing": existing_ac.get(c["key"], 0),
            "acres_new": c.get("acres", 0),
            "start_q": c["start_q"], "build_months": c.get("build_months", 12),
            "end_q": c["end_q"], "ramp_years": c.get("ramp_years", 0),
            "capex": c.get("capex", 0),
            "is_infra": c.get("is_infra", False),
            "is_buy": c.get("is_buy", False),
        }
        if not c.get("is_infra"):
            item["rev_per_ac"] = rev_per_ac.get(c["key"], 0)
        if c.get("is_buy"):
            item["rev_per_ac"] = s["tomatoBuyRevPerAc"]
            item["acres_existing"] = 6
            item["acres_new"] = 0
            tb_item = item
            continue
        # Infra-specific display units
        if c["key"] == "LAND":
            item["units_new"] = s["landAcres"]
            item["unit_label"] = "ac"
        elif c["key"] == "PACK":
            item["units_new"] = s["packhouseAcres"]
            item["unit_label"] = "ac"
        elif c["key"] == "HOUSE":
            item["units_new"] = s["housingPods"]
            item["unit_label"] = "pods"
        gantt_data.append(item)
    # T_BUY always last row
    if tb_item:
        gantt_data.append(tb_item)

    return {
        **{k: v for k, v in result.items() if k != "loans"},
        "loan_data": {n: [{"interest": d["interest"], "principal": d["principal"], "end_bal": d["end_bal"]} for d in sched] for n, sched in result["loans"]["data"].items()},
        "loan_total_int": result["loans"]["total_int"],
        "loan_total_prin": result["loans"]["total_prin"],
        "loan_total_ds": result["loans"]["total_ds"],
        "crop_irrs": crop_irrs,
        "total_irr": total_irr, "total_unlev": total_unlev,
        "crops": [{"key": c["key"], "label": c["label"], "acres": c["acres"], "capex": c["capex"]} for c in crop_data if not c.get("is_infra")],
        "rev_rows": rev_rows, "exp_rows": exp_rows, "total_acres": total_acres,
        "expansion_int": expansion_int, "expansion_prin": expansion_prin,
        "expansion_ds": [expansion_int[i] + expansion_prin[i] for i in range(N_YEARS)],
        "expansion_loan_detail": expansion_loan_detail,
        "expansion_loan_order": [c["label"] for c in crop_data],
        "ownership_detail": ownership_detail,
        "partners_no_exp": partners_no_exp,
        "gantt_data": gantt_data,
        "scenario_summary": compute_scenario_summary(s),
        **kpis,
    }
