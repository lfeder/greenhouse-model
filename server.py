"""
Tiny Flask server: receives slider values from HTML, runs model.py, returns JSON.
Start with: python server.py
Then open http://localhost:5050
"""

from flask import Flask, send_from_directory, request, jsonify
import model

app = Flask(__name__, static_folder=".")

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(".", path)

@app.route("/api/compute", methods=["POST"])
def compute():
    params = request.json or {}

    # Extract crop configs from params
    crops_input = params.get("crops", [])
    rev_inflation = params.get("revInflation", 4)
    cost_inflation = params.get("costInflation", 3)
    debug = params.get("debug", False)
    t_bill_rate = params.get("tBillRate", 0.065)
    js_transfer_pct = params.get("jsTransferPct", 0)

    # Revenue & expense
    if debug:
        rev = [14_200_000 * 1.05**i for i in range(10)]
        exp = [9_700_000 * 1.05**i for i in range(10)]
        dep_crops = []
    else:
        # Build crop revenue from expansion config
        total_rev = [0] * 10
        total_exp = [0] * 10

        # Existing operations
        for i, y in enumerate(model.YEARS):
            n = y - 2026
            rev_mult = (1 + rev_inflation / 100) ** n
            cst_mult = (1 + cost_inflation / 100) ** n
            total_rev[i] += model.EXISTING_KJ_REV * rev_mult
            lettuce_base = model.EXISTING_L_REV if y <= 2026 else params.get("lettuceLbs", 600000) * params.get("lettucePrice", 7.0)
            total_rev[i] += lettuce_base * rev_mult
            total_exp[i] += model.EXISTING_KJ_REV * model.EXISTING_EXP_RATIO * cst_mult
            total_exp[i] += lettuce_base * (model.EXISTING_EXP_RATIO if y <= 2026 else params.get("lettuceExpPct", 70) / 100) * cst_mult

        # New crops
        dep_crops = []
        for crop in crops_input:
            end_q = model.get_end_quarter(crop["startQ"], crop["buildMonths"])
            base_rev_per_ac = model.BASE_REV_PER_AC * crop.get("revMult", 1.0)
            acres = crop.get("acres", 0)
            if crop.get("isLettuce"):
                base_rev = params.get("lettuceLbs", 600000) * params.get("lettucePrice", 7.0)
            else:
                base_rev = acres * base_rev_per_ac
            base_exp = base_rev * crop.get("expPct", 70) / 100

            cfg = {
                "base_rev": base_rev, "base_exp": base_exp,
                "end_q": end_q, "ramp_years": crop.get("rampYears", 2),
            }
            crop_rev, crop_exp = model.compute_crop_revenue(cfg, rev_inflation, cost_inflation)
            for i in range(10):
                total_rev[i] += crop_rev[i]
                total_exp[i] += crop_exp[i]

            end_year = end_q // 10
            dep_crops.append({"end_year": end_year, "capex": crop.get("capex", 0)})

        rev = total_rev
        exp = total_exp

    # Loans
    loans = model.compute_loan_schedules()

    # Depreciation
    dep = model.compute_depreciation(
        crops=dep_crops,
        exist_fed_years=params.get("existFedYearsLeft", 8),
        exist_fed_annual=params.get("existFedAnnual", 200_000),
        exist_state_end=params.get("existStateEndYear", 2029),
        exist_state_annual=params.get("existStateAnnual", 1_000_000),
        fed_bonus_pct=params.get("fedBonusPct", 100) / 100,
        state_useful_life=params.get("stateUsefulLife", 10),
    )

    # Existing debt
    exist_debt = []
    for y in model.YEARS:
        d = 0
        if y < model.EXISTING_DEBT_LETTUCE_END:
            d += model.EXISTING_DEBT_LETTUCE
        elif y == model.EXISTING_DEBT_LETTUCE_END:
            d += model.EXISTING_DEBT_LETTUCE * 0.25
        if y <= model.EXISTING_DEBT_LAND_END:
            d += model.EXISTING_DEBT_LAND
        exist_debt.append(d)

    # Full model
    result = model.run_full_model(rev, exp, loans["total_int"], loans["total_prin"], loans["total_ds"], dep["fed"], dep["state"], t_bill_rate)

    # Ownership
    equity_by_year = {}
    for crop in crops_input:
        end_q = model.get_end_quarter(crop["startQ"], crop["buildMonths"])
        start_year = crop["startQ"] // 10
        capex = crop.get("capex", 0)
        financing = params.get("financingPct", 80)
        crop_equity = capex * (1 - financing / 100)
        equity_by_year[start_year] = equity_by_year.get(start_year, 0) + crop_equity

    pe_vals = [result["op_inc"][i] * params.get("yearPE", {}).get(str(model.YEARS[i]), 8) for i in range(10)]
    ownership = model.compute_ownership_trajectory(equity_by_year, pe_vals, js_transfer_pct)

    # Partner cash
    partners, grant_pct = model.compute_partner_cash(result, ownership)

    # Loan detail for debt page
    loan_detail = {}
    for name, data in loans["loan_data"].items():
        loan_detail[name] = {
            "interest": [d["interest"] for d in data],
            "principal": [d["principal"] for d in data],
            "end_bal": [d["end_bal"] for d in data],
        }

    return jsonify({
        "years": model.YEARS,
        "rev": result["rev"], "exp": result["exp"],
        "op_inc": result["op_inc"],
        "capex_res": result["capex_res"],
        "taxable_inc": result["taxable_inc"],
        "total_tax_liab": result["total_tax_liab"],
        "tax_cash_dist": result["tax_cash_dist"],
        "distrib_cash": result["distrib_cash"],
        "tax_detail": result["tax_detail"],
        "loan_int": result["loan_int"],
        "loan_prin": result["loan_prin"],
        "total_ds": result["total_ds"],
        "fed_dep": result["fed_dep"],
        "state_dep": result["state_dep"],
        "pedd_payments": result["pedd_payments"],
        "pedd_detail": result["detail"],
        "tier0_actual": result["tier0_actual"],
        "tier1_actual": result["tier1_actual"],
        "tier1_shortfall": result["tier1_shortfall"],
        "tier2": result["tier2"],
        "cum_pedd": result["cum_pedd"],
        "ownership": ownership,
        "partners": partners,
        "grant_pct": grant_pct,
        "loan_detail": loan_detail,
        "exist_debt": exist_debt,
        "dep_fed": dep["fed"],
        "dep_state": dep["state"],
    })

if __name__ == "__main__":
    print("Starting server at http://localhost:5050")
    app.run(port=5050, debug=True)
