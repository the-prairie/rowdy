"""Registered project boundary, immutable context, conflict-checked real file writes."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def load_json(path, limit=8_000_000):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError("File exceeds the declared input budget")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON is unsupported")))


def ident(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,95}", value):
        raise ValueError("Invalid registered SQL identifier")
    return value


def git(root, *args):
    try:
        done = subprocess.run(["git", "--no-pager", *args], cwd=root, capture_output=True, text=True, timeout=5,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
        return done.stdout.strip() if done.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


class Conflict(ValueError):
    pass


class Project:
    def __init__(self, root, writable=False):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.writable = writable
        self.lock = threading.RLock()
        self.profile()  # Validate before exposing files or data.

    def path(self, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Only project-relative registered paths are accepted")
        current = self.root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlinked project files are not exposed")
        resolved = current.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Path leaves the registered project")
        return resolved

    def profile(self):
        p = load_json(self.path(".rowdy/project.json"), 256_000)
        if p.get("version") != 1 or p.get("data_mode") not in ("synthetic", "non_sensitive"):
            raise ValueError("Use a v1 profile with explicitly synthetic or non_sensitive inputs. PHI is not enabled.")
        models = p.get("models")
        if not isinstance(models, list) or not models or len(models) > 150:
            raise ValueError("Register between 1 and 150 models")
        names, paths = set(), set()
        for m in models:
            name = ident(m["name"])
            if name in names or m["file"] in paths or not m["file"].endswith(".sql"):
                raise ValueError("Duplicate model identity or unsupported file")
            names.add(name); paths.add(m["file"])
            if not self.path(m["file"]).is_file():
                raise ValueError("A registered model file is missing")
            if not isinstance(m.get("keys", []), list):
                raise ValueError("Keys must be a list of column names")
            for key in m.get("keys", []):
                ident(key)
        self.path(p["snapshot"])
        return p

    def model(self, name):
        return next((m for m in self.profile()["models"] if m["name"] == name), None) or self._missing()

    @staticmethod
    def _missing():
        raise ValueError("Model is not registered in this project")

    def snapshot(self):
        data = load_json(self.path(self.profile()["snapshot"]))
        if not isinstance(data.get("tables"), dict) or len(data["tables"]) > 30:
            raise ValueError("Invalid snapshot table registry")
        total = 0
        for name, table in data["tables"].items():
            ident(name)
            if not table.get("columns") or len(table["columns"]) > 100:
                raise ValueError("Invalid snapshot schema")
            seen = set()
            for col, typ in table["columns"]:
                if ident(col) in seen or typ not in ("TEXT", "INTEGER", "REAL"):
                    raise ValueError("Invalid or duplicate snapshot column")
                seen.add(col)
            if len(table.get("rows", [])) > 5000:
                raise ValueError("Snapshot table exceeds 5,000 rows")
            total += len(table.get("rows", []))
            for row in table.get("rows", []):
                if not isinstance(row, list) or len(row) != len(seen) or any(type(v) not in (str, int, float, type(None)) for v in row):
                    raise ValueError("Snapshot row does not match its schema")
        if total > 20000:
            raise ValueError("Snapshot exceeds the total replay budget")
        if set(data["tables"]) & {m["name"] for m in self.profile()["models"]}:
            raise ValueError("Source and model names must not collide")
        return data

    def context(self):
        profile = self.profile()
        sources = {m["name"]: self.path(m["file"]).read_text(encoding="utf-8") for m in profile["models"]}
        if any(len(s.encode()) > 65536 for s in sources.values()):
            raise ValueError("Model exceeds 64 KiB")
        inputs = self.snapshot()
        head = git(self.root, "rev-parse", "HEAD")
        branch = git(self.root, "branch", "--show-current")
        identity = {"profile_hash": digest(profile), "input_hash": digest(inputs),
                    "source_hashes": {n: digest(s) for n, s in sources.items()}, "head": head}
        return {"profile": profile, "sources": sources, "inputs": inputs, "identity": identity,
                "context_hash": digest(identity), "git": {"head": head, "branch": branch or "no branch",
                "dirty": bool(git(self.root, "status", "--porcelain"))}, "root": str(self.root),
                "writable": self.writable}

    def read(self, name):
        ctx = self.context(); m = self.model(name)
        return {"model": m, "sql": ctx["sources"][name], "context_hash": ctx["context_hash"],
                "source_hash": digest(ctx["sources"][name]), "git": ctx["git"], "writable": self.writable}

    def apply(self, name, sql, expected_context, expected_source):
        if not self.writable:
            raise ValueError("Repository writes are disabled. Restart with --allow-writes after reviewing the project.")
        if not isinstance(sql, str) or len(sql.encode()) > 65536:
            raise ValueError("Invalid source content")
        with self.lock:
            ctx = self.context()
            if ctx["context_hash"] != expected_context or digest(ctx["sources"][name]) != expected_source:
                raise Conflict("Source, inputs, grain, expectations, profile or Git revision changed. Review again before applying.")
            m = self.model(name); path = self.path(m["file"])
            old = path.read_text(encoding="utf-8")
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".rowdy-edit-")
            try:
                os.fchmod(fd, path.stat().st_mode & 0o777)
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    f.write(sql); f.flush(); os.fsync(f.fileno())
                if self.context()["context_hash"] != expected_context:
                    raise Conflict("Project changed while preparing the write")
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
            return {**self.read(name), "previous_sql": old, "applied": True,
                    "git_diff": git(self.root, "diff", "--no-ext-diff", "--no-textconv", "--", m["file"]) or "",
                    "committed": False, "deployed": False}
