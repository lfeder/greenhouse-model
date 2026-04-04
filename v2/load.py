"""
Load constants from JSON and build data structures from settings.
"""
import json
from pathlib import Path

_DIR = Path(__file__).parent
with open(_DIR / "constants.json") as f:
    C = json.load(f)

YEARS = C["years"]
N_YEARS = len(YEARS)

# Existing ops — per-crop acres and rev/ac
EXISTING_K_ACRES = C["existing_ops"]["keiki_acres"]
EXISTING_K_REV_PER_AC = C["existing_ops"]["keiki_rev_per_ac"]
EXISTING_K_REV = EXISTING_K_ACRES * EXISTING_K_REV_PER_AC

EXISTING_J_ACRES = C["existing_ops"]["japanese_acres"]
EXISTING_J_REV_PER_AC = C["existing_ops"]["japanese_rev_per_ac"]
EXISTING_J_REV = EXISTING_J_ACRES * EXISTING_J_REV_PER_AC

EXISTING_E_ACRES = C["existing_ops"]["english_acres"]
EXISTING_E_LBS_PER_AC = C["existing_ops"]["english_lbs_per_ac"]
EXISTING_E_BASE_PRICE = 3.0  # $/lb baseline for 2026
NEW_E_REV_PER_AC = 800000  # new English GH target rev/ac at base price
EXISTING_E_REV = EXISTING_E_ACRES * EXISTING_E_LBS_PER_AC * EXISTING_E_BASE_PRICE

EXISTING_L_ACRES = C["existing_ops"]["lettuce_acres"]
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

# Per-crop rev/ac for new expansion crops (matches existing ops)
CROP_REV_PER_AC = {
    "K": EXISTING_K_REV_PER_AC,
    "J": EXISTING_J_REV_PER_AC,
}

CROP_DEFS = [
    # (key, label, acres_key, mrgn_key, cost_key)
    ("K",  "Keiki",          "newKAcres",  "keikiMrgnPct",    "cucumberCostPerAc"),
    ("J",  "Japanese",       "newJAcres",  "japaneseMrgnPct", "cucumberCostPerAc"),
    ("E",  "English",        "newEAcres",  "englishMrgnPct",  "cucumberCostPerAc"),
    ("T",  "Tomato (Build)", "newTAcres",  "tomatoMrgnPct",  "tomatoCostPerAc"),
    ("L",  "Lettuce",        "newLAcres",  "lettuceMrgnPct", "lettuceCostPerAc"),
]


# ============================================================
# Quarter / ramp helpers
# ============================================================

def prev_quarter(q):
    y, qtr = q // 10, q % 10
    if qtr == 1:
        return (y - 1) * 10 + 4
    return y * 10 + qtr - 1


def get_end_quarter(start_q, months):
    y = start_q // 10
    q = start_q % 10
    total_months = (q - 1) * 3 + months
    end_q_num = (total_months - 1) // 3 + 1
    end_y = y + (end_q_num - 1) // 4
    end_q = (end_q_num - 1) % 4 + 1
    return end_y * 10 + end_q


def ramp_factor(prod_yr, ramp_years):
    if ramp_years <= 0 or prod_yr >= ramp_years + 1:
        return 1.0
    return prod_yr / (ramp_years + 1)


def ramp_crop_rev_exp(base_rev, base_exp, end_q, ramp_years, rev_inf, cost_inf):
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
# Build data structures from settings
# ============================================================

def build_crops(s):
    """Build crop list from slider settings."""
    crops = []
    for key, label, acres_key, mrgn_key, cost_key in CROP_DEFS:
        acres = s[acres_key]
        if key == "L":
            base_rev = s["lettuceLbs"] * s["lettucePrice"] * acres / EXISTING_L_ACRES if EXISTING_L_ACRES and acres else 0
        elif key == "T":
            base_rev = acres * s["tomatoBuildRevPerAc"]
        elif key == "E":
            base_rev = acres * NEW_E_REV_PER_AC * s["englishPricePerLb"] / EXISTING_E_BASE_PRICE
        else:
            base_rev = acres * CROP_REV_PER_AC.get(key, 875000)
        exp_pct = s[mrgn_key] / 100
        start_q = s[f"{key.lower()}StartQ"]
        build_mo = s[f"{key.lower()}BuildMonths"]
        ramp_yr = s[f"{key.lower()}RampYears"]
        end_q = get_end_quarter(start_q, build_mo)
        gh_capex = acres * s[cost_key]
        startup_cap = base_rev * exp_pct / 4 * 0.5  # 3 months of expenses at 100%, 50% prefunded (excl labor/utilities)
        startup_yr = prev_quarter(end_q) // 10
        crops.append({
            "key": key, "label": label, "acres": acres,
            "base_rev": base_rev, "base_exp": base_rev * exp_pct,
            "capex": gh_capex, "gh_capex": gh_capex,
            "start_q": start_q, "end_q": end_q,
            "build_months": build_mo, "ramp_years": ramp_yr,
            "end_year": end_q // 10,
            "startup_capital": startup_cap, "startup_year": startup_yr,
        })
    # Tomato purchase (always)
    tb_rev = 6 * s["tomatoBuyRevPerAc"]
    tb_exp_pct = s["tomatoBuyMrgnPct"] / 100
    tb_start_q = s["tStartQ"]
    tb_build = s["tbBuildMonths"]
    tb_end_q = get_end_quarter(tb_start_q, tb_build)
    tb_startup = tb_rev * tb_exp_pct * 1.5 / 12 * 0.5
    tb_startup_yr = prev_quarter(tb_end_q) // 10
    crops.append({
        "key": "T_BUY", "label": "Tomato (Buy)", "acres": 6,
        "base_rev": tb_rev, "base_exp": tb_rev * tb_exp_pct,
        "capex": 6 * s["tomatoPurchasePrice"],
        "start_q": tb_start_q, "end_q": tb_end_q,
        "build_months": tb_build, "ramp_years": s["tbRampYears"],
        "end_year": tb_end_q // 10, "is_buy": True,
        "startup_capital": tb_startup, "startup_year": tb_startup_yr,
    })
    return crops


def build_rev_exp(s):
    """Build per-component revenue/expense arrays from settings."""
    ri = s["revInflation"]
    ci = s["costInflation"]
    debug = s["debug"]

    # Ordered lists: [[name, data], ...] — order matters for display
    rev_rows = []
    exp_rows = []
    rev = [0.0] * N_YEARS
    exp = [0.0] * N_YEARS

    # Existing ops — K, J (fixed base rev)
    for label, base in [("Existing Keiki", EXISTING_K_REV), ("Existing Japanese", EXISTING_J_REV)]:
        r = [base * (1 + ri / 100) ** (y - 2026) for y in YEARS]
        e = [base * EXISTING_EXP_RATIO * (1 + ci / 100) ** (y - 2026) for y in YEARS]
        for i in range(N_YEARS):
            rev[i] += r[i]; exp[i] += e[i]
        rev_rows.append([label, r]); exp_rows.append([label, e])

    # Existing English — 2026 uses base price, 2027+ uses slider $/lb
    e_price = s["englishPricePerLb"]
    e_base_rev = EXISTING_E_ACRES * EXISTING_E_LBS_PER_AC * EXISTING_E_BASE_PRICE
    e_slider_rev = EXISTING_E_ACRES * EXISTING_E_LBS_PER_AC * e_price
    ee_rev = [0.0] * N_YEARS; ee_exp = [0.0] * N_YEARS
    for i, y in enumerate(YEARS):
        n = y - 2026
        rm = (1 + ri / 100) ** n; cm = (1 + ci / 100) ** n
        e_base = e_base_rev if y <= 2026 else e_slider_rev
        ee_rev[i] = e_base * rm
        ee_exp[i] = e_base * EXISTING_EXP_RATIO * cm
        rev[i] += ee_rev[i]; exp[i] += ee_exp[i]
    rev_rows.append(["Existing English", ee_rev]); exp_rows.append(["Existing English", ee_exp])

    # Existing lettuce — 2026=$3M, 2028+=lbs×$/lb, 2027=avg(2026,2028)
    l_2026 = EXISTING_L_REV
    l_2028 = s["lettuceLbs"] * s["lettucePrice"]
    l_2027 = (l_2026 + l_2028) / 2
    el_rev = [0.0] * N_YEARS; el_exp = [0.0] * N_YEARS
    for i, y in enumerate(YEARS):
        n = y - 2026
        rm = (1 + ri / 100) ** n; cm = (1 + ci / 100) ** n
        if y <= 2026:
            l_base = l_2026
        elif y == 2027:
            l_base = l_2027
        else:
            l_base = l_2028
        el_rev[i] = l_base * rm
        el_exp[i] = l_base * (EXISTING_EXP_RATIO if y <= 2026 else s["lettuceMrgnPct"] / 100) * cm
        rev[i] += el_rev[i]; exp[i] += el_exp[i]
    rev_rows.append(["Existing Lettuce", el_rev]); exp_rows.append(["Existing Lettuce", el_exp])

    # New crops — K, J, E, L, then T (matching CROP_DEFS order, TB excluded from table)
    crop_data = [] if debug else build_crops(s)
    dep_crops = []
    for crop in crop_data:
        cr, ce = ramp_crop_rev_exp(crop["base_rev"], crop["base_exp"], crop["end_q"], crop["ramp_years"], ri, ci)
        if not crop.get("is_buy"):
            for i in range(N_YEARS):
                rev[i] += cr[i]; exp[i] += ce[i]
            rev_rows.append([crop["label"], cr]); exp_rows.append([crop["label"], ce])
            dep_crops.append({"end_year": crop["end_year"], "capex": crop["capex"], "label": crop["label"]})

    # Startup capital: 3 months of expenses (at 100%) prefunded one quarter before production
    startup_exp = [0.0] * N_YEARS
    for crop in crop_data:
        if crop.get("is_buy"):
            continue
        sc = crop.get("startup_capital", 0)
        sc_yr = crop.get("startup_year")
        if sc and sc_yr and sc_yr in YEARS:
            n = sc_yr - 2026
            startup_exp[YEARS.index(sc_yr)] += sc * (1 + ci / 100) ** n
    if any(v != 0 for v in startup_exp):
        for i in range(N_YEARS):
            exp[i] += startup_exp[i]
        exp_rows.append(["Startup Capital", startup_exp])

    # Shared infrastructure: Land, Packhouse, Housing
    if not debug and crop_data:
        build_crops_only = [c for c in crop_data if not c.get("is_buy")]
        if build_crops_only:
            earliest_start_q = min(c["start_q"] for c in build_crops_only)

            # Packhouse
            pack_acres = s["packhouseAcres"]
            pack_capex = pack_acres * s["packhouseCostPerAc"]
            pack_start_q = s["packStartQ"]
            pack_build_mo = s["packBuildMonths"]
            pack_end_q = get_end_quarter(pack_start_q, pack_build_mo)
            crop_data.append({
                "key": "PACK", "label": "Packhouse", "acres": 0,
                "base_rev": 0, "base_exp": 0, "capex": pack_capex,
                "start_q": pack_start_q, "end_q": pack_end_q,
                "build_months": pack_build_mo, "ramp_years": 0,
                "end_year": pack_end_q // 10, "is_infra": True,
            })
            dep_crops.append({"end_year": pack_end_q // 10, "capex": pack_capex, "label": "Packhouse"})

            # Housing
            housing_pods = s["housingPods"]
            housing_capex = housing_pods * s["housingCostPerPod"]
            housing_start_q = s["housingStartQ"]
            housing_build_mo = s["housingBuildMonths"]
            housing_end_q = get_end_quarter(housing_start_q, housing_build_mo)
            crop_data.append({
                "key": "HOUSE", "label": "3BR Pods (6beds)", "acres": 0,
                "base_rev": 0, "base_exp": 0, "capex": housing_capex,
                "start_q": housing_start_q, "end_q": housing_end_q,
                "build_months": housing_build_mo, "ramp_years": 0,
                "end_year": housing_end_q // 10, "is_infra": True,
            })
            dep_crops.append({"end_year": housing_end_q // 10, "capex": housing_capex, "label": "Housing"})

            # Land (last row)
            land_acres = s["landAcres"]
            land_capex = land_acres * s["landCostPerAc"]
            land_start_q = s["landStartQ"]
            land_end_q = get_end_quarter(land_start_q, 3)  # ~1 quarter for acquisition
            crop_data.append({
                "key": "LAND", "label": "Land", "acres": 0,
                "base_rev": 0, "base_exp": 0, "capex": land_capex,
                "start_q": land_start_q, "end_q": land_end_q,
                "build_months": 3, "ramp_years": 0,
                "end_year": land_start_q // 10, "is_infra": True,
            })
            # Land is not depreciable — don't add to dep_crops

    # Total GH acres per year (existing + new as they come online)
    existing_acres = EXISTING_K_ACRES + EXISTING_J_ACRES + EXISTING_E_ACRES + EXISTING_L_ACRES
    total_acres = [existing_acres] * N_YEARS
    for crop in crop_data:
        if crop.get("is_buy"):
            continue
        end_year = crop["end_year"]
        for i, y in enumerate(YEARS):
            if y >= end_year:
                total_acres[i] += crop["acres"]

    return rev, exp, rev_rows, exp_rows, crop_data, dep_crops, total_acres


def allocate_capital_stack(crop_data, s):
    """Allocate bank/SBIC/3P/JJB across crops.

    Modes:
      jjb_only: bank + JJB equity (no co-investor)
      sbic:     bank + SBIC debt + JJB equity
      3p:       bank + JJB equity + 3P equity (above JJB cap)
      sbic_3p:  bank + SBIC debt + JJB equity + 3P equity (above JJB cap)

    Global allocation: bank fills first (up to bank_cap at bank_ltv).
    SBIC fills mezzanine debt (up to sbic_cap at target_ltv).
    JJB equity fills remainder (up to jjb_cap).
    3P fills any remaining equity need.
    """
    bank_cap = s["bankLoanCap"]
    bank_ltv = s["financingPct"] / 100
    target_ltv = s["targetLtv"] / 100
    jjb_cap = s["jjbEquityCap"]
    mode = s["financingMode"]
    sbic_cap = s.get("sbicCap", 25000000)

    build_crops = [c for c in crop_data if not c.get("is_buy")]
    total_capex = sum(c["capex"] for c in build_crops)
    total_startup = sum(c.get("startup_capital", 0) for c in build_crops)
    lendable_base = total_capex + total_startup

    # Step 1: bank fills first
    total_bank = min(total_capex * bank_ltv, bank_cap)
    total_sbic = 0
    total_3p = 0

    if mode == "jjb_only":
        total_jjb = lendable_base - total_bank

    elif mode == "sbic":
        total_debt = lendable_base * target_ltv
        total_bank = min(total_capex * bank_ltv, bank_cap, total_debt)
        total_sbic = min(total_debt - total_bank, sbic_cap)
        total_jjb = lendable_base - total_bank - total_sbic

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
        total_sbic = min(total_debt - total_bank, sbic_cap)
        equity_needed = lendable_base - total_bank - total_sbic
        if equity_needed <= jjb_cap:
            total_jjb = equity_needed
        else:
            total_jjb = jjb_cap
            total_3p = equity_needed - jjb_cap

    else:
        # Fallback: JJB covers everything after bank
        total_jjb = lendable_base - total_bank

    # Distribute pro-rata per crop by capex
    for crop in build_crops:
        capex = crop["capex"]
        pct = capex / total_capex if total_capex > 0 else 0
        crop["bank_loan"] = total_bank * pct
        crop["sbic_loan"] = total_sbic * pct
        crop["jjb_equity"] = total_jjb * pct
        crop["third_party_equity"] = total_3p * pct


def compute_kpis(s, crop_data, debug):
    """Compute KPI card values."""
    build_crops = [c for c in crop_data if not c.get("is_buy")]
    crop_capex = sum(c["capex"] for c in build_crops if not c.get("is_infra"))
    shared_capex = sum(c["capex"] for c in build_crops if c.get("is_infra"))
    startup_total = sum(c.get("startup_capital", 0) for c in build_crops)
    bank_total = sum(c.get("bank_loan", 0) for c in build_crops)
    sbic_total = sum(c.get("sbic_loan", 0) for c in build_crops)
    tp_total = sum(c.get("third_party_equity", 0) for c in build_crops)
    jjb_equity = sum(c.get("jjb_equity", 0) for c in build_crops) + startup_total
    return {
        "total_capex": crop_capex, "shared_capex": shared_capex,
        "bank_total": bank_total, "sbic_total": sbic_total,
        "tp_equity": tp_total, "jjb_equity": jjb_equity, "startup_capital": startup_total,
    }
