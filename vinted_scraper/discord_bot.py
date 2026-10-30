"""Receives presses of the Ignore buttons on Discord alerts, over a bot's gateway connection.

Discord delivers button presses only to the bot that sent the message: through the gateway
(outgoing, works behind a router) or a public HTTPS endpoint. So this needs `run` to be running.
"""

from __future__ import annotations

import json
import logging
import queue
import random
import select
import sqlite3
import threading
import time

from curl_cffi import CurlError, CurlInfo, CurlWsFlag
from curl_cffi.const import CurlECode
from curl_cffi.requests import WebSocket

from .notify import API, _post
from .storage import Store

log = logging.getLogger(__name__)

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
# Close codes that reconnecting can't fix: bad token, bad intents, ...
FATAL = {4004, 4010, 4011, 4012, 4013, 4014}


class Closed(Exception):
    def __init__(self, code: int | None):
        super().__init__(f"closed by Discord (code {code})")
        self.code = code


def toggled(components: list[dict], custom_id: str) -> list[dict]:
    """The message's buttons with the pressed one switched between Ignore and Undo."""
    action, _, item = custom_id.partition(":")
    ignore = action == "ignore"
    rows = []
    for row in components:
        buttons = []
        for button in row.get("components", []):
            if button.get("custom_id") == custom_id:
                place = button["label"].split(" ", 1)[1]
                button = {**button, "style": 4 if ignore else 2, "label": f"{'Undo' if ignore else 'Ignore'} {place}",
                          "custom_id": f"{'unignore' if ignore else 'ignore'}:{item}"}
            buttons.append(button)
        rows.append({**row, "components": buttons})
    return rows


class IgnoreListener:
    def __init__(self, token: str, database: str):
        self.token, self.database = token, database
        self.saves: queue.Queue[tuple[int, bool]] = queue.Queue()

    def start(self) -> None:
        threading.Thread(target=self.listen, name="discord", daemon=True).start()
        threading.Thread(target=self.save_presses, name="discord-saves", daemon=True).start()

    def listen(self) -> None:
        delay = 5
        while True:
            started = time.monotonic()
            try:
                self.session()
            except Closed as exc:
                if exc.code in FATAL:
                    log.error("Discord refused the bot, Ignore buttons won't work: %s. Check bot_token.", exc)
                    return
                log.info("Discord connection ended: %s", exc)
            except (CurlError, OSError, ValueError, KeyError) as exc:
                log.warning("Discord connection lost: %s", exc)
            delay = 5 if time.monotonic() - started > 300 else min(delay * 2, 300)
            time.sleep(delay + random.uniform(0, 3))

    def session(self) -> None:
        """One gateway connection, from identifying until Discord or the network ends it."""
        ws = WebSocket().connect(GATEWAY, impersonate="chrome", timeout=30)
        try:
            hello = read(ws, 30)
            if not hello or hello.get("op") != 10:
                raise ValueError(f"expected hello from Discord, got {hello!r}")
            interval = hello["d"]["heartbeat_interval"] / 1000
            ws.send_json({"op": 2, "d": {"token": self.token, "intents": 0,  # presses need no intents
                                         "properties": {"os": "linux", "browser": "vinted-scraper", "device": "vinted-scraper"}}})
            seq, acked = None, True
            next_beat = time.monotonic() + interval * random.random()
            while True:
                msg = read(ws, next_beat - time.monotonic())
                if msg is None:
                    if not acked:
                        raise ValueError("Discord stopped answering heartbeats")
                    ws.send_json({"op": 1, "d": seq})
                    acked, next_beat = False, time.monotonic() + interval
                    continue
                seq = msg.get("s") or seq
                op = msg.get("op")
                if op == 11:
                    acked = True
                elif op == 1:
                    ws.send_json({"op": 1, "d": seq})
                elif op in (7, 9):  # asked to reconnect, or the session is invalid
                    return
                elif msg.get("t") == "READY":
                    log.info("Discord bot connected, Ignore buttons are live")
                elif msg.get("t") == "INTERACTION_CREATE":
                    self.handle(msg["d"])
        finally:
            ws.terminate()

    def handle(self, event: dict) -> None:
        custom_id = event.get("data", {}).get("custom_id", "")
        action, _, item = custom_id.partition(":")
        if action not in ("ignore", "unignore") or not item.isdigit():
            return
        # Discord wants an answer within 3 s, a running scrape can hold the database for minutes: save afterwards.
        _post(f"{API}/interactions/{event['id']}/{event['token']}/callback",
              {"type": 7, "data": {"components": toggled(event["message"]["components"], custom_id)}})
        self.saves.put((int(item), action == "ignore"))

    def save_presses(self) -> None:
        store = None
        while True:
            item_id, ignore = self.saves.get()
            while True:
                try:
                    store = store or Store(self.database, timeout=600)
                    store.set_ignored(item_id, ignore)
                    log.info("listing %s %s", item_id, "ignored" if ignore else "no longer ignored")
                    break
                except sqlite3.OperationalError as exc:
                    log.warning("could not save ignored listing %s yet: %s", item_id, exc)
                    time.sleep(10)


def read(ws: WebSocket, wait: float) -> dict | None:
    """The next gateway message, or None if nothing arrives in `wait` seconds. WebSocket.recv()
    would block forever, and a curl handle can't send heartbeats from a second thread."""
    sock = ws.curl.getinfo(CurlInfo.ACTIVESOCKET)
    deadline = time.monotonic() + wait
    chunks: list[bytes] = []
    while True:
        try:
            chunk, frame = ws.recv_fragment()
        except CurlError as exc:
            if exc.code != CurlECode.AGAIN:
                raise
            left = deadline - time.monotonic()
            if left <= 0 and not chunks:
                return None
            select.select([sock], [], [], max(left, 0.5))
            continue
        if frame.flags & CurlWsFlag.CLOSE:
            raise Closed(ws.close_code)
        if frame.flags & (CurlWsFlag.PING | CurlWsFlag.PONG):
            continue
        chunks.append(chunk)
        if frame.bytesleft == 0 and not frame.flags & CurlWsFlag.CONT:
            return json.loads(b"".join(chunks))
