"""
Flask server for v2. Thin wrapper — all math in main.py.
Start: python server.py → http://localhost:5050
"""

from flask import Flask, send_from_directory, request, jsonify, Response
from pathlib import Path
from datetime import datetime, timezone, timedelta
import subprocess
import json
import main

app = Flask(__name__, static_folder=".")
DIR = Path(__file__).parent
SETTINGS_PATH = DIR / "settings.json"


def _version_stamp():
    """HST MM-DD HH:MM from last git commit time. Falls back to current time."""
    try:
        ts = int(subprocess.check_output(
            ["git", "log", "-1", "--format=%ct"], cwd=DIR, text=True).strip())
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        dt = datetime.now(timezone.utc)
    hst = dt.astimezone(timezone(timedelta(hours=-10)))
    return hst.strftime("%m-%d %H:%M")


def serve_html(name):
    html = (DIR / name).read_text(encoding="utf-8")
    html = html.replace("{{VERSION}}", _version_stamp())
    return Response(html, mimetype="text/html")


def load_settings():
    with open(SETTINGS_PATH) as f:
        return json.load(f)

def save_settings(s):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(s, f, indent=2)


@app.route("/")
def index():
    return serve_html("index.html")

@app.route("/<path:path>")
def static_files(path):
    if path.endswith(".html") and (DIR / path).exists():
        return serve_html(path)
    return send_from_directory(".", path)

@app.route("/api/settings")
def get_settings():
    return jsonify(load_settings())

@app.route("/api/constants")
def get_constants():
    with open(DIR / "constants.json") as f:
        return jsonify(json.load(f))

@app.route("/api/save_scenario", methods=["POST"])
def api_save_scenario():
    body = request.json or {}
    scenario = body.get("scenario")
    overrides = body.get("overrides", {})
    if scenario not in ("A", "B", "C"):
        return jsonify({"error": "Invalid scenario"}), 400
    settings = load_settings()
    es = settings.get("expansion_scenarios", {})
    es[scenario] = overrides
    settings["expansion_scenarios"] = es
    save_settings(settings)
    return jsonify({"ok": True})

@app.route("/api/compute", methods=["POST"])
def api_compute():
    params = request.json or {}
    settings = load_settings()
    settings.update(params)
    save_settings(settings)
    result = main.run_everything(settings)
    return jsonify(result)


if __name__ == "__main__":
    print("Starting server at http://localhost:5050")
    print("Slider changes auto-save to settings.json")
    app.run(port=5050, debug=True, use_reloader=True,
            extra_files=[str(DIR / "main.py"), str(DIR / "constants.json")])
