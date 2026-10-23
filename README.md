# vinted-scraper

Watches Vinted searches every 30 minutes, scores each listing by **value for money**, and
notifies you (console / Discord / Telegram / ntfy / webhook) about the best new deals and
price drops.

It comes with a scorer for **used mini PCs** (OptiPlex Micro, ThinkCentre Tiny, EliteDesk/ProDesk
Mini, NUC, Beelink, ...). For any other search there is a generic **rules** scorer, and the
**price** scorer simply ranks cheapest first.

## How the mini PC score works

```
points = CPU (PassMark/1000) + RAM (GB, capped) + storage (GB, capped) + SSD bonus
         × form-factor multiplier (Micro/Tiny > SFF > tower)
cost   = price + Vinted fee + shipping estimate + electricity for N years of 24/7 use
value  = 100 × points / cost          → "points per 100 €", what everything is ranked by
```

- **CPU**: recognises Intel Core 2nd–14th gen (desktop, T, U, G7, P and H parts), N100/N5105/J4125
  and similar, Pentium G, and Ryzen (GE, U, H, HS) from the title and description. It has a table
  of about 165 common mini PC CPUs with approximate PassMark scores and TDP. Unknown models are
  estimated from tier + generation + suffix. Listings that only say "i5 8th gen" are handled too.
- **Power draw**: TDP class → estimated idle/average watts → kWh → money, added to the cost.
  A 65 W desktop CPU therefore loses to an equally fast 35 W T-series part, and an N100 does
  very well. Set `kwh_price` and `years` to match your situation.
- **RAM/storage** are parsed in German, French, Dutch, Polish, Italian, Spanish and English,
  including `8/256` and `16Go/512Go` styles.
- **Rejects** broken listings (`defekt`, `for parts`, `pour pièces`, BIOS locked, ...) and bundle
  lots. You can also reject barebones (no RAM/SSD) and listings without a power supply, or apply a
  penalty to them instead.
- When the title has no CPU or RAM info, it fetches the item description (up to
  `fetch_details` per run) and caches it.

Every weight is configurable per search in `config.yaml`.

Try the scorer without scraping:

```bash
python -m vinted_scraper -c config.yaml score "OptiPlex 7070 Micro i5-9500T 16GB 256GB SSD" --price 110
```

## Avoiding Cloudflare blocks

- Uses [curl_cffi](https://github.com/lexiforest/curl_cffi), which reproduces real Chrome, Edge
  and Safari TLS/HTTP2 fingerprints. That fingerprint is what Cloudflare mostly checks, and
  plain `requests` gets blocked almost straight away.
- Never overrides the User-Agent, so it always matches the fingerprint. It also sends an
  `Accept-Language` that matches the Vinted country site.
- Visits the homepage first to collect session cookies like a browser would, and renews them on 401.
- Waits a random 2–6 s between requests, and adds ±4 min of jitter to the 30-minute schedule.
- On 403/429 or a challenge page it backs off exponentially, then switches to a different
  browser fingerprint (and proxy, if you configured any) with a new session. If it is still
  blocked, it skips the rest of that run instead of piling on more requests.

At one run per half hour this traffic looks like a person browsing. Datacenter IPs (cheap VPS)
are treated with more suspicion than home connections. If you do get blocked, add residential
proxies under `client.proxies`. No scraper can promise it will never be blocked, and Vinted
changes its internal API from time to time.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml      # edit searches, prices, notifiers

python -m vinted_scraper probe "https://www.vinted.de/catalog?search_text=optiplex"  # connectivity check
python -m vinted_scraper -c config.yaml once     # one run
python -m vinted_scraper -c config.yaml run      # every 30 min, forever
python -m vinted_scraper -c config.yaml top      # best items seen in the last 72h
```

Secrets can be referenced as `${DISCORD_WEBHOOK}` in the config and put in a `.env` file.

### Run it permanently

- **systemd** (on a Pi, NAS, or the mini PC itself): `deploy/vinted-scraper.service` +
  `deploy/vinted-scraper.timer` run `once` every 30 minutes with a random delay.
- **Docker**: `docker compose up -d` (mounts `config.yaml` and `./data`).

## Other searches

Add an entry under `searches:`. Each search can have its own country domain, queries, or a
search URL pasted from the browser with all its filters. You can set a price range, include/exclude
regexes, a scorer and notification rules:

```yaml
- name: lego-technic
  url: "https://www.vinted.de/catalog?search_text=lego%20technic&price_to=80"
  notify: {min_value: 30, top: 3}
  scorer:
    type: rules
    base: 10
    rules:
      - {match: "\\b(neu|new|neuf|ovp|sealed)\\b", points: 8}
      - {match: "\\b(incomplete|unvollständig|fehlt)\\b", points: -8}
      - {match: "(\\d+)\\s*teile", per_unit: 0.01, max: 10}   # points per piece count
```

You can also use `type: computer` for laptop or desktop searches. Change the `form_factor`
multipliers and `min_cpu_mark` to suit.

## Development

```bash
pip install pytest && python -m pytest
```
