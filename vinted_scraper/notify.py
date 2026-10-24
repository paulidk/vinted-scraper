"""Where deals get sent: console, Discord, Telegram, ntfy or any webhook."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
    rank: int = 0  # place on the podium, 0 for a plain deal
    change: str = ""  # podium only: "new", "up from #3", ...
    extra: str = ""  # where it is, shipping, "VB" (Kleinanzeigen)

    @property
    def medal(self) -> str:
        return MEDALS[self.rank - 1] if 0 < self.rank <= len(MEDALS) else f"#{self.rank}" if self.rank else ""

    def line(self) -> str:
        drop = f" (was {self.price_drop_from:g})" if self.price_drop_from else ""
        head = f"{self.medal} " if self.rank else f"[{self.search}] "
        change = f"  ({self.change})" if self.change else ""
        extra = f"\n  {self.extra}" if self.extra else ""
        return (f"{head}{self.value:.1f} pts/100{self.currency} | {self.price:g} {self.currency}{drop} | {self.title}{change}"
                f"\n  {self.summary}{extra}\n  {self.url}")


MEDALS = ["🥇", "🥈", "🥉"]


@dataclass
class Podium:
    """The best listings of one search, sent whenever that ranking changes."""

    search: str
    deals: list[Deal]  # best first
    dropped: list[str] = field(default_factory=list)  # titles that left the podium

    @property
    def heading(self) -> str:
        return f"Top {len(self.deals)} changed · {self.search}"

    def text(self) -> str:
        lines = [f"🏆 {self.heading}", *(d.line() for d in self.deals)]
        if self.dropped:
            lines.append("Out: " + "; ".join(self.dropped))
        return "\n".join(lines)


class Notifier:
    def send(self, deals: list[Deal]) -> None:
        raise NotImplementedError

    def send_podium(self, podium: Podium) -> None:
        self.send(podium.deals)


class ConsoleNotifier(Notifier):
    def send(self, deals):
        for deal in deals:
            print(deal.line(), flush=True)

    def send_podium(self, podium):
        print(podium.text(), flush=True)


class DiscordNotifier(Notifier):
    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, deals):
        for i in range(0, len(deals), 10):  # Discord allows 10 embeds per message
            _post(self.url, {"embeds": [self._embed(d) for d in deals[i : i + 10]]})

    def send_podium(self, podium):
        content = f"**🏆 {podium.heading}**"
        if podium.dropped:
            content += "\nOut: " + "; ".join(podium.dropped)
        _post(self.url, {"content": content[:2000], "embeds": [self._embed(d) for d in podium.deals[:10]]})

    @staticmethod
    def _embed(d: Deal) -> dict:
        return {
            "title": (f"{d.medal} {d.title}" if d.rank else d.title)[:250],
            "url": d.url,
            "description": (f"**{d.price:g} {d.currency}**"
                            + (f" ~~{d.price_drop_from:g}~~" if d.price_drop_from else "")
                            + f" · value {d.value:.1f}" + (f" · {d.change}" if d.change else "")
                            + f"\n{d.summary}" + (f"\n📍 {d.extra}" if d.extra else ""))[:4000],
            "thumbnail": {"url": d.photo} if d.photo else None,
            "footer": {"text": d.search},
        }


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str | int):
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = chat_id

    def send(self, deals):
        for d in deals:
            _post(self.url, {"chat_id": self.chat_id, "text": d.line(), "disable_web_page_preview": False})

    def send_podium(self, podium):
        _post(self.url, {"chat_id": self.chat_id, "text": podium.text()[:4096], "disable_web_page_preview": True})


class NtfyNotifier(Notifier):
    def __init__(self, topic_url: str):
        self.url = topic_url

    def send(self, deals):
        for d in deals:
            self._push(d.title, f"{d.price:g} {d.currency} · value {d.value:.1f}\n{d.summary}" + (f"\n{d.extra}" if d.extra else ""), d.url)

    def send_podium(self, podium):
        body = "\n".join(f"{d.medal} {d.price:g} {d.currency} · {d.value:.1f} · {d.title}"
                         + (f" ({d.change})" if d.change else "") for d in podium.deals)
        if podium.dropped:
            body += "\nOut: " + "; ".join(podium.dropped)
        self._push(podium.heading, body, podium.deals[0].url if podium.deals else "")

    def _push(self, title: str, body: str, click: str) -> None:
        try:
            requests.post(
                self.url,
                data=body.encode(),
                # HTTP headers must be ASCII, so emoji and accents are dropped from the title.
                headers={"Title": title[:200].encode("ascii", "ignore").decode(), "Click": click},
                timeout=20,
            )
        except requests.RequestsError as exc:
            log.warning("ntfy failed: %s", exc)


class WebhookNotifier(Notifier):
    def __init__(self, url: str):
        self.url = url

    def send(self, deals):
        _post(self.url, {"deals": [d.__dict__ for d in deals]})

    def send_podium(self, podium):
        _post(self.url, {"podium": {"search": podium.search, "deals": [d.__dict__ for d in podium.deals],
                                    "dropped": podium.dropped}})


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
