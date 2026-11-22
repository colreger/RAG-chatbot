"""Content-free local HTTP telemetry with bounded labels."""

from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
import os
import sys
from threading import Lock
from time import perf_counter
from uuid import uuid4

ROUTES = frozenset({"/claims/{claim_id}", "/ask", "/field/investigate"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
EXCLUDED = frozenset({"/healthz", "/ops/metrics"})


def request_logger():
    logger = logging.getLogger("support.requests")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    if os.environ.get("RAG_LOG_REQUESTS", "false").lower() == "true" and not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


class RequestTelemetry:
    def __init__(self):
        self.lock = Lock()
        self.rows = defaultdict(lambda: {"count": 0, "duration_ms_total": 0.0, "duration_ms_max": 0.0})
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.logger = request_logger()

    def record(self, *, request_id, route, method, status_code, duration_ms):
        route = route if route in ROUTES else "unmatched"
        method = method if method in METHODS else "OTHER"
        status_code = status_code if 100 <= status_code <= 599 else 500
        duration_ms = max(0, duration_ms)
        with self.lock:
            row = self.rows[route, method, status_code]
            row["count"] += 1
            row["duration_ms_total"] += duration_ms
            row["duration_ms_max"] = max(row["duration_ms_max"], duration_ms)
        self.logger.info(json.dumps({
            "event": "http_request", "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id, "route": route, "method": method,
            "status_code": status_code, "duration_ms": round(duration_ms, 3),
        }))

    def snapshot(self):
        with self.lock:
            metrics = [
                {"route": route, "method": method, "status_code": status, "count": row["count"],
                 "duration_ms_total": round(row["duration_ms_total"], 3),
                 "duration_ms_max": round(row["duration_ms_max"], 3)}
                for (route, method, status), row in sorted(self.rows.items())
            ]
        return {"scope": "process-local HTTP aggregates", "started_at": self.started_at, "metrics": metrics}


class RequestTelemetryMiddleware:
    def __init__(self, app, telemetry):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        started = perf_counter()
        status = 500
        sent_headers = False

        async def observed_send(message):
            nonlocal status, sent_headers
            if message["type"] == "http.response.start":
                status = message["status"]
                sent_headers = True
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() not in (b"x-request-id", b"cache-control")]
                message = {**message, "headers": headers + [
                    (b"x-request-id", request_id.encode("ascii")), (b"cache-control", b"no-store"),
                ]}
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except Exception:
            status = 500
            if sent_headers:
                # A completed HTTP status cannot be replaced. Suppress the
                # original exception body; streaming is outside this demo.
                raise RuntimeError("Response failed after headers were sent") from None
            body = b'{"detail":"Internal service error"}'
            await observed_send({"type": "http.response.start", "status": 500, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii")),
            ]})
            await observed_send({"type": "http.response.body", "body": body})
        finally:
            route = getattr(scope.get("route"), "path", None)
            if route not in EXCLUDED:
                self.telemetry.record(
                    request_id=request_id, route=route, method=scope.get("method", "OTHER"),
                    status_code=status, duration_ms=(perf_counter() - started) * 1000,
                )
