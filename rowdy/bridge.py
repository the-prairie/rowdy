"""Bounded loopback native-host bridge; no tokens in argv or error output.

The native transport owns process deadlines. This module separately validates the
service and exact response identity. Cancellation of the bridge does not assert
cancellation of a previously submitted service job (local jobs remain bounded).
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib.parse import urlparse
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from . import __version__
from .project import digest

PROTOCOL = "rowdy.native/1"
MAX_INPUT = 100_000
MAX_RESPONSE = 2_000_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Native service redirects are not allowed")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("Non-finite JSON value")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def validate_request(payload):
    allowed = {"path", "sql", "operation", "protocol", "request_id", "session", "request"}
    if not isinstance(payload, dict) or payload.keys() - allowed:
        raise ValueError("Unknown native request fields")
    if payload.get("operation") not in ("preview", "verify"):
        raise ValueError("Native bridge action not enabled")
    path, sql = payload.get("path"), payload.get("sql")
    if not isinstance(path, str) or not path or not Path(path).is_absolute() or "\0" in path:
        raise ValueError("Native file must be absolute")
    if not isinstance(sql, str) or len(sql.encode("utf-8")) > 65_536:
        raise ValueError("Native SQL exceeds its buffer budget")
    # Original Python callers remain accepted. Versioned hosts must supply the
    # complete identity envelope; a partial or unknown version never downgrades.
    extra = payload.keys() & {"protocol", "request_id", "session", "request"}
    if extra:
        if extra != {"protocol", "request_id", "session", "request"} or payload["protocol"] != PROTOCOL:
            raise ValueError("Native protocol mismatch")
        for name in ("request_id", "session"):
            if not isinstance(payload[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", payload[name]):
                raise ValueError("Invalid native request identity")
        if type(payload["request"]) is not int or not 0 <= payload["request"] < 2**63:
            raise ValueError("Invalid native request generation")
    return payload


def runtime(home):
    file = Path(home).expanduser() / "runtime.json"
    # O_NOFOLLOW plus fstat avoids a symlink race at the final path component.
    fd = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Runtime descriptor must be a regular file")
        if os.name == "posix" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("Runtime descriptor must belong to the current user and be owner-only")
        raw = stream.read(16_385)
    if len(raw) > 16_384:
        raise ValueError("Runtime descriptor exceeds budget")
    data = strict_json(raw)
    u = urlparse(data["url"])
    if u.scheme != "http" or u.hostname != "127.0.0.1" or not u.port or u.username or u.password or u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("Native bridge requires an exact loopback endpoint")
    if data.get("version") != __version__:
        raise ValueError("Native bridge/service version mismatch")
    if not isinstance(data.get("token"), str) or len(data["token"]) < 32:
        raise ValueError("Invalid native service credential")
    data["url"] = data["url"].rstrip("/")
    return data


def validate_receipt(result, body):
    if not isinstance(result, dict):
        raise ValueError("Invalid native receipt")
    expected = {"project": body["project"], "model": body["model"],
                "context_hash": body["context_hash"], "sql": body["sql"],
                "sql_hash": digest(body["sql"]), "request": body["request"],
                "session": body["session"], "version": __version__,
                "kind": "verification" if body["verify"] else "preview"}
    if any(type(result.get(k)) is not type(v) or result.get(k) != v for k, v in expected.items()):
        raise ValueError("Native response identity mismatch")
    if not isinstance(result.get("id"), str) or not result["id"]:
        raise ValueError("Receipt identifier missing")
    for flag in ("warehouse_verified", "deployed", "consumer_verified"):
        if result.get(flag) is not False:
            raise ValueError("Local receipt has an unsupported authority claim")
    statuses = ("passed", "not_passed", "unverified") if body["verify"] else ("executed",)
    if result.get("status") not in statuses:
        raise ValueError("Invalid local outcome")
    checks = result.get("checks")
    if not isinstance(checks, list):
        raise ValueError("Checks are missing")
    if result["status"] == "passed" and (not checks or any(not isinstance(c, dict) or c.get("status") != "passed" for c in checks)):
        raise ValueError("Passed claim lacks passing checks")
    preview = result.get("preview")
    if not isinstance(preview, dict) or not isinstance(preview.get("columns"), list) or not isinstance(preview.get("rows"), list):
        raise ValueError("Invalid preview shape")
    columns = preview["columns"]
    if any(not isinstance(c, str) for c in columns) or len(set(columns)) != len(columns) or len(columns) > 100:
        raise ValueError("Invalid preview columns")
    if type(preview.get("count")) is not int or preview["count"] != len(preview["rows"]):
        raise ValueError("Preview count does not match returned rows")
    if any(not isinstance(r, dict) or set(r) != set(columns) for r in preview["rows"]):
        raise ValueError("Preview rows do not match columns")
    return result


def invoke(home, payload):
    payload = validate_request(payload)
    data = runtime(home)
    # Never inherit HTTP_PROXY or forward a token to a redirect destination.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    def call(path, body=None):
        req = Request(data["url"] + path,
                      data=json.dumps(body, allow_nan=False).encode() if body is not None else None,
                      headers={"X-Rowdy-Token": data["token"], "Content-Type": "application/json"})
        with opener.open(req, timeout=10) as response:
            if response.status != 200:
                raise ValueError("Unexpected service status")
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError("Native response exceeds budget")
        return strict_json(raw)
    boot = call("/api/bootstrap")
    if boot.get("version") != __version__:
        raise ValueError("Bootstrap version mismatch")
    path = Path(payload["path"]).resolve()
    matches = [(p, m) for p in boot["projects"] for m in p["models"]
               if (Path(p["root"]).resolve() / m["file"]).resolve() == path]
    if len(matches) != 1:
        raise ValueError("Active file is not uniquely registered in Rowdy")
    p, m = matches[0]
    body = {"project": p["id"], "model": m["name"], "sql": payload["sql"],
            "context_hash": p["context_hash"], "request": payload.get("request", time.time_ns()),
            "session": payload.get("session", "native-" + str(os.getpid())),
            "verify": payload["operation"] == "verify"}
    result = validate_receipt(call("/api/run", body), body)
    # Reject applicability when disk/profile/input/Git changed during execution.
    after = call("/api/bootstrap")
    current = [x for x in after.get("projects", []) if x["id"] == p["id"]]
    if after.get("version") != __version__ or len(current) != 1 or current[0]["context_hash"] != body["context_hash"] or current[0]["git"] != p["git"]:
        raise ValueError("Project changed during evaluation; original receipt remains historical")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=os.environ.get("ROWDY_HOME", str(Path.home() / ".local/share/rowdy")))
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("Native input exceeds budget")
        payload = validate_request(strict_json(raw))
        receipt = invoke(args.home, payload)
        envelope = {"ok": True, "protocol": PROTOCOL, "request_id": payload.get("request_id"),
                    "path": payload["path"], "operation": payload["operation"], "receipt": receipt}
        print(json.dumps(envelope, allow_nan=False))
    except Exception:
        # Never expose HTTPError payloads, query text, local paths or tokens.
        print(json.dumps({"ok": False, "error": "Native action failed. Check registered path, protocol, runtime version, and local service status."}))
        sys.exit(1)


if __name__ == "__main__":
    main()
