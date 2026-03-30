"""
Flask server for v2.
Every slider change: save settings.json → run model → write output.csv → return JSON.
Start: python server.py
Open: http://localhost:5050
"""

from flask import Flask, send_from_directory, request, jsonify
from pathlib import Path
import json
import model

app = Flask(__name__, static_folder=".")
DIR = Path(__file__).parent
SETTINGS_PATH = DIR / "settings.json"


def load_settings():
    with open(SETTINGS_PATH) as f:
        return json.load(f)


def save_settings(s):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(s, f, indent=2)


def build_crops(s):
    """Build crop list from slider settings."""
    base = model.BASE_REV_PER_AC
    crops = []

    defs = [
        ("K",  "Keiki",             "newKAcres",  "cukeExpPct",    "cucumberCostPerAc", 1.0,   None),
        ("J",  "Japanese",          "newJAcres",  "cukeExpPct",    "cucumberCostPerAc", 1.0,   None),
        ("E",  "English",           "newEAcres",  "cukeExpPct",    "cucumberCostPerAc", None,   "englishRevPct"),
        ("T",  "Tomato (Build)",    "newTAcres",  "tomatoExpPct",  "tomatoCostPerAc",   None,   "tomatoRevPct"),
        ("L",  "Lettuce",           "newLAcres",  "lettuceExpPct", "lettuceCostPerAc",  None,   None),
    ]

    for key, label, acres_key, exp_key, cost_key, fixed_mult, mult_key in defs:
        acres = s.get(acres_key, 0)
        if acres <= 0:
            continue

        if key == "L":
            base_rev = s.get("lettuceLbs", 600000) * s.get("lettucePrice", 7.0)
        else:
            mult = s.get(mult_key, (fixed_mult or 1.0) * 100) / 100 if mult_key else (fixed_mult or 1.0)
            base_rev = acres * base * mult

        exp_pct = s.get(exp_key, 70) / 100
        start_q = s.get(f"{key.lower()}StartQ", 20271)
        build_mo = s.get(f"{key.lower()}BuildMonths", 12)
        ramp_yr = s.get(f"{key.lower()}RampYears", 2)
        end_q = model.get_end_quarter(start_q, build_mo)

        crops.append({
            "key": key, "label": label, "acres": acres,
            "base_rev": base_rev, "base_exp": base_rev * exp_pct,
            "capex": acres * s.get(cost_key, 1000000),
            "start_q": start_q, "end_q": end_q,
            "build_months": build_mo, "ramp_years": ramp_yr,
            "end_year": end_q // 10,
        })

    # Tomato purchase (always included)
    t_mult = s.get("tomatoRevPct", 120) / 100
    tb_rev = 6 * base * t_mult
    tb_exp_pct = s.get("tomatoExpPct", 70) / 100
    tb_start_q = s.get("tStartQ", 20281)  # same start as T build
    tb_build = s.get("tbBuildMonths", 6)
    tb_end_q = model.get_end_quarter(tb_start_q, tb_build)
    crops.append({
        "key": "TB", "label": "Tomato (Purchased)", "acres": 6,
        "base_rev": tb_rev, "base_exp": tb_rev * tb_exp_pct,
        "capex": s.get("tomatoPurchasePrice", 8000000),
        "start_q": tb_start_q, "end_q": tb_end_q,
        "build_months": tb_build, "ramp_years": s.get("tbRampYears", 1),
        "end_year": tb_end_q // 10, "is_buy": True,
    })

    return crops


def compute(s):
    """Run the full model from settings."""
    debug = s.get("debug", False)
    ri = s.get("revInflation", 4)
    ci = s.get("costInflation", 3)

    if debug:
        d = model.DEBUG
        rev = [d["rev_2026"] * (1 + d["growth"])**i for i in range(model.N_YEARS)]
        exp = [d["exp_2026"] * (1 + d["growth"])**i for i in range(model.N_YEARS)]
        dep_crops = []
        crop_data = []
    else:
        rev = [0.0] * model.N_YEARS
        exp = [0.0] * model.N_YEARS

        # Existing ops
        for i, y in enumerate(model.YEARS):
            n = y - 2026
            rm = (1 + ri / 100) ** n
            cm = (1 + ci / 100) ** n
            rev[i] += model.EXISTING_KJ_REV * rm
            l_base = model.EXISTING_L_REV if y <= 2026 else s.get("lettuceLbs", 600000) * s.get("lettucePrice", 7.0)
            rev[i] += l_base * rm
            exp[i] += model.EXISTING_KJ_REV * model.EXISTING_EXP_RATIO * cm
            l_exp = model.EXISTING_EXP_RATIO if y <= 2026 else s.get("lettuceExpPct", 70) / 100
            exp[i] += l_base * l_exp * cm

        # New crops
        crop_data = build_crops(s)
        dep_crops = []
        for crop in crop_data:
            cr, ce = model.crop_annual_rev_exp(crop["base_rev"], crop["base_exp"], crop["end_q"], crop["ramp_years"], ri, ci)
            if not crop.get("is_buy"):  # TB excluded from main rev/exp
                for i in range(model.N_YEARS):
                    rev[i] += cr[i]
                    exp[i] += ce[i]
            if not crop.get("is_buy"):
                dep_crops.append({"end_year": crop["end_year"], "capex": crop["capex"]})

    result = model.run_full_model(rev, exp, s, dep_crops)

    # Crop IRR (per-crop cashflow simulation)
    crop_irrs = []
    for crop in crop_data:
        cr, ce = model.crop_annual_rev_exp(crop["base_rev"], crop["base_exp"], crop["end_q"], crop["ramp_years"], ri, ci)
        financing = s.get("financingPct", 65) / 100
        if crop.get("is_buy"):
            hard_assets = s.get("tomatoHardAssets", 5000000)
            loan_base = min(hard_assets, crop["capex"])
        else:
            loan_base = crop["capex"]
        loan = loan_base * financing
        equity = crop["capex"] - loan
        rate = s.get("interestRate", 7) / 100
        term = s.get("loanTermYears", 10)
        annual_ds = model._annual_pmt(loan, rate, term) if loan > 0 else 0

        cfs = []
        start_year = crop["start_q"] // 10
        end_year = crop["end_q"] // 10
        for i, y in enumerate(model.YEARS):
            cf = cr[i] - ce[i]  # operating income
            # Debt service (IO during build + 6mo, then P&I)
            io_end_y = end_year + (1 if (crop["end_q"] % 10) > 2 else 0)
            if start_year <= y <= start_year + term - 1:
                if y < io_end_y:
                    cf -= loan * rate  # IO
                else:
                    cf -= annual_ds  # P&I
            if y == start_year:
                cf -= equity
            cfs.append(cf)

        irr = model.calc_irr(cfs) * 100 if any(c != 0 for c in cfs) else 0
        crop_irrs.append({
            "key": crop["key"], "label": crop["label"],
            "capex": crop["capex"], "equity": equity,
            "irr": round(irr, 1), "cashflows": [round(c) for c in cfs],
        })

    # Total IRR (all built crops combined)
    built_cfs = [0] * model.N_YEARS
    for ci_data in crop_irrs:
        if ci_data["key"] != "TB":
            for i in range(model.N_YEARS):
                built_cfs[i] += ci_data["cashflows"][i]
    total_irr = model.calc_irr(built_cfs) * 100 if any(c != 0 for c in built_cfs) else 0

    # Write CSV
    model.write_csv(result)

    return {
        **{k: v for k, v in result.items() if k != "loans"},
        "loan_data": {n: [{"interest": d["interest"], "principal": d["principal"], "end_bal": d["end_bal"]} for d in sched] for n, sched in result["loans"]["data"].items()},
        "loan_total_int": result["loans"]["total_int"],
        "loan_total_prin": result["loans"]["total_prin"],
        "loan_total_ds": result["loans"]["total_ds"],
        "crop_irrs": crop_irrs,
        "total_irr": round(total_irr, 1),
        "crops": [{"key": c["key"], "label": c["label"], "acres": c["acres"], "capex": c["capex"]} for c in crop_data],
    }


# === ROUTES ===

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(".", path)

@app.route("/api/settings")
def get_settings():
    return jsonify(load_settings())

@app.route("/api/constants")
def get_constants():
    with open(DIR / "constants.json") as f:
        return jsonify(json.load(f))

@app.route("/api/compute", methods=["POST"])
def api_compute():
    params = request.json or {}
    settings = load_settings()
    settings.update(params)
    save_settings(settings)
    return jsonify(compute(settings))


if __name__ == "__main__":
    print("Starting server at http://localhost:5050")
    print("Slider changes auto-save to settings.json")
    print("output.csv updated on every computation")
    app.run(port=5050, debug=True)
