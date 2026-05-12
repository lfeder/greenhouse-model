"""Generate Excel snapshot of the expansion model for offline review.

7 sheets total:
  1. Inputs
  2-5. One sheet per scenario (Current, No-Expansion, 3-NO PH, 4-FULL PH)
       Each contains KPIs, Rev-Exp, Ownership, Waterfall, Partner Cash, Tax, Crop IRRs stacked.
  6. Scenario Compare (cross-scenario rev/EBITDA/per-partner)
  7. CF + Stake Summary

Usage:  python export_excel.py
"""
import json
from datetime import datetime
from pathlib import Path

import main
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

DIR = Path(__file__).parent

PRESETS = {
    "3-NO": dict(newKAcres=1.4, newJAcres=1.4, newEAcres=1.4, newLAcres=0, newTAcres=0,
                 packhouseAcres=0,   packhouseCostPerAc=4_000_000, housingPods=2, debug=False),
    "4-FULL": dict(newKAcres=1.4, newJAcres=1.4, newEAcres=2.8, newLAcres=0, newTAcres=0,
                   packhouseAcres=0.5, packhouseCostPerAc=4_000_000, housingPods=2, debug=False),
}
SCENARIO_ORDER = ["Current", "No Expansion", "3 blk · No PH", "4 blk · Full PH"]
SCENARIO_SHEETS = {"Current": "Current", "No Expansion": "No Exp",
                   "3 blk · No PH": "3-NO PH", "4 blk · Full PH": "4-FULL PH"}

HDR_FONT = Font(bold=True)
HDR_FILL = PatternFill(start_color="C8E6C9", end_color="C8E6C9", fill_type="solid")
SECTION_FILL = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
SECTION_FONT = Font(bold=True)
TOPIC_FILL = PatternFill(start_color="2E7D32", end_color="2E7D32", fill_type="solid")
TOPIC_FONT = Font(bold=True, color="FFFFFF", size=12)


def load_settings():
    return json.loads((DIR / "settings.json").read_text(encoding="utf-8"))


def write_topic_banner(ws, row, title, span):
    c = ws.cell(row=row, column=1, value=title)
    c.font = TOPIC_FONT
    c.fill = TOPIC_FILL
    for col in range(2, span + 1):
        ws.cell(row=row, column=col).fill = TOPIC_FILL
    return row + 1


def write_header(ws, row, labels):
    for col, lbl in enumerate(labels, 1):
        c = ws.cell(row=row, column=col, value=lbl)
        c.font = HDR_FONT
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal="center")
    return row + 1


def write_section(ws, row, title, span):
    c = ws.cell(row=row, column=1, value=title)
    c.font = SECTION_FONT
    c.fill = SECTION_FILL
    for col in range(2, span + 1):
        ws.cell(row=row, column=col).fill = SECTION_FILL
    return row + 1


def write_row(ws, row, label, values, fmt=None):
    ws.cell(row=row, column=1, value=label)
    for i, v in enumerate(values):
        c = ws.cell(row=row, column=i + 2, value=v)
        if fmt and isinstance(v, (int, float)):
            c.number_format = fmt
    return row + 1


def autosize(ws):
    for col in range(1, ws.max_column + 1):
        letter = get_column_letter(col)
        max_len = 12
        for row in range(1, ws.max_row + 1):
            v = ws.cell(row=row, column=col).value
            if v is not None:
                ln = len(str(v))
                if ln > max_len:
                    max_len = ln
        ws.column_dimensions[letter].width = min(max_len + 2, 32)


def _hdr_years(ws, r, Y):
    return write_header(ws, r, ["Row"] + [f"'{str(y)[-2:]}" for y in Y])


def write_scenario_sheet(wb, scenario_name, d):
    """Write one sheet containing all 7 topics stacked for this scenario."""
    sheet_name = SCENARIO_SHEETS[scenario_name]
    ws = wb.create_sheet(sheet_name)
    Y = d["years"]
    nY = len(Y)
    span = nY + 1
    r = 1

    # ── Header ─────────────────────────────────────────────────────────────
    c = ws.cell(row=r, column=1, value=f"Scenario: {scenario_name}")
    c.font = Font(bold=True, size=14, color="000000")
    r += 2

    # ── 1. KPIs ────────────────────────────────────────────────────────────
    r = write_topic_banner(ws, r, "1. KPIs", span)
    r = write_header(ws, r, ["Metric", "Value", "Detail"])
    last_idx = nY - 1 - 2  # _DN=2
    steady_rev = d["rev"][last_idx]
    steady_op = d["op_inc"][last_idx]
    bridge = (d.get("partners_no_exp") or {}).get("guarantee_total", 0) or 0
    combined_ds = [d["loan_total_ds"][i] + d["expansion_ds"][i] for i in range(nY)]
    kpi_rows = [
        ("$ Uses", d["total_capex"] + d["shared_capex"] + d.get("startup_capital", 0), "capex + startup"),
        ("  Crop capex", d["total_capex"], ""),
        ("  Shared infra", d["shared_capex"], ""),
        ("  Startup capital", d.get("startup_capital", 0), ""),
        ("$ Sources", d.get("bank_total", 0) + d.get("jjb_equity", 0), "bank + JJB equity"),
        ("  Bank loan", d.get("bank_total", 0), ""),
        ("  JJB equity", d.get("jjb_equity", 0), ""),
        ("  Bridge loan", bridge, ""),
        ("Steady Revenue", steady_rev, f"year {Y[last_idx]}"),
        ("Steady Op Income", steady_op, ""),
        ("Steady Op Margin", steady_op / steady_rev if steady_rev else 0, "ratio"),
        ("Peak debt service", max(combined_ds), ""),
        ("Total IRR (%)", d["total_irr"], "all new crops"),
        ("Total Unlev (%)", d.get("total_unlev", 0), ""),
    ]
    for lbl, v, dt in kpi_rows:
        ws.cell(row=r, column=1, value=lbl)
        c = ws.cell(row=r, column=2, value=v)
        if "%" in lbl or "Margin" in lbl:
            c.number_format = "0.00"
        else:
            c.number_format = "$#,##0"
        ws.cell(row=r, column=3, value=dt)
        r += 1
    r += 2

    # ── 2. Revenue & Expense ──────────────────────────────────────────────
    r = write_topic_banner(ws, r, "2. Revenue & Expense", span)
    r = _hdr_years(ws, r, Y)
    r = write_section(ws, r, "Revenue", span)
    for label, data in d.get("rev_rows", []):
        r = write_row(ws, r, label, data, "$#,##0")
    r = write_row(ws, r, "Total Revenue", d["rev"], "$#,##0")
    r = write_section(ws, r, "Expenses", span)
    for label, data in d.get("exp_rows", []):
        r = write_row(ws, r, label, data, "$#,##0")
    r = write_row(ws, r, "Total Expenses", d["exp"], "$#,##0")
    r = write_row(ws, r, "Operating Income", d["op_inc"], "$#,##0")
    r += 2

    # ── 3. Ownership ──────────────────────────────────────────────────────
    r = write_topic_banner(ws, r, "3. Ownership", span)
    r = _hdr_years(ws, r, Y)
    for p in ["EB", "JS", "JJB"]:
        r = write_row(ws, r, p + " %", [o.get(p, 0) for o in d["ownership"]], "0.00")
    if d.get("ownership_detail"):
        r = write_section(ws, r, "Ownership Detail", span)
        for row in d["ownership_detail"]:
            lbl = row[0]
            fmt = "$#,##0" if "($)" in lbl else "0.00"
            r = write_row(ws, r, lbl, row[1:], fmt)
    r += 2

    # ── 4. Waterfall ──────────────────────────────────────────────────────
    r = write_topic_banner(ws, r, "4. Waterfall", span)
    r = _hdr_years(ws, r, Y)
    r = write_row(ws, r, "Operating Income", d["op_inc"], "$#,##0")
    if d.get("jjb_startup_line"):
        r = write_row(ws, r, "JJB Startup Capital (add back)", d["jjb_startup_line"], "$#,##0")
    r = write_row(ws, r, "Existing DS", [-v for v in d["loan_total_ds"]], "$#,##0")
    r = write_row(ws, r, "Expansion DS", [-v for v in d["expansion_ds"]], "$#,##0")
    r = write_row(ws, r, "Capex Reserve", [-v for v in d["capex_res"]], "$#,##0")
    r = write_row(ws, r, "Distributable Cash", d["distrib_cash"], "$#,##0")
    r = write_section(ws, r, "Waterfall", span)
    r = write_row(ws, r, "Tax", [-v for v in d["tax_cash"]], "$#,##0")
    r = write_row(ws, r, "Tier 0 (EB)", [-v for v in d["tier0_actual"]], "$#,##0")
    r = write_row(ws, r, "Tier 1 (Partners)", [-v for v in d["tier1_actual"]], "$#,##0")
    r = write_row(ws, r, "PE/DD Repayment", [-v for v in d["pedd_payments"]], "$#,##0")
    r = write_row(ws, r, "Tier 2 (Pro-Rata)", [-v for v in d["tier2"]], "$#,##0")
    if any(v > 50 for v in d.get("tier1_shortfall", [])):
        r = write_row(ws, r, "T1 Shortfall -> DD", [-v for v in d["tier1_shortfall"]], "$#,##0")
    r += 2

    # ── 5. Partner Cash ───────────────────────────────────────────────────
    r = write_topic_banner(ws, r, "5. Partner Cash", span)
    r = _hdr_years(ws, r, Y)
    for p in ["EB", "JS", "JJB"]:
        pd = d.get("partners", {}).get(p)
        if not pd:
            continue
        r = write_section(ws, r, p, span)
        r = write_row(ws, r, "Tax Dist", pd["tax_dist"], "$#,##0")
        if pd.get("tier_0"):
            r = write_row(ws, r, "Tier 0", pd["tier_0"], "$#,##0")
        r = write_row(ws, r, "Tier 1", pd["tier_1"], "$#,##0")
        if pd.get("pedd"):
            r = write_row(ws, r, "PE/DD", pd["pedd"], "$#,##0")
        r = write_row(ws, r, "Tier 2", pd["tier_2"], "$#,##0")
        if pd.get("equity_buy_sell"):
            r = write_row(ws, r, "Equity Buy/Sell", pd["equity_buy_sell"], "$#,##0")
        r = write_row(ws, r, f"Total {p}", pd["total"], "$#,##0")
        ex_tax = [pd["total"][i] - pd["tax_dist"][i] for i in range(nY)]
        r = write_row(ws, r, f"After-Tax Cash ({p})", ex_tax, "$#,##0")
        ne_arr = (d.get("partners_no_exp") or {}).get(p)
        if ne_arr:
            r = write_row(ws, r, "No-Expansion baseline", ne_arr, "$#,##0")
            r = write_row(ws, r, "Expansion Impact",
                           [pd["total"][i] - ne_arr[i] for i in range(nY)], "$#,##0")
    r += 2

    # ── 6. Tax Detail ─────────────────────────────────────────────────────
    r = write_topic_banner(ws, r, "6. Tax", span)
    r = _hdr_years(ws, r, Y)
    r = write_row(ws, r, "Operating Income", d["op_inc"], "$#,##0")
    r = write_row(ws, r, "Total Interest",
                   [-v for v in d.get("total_interest", d["loan_total_int"])], "$#,##0")
    r = write_row(ws, r, "Taxable Income", d["taxable_inc"], "$#,##0")
    r = write_section(ws, r, "Federal", span)
    r = write_row(ws, r, "Bonus Depreciation", [-v for v in d["dep"]["fed"]], "$#,##0")
    r = write_row(ws, r, "Fed Taxable", [td["fed_taxable"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "EB Share", [td["eb_fed"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "NOL Used", [-td["nol_used"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "NOL Generated", [td["nol_generated"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "NOL Balance", [td["nol_remaining"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "EB Net Taxable", [td["eb_net_fed"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "Fed Tax (EB)", [-td["fed_tax"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "Fed Tax (entity)", [-td["fed_dist"] for td in d["tax_detail"]], "$#,##0")
    r = write_section(ws, r, "Hawaii", span)
    r = write_row(ws, r, "State Depreciation", [-v for v in d["dep"]["state"]], "$#,##0")
    r = write_row(ws, r, "HI Taxable", [td["hi_taxable"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "EB HI Taxable", [td["eb_hi"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "HI Tax (EB)", [-td["hi_tax"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "HI Tax (entity)", [-td["hi_dist"] for td in d["tax_detail"]], "$#,##0")
    r = write_row(ws, r, "Tax Liability", d["tax_liab"], "$#,##0")
    r = write_row(ws, r, "Tax Cash Dist", [-v for v in d["tax_cash"]], "$#,##0")
    r += 2

    # ── 7. Crop IRRs ──────────────────────────────────────────────────────
    irrs = d.get("crop_irrs", [])
    if irrs:
        r = write_topic_banner(ws, r, "7. Crop IRRs", 9)
        r = write_header(ws, r, ["Key", "Label", "Lev IRR %", "Unlev %", "Equity $",
                                   "Base Rev", "Base Exp", "Op Inc", "CapEx"])
        for ci in irrs:
            ud = ci.get("unlev_detail", {})
            row_data = [ci.get("key"), ci.get("label"), ci.get("irr"), ci.get("unlev"),
                        ci.get("equity"), ud.get("base_rev"), ud.get("base_exp"),
                        ud.get("op_inc"), ud.get("capex")]
            for col, val in enumerate(row_data, 1):
                c = ws.cell(row=r, column=col, value=val)
                if col in (5, 6, 7, 8, 9) and isinstance(val, (int, float)):
                    c.number_format = "$#,##0"
                elif col in (3, 4) and isinstance(val, (int, float)):
                    c.number_format = "0.0"
            r += 1

    autosize(ws)


# ============================================================
# Cross-scenario sheets
# ============================================================

def write_scenario_comparison(wb, scenarios, constants):
    any_d = next(iter(scenarios.values()))
    Y = any_d["years"]
    span = len(Y) + 1
    ws = wb.create_sheet("Scenario Compare")
    r = _hdr_years(ws, 1, Y)
    resol = constants.get("resolution_data", {})

    r = write_section(ws, r, "RESOLUTION (hardcoded baseline)", span)
    if resol:
        r = write_row(ws, r, "EBITDA", resol["EBITDA"] + [None, None, None], "$#,##0")
        for p in ["EB", "JS", "JJB"]:
            r = write_row(ws, r, f"{p} (with tax)", resol[p]["withTax"] + [None, None, None], "$#,##0")
            r = write_row(ws, r, f"{p} (after tax)", resol[p]["noTax"] + [None, None, None], "$#,##0")

    for name in SCENARIO_ORDER:
        d = scenarios[name]
        r = write_section(ws, r, name.upper(), span)
        if name == "No Expansion":
            ne = (d.get("partners_no_exp") or {}).get("_waterfall") or {}
            r = write_row(ws, r, "Revenue", ne.get("rev", d["rev"]), "$#,##0")
            r = write_row(ws, r, "EBITDA", ne.get("op_inc", d["op_inc"]), "$#,##0")
            tax = (d.get("partners_no_exp") or {}).get("_tax", {})
            for p in ["EB", "JS", "JJB"]:
                arr = (d.get("partners_no_exp") or {}).get(p, [0] * len(Y))
                r = write_row(ws, r, f"{p} (with tax)", arr, "$#,##0")
                no_tax = [arr[i] - tax.get(p, [0] * len(Y))[i] for i in range(len(Y))]
                r = write_row(ws, r, f"{p} (after tax)", no_tax, "$#,##0")
        else:
            r = write_row(ws, r, "Revenue", d["rev"], "$#,##0")
            r = write_row(ws, r, "EBITDA", d["op_inc"], "$#,##0")
            for p in ["EB", "JS", "JJB"]:
                pd = d.get("partners", {}).get(p, {})
                total = pd.get("total", [0] * len(Y))
                tax = pd.get("tax_dist", [0] * len(Y))
                r = write_row(ws, r, f"{p} (with tax)", total, "$#,##0")
                no_tax = [total[i] - tax[i] for i in range(len(Y))]
                r = write_row(ws, r, f"{p} (after tax)", no_tax, "$#,##0")
    autosize(ws)


def write_cf_stake_summary(wb, scenarios, constants):
    any_d = next(iter(scenarios.values()))
    Y = any_d["years"]
    ws = wb.create_sheet("CF + Stake Summary")
    r = write_header(ws, 1, ["Scenario", "EB", "JS", "JJB", "Detail"])
    resol = constants.get("resolution_data", {})

    r = write_section(ws, r, "Cum After-Tax CF ('26-'29)", 5)
    if resol:
        cum = {p: sum(resol[p]["noTax"][:4]) for p in ["EB", "JS", "JJB"]}
        r = write_row(ws, r, "Resolution",
                       [cum["EB"], cum["JS"], cum["JJB"], "Hardcoded PDF"], "$#,##0")
    for name in SCENARIO_ORDER:
        d = scenarios[name]
        if name == "No Expansion":
            partners_arr = d.get("partners_no_exp", {})
            tax = partners_arr.get("_tax", {})
            cum = {}
            for p in ["EB", "JS", "JJB"]:
                arr = partners_arr.get(p, [0] * len(Y))
                cum[p] = sum(arr[i] - tax.get(p, [0] * len(Y))[i] for i in range(4))
        else:
            cum = {}
            for p in ["EB", "JS", "JJB"]:
                pd = d.get("partners", {}).get(p, {})
                total = pd.get("total", [0] * len(Y))
                tax_d = pd.get("tax_dist", [0] * len(Y))
                cum[p] = sum(total[i] - tax_d[i] for i in range(4))
        r = write_row(ws, r, name, [cum["EB"], cum["JS"], cum["JJB"], ""], "$#,##0")

    r = write_section(ws, r, "Stake Value '33 (EBITDA × multiple × own%)", 5)
    multiples = {"Resolution": 7, "Current": 7, "No Expansion": 7,
                 "3 blk · No PH": 8.5, "4 blk · Full PH": 10}
    idx2033 = Y.index(2033) if 2033 in Y else len(Y) - 3
    if resol:
        ebitda33 = resol["EBITDA"][-1] * 1.04
        own = {"EB": 27.5, "JS": 22.5, "JJB": 50.0}
        mult = 7
        bv = ebitda33 * mult
        r = write_row(ws, r, "Resolution",
                       [bv * own["EB"] / 100, bv * own["JS"] / 100, bv * own["JJB"] / 100,
                        f"EBITDA {ebitda33/1e6:.2f}M x {mult}x = {bv/1e6:.2f}M biz"], "$#,##0")
    for name in SCENARIO_ORDER:
        d = scenarios[name]
        mult = multiples.get(name, 7)
        if name == "No Expansion":
            ne = (d.get("partners_no_exp") or {}).get("_waterfall", {})
            ebitda33 = ne.get("op_inc", [0] * len(Y))[idx2033]
            own = {"EB": 27.5, "JS": 22.5, "JJB": 50.0}
        else:
            ebitda33 = d.get("op_inc", [0] * len(Y))[idx2033]
            own_row = d.get("ownership", [{}])[idx2033] if d.get("ownership") else {}
            own = {p: own_row.get(p, 0) for p in ["EB", "JS", "JJB"]}
        bv = ebitda33 * mult
        r = write_row(ws, r, name,
                       [bv * own["EB"] / 100, bv * own["JS"] / 100, bv * own["JJB"] / 100,
                        f"EBITDA {ebitda33/1e6:.2f}M x {mult}x = {bv/1e6:.2f}M biz"], "$#,##0")
    autosize(ws)


def write_inputs(wb, settings):
    ws = wb.create_sheet("Inputs", 0)
    r = write_header(ws, 1, ["Key", "Value"])
    for k in sorted(settings.keys()):
        ws.cell(row=r, column=1, value=k)
        v = settings[k]
        if isinstance(v, (int, float, str, bool)):
            ws.cell(row=r, column=2, value=v)
        else:
            ws.cell(row=r, column=2, value=str(v))
        r += 1
    autosize(ws)


def main_export():
    settings = load_settings()
    constants = json.loads((DIR / "constants.json").read_text(encoding="utf-8"))

    print("Running scenarios...")
    scenarios = {}
    print("  Current...")
    scenarios["Current"] = main.run_everything(dict(settings))
    print("  No Expansion...")
    s_ne = dict(settings)
    s_ne.update({"newKAcres": 0, "newJAcres": 0, "newEAcres": 0, "newLAcres": 0, "newTAcres": 0,
                 "packhouseAcres": 0, "housingPods": 0})
    scenarios["No Expansion"] = main.run_everything(s_ne)
    print("  3 blk No PH...")
    s3 = dict(settings); s3.update(PRESETS["3-NO"])
    scenarios["3 blk · No PH"] = main.run_everything(s3)
    print("  4 blk Full PH...")
    s4 = dict(settings); s4.update(PRESETS["4-FULL"])
    scenarios["4 blk · Full PH"] = main.run_everything(s4)

    wb = Workbook()
    wb.remove(wb.active)

    # Sheet 1: Inputs
    write_inputs(wb, settings)

    # Sheets 2-5: one per scenario, all 7 topics stacked
    for name in SCENARIO_ORDER:
        write_scenario_sheet(wb, name, scenarios[name])

    # Sheets 6-7: cross-scenario
    write_scenario_comparison(wb, scenarios, constants)
    write_cf_stake_summary(wb, scenarios, constants)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = DIR / f"expansion_export_{stamp}.xlsx"
    wb.save(out)
    print(f"\nWrote: {out}")
    print(f"  Size: {out.stat().st_size / 1024:.1f} KB")
    print(f"  Sheets ({len(wb.sheetnames)}): {', '.join(wb.sheetnames)}")
    return out


if __name__ == "__main__":
    main_export()
