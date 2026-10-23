import pytest

from vinted_scraper import client as client_mod
from vinted_scraper.client import BlockedError, ClientSettings, VintedClient


class Resp:
    def __init__(self, status, json_data=None, headers=None, text=""):
        self.status_code = status
        self._json = json_data or {}
        self.headers = headers or {"content-type": "application/json"}
        self.text = text
        self.content = text.encode()

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, responses, log):
        self.responses = responses
        self.log = log
        self.cookies = {"access_token_web": "x"}
        self.headers = {}

    def get(self, url, **kw):
        self.log.append(url)
        return self.responses.pop(0)

    def close(self):
        pass


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)


def make_client(monkeypatch, api_responses):
    log, sessions = [], []

    def new_session(self):
        sessions.append(self.settings.impersonate[self._fingerprint_idx])
        return FakeSession(api_responses, log)

    monkeypatch.setattr(VintedClient, "_new_session", new_session)
    c = VintedClient(ClientSettings(min_delay=0, max_delay=0, max_retries=3))
    return c, log, sessions


def test_recovers_from_cloudflare_challenge_with_new_fingerprint(monkeypatch, no_sleep):
    challenge = Resp(403, headers={"content-type": "text/html", "cf-mitigated": "challenge"}, text="Just a moment...")
    ok = Resp(200, {"items": [{"id": 1}], "pagination": {"total_pages": 1}})
    c, log, sessions = make_client(monkeypatch, [challenge, ok])
    assert c.search({"search_text": "x"}) == [{"id": 1}]
    assert len(sessions) == 2 and sessions[0] != sessions[1]


def test_refreshes_session_on_401(monkeypatch, no_sleep):
    ok = Resp(200, {"items": [], "pagination": {}})
    c, log, sessions = make_client(monkeypatch, [Resp(401), ok])
    c.search({})
    assert len(sessions) == 2 and sessions[0] == sessions[1]  # same fingerprint, fresh cookies


def test_gives_up_after_max_retries(monkeypatch, no_sleep):
    c, _, _ = make_client(monkeypatch, [Resp(429)] * 3)
    with pytest.raises(BlockedError):
        c.search({})


def test_paginates(monkeypatch, no_sleep):
    def page(n):
        return Resp(200, {"items": [{"id": i} for i in range(n)], "pagination": {"total_pages": 3}})

    c, log, _ = make_client(monkeypatch, [page(2), page(2), page(1)])
    assert len(c.search({}, pages=5, per_page=2)) == 5
    assert len(log) == 3
