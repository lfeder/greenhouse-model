"""
Flask server for v2. Thin wrapper — all math in model.py.
Start: python server.py → http://localhost:5050
"""

from flask import Flask, send_from_directory, request, jsonify
from pathlib import Path
import json
import threading
import time
import model

app = Flask(__name__, static_folder=".")
DIR = Path(__file__).parent
SETTINGS_PATH = DIR / "settings.json"

# Background gsheet writer: updates every 10s if data changed
_gsheet_data = None
_gsheet_dirty = False
_gsheet_lock = threading.Lock()

def _gsheet_worker():
    global _gsheet_dirty
    while True:
        time.sleep(10)
        with _gsheet_lock:
            if _gsheet_dirty and _gsheet_data:
                _gsheet_dirty = False
                data = _gsheet_data
        if data:
            try:
                model.write_gsheet(data)
                print("[gsheet] updated")
            except Exception as e:
                print(f"[gsheet] error: {e}")
            data = None

def _queue_gsheet(data):
    global _gsheet_data, _gsheet_dirty
    with _gsheet_lock:
        _gsheet_data = data
        _gsheet_dirty = True


def load_settings():
    with open(SETTINGS_PATH) as f:
        return json.load(f)

def save_settings(s):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(s, f, indent=2)


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
    result = model.run_everything(settings)
    _queue_gsheet(result)
    return jsonify(result)


if __name__ == "__main__":
    print("Starting server at http://localhost:5050")
    print("Slider changes auto-save to settings.json")
    print("output.csv updated on every computation")
    print("Google Sheet updates every 10s when data changes")
    t = threading.Thread(target=_gsheet_worker, daemon=True)
    t.start()
    app.run(port=5050, debug=True, use_reloader=False)
