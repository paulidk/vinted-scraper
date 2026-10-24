# vinted-scraper

Watches Vinted searches every 30 minutes, scores each listing by **value for money**, and
notifies you (console / Discord / Telegram / ntfy / webhook) about the best new deals and
price drops.

It comes with a scorer for **used mini PCs** (OptiPlex Micro, ThinkCentre Tiny, EliteDesk/ProDesk
Mini, NUC, Beelink, ...) that also handles desktops, workstations and servers (Xeon, EPYC,
dual-socket). For any other search there is a generic **rules** scorer, and the
**price** scorer simply ranks cheapest first.

## How the mini PC score works

```
points = CPU (PassMark/1000) + RAM (GB, capped) + storage (GB, capped) + SSD bonus
         × form-factor multiplier (Micro/Tiny > SFF > tower)
cost   = price + Vinted fee + shipping estimate + electricity for N years of 24/7 use
value  = 100 × points / cost          → "points per 100 €", what everything is ranked by
```

- **CPU**: recognises Intel Core 2nd–14th gen (desktop, T, U, G7, P and H parts), N100/N5105/J4125
  and similar, Pentium G, Ryzen (desktop, GE, U, H, HS), Xeon and EPYC from the title and
  description. It has a table of about 380 common CPUs with approximate PassMark scores and TDP. Unknown models are
  estimated from tier + generation + suffix. Listings that only say "i5 8th gen" are handled too.
- **Power draw**: TDP class → estimated idle/average watts → kWh → money, added to the cost.
  A 65 W desktop CPU therefore loses to an equally fast 35 W T-series part, and an N100 does
  very well. Set `kwh_price` and `years` to match your situation.
- **RAM/storage** are parsed in German, French, Dutch, Polish, Italian, Spanish and English,
  including `8/256` and `16Go/512Go` styles.
- **Rejects** broken listings (`defekt`, `for parts`, `pour pièces`, BIOS locked, ...), bundle
  lots, and loose processors or boards (`Processore i5-10500T LGA 1200`, a title that is only a CPU
  model). You can also reject barebones (no RAM/SSD) and listings without a power supply, or apply a
  penalty to them instead.
- When the title has no CPU or RAM info, it fetches the item description (up to
  `fetch_details` per run) and caches it.

## Desktops and servers

`config.example.yaml` has two more searches (commented out):

- **desktops** takes Vinted's whole "Desktop-Computer" category (`catalog[]=3581`) newest first
  instead of search terms. Vinted's text search is fuzzy ("server" finds soda machines), and the
  category catches every tower, gaming PC and workstation. Form factors are weighted equally.
- **servers** searches rare names (poweredge, proliant, supermicro, xeon, ...) sorted by
  **relevance**, which works much better than newest first for those. Xeon E3/E5/E-2xxx/W/Scalable/D
  and EPYC are in the CPU table, `2x Xeon` counts both sockets (speed ×1.9, power ×2), and rack or
  tower servers get 35 W extra idle draw (`extra_watts`). At 24/7 that running cost is what
  makes most servers poor value; lower `hours_per_day` if yours won't run all day.

Loose CPUs, mainboards and RAID controllers are skipped (`reject: cpu_only`).

## Top-3 alerts

With `notify: {podium: 3}` a search keeps a ranking of its best 3 listings and sends it as one
message only when that ranking changes: a better listing enters, the order changes, a listing
on it gets cheaper, or one is sold. Each place says what changed (`new`, `up from #3`,
`price drop`) and the message lists what dropped out.

- **Every run re-scores the stored listings** with the current config, so a config change or a
  parser fix also applies to listings that were scraped earlier.
- Searches are sorted newest first, so a good listing soon falls off the first result page while
  still being for sale. It stays ranked for `podium_hours` (default 72). A listing on the podium
  that wasn't in this run's results is checked on its item page, and dropped if it's sold or
  deleted. That's at most 2 × `podium` extra page loads per run.
- When Vinted blocks a run, the podium is left as it is until the next clean run.
- `min_value` still applies; `top` (alerts per run) doesn't.

```bash
python -m vinted_scraper -c config.yaml podium      # current top 3 of every search
```

The web UI's Deals tab shows the same podium for every search.

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
- Loads each search term `pages` deep (default 5 pages of 96) only once. Results are newest
  first, so later runs stop paging at listings they have already seen, which usually means a
  single page per term.
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
python -m vinted_scraper -c config.yaml podium   # current top 3 of every search
python -m vinted_scraper -c config.yaml ui       # web page for editing the config
```

Secrets can be referenced as `${DISCORD_WEBHOOK}` in the config and put in a `.env` file.

### Web UI for the config

```bash
python -m vinted_scraper -c config.yaml ui      # then open http://localhost:8765
```

A local page for editing `config.yaml` without touching YAML:

- add, duplicate and delete searches (from Mini PC, Any item or Cheapest first templates)
- set filters, alert rules, scorer weights and rules, and test the scorer on any title and price
- set up notifiers (Discord, Telegram, ntfy, ...) and the scraping and proxy settings
- view and edit the raw YAML, and browse the best deals found so far

Saving checks the config first and keeps the previous version as `config.yaml.bak`. YAML
comments are not kept. Restart the scraper after saving so it loads the new config. The UI only
listens on localhost by default. `--host 0.0.0.0` makes it reachable from your network, which
also shows your tokens to anyone who can reach it. With Docker, run
`docker compose --profile ui up ui`.

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
