"""Small local web UI for editing config.yaml, testing scorers and browsing deals.

Standard library only. It binds to 127.0.0.1 by default: the config can hold
webhook URLs and bot tokens, so don't expose it to a network you don't trust.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from .config import load_config
from .models import Listing
from .notify import build_notifiers
from .scoring import build_scorer
from .storage import Store

log = logging.getLogger(__name__)

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


class ConfigError(ValueError):
    pass


def validate(raw: dict) -> None:
    """Raise ConfigError with a readable message if the runner would reject this config."""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
        yaml.safe_dump(raw, tmp, sort_keys=False, allow_unicode=True)
    try:
        config = load_config(tmp.name)
        for search in config.searches:
            try:
                build_scorer(search.scorer)
            except (TypeError, ValueError, re.error) as exc:
                raise ConfigError(f"search {search.name!r}: scorer: {exc}") from exc
        build_notifiers(config.notifiers)
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError, re.error, yaml.YAMLError) as exc:
        msg = str(exc) if type(exc) is ValueError else f"{type(exc).__name__}: {exc}"
        raise ConfigError(msg.replace(tmp.name, "config")) from exc
    finally:
        os.unlink(tmp.name)


def dump(raw: dict) -> str:
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=120)


class App:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)

    def load_raw(self) -> tuple[dict, bool]:
        """Return the config as written (no ${VAR} expansion) and whether the file exists."""
        if self.config_path.exists():
            return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}, True
        if EXAMPLE.exists():
            return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8")) or {}, False
        return {"searches": []}, False

    def save(self, raw: dict) -> None:
        validate(raw)
        if self.config_path.exists():
            shutil.copy2(self.config_path, self.config_path.with_suffix(self.config_path.suffix + ".bak"))
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        text = dump(raw)
        tmp = self.config_path.with_suffix(".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.config_path)
        except OSError:
            # e.g. config.yaml is a Docker bind mount, which can't be replaced by rename
            tmp.unlink(missing_ok=True)
            self.config_path.write_text(text, encoding="utf-8")

    def score(self, body: dict) -> dict:
        scorer = build_scorer(body.get("scorer") or {})
        listing = Listing(
            id=0,
            title=body.get("title", ""),
            price=float(body.get("price") or 0),
            currency="EUR",
            url="",
            description=body.get("description", ""),
        )
        result = scorer.score(listing)
        return {**result.__dict__, "value": result.value}

    def top(self, search: str | None, limit: int, hours: float) -> list[dict]:
        try:
            database = load_config(self.config_path).database
        except (OSError, ValueError, KeyError, TypeError):
            database = (self.load_raw()[0] or {}).get("database", "data/vinted.db")
        if not Path(database).exists():
            return []
        rows = Store(database).top(search or None, limit, hours)
        keys = ("search", "title", "price", "currency", "value", "points", "cost", "summary", "url", "photo", "last_seen")
        return [{k: row[k] for k in keys} for row in rows]


def make_handler(app: App, allowed_hosts: set[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "vinted-scraper-ui"

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _trusted(self) -> bool:
            # Blocks DNS rebinding (foreign Host) and cross-site requests (foreign Origin).
            host = (self.headers.get("Host") or "").lower()
            if host not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlparse(origin).netloc.lower() == host

        def _body(self) -> dict:
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ConfigError("expected a JSON body")
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ConfigError("expected a JSON object")
            return data

        def do_GET(self):
            if not self._trusted():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            url = urlparse(self.path)
            if url.path == "/":
                page = resources.files("vinted_scraper").joinpath("webui.html").read_bytes()
                return self._send(200, page, "text/html; charset=utf-8")
            if url.path == "/api/config":
                raw, exists = app.load_raw()
                return self._json({"config": raw, "exists": exists, "path": str(app.config_path)})
            if url.path == "/api/top":
                q = parse_qs(url.query)
                rows = app.top(q.get("search", [""])[0], int(q.get("n", ["50"])[0]), float(q.get("hours", ["72"])[0]))
                return self._json({"items": rows})
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self):
            if not self._trusted():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            try:
                body = self._body()
                path = urlparse(self.path).path
                if path == "/api/config":
                    app.save(body.get("config") or {})
                    return self._json({"ok": True, "path": str(app.config_path)})
                if path == "/api/validate":
                    raw = body.get("config") or {}
                    try:
                        validate(raw)
                    except ConfigError as exc:
                        return self._json({"ok": False, "error": str(exc), "yaml": dump(raw)})
                    return self._json({"ok": True, "yaml": dump(raw)})
                if path == "/api/parse-yaml":
                    raw = yaml.safe_load(body.get("yaml") or "") or {}
                    if not isinstance(raw, dict):
                        raise ConfigError("the YAML must be a mapping at the top level")
                    return self._json({"config": raw})
                if path == "/api/score":
                    return self._json(app.score(body))
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            except (ConfigError, ValueError, TypeError, KeyError, re.error, yaml.YAMLError) as exc:
                return self._json({"ok": False, "error": str(exc)}, status=400)

    return Handler


def serve(config_path: str | Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    app = App(config_path)
    names = {host, "localhost", "127.0.0.1"} if host in ("127.0.0.1", "localhost") else {host}
    allowed = {f"{name}:{port}" for name in names}
    if host in ("0.0.0.0", "::"):
        # Listening everywhere: we can't know the name people will use, so accept any Host,
        # but Origin must still match it.
        log.warning("UI is reachable from your network and shows your config (incl. tokens) to anyone who can reach it")
        allowed = _AnyHost()
    server = ThreadingHTTPServer((host, port), make_handler(app, allowed))
    print(f"Config UI running at http://{'localhost' if host in ('127.0.0.1', 'localhost') else host}:{port}  (Ctrl+C to stop)")
    print(f"Editing {app.config_path.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _AnyHost(set):
    def __contains__(self, item) -> bool:
        return bool(item)
