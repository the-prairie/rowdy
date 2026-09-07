"""Native v2 read-only actions. No shell, Core, warehouse, or source-write path.

Results belong to one buffer and frozen registered context. Traces are observations
of the saved-source snapshot, never claims about the physical warehouse or an
unsaved candidate. This module is also exercised through the actual stdio bridge.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler

from . import __version__
from .bridge import MAX_RESPONSE, NoRedirect, runtime, strict_json, validate_receipt
from .project import digest

PROTOCOL = "rowdy.native/2"
class EvaluationUnavailable(ValueError):
    """A local SQL edit could not be evaluated; retain last-good evidence."""


BASE_FIELDS = {"protocol", "request_id", "session", "request", "path", "sql", "operation"}


def validate(payload):
    if not isinstance(payload, dict) or not BASE_FIELDS <= payload.keys():
        raise ValueError("Complete native identity is required")
    operation = payload["operation"]
    extras = {"expected_binding", "stage"} if operation in ("preview", "verify") else {"origin_id", "row_index", "column"} if operation == "trace" else None
    if extras is None or payload.keys() - (BASE_FIELDS | extras) or payload["protocol"] != PROTOCOL:
        raise ValueError("Native action or protocol is not enabled")
    for field in ("request_id", "session"):
        if not isinstance(payload[field], str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", payload[field]):
            raise ValueError("Invalid native identity")
    if type(payload["request"]) is not int or not 0 <= payload["request"] < 2**63:
        raise ValueError("Invalid native generation")
    if not isinstance(payload["path"], str) or not Path(payload["path"]).is_absolute() or "\0" in payload["path"] or not payload["path"].endswith(".sql"):
        raise ValueError("A registered absolute SQL path is required")
    if not isinstance(payload["sql"], str) or len(payload["sql"].encode()) > 65_536:
        raise ValueError("Native buffer exceeds budget")
    if "expected_binding" in payload and (not isinstance(payload["expected_binding"], str) or not re.fullmatch(r"[a-f0-9]{64}", payload["expected_binding"])):
        raise ValueError("Invalid frozen context")
    stage = payload.get("stage", "result")
    if not isinstance(stage, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", stage):
        raise ValueError("Invalid preview scope")
    if operation == "trace":
        if not {"origin_id", "row_index", "column"} <= payload.keys():
            raise ValueError("Tracing requires an originating row")
        if not isinstance(payload["origin_id"], str) or not re.fullmatch(r"[a-f0-9]{24}", payload["origin_id"]):
            raise ValueError("Invalid originating receipt")
        if type(payload["row_index"]) is not int or not 0 <= payload["row_index"] < 2000:
            raise ValueError("Invalid originating row")
        if not isinstance(payload["column"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", payload["column"]):
            raise ValueError("Invalid identifier column")
    return payload


def connect(home):
    config = runtime(home)
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(path, body=None):
        request = Request(config["url"] + path,
                          data=json.dumps(body, allow_nan=False).encode() if body is not None else None,
                          headers={"X-Rowdy-Token": config["token"], "Content-Type": "application/json"})
        with opener.open(request, timeout=10) as response:
            if response.status != 200:
                raise ValueError("Unexpected local service outcome")
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("Native response exceeds budget")
        return strict_json(raw)
    return call


def resolve(boot, path):
    if not isinstance(boot, dict) or boot.get("version") != __version__:
        raise ValueError("Native service version mismatch")
    target = Path(path).resolve()
    matches = [(p, m) for p in boot["projects"] for m in p["models"]
               if (Path(p["root"]).resolve() / m["file"]).resolve() == target]
    if len(matches) != 1:
        raise ValueError("Active file is not uniquely registered")
    project, model = matches[0]
    capabilities = project.get("capabilities", {})
    # The native development channel refuses a service configured for cloud access,
    # even though the available actions below cannot call its cloud routes.
    if capabilities.get("dbt") is not False or capabilities.get("bigquery") is not False or capabilities.get("local_replay") is not True:
        raise ValueError("Native local mode requires both cloud adapters disabled")
    if project.get("data_mode") not in ("synthetic", "non_sensitive"):
        raise ValueError("Native local input mode is not approved")
    return project, model


def binding(project):
    return digest({"context_hash": project["context_hash"], "git": project["git"], "root": project["root"]})


def trace_metadata(project):
    cfg = project.get("trace")
    if not cfg:
        return None
    return {"namespace": cfg["namespace"], "event_key": cfg["event_key"],
            "identities": cfg["identities"], "window": cfg["window"]}


def validate_trace(result, body, cfg):
    if not isinstance(result, dict) or result.get("kind") != "trace" or result.get("project") != body["project"]:
        raise ValueError("Trace identity mismatch")
    if result.get("version") != __version__ or not re.fullmatch(r"[a-f0-9]{24}", result.get("id", "")):
        raise ValueError("Invalid trace receipt")
    expected_root = {"type": body["type"], "value": body["value"], "namespace": body["namespace"]}
    if result.get("root") != expected_root or result.get("context_hash") != body["context_hash"] or result.get("origin_run") != body["origin_run"]:
        raise ValueError("Trace origin or context mismatch")
    # The engine normalizes clock strings to UTC, so compare instants, not spelling.
    from datetime import datetime
    instant = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    if instant(result["start"]) != instant(body["start"]) or instant(result["end"]) != instant(body["end"]):
        raise ValueError("Trace scope mismatch")
    events = result.get("events")
    if not isinstance(events, list) or len(events) > 2000 or len({e["id"] for e in events}) != len(events):
        raise ValueError("Invalid trace event set")
    allowed = {s["table"] for s in cfg["stages"]}
    for event in events:
        if not isinstance(event.get("record"), dict) or event["record"].get(cfg["namespace"]) != body["namespace"]:
            raise ValueError("Trace crossed its namespace")
        if {s["table"] for s in event["stages"]} != allowed:
            raise ValueError("Trace stage registry mismatch")
        for stage in event["stages"]:
            if stage["state"] not in ("found", "not_found_in_scope") or not isinstance(stage["rows"], list):
                raise ValueError("Invalid trace stage state")
            if bool(stage["rows"]) != (stage["state"] == "found"):
                raise ValueError("Trace stage disagrees with its rows")
            if any(r.get(cfg["namespace"]) != body["namespace"] for r in stage["rows"]):
                raise ValueError("Trace representation crossed its namespace")
    if not all(isinstance(result.get(k), list) for k in ("coverage", "queries", "unresolved")):
        raise ValueError("Trace coverage is missing")
    return result


def invoke(home, payload):
    payload = validate(payload)
    call = connect(home)
    p, m = resolve(call("/api/bootstrap"), payload["path"])
    frozen = binding(p)
    if payload.get("expected_binding", frozen) != frozen:
        raise ValueError("Fixed replay context changed; preview explicitly before resuming live mode")
    if payload["operation"] == "trace":
        cfg = p.get("trace")
        if not cfg or payload["column"] not in cfg["identities"]:
            raise ValueError("This column is not a registered trace identity")
        origin = call("/api/receipt?" + urlencode({"id": payload["origin_id"]}))
        if origin.get("kind") not in ("preview", "verification") or origin.get("project") != p["id"] or origin.get("model") != m["name"]:
            raise ValueError("Trace origin is not this model's result")
        if origin.get("context_hash") != p["context_hash"] or origin.get("sql") != payload["sql"] or origin.get("sql_hash") != digest(payload["sql"]):
            raise ValueError("Trace origin is historical for the selected buffer")
        rows = origin.get("preview", {}).get("rows", [])
        if payload["row_index"] >= len(rows):
            raise ValueError("Trace row is outside the originating result")
        row = rows[payload["row_index"]]
        value, namespace = row.get(payload["column"]), row.get(cfg["namespace"])
        if not isinstance(value, str) or not value or not isinstance(namespace, str) or not namespace:
            raise ValueError("Tracing requires explicit string identity and namespace cells")
        body = {"project": p["id"], "context_hash": p["context_hash"], "origin_run": origin["id"],
                "type": payload["column"], "value": value, "namespace": namespace,
                "start": cfg["window"][0], "end": cfg["window"][1]}
        result = validate_trace(call("/api/trace", body), body, cfg)
    else:
        body = {"project": p["id"], "model": m["name"], "sql": payload["sql"], "context_hash": p["context_hash"],
                "request": payload["request"], "session": payload["session"], "stage": payload.get("stage", "result"),
                "verify": payload["operation"] == "verify"}
        try:
            raw_result = call("/api/run", body)
        except HTTPError as error:
            if error.code in (400, 500):
                raise EvaluationUnavailable("Local evaluation unavailable for this edit") from None
            raise
        result = validate_receipt(raw_result, body)
        if result.get("stage") != body["stage"] or result.get("dialect") != "sqlite":
            raise ValueError("Preview changed its scope or engine")
    after, after_model = resolve(call("/api/bootstrap"), payload["path"])
    if binding(after) != frozen or after_model != m:
        raise ValueError("Project changed during evaluation; receipt remains historical")
    return {"ok": True, "protocol": PROTOCOL,
            "request_id": payload["request_id"], "request": payload["request"], "session": payload["session"],
            "operation": payload["operation"], "path": payload["path"], "sql_hash": digest(payload["sql"]),
            "binding": {"hash": frozen, "project": p["id"], "model": m["name"], "root": p["root"],
                        "context_hash": p["context_hash"], "git": p["git"], "trace": trace_metadata(p),
                        "models": [{"name": x["name"], "file": x["file"]} for x in p["models"]]},
            "cloud_enabled": False, "source_writes_enabled": False, "receipt": result}


def main():
    import argparse
    import os
    import sys
    from .bridge import MAX_INPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=os.environ.get("ROWDY_HOME", str(Path.home() / ".local/share/rowdy")))
    args = parser.parse_args()
    payload = None
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("Native request exceeds budget")
        payload = validate(strict_json(raw))
        result = invoke(args.home, payload)
    except Exception as error:
        # Failures are protocol outcomes, not successful query receipts. Never
        # echo query text, filesystem paths, credentials or HTTP response bodies.
        result = {"ok": False, "protocol": PROTOCOL,
                  "code": "evaluation_unavailable" if isinstance(error, EvaluationUnavailable) else "native_action_blocked"}
        if payload is not None:
            for key in ("request_id", "request", "session"):
                result[key] = payload[key]
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
