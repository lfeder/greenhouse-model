"""
Flask server: slider changes auto-save to settings.json, run model, write output.csv.
Start: python server.py
Open: http://localhost:5050
"""

from flask import Flask, send_from_directory, request, jsonify
from pathlib import Path
import json
import model

app = Flask(__name__, static_folder=".")
BASE = Path(__file__).parent
SETTINGS_PATH = BASE / "settings.json"


def load_settings():
    with open(SETTINGS_PATH) as f:
        return json.load(f)


def save_settings(settings):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(".", path)


@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Return current slider values."""
    return jsonify(load_settings())


@app.route("/api/compute", methods=["POST"])
def compute():
    """Receive slider values, save to settings.json, run model, write output.csv, return JSON."""
    params = request.json or {}

    # Merge into settings and save
    settings = load_settings()
    settings.update(params)
    save_settings(settings)

    # Run the model
    result = run_model(settings)
    return jsonify(result)


def run_model(s):
    """Run the full model from settings dict. Returns all data for HTML rendering."""
    debug = s.get("debug", False)
    rev_inflation = s.get("revInflation", 4)
    cost_inflation = s.get("costInflation", 3)
    t_bill_rate = s.get("tBillRate", 4) / 100

    # Build revenue & expense
    if debug:
        d = model.DEBUG_DEFAULTS
        rev = [d["rev_2026"] * (1 + d["growth"])**i for i in range(10)]
        exp = [d["exp_2026"] * (1 + d["growth"])**i for i in range(10)]
        dep_crops = []
    else:
        rev = [0.0] * 10
        exp = [0.0] * 10

        # Existing ops
        for i, y in enumerate(model.YEARS):
            n = y - 2026
            rm = (1 + rev_inflation / 100) ** n
            cm = (1 + cost_inflation / 100) ** n
            rev[i] += model.EXISTING_KJ_REV * rm
            l_base = model.EXISTING_L_REV if y <= 2026 else s.get("lettuceLbs", 600000) * s.get("lettucePrice", 7.0)
            rev[i] += l_base * rm
            exp[i] += model.EXISTING_KJ_REV * model.EXISTING_EXP_RATIO * cm
            l_exp_pct = model.EXISTING_EXP_RATIO if y <= 2026 else s.get("lettuceExpPct", 70) / 100
            exp[i] += l_base * l_exp_pct * cm

        # New crops
        crop_defs = build_crop_list(s)
        dep_crops = []
        for crop in crop_defs:
            end_q = model.get_end_quarter(crop["startQ"], crop["buildMonths"])
            cfg = {"base_rev": crop["baseRev"], "base_exp": crop["baseExp"], "end_q": end_q, "ramp_years": crop["rampYears"]}
            cr, ce = model.compute_crop_revenue(cfg, rev_inflation, cost_inflation)
            for i in range(10):
                rev[i] += cr[i]
                exp[i] += ce[i]
            dep_crops.append({"end_year": end_q // 10, "capex": crop["capex"]})

    # Loans
    loans = model.compute_loan_schedules()

    # Depreciation
    dep = model.compute_depreciation(
        crops=dep_crops,
        exist_fed_years=s.get("existFedYearsLeft", 8),
        exist_fed_annual=s.get("existFedAnnual", 200000),
        exist_state_end=s.get("existStateEndYear", 2029),
        exist_state_annual=s.get("existStateAnnual", 1000000),
        fed_bonus_pct=s.get("fedBonusPct", 100) / 100,
        state_useful_life=s.get("stateUsefulLife", 10),
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
    if not debug:
        for crop in build_crop_list(s):
            start_year = crop["startQ"] // 10
            financing = s.get("financingPct", 65)
            crop_equity = crop["capex"] * (1 - financing / 100)
            equity_by_year[start_year] = equity_by_year.get(start_year, 0) + crop_equity

    year_pe = s.get("yearPE", {})
    pe_vals = [result["op_inc"][i] * int(year_pe.get(str(model.YEARS[i]), 8)) for i in range(10)]
    ownership = model.compute_ownership_trajectory(equity_by_year, pe_vals, s.get("jsTransferPct", 0))

    # Partner cash
    partners, grant_pct = model.compute_partner_cash(result, ownership)

    # Write CSV for debugging
    model.write_output_csv(result, ownership, partners, grant_pct, loans, dep, exist_debt)

    # Loan detail
    loan_detail = {}
    for name, data in loans["loan_data"].items():
        loan_detail[name] = {
            "interest": [d["interest"] for d in data],
            "principal": [d["principal"] for d in data],
            "end_bal": [d["end_bal"] for d in data],
        }

    return {
        "years": model.YEARS,
        "rev": result["rev"], "exp": result["exp"],
        "op_inc": result["op_inc"], "capex_res": result["capex_res"],
        "taxable_inc": result["taxable_inc"],
        "total_tax_liab": result["total_tax_liab"],
        "tax_cash_dist": result["tax_cash_dist"],
        "distrib_cash": result["distrib_cash"],
        "tax_detail": result["tax_detail"],
        "loan_int": result["loan_int"], "loan_prin": result["loan_prin"], "total_ds": result["total_ds"],
        "fed_dep": result["fed_dep"], "state_dep": result["state_dep"],
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
        "dep_fed": dep["fed"], "dep_state": dep["state"],
    }


def build_crop_list(s):
    """Build crop configs from settings."""
    base = model.BASE_REV_PER_AC
    crops = []

    def add(key, label, acres_key, exp_key, cost_key, rev_mult_key=None, rev_mult=1.0, is_lettuce=False, is_buy=False, fixed_acres=None):
        acres = fixed_acres if fixed_acres else s.get(acres_key, 0)
        if acres <= 0:
            return
        if is_lettuce:
            br = s.get("lettuceLbs", 600000) * s.get("lettucePrice", 7.0)
        else:
            mult = s.get(rev_mult_key, rev_mult * 100) / 100 if rev_mult_key else rev_mult
            br = acres * base * mult
        exp_pct = s.get(exp_key, 70) / 100
        if is_buy:
            capex = s.get("tomatoPurchasePrice", 8000000)
            start_q = s.get("tStartQ", 20281)  # same as T build
        else:
            capex = acres * s.get(cost_key, 1000000)
            start_q = s.get(f"{key.lower()}StartQ", 20271)

        crops.append({
            "key": key, "label": label, "acres": acres,
            "baseRev": br, "baseExp": br * exp_pct,
            "capex": capex,
            "startQ": start_q,
            "buildMonths": s.get(f"{key.lower()}BuildMonths", 12),
            "rampYears": s.get(f"{key.lower()}RampYears", 2),
            "isBuy": is_buy,
        })

    add("K", "Keiki", "newKAcres", "cukeExpPct", "cucumberCostPerAc", rev_mult=1.0)
    add("J", "Japanese", "newJAcres", "cukeExpPct", "cucumberCostPerAc", rev_mult=1.0)
    add("E", "English", "newEAcres", "cukeExpPct", "cucumberCostPerAc", rev_mult_key="englishRevPct")
    add("T", "Tomato (Build)", "newTAcres", "tomatoExpPct", "tomatoCostPerAc", rev_mult_key="tomatoRevPct")
    add("TB", "Tomato (Purchased)", None, "tomatoExpPct", None, rev_mult_key="tomatoRevPct", is_buy=True, fixed_acres=6)
    add("L", "Lettuce", "newLAcres", "lettuceExpPct", "lettuceCostPerAc", is_lettuce=True)

    return crops


if __name__ == "__main__":
    print("Starting server at http://localhost:5050")
    print("Output written to output.csv on every computation")
    app.run(port=5050, debug=True)
