"""Run the local daily strategy dashboard with Python 3.9+."""

import argparse
import json
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from quant_sim.backtest import Research
from quant_sim.context import MarketContext, update_public
from quant_sim.experiments import EXPERIMENTS, ROOT, run_experiments
from quant_sim.lab import CORRECTED_BENCHMARK, Extension, PolicyLab
from quant_sim.market import (
    DATA_DIR, NEW_YORK, SYMBOLS, DataError, csv_text, download_snapshot,
    latest_due_session, load_snapshot, save_snapshot,
)
from quant_sim.scenarios import CHOICES, HORIZON, ScenarioStudy
from quant_sim.policies import REGISTRY

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/charts.js": ("charts.js", "text/javascript"),
    "/styles.css": ("styles.css", "text/css"),
    "/scenarios": ("scenarios.html", "text/html"),
    "/scenarios.js": ("scenarios.js", "text/javascript"),
    "/research": ("research.html", "text/html"),
    "/research.js": ("research.js", "text/javascript"),
}


class DashboardData:
    def __init__(self, snapshot):
        self.research = Research(snapshot)
        self.study = ScenarioStudy(snapshot)
        self.extension = Extension(PolicyLab(snapshot), snapshot)


class Dashboard(ThreadingHTTPServer):
    def __init__(self, port):
        self.data = DashboardData(load_snapshot())
        self.refresh_lock = Lock()
        super().__init__(("127.0.0.1", port), Handler)

    def summary(self, data):
        due = latest_due_session(datetime.now(NEW_YORK)).isoformat()
        summary = data.research.summary()
        symbols = {symbol: {**entry, "strategies": [*entry["strategies"], *data.extension.summary[symbol]]}
                   for symbol, entry in summary["symbols"].items()}
        return {**summary, "symbols": symbols, "expected_session": due}


class Handler(BaseHTTPRequestHandler):
    def send_payload(self, body, content_type, status=HTTPStatus.OK, filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_payload(body, "application/json", status)

    def do_GET(self):
        path = urlsplit(self.path).path
        data = self.server.data
        research = data.research
        if path in ASSETS:
            filename, content_type = ASSETS[path]
            self.send_payload((STATIC / filename).read_bytes(), content_type)
            return
        if path == "/api/summary":
            self.send_json(self.server.summary(data))
            return
        if path in ("/api/research", "/api/research/config"):
            filename = ROOT / "reports/multisignal_summary.json"
            if filename.exists():
                summary = json.loads(filename.read_text(encoding="utf-8"))
                if path.endswith("/config"):
                    config = ROOT / "reports/runs" / Path(summary["run_id"]).name / "config.json"
                    if config.is_file():
                        self.send_payload(config.read_bytes(), "application/json", filename="config.json")
                        return
                else:
                    self.send_json(summary)
                    return
            self.send_json({"error": "请先运行 python3 app.py --experiment all。"}, HTTPStatus.NOT_FOUND)
            return
        if path == "/api/scenarios":
            due = latest_due_session(datetime.now(NEW_YORK)).isoformat()
            self.send_json({**data.study.summary(), "expected_session": due,
                            "strategies": [*CHOICES, *data.extension.scenario_choices()]})
            return
        if path.startswith("/api/scenarios/"):
            params = path[len("/api/scenarios/") :].split("/")
            if len(params) == 2 and params[0] in SYMBOLS and params[1] in dict(CHOICES):
                self.send_json(data.study.result(*params, diagnostics=True))
                return
            if len(params) == 2 and params[0] in SYMBOLS and (params[1] in REGISTRY or params[1] == CORRECTED_BENCHMARK):
                if data.study.reference is None:
                    self.send_json({"actual": None, "scenarios": []})
                    return
                def window(index):
                    days = data.study.days
                    return data.extension.lab.study_window(*params, days[index], days[min(index + HORIZON, len(days) - 1)])
                self.send_json({"actual": window(data.study.reference),
                                "scenarios": [window(index) for index, _ in data.study.matches]})
                return
        if path.startswith("/api/detail/"):
            key = tuple(path[len("/api/detail/") :].split("/"))
            detail = (research.details.get(key) or data.extension.detail(*key)) if len(key) == 2 else None
            if detail:
                self.send_json(detail)
                return
        if path.startswith("/api/data/"):
            filename = path[len("/api/data/") :]
            for symbol in SYMBOLS:
                if filename == f"{symbol}.csv":
                    body = csv_text(research.snapshot, symbol).encode("utf-8")
                    self.send_payload(body, "text/csv", filename=filename)
                    return
        self.send_json({"error": "页面或数据不存在。"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/refresh":
            self.send_json({"error": "页面或数据不存在。"}, HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            self.send_json({"error": "请从本地面板更新行情。"}, HTTPStatus.FORBIDDEN)
            return
        if not self.server.refresh_lock.acquire(blocking=False):
            self.send_json({"error": "行情更新正在进行。"}, HTTPStatus.CONFLICT)
            return
        try:
            snapshot = download_snapshot()
            update_public(datetime.fromisoformat(snapshot["as_of"]).date())
            data = DashboardData(snapshot)
            save_snapshot(snapshot)
            self.server.data = data
        except (DataError, OSError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return
        finally:
            self.server.refresh_lock.release()
        self.send_json(self.server.summary(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="Fetch and validate a fresh daily snapshot.")
    mode.add_argument("--export-data", action="store_true", help="Write the three full-history CSVs.")
    mode.add_argument("--update-context", action="store_true", help="Refresh VIX, ETF proxies and price prehistory.")
    mode.add_argument("--validate-context", action="store_true", help="Print point-in-time coverage and data problems.")
    mode.add_argument("--experiment", choices=("all", *EXPERIMENTS), help="Run a frozen local research experiment suite.")
    args = parser.parse_args()

    try:
        if args.refresh:
            snapshot = download_snapshot()
            update_public(datetime.fromisoformat(snapshot["as_of"]).date())
            DashboardData(snapshot)
            save_snapshot(snapshot)
            print(f'Yahoo Finance · {snapshot["as_of"]} · snapshot saved')
            return
        if args.update_context:
            result = update_public(datetime.fromisoformat(load_snapshot()["as_of"]).date())
            print(json.dumps({"cached": list(result["series"]), "errors": result["errors"]}, ensure_ascii=False))
            return
        if args.validate_context:
            context = MarketContext(load_snapshot())
            print(json.dumps({"coverage": context.coverage(), "errors": context.errors}, ensure_ascii=False, indent=2))
            return
        if args.experiment:
            print(run_experiments(load_snapshot(), args.experiment))
            return
        if args.export_data:
            snapshot = load_snapshot()
            for symbol in SYMBOLS:
                (DATA_DIR / f"{symbol}.csv").write_text(csv_text(snapshot, symbol), encoding="utf-8")
            print(f'QQQ, SOXX, SPY · CSV exported through {snapshot["as_of"]}')
            return
        with Dashboard(args.port) as server:
            print(f"Daily strategy dashboard · http://127.0.0.1:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except (DataError, OSError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
