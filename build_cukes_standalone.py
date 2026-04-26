"""Build a single-file standalone version of cukes.html.

Fetches sheet data via gviz, computes _summary aggregates exactly as
cukes.html does at runtime, and emits cukes-standalone.html with the
data baked in. No live fetches, no local server, no /api/settings —
everything self-contained. Re-run any time to refresh the snapshot.
"""

from __future__ import annotations
import datetime
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
SHEET_GROW = "1VtEecYn-W1pbnIU1hRHfxIpkH2DtK7hj0CpcpiLoziM"
SHEET_INVOICES = "124y8JdWXmbf_hb1vfimHmGaKLVXrRHybw02w_ozCExE"
GROW_TAB = "grow_C_harvest"
INVOICE_GIDS = ["1254110782", "544460225"]

YEARS = [2025, 2026]
CUTOFF = {2025: 12, 2026: 3}
TRANSPLANT_OFFSET_DAYS = 14


def gviz_fetch(sheet_id: str, *, gid: str | None = None, tab: str | None = None) -> dict:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:json"
    if gid:
        url += f"&gid={gid}"
    if tab:
        url += f"&sheet={tab}"
    raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    m = re.search(r"google\.visualization\.Query\.setResponse\((.+)\);?\s*$", raw, re.S)
    if not m:
        raise RuntimeError(f"unexpected gviz payload from {url[:80]}…")
    payload = json.loads(m.group(1))
    return payload["table"] if "table" in payload else payload


def parse_rows(table: dict) -> list[dict]:
    """Mirror cukes.html parseGvizTable: only HarvestDate/InvoiceDate populates _y/_m/_d."""
    cols = [c.get("label") for c in table["cols"]]
    rows: list[dict] = []
    for r in table["rows"]:
        obj: dict = {}
        for i, cell in enumerate(r["c"]):
            col = cols[i]
            if not col:
                continue
            if cell is None or cell.get("v") is None:
                obj[col] = ""
                continue
            v = cell["v"]
            if isinstance(v, str) and v.startswith("Date("):
                m = re.match(r"Date\((\d+),(\d+),(\d+)", v)
                if m:
                    obj[col] = cell.get("f") or v
                    if col in ("HarvestDate", "InvoiceDate"):
                        obj["_y"] = int(m.group(1))
                        obj["_m"] = int(m.group(2)) + 1
                        obj["_d"] = int(m.group(3))
            else:
                obj[col] = v
        rows.append(obj)
    return rows


def is_grade1(r: dict) -> bool:
    g = r.get("Grade")
    try:
        return round(float(g)) == 1
    except (TypeError, ValueError):
        return False


def is_cuke(r: dict) -> bool:
    farm = str(r.get("Farm", "")).lower()
    variety = str(r.get("Variety", "")).lower()
    return farm in ("cuke", "cucumber") or variety in ("cuke", "cucumber")


def classify_kje(s) -> str | None:
    c = str(s or "").strip().upper()[:1]
    return c if c in ("K", "J", "E") else None


def aggregate(rows, filter_fn, value_fn) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        if not filter_fn(r):
            continue
        y, m = r.get("_y"), r.get("_m")
        if not y or not m:
            continue
        if y not in YEARS or m > CUTOFF[y]:
            continue
        v = value_fn(r) or 0
        key = f"{y}-{m}"
        out[key] = out.get(key, 0) + v
    return out


def grow_by_vm(rows) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        if not is_grade1(r):
            continue
        y, m = r.get("_y"), r.get("_m")
        if not y or not m or y not in YEARS or m > CUTOFF[y]:
            continue
        v = classify_kje(r.get("Variety"))
        if not v:
            continue
        lbs = r.get("GreenhouseNetWeight") or 0
        key = f"{v}-{y}-{m}"
        out[key] = out.get(key, 0) + lbs
    return out


def retire_weeks_by_month(rows) -> dict[str, float]:
    """Mirror cukes.html: bucket cycles by (year, last-harvest month)."""
    now = datetime.datetime.utcnow() - datetime.timedelta(hours=10)
    cur_y, cur_m = now.year, now.month

    by_cycle: dict[str, dict] = {}
    for r in rows:
        sc = r.get("SeedingCycle")
        if not sc:
            continue
        y, m, d = r.get("_y"), r.get("_m"), r.get("_d")
        if not y or not m:
            continue
        try:
            hd = float(r.get("HarvestDay"))
        except (TypeError, ValueError):
            continue
        if hd < 0:
            continue
        date_key = y * 10000 + m * 100 + (d or 0)
        c = by_cycle.setdefault(sc, {"lastDateKey": 0, "lastY": 0, "lastM": 0, "maxDay": 0})
        if date_key > c["lastDateKey"]:
            c["lastDateKey"] = date_key
            c["lastY"] = y
            c["lastM"] = m
        if hd > c["maxDay"]:
            c["maxDay"] = hd

    buckets: dict[str, list[float]] = {}
    for c in by_cycle.values():
        if c["lastY"] == cur_y and c["lastM"] == cur_m:
            continue
        key = f"{c['lastY']}-{c['lastM']}"
        buckets.setdefault(key, []).append(c["maxDay"])

    out = {}
    for key, arr in buckets.items():
        avg = sum(arr) / len(arr)
        out[key] = (avg - TRANSPLANT_OFFSET_DAYS) / 7
    return out


def compute_summary():
    print("Fetching grow sheet…")
    grow_tbl = gviz_fetch(SHEET_GROW, tab=GROW_TAB)
    grow_rows = parse_rows(grow_tbl)

    print("Fetching invoices…")
    inv_rows: list[dict] = []
    for gid in INVOICE_GIDS:
        inv_rows.extend(parse_rows(gviz_fetch(SHEET_INVOICES, gid=gid)))

    grow_totals = aggregate(grow_rows, is_grade1, lambda r: r.get("GreenhouseNetWeight"))
    sale_totals = aggregate(inv_rows, lambda r: is_cuke(r) and is_grade1(r), lambda r: r.get("Pounds"))
    grade1_grow = [r for r in grow_rows if is_grade1(r)]
    retire_weeks = retire_weeks_by_month(grade1_grow)
    gbvm = grow_by_vm(grow_rows)

    price_num = {"K": 0.0, "J": 0.0, "E": 0.0}
    price_den = {"K": 0.0, "J": 0.0, "E": 0.0}
    sale_by_vm: dict[str, float] = {}
    for r in inv_rows:
        if not (is_cuke(r) and is_grade1(r)):
            continue
        v = classify_kje(r.get("ProductCode"))
        if not v:
            continue
        try:
            lbs = float(r.get("Pounds") or 0)
            dlrs = float(r.get("Dollars") or 0)
        except (TypeError, ValueError):
            continue
        if lbs <= 0:
            continue
        y, m = r.get("_y"), r.get("_m")
        if not y or not m or y not in YEARS or m > CUTOFF[y]:
            continue
        price_num[v] += dlrs
        price_den[v] += lbs
        key = f"{v}-{y}-{m}"
        sale_by_vm[key] = sale_by_vm.get(key, 0) + lbs

    prices = {v: (price_num[v] / price_den[v]) if price_den[v] > 0 else 0 for v in "KJE"}

    return {
        "timestamp": int(datetime.datetime.utcnow().timestamp() * 1000),
        "growTotals": grow_totals,
        "saleTotals": sale_totals,
        "growByVM": gbvm,
        "saleByVM": sale_by_vm,
        "retireWeeks": retire_weeks,
        "prices": prices,
    }


def build_html(summary: dict) -> str:
    cukes = (ROOT / "cukes.html").read_text(encoding="utf-8")
    style = (ROOT / "style.css").read_text(encoding="utf-8")
    utils = (ROOT / "utils.js").read_text(encoding="utf-8")

    # Inline external stylesheet + utils.js. Strip the Google Fonts link so the
    # file works offline; system mono is fine.
    cukes = re.sub(
        r'<link href="https://fonts\.googleapis\.com/[^"]+" rel="stylesheet">\s*',
        "",
        cukes,
    )
    cukes = cukes.replace(
        '<link rel="stylesheet" href="style.css">',
        f"<style>\n{style}\n</style>",
    )
    cukes = cukes.replace(
        '<script src="utils.js"></script>',
        f"<script>\n{utils}\n</script>",
    )

    # Snapshot timestamp instead of server-side {{VERSION}}.
    stamp = (datetime.datetime.utcnow() - datetime.timedelta(hours=10)).strftime("%m-%d %H:%M")
    cukes = cukes.replace("{{VERSION}}", f"{stamp} (snapshot)")

    # Replace settings/constants fetch + sheet fetch with embedded data.
    constants = json.loads((ROOT / "constants.json").read_text(encoding="utf-8"))
    settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))

    inject = (
        f"const _SNAPSHOT = {json.dumps(summary, separators=(',', ':'))};\n"
        f"const _SNAPSHOT_SETTINGS = {json.dumps(settings, separators=(',', ':'))};\n"
        f"const _SNAPSHOT_CONSTANTS = {json.dumps(constants, separators=(',', ':'))};\n"
    )

    cukes = cukes.replace(
        "async function loadSettingsConstants() {\n"
        "  const [s, c] = await Promise.all([\n"
        "    fetch('/api/settings').then(r => r.json()),\n"
        "    fetch('/api/constants').then(r => r.json()),\n"
        "  ]);\n"
        "  _settings = s;\n"
        "  _constants = c;\n"
        "}",
        inject + "async function loadSettingsConstants() {\n"
        "  _settings = _SNAPSHOT_SETTINGS;\n"
        "  _constants = _SNAPSHOT_CONSTANTS;\n"
        "}",
    )

    cukes = cukes.replace(
        "async function load() {\n"
        "  try {\n"
        "    await loadSettingsConstants();\n"
        "    const cached = loadCache();\n"
        "    if (cached) {\n"
        "      _summary = cached;\n"
        "      renderAll();\n"
        "      return;\n"
        "    }\n"
        "    document.getElementById('status').textContent = 'Fetching from Sheets…';\n"
        "    _summary = await fetchSheetSummary();\n"
        "    saveCache(_summary);\n"
        "    renderAll();",
        "async function load() {\n"
        "  try {\n"
        "    await loadSettingsConstants();\n"
        "    _summary = _SNAPSHOT;\n"
        "    renderAll();",
    )

    # Drop the cross-page nav (this is a single standalone file).
    cukes = cukes.replace(
        "<script>document.getElementById('nav').innerHTML = buildNav('cukes.html'); initTheme();</script>",
        "<script>initTheme();</script>",
    )

    return cukes


def main():
    summary = compute_summary()
    html = build_html(summary)
    out = ROOT / "cukes-standalone.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out} ({len(html):,} bytes)")
    print(
        f"Prices  K=${summary['prices']['K']:.2f}  "
        f"J=${summary['prices']['J']:.2f}  E=${summary['prices']['E']:.2f}"
    )
    print(f"retireWeeks buckets: {len(summary['retireWeeks'])}")


if __name__ == "__main__":
    main()
