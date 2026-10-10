from flask import Flask, jsonify, render_template, request, send_file

from engine import data, export, simulate

app = Flask(__name__)


@app.get("/")
def simulator_page():
    return render_template("simulator.html", active="simulator")


@app.get("/pnl")
def pnl_page():
    return render_template("pnl.html", active="pnl")


@app.get("/api/meta")
def meta():
    return jsonify({
        "chains": [{"id": c["id"], "name": c["name"]} for c in data.CHAINS],
        "states": [{"id": s["id"], "name": s["name"]} for s in data.STATES],
        "skus": [{"id": s["id"], "name": s["name"], "category": s["category"]}
                 for s in data.SKUS],
        "dims": [{"id": "chain", "name": "Chain"}, {"id": "state", "name": "State"},
                 {"id": "so", "name": "Sales office"}, {"id": "sku", "name": "SKU"}],
    })


def _payload():
    body = request.get_json(silent=True) or {}
    return body.get("levers"), body.get("scope"), body.get("dims")


@app.post("/api/simulate")
def api_simulate():
    levers, scope, _ = _payload()
    return jsonify(simulate.simulate(levers, scope))


@app.post("/api/pnl")
def api_pnl():
    levers, scope, dims = _payload()
    return jsonify(simulate.pnl(levers, scope, dims))


@app.post("/api/export")
def api_export():
    levers, scope, dims = _payload()
    buf = export.build_workbook(levers, scope, dims)
    return send_file(buf, as_attachment=True, download_name="heritageedge-pnl.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
