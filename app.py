from flask import Flask, render_template, request, jsonify
import json
import os

app = Flask(__name__)
DATA_FILE = "data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/cells", methods=["GET"])
def get_cells():
    return jsonify(load_data())

@app.route("/api/cells/<cell_id>", methods=["POST"])
def update_cell(cell_id):
    data = load_data()
    payload = request.json
    data[cell_id] = payload
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/cells/<cell_id>", methods=["DELETE"])
def delete_cell(cell_id):
    data = load_data()
    if cell_id in data:
        del data[cell_id]
        save_data(data)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
