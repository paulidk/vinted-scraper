import json
from types import SimpleNamespace

import pytest
from curl_cffi import CurlError, CurlWsFlag
from curl_cffi.const import CurlECode

from vinted_scraper import discord_bot
from vinted_scraper.discord_bot import Closed, IgnoreListener, read, toggled
from vinted_scraper.storage import Store

ROWS = [{"type": 1, "components": [{"type": 2, "style": 2, "label": "Ignore 🥇", "custom_id": "ignore:7"},
                                   {"type": 2, "style": 2, "label": "Ignore 🥈", "custom_id": "ignore:8"}]}]


def event(custom_id, rows=ROWS):
    return {"id": "55", "token": "tk", "data": {"custom_id": custom_id}, "message": {"components": rows}}


def test_toggled_switches_only_the_pressed_button():
    rows = toggled(ROWS, "ignore:7")
    first, second = rows[0]["components"]
    assert (first["label"], first["custom_id"], first["style"]) == ("Undo 🥇", "unignore:7", 4)
    assert second == ROWS[0]["components"][1]
    back = toggled(rows, "unignore:7")[0]["components"][0]
    assert (back["label"], back["custom_id"], back["style"]) == ("Ignore 🥇", "ignore:7", 2)


def test_press_is_answered_then_saved(tmp_path, monkeypatch):
    posts = []
    monkeypatch.setattr(discord_bot, "_post", lambda url, payload: posts.append((url, payload)))
    listener = IgnoreListener("tok", str(tmp_path / "db.sqlite"))
    listener.handle(event("ignore:7"))
    listener.handle(event("something:else"))
    [(url, payload)] = posts
    assert url.endswith("/interactions/55/tk/callback") and payload["type"] == 7
    assert payload["data"]["components"][0]["components"][0]["label"] == "Undo 🥇"
    assert listener.saves.get_nowait() == (7, True) and listener.saves.empty()


class Stop(Exception):
    pass


def test_presses_are_saved_and_a_locked_database_is_retried(tmp_path, monkeypatch):
    path = str(tmp_path / "db.sqlite")
    listener = IgnoreListener("tok", path)
    attempts = []
    real = Store.set_ignored

    def flaky(self, item_id, ignored):
        attempts.append(item_id)
        if len(attempts) == 1:
            raise discord_bot.sqlite3.OperationalError("database is locked")
        real(self, item_id, ignored)

    monkeypatch.setattr(Store, "set_ignored", flaky)
    monkeypatch.setattr(discord_bot.time, "sleep", lambda s: None)
    listener.saves.put((7, True))
    gets = listener.saves.get
    monkeypatch.setattr(listener.saves, "get", lambda: gets() if not listener.saves.empty() else (_ for _ in ()).throw(Stop()))
    with pytest.raises(Stop):
        listener.save_presses()
    assert attempts == [7, 7] and Store(path).ignored() == {7}


class FakeWs:
    """Hands out queued frames; Ellipsis means nothing has arrived yet."""

    close_code = 4004

    def __init__(self, *frames):
        self.frames, self.sent = list(frames), []
        self.curl = SimpleNamespace(getinfo=lambda _: 0)

    def recv_fragment(self):
        frame = self.frames.pop(0)
        if frame is ...:
            raise CurlError("again", CurlECode.AGAIN)
        if frame == "close":
            return b"", SimpleNamespace(flags=CurlWsFlag.CLOSE, bytesleft=0)
        return frame[0], SimpleNamespace(flags=frame[1], bytesleft=frame[2])

    def send_json(self, payload):
        self.sent.append(payload)

    def terminate(self):
        pass


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(discord_bot.select, "select", lambda *a: ([], [], []))


def text(payload):
    return (json.dumps(payload).encode(), CurlWsFlag.TEXT, 0)


def test_read_assembles_fragments_and_times_out(no_wait):
    data = json.dumps({"op": 11}).encode()
    ws = FakeWs((data[:3], CurlWsFlag.TEXT | CurlWsFlag.CONT, 0), ..., (data[3:], CurlWsFlag.TEXT, 0))
    assert read(ws, 5) == {"op": 11}
    assert read(FakeWs(...), 0) is None
    with pytest.raises(Closed) as closed:
        read(FakeWs("close"), 5)
    assert closed.value.code == 4004


def test_session_identifies_beats_and_handles_presses(monkeypatch, no_wait):
    ws = FakeWs(text({"op": 10, "d": {"heartbeat_interval": 0}}), ..., text({"op": 11}),
                text({"op": 0, "s": 3, "t": "INTERACTION_CREATE", "d": event("ignore:8")}), ...,
                text({"op": 7}))
    monkeypatch.setattr(discord_bot, "WebSocket", lambda: SimpleNamespace(connect=lambda *a, **k: ws))
    posts = []
    monkeypatch.setattr(discord_bot, "_post", lambda url, payload: posts.append(url))
    listener = IgnoreListener("tok", "unused")
    listener.session()  # op 7 ends it, so listen() reconnects
    assert ws.sent[0]["op"] == 2 and ws.sent[0]["d"]["token"] == "tok"
    assert ws.sent[1:] == [{"op": 1, "d": None}, {"op": 1, "d": 3}]
    assert len(posts) == 1 and listener.saves.get_nowait() == (8, True)


def test_rejected_token_stops_reconnecting(monkeypatch, caplog):
    listener = IgnoreListener("tok", "unused")

    def refused():
        raise Closed(4004)

    monkeypatch.setattr(listener, "session", refused)
    listener.listen()  # returns instead of looping
    assert "Check bot_token" in caplog.text and "tok " not in caplog.text
