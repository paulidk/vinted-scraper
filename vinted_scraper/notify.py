"""Where deals get sent: console, Discord, Telegram, ntfy or any webhook."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from curl_cffi import requests

log = logging.getLogger(__name__)


@dataclass
class Deal:
    search: str
    title: str
    price: float
    currency: str
    value: float
    summary: str
    url: str
    photo: str = ""
    price_drop_from: float | None = None

    def line(self) -> str:
        drop = f" (was {self.price_drop_from:g})" if self.price_drop_from else ""
        return f"[{self.search}] {self.value:.1f} pts/100{self.currency} | {self.price:g} {self.currency}{drop} | {self.title}\n  {self.summary}\n  {self.url}"


class Notifier:
    def send(self, deals: list[Deal]) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def send(self, deals):
        for deal in deals:
            print(deal.line(), flush=True)


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, deals):
        for i in range(0, len(deals), 10):  # Discord allows 10 embeds per message
            embeds = [
                {
                    "title": d.title[:250],
                    "url": d.url,
                    "description": f"**{d.price:g} {d.currency}**"
                    + (f" ~~{d.price_drop_from:g}~~" if d.price_drop_from else "")
                    + f" · value {d.value:.1f}\n{d.summary}"[:4000],
                    "thumbnail": {"url": d.photo} if d.photo else None,
                    "footer": {"text": d.search},
                }
                for d in deals[i : i + 10]
            ]
            _post(self.url, {"embeds": embeds})


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str | int):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id

    def send(self, deals):
        for d in deals:
            _post(self.url, {"chat_id": self.chat_id, "text": d.line(), "disable_web_page_preview": False})


class NtfyNotifier(Notifier):
    def __init__(self, topic_url: str):
        self.url = topic_url

    def send(self, deals):
        for d in deals:
            try:
                requests.post(
                    self.url,
                    data=f"{d.price:g} {d.currency} · value {d.value:.1f}\n{d.summary}".encode(),
                    headers={"Title": d.title[:200].encode("ascii", "ignore").decode(), "Click": d.url},
                    timeout=20,
                )
            except requests.RequestsError as exc:
                log.warning("ntfy failed: %s", exc)


class WebhookNotifier(Notifier):
    def __init__(self, url: str):
        self.url = url

    def send(self, deals):
        _post(self.url, {"deals": [d.__dict__ for d in deals]})


def _post(url: str, payload: dict) -> None:
    try:
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code >= 400:
            log.warning("notification to %s failed: HTTP %s %s", url.split("/")[2], resp.status_code, resp.text[:200])
    except requests.RequestsError as exc:
        log.warning("notification failed: %s", exc)


def build_notifiers(configs: list[dict] | None) -> list[Notifier]:
    kinds = {
        "console": ConsoleNotifier,
        "discord": DiscordNotifier,
        "telegram": TelegramNotifier,
        "ntfy": NtfyNotifier,
        "webhook": WebhookNotifier,
    }
    out: list[Notifier] = []
    for cfg in configs or [{"type": "console"}]:
        cfg = dict(cfg)
        kind = cfg.pop("type")
        if kind not in kinds:
            raise ValueError(f"unknown notifier {kind!r}; choose from {sorted(kinds)}")
        out.append(kinds[kind](**cfg))
    return out
