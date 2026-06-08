import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import Flask, Response, g, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest


LOG_FILE = os.getenv("APP_LOG_FILE", "/var/log/app/app.log")

app = Flask(__name__)

REQUEST_COUNTER = Counter(
    "app_requests",
    "Total HTTP requests handled by the application.",
    ["method", "path", "status"],
)
ERROR_COUNTER = Counter(
    "app_errors",
    "Total application errors returned by the application.",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "app_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path"],
)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "message": record.getMessage(),
        }

        extra = getattr(record, "extra_fields", {})
        if extra:
            payload.update(extra)

        return json.dumps(payload, separators=(",", ":"))


def build_logger():
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("observability_lab")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = JsonFormatter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)

    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = build_logger()


@app.before_request
def before_request():
    g.start_time = time.perf_counter()
    g.trace_id = str(uuid4())


@app.after_request
def after_request(response):
    if request.path != "/metrics":
        method = request.method
        path = request.path
        status = str(response.status_code)
        duration = time.perf_counter() - g.start_time

        REQUEST_COUNTER.labels(method=method, path=path, status=status).inc()
        REQUEST_LATENCY.labels(method=method, path=path).observe(duration)

        if response.status_code >= 500:
            ERROR_COUNTER.labels(method=method, path=path, status=status).inc()

        logger.info(
            "request completed",
            extra={
                "extra_fields": {
                    "trace_id": g.trace_id,
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "duration_ms": round(duration * 1000, 2),
                    "remote_addr": request.headers.get("X-Forwarded-For", request.remote_addr),
                }
            },
        )

    return response


@app.route("/")
def index():
    return {
        "service": "observability-lab-app",
        "status": "ok",
        "endpoints": ["/health", "/work", "/fail", "/metrics"],
    }


@app.route("/health")
def health():
    return {"status": "healthy"}


@app.route("/work")
def work():
    time.sleep(random.uniform(0.03, 0.25))
    return {"result": "completed"}


@app.route("/fail")
def fail():
    logger.error(
        "simulated application failure",
        extra={
            "extra_fields": {
                "trace_id": getattr(g, "trace_id", str(uuid4())),
                "method": request.method,
                "path": request.path,
                "status": 500,
            }
        },
    )
    return {"error": "simulated failure"}, 500


@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
