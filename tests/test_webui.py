import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
import yaml

from vinted_scraper.webui import App, make_handler


@pytest.fixture
def server(tmp_path):
    cfg = tmp_path / "config.yaml"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(App(cfg), {f"127.0.0.1:{port}", f"localhost:{port}"})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", cfg
    httpd.shutdown()


def call(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


GOOD = {"searches": [{"name": "pcs", "queries": ["optiplex"], "scorer": {"type": "computer", "min_cpu_mark": 5000}}],
        "notifiers": [{"type": "discord", "webhook_url": "${DISCORD_WEBHOOK}"}]}


def test_serves_page_and_example_config(server):
    base, _ = server
    status, page = call(base + "/")
    assert status == 200 and b"Vinted scraper config" in page
    status, body = call(base + "/api/config")
    data = json.loads(body)
    assert data["exists"] is False and data["config"]["searches"][0]["name"] == "mini-pc"


def test_save_round_trip_keeps_env_placeholders(server):
    base, cfg = server
    status, body = call(base + "/api/config", {"config": GOOD})
    assert status == 200, body
    assert yaml.safe_load(cfg.read_text()) == GOOD
    assert "${DISCORD_WEBHOOK}" in cfg.read_text()
    call(base + "/api/config", {"config": GOOD})
    assert cfg.with_suffix(".yaml.bak").exists()


@pytest.mark.parametrize(
    "bad, msg",
    [
        ({"searches": []}, "no searches"),
        ({"searches": [{"name": "a"}, {"name": "a"}]}, "unique"),
        ({"searches": [{"name": "a", "scorer": {"type": "computer", "bogus": 1}}]}, "bogus"),
        ({"searches": [{"name": "a"}], "notifiers": [{"type": "pigeon"}]}, "pigeon"),
        ({"searches": [{"name": "a", "exclude": "("}]}, "unterminated"),
        ({"searches": [{"name": "a", "scorer": {"type": "rules", "rules": [{"match": "["}]}}]}, "unterminated"),
    ],
)
def test_invalid_config_is_not_saved(server, bad, msg):
    base, cfg = server
    status, body = call(base + "/api/config", {"config": bad})
    assert status == 400 and msg in json.loads(body)["error"]
    assert not cfg.exists()


def test_validate_returns_yaml_even_when_invalid(server):
    base, _ = server
    data = json.loads(call(base + "/api/validate", {"config": {"searches": []}})[1])
    assert data["ok"] is False and "searches: []" in data["yaml"]


def test_score_endpoint(server):
    base, _ = server
    data = json.loads(call(base + "/api/score", {"scorer": {"type": "computer"}, "title": "M720q i5-8500T 16GB", "price": 100})[1])
    assert data["value"] > 0 and "i5-8500t" in data["summary"]


def test_rejects_cross_site_and_rebinding(server):
    base, cfg = server
    assert call(base + "/api/config", {"config": GOOD}, {"Origin": "https://evil.example"})[0] == 403
    assert call(base + "/api/config", {"config": GOOD}, {"Host": "evil.example"})[0] == 403
    status, _ = call(base + "/api/config", {"config": GOOD}, {"Content-Type": "text/plain"})
    assert status == 400
    assert not cfg.exists()
