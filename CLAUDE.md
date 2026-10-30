# CLAUDE.md

Notes for Claude Code sessions in this repo. Also read `README.md` and `git log`: the commit
messages explain why each change was made.

## What this is

A Vinted watcher. Every ~30 min it scrapes the configured searches, gives each listing a
value score (points per 100 € of total cost), stores results in SQLite, and notifies about
new deals and price drops. It was built for used mini PCs (OptiPlex Micro, ThinkCentre Tiny,
EliteDesk Mini, NUC, ...), but any Vinted search works through the generic `rules` / `price`
scorers. A local web UI edits `config.yaml`.

## The user's setup

- Arch Linux, **fish** shell. Activate the venv with `source .venv/bin/activate.fish`, not
  `activate`. Arch blocks system-wide `pip install` (PEP 668), so always use the venv, or call
  `.venv/bin/python` / `.venv/bin/pip` directly.
- Checkout: `~/Desktop/vinted-scraper/vinted-scraper`.
- Branch `claude/vinted-mini-pc-scraper-u4ym6a` is the only branch and is used as main.
  Commit and push there.
- `config.yaml`, `config.yaml.bak`, `.env` and `data/` are gitignored. They hold the user's
  settings and may contain Discord/Telegram tokens. Never commit them.

## Commands

```fish
python -m pytest -q                                 # all tests are offline, no network needed
ruff check --select E,F,W,B --ignore E501 .         # lint config used so far
python -m vinted_scraper probe "https://www.vinted.de/catalog?search_text=optiplex"  # live check
python -m vinted_scraper -v -c config.yaml once     # one full run with debug logging
python -m vinted_scraper -c config.yaml run         # forever, each search on its own interval, quiet_hours off
python -m vinted_scraper -c config.yaml once        # every search now; `once --if-due` = only due ones (timer)
python -m vinted_scraper -c config.yaml once --full --pages 10 mini-pc   # deep re-sweep, ignores "already known"
python -m vinted_scraper -c config.yaml top         # best items seen in the last 72h (rescored first)
python -m vinted_scraper -c config.yaml podium      # current top-N of every search
python -m vinted_scraper -c config.yaml ui          # config editor at http://localhost:8765
python -m vinted_scraper -c config.yaml score "M720q i5-8500T 16GB 256GB" --price 100
```

`probe` prints a diagnostics block (status, cookies, start of the response) when it finds
nothing. Start there when scraping breaks.

## Layout

| File | Role |
|---|---|
| `vinted_scraper/client.py` | curl_cffi HTTP client, Cloudflare handling, catalog-page and item-page parsing |
| `vinted_scraper/kleinanzeigen.py` | Kleinanzeigen client: search paths, Astro island parsing, listing page (description, reserved/deleted) |
| `vinted_scraper/ebay.py` | eBay client on the official Browse API (OAuth app token), search filters, `getItem` for description/ended/price |
| `vinted_scraper/models.py` | `Listing` built from a normalized item dict |
| `vinted_scraper/scoring/cpu_db.py` | ~380 CPUs (mini PC, desktop, Xeon, EPYC) → approx. PassMark + TDP, estimation for unknown models |
| `vinted_scraper/scoring/gpu_db.py` | ~120 graphics cards → approx. G3D Mark, idle watts |
| `vinted_scraper/scoring/specs.py` | free-text → CPU, GPU, RAM, storage, form factor, defect/lot/barebone/part/offsite flags |
| `vinted_scraper/scoring/computer.py` | mini PC value scorer: points vs. price + fees + shipping + electricity |
| `vinted_scraper/scoring/memory.py` | `type: ram`: RAM sticks/kits → GB, DDR gen, SO-DIMM/DIMM, MHz; points = GB |
| `vinted_scraper/scoring/drives.py` | `type: drive`: drive sets → TB, HDD/SSD, 3.5"/2.5", hours; cost adds idle power per drive |
| `vinted_scraper/scoring/nas.py` | `type: nas`: model → bays, x86, year (Synology/QNAP/Asustor/...); included TB |
| `vinted_scraper/scoring/rules.py` | generic regex-points scorer; `type: price` = no rules = cheapest first |
| `vinted_scraper/runner.py` | fetch → filter → score (+ optional description fetch) → store → notify |
| `vinted_scraper/ranking.py` | rescoring stored listings with the current config; podium diff → `Podium` message |
| `vinted_scraper/storage.py` | SQLite: seen items, cached descriptions, notified price, sold (`gone`), last podium per search |
| `vinted_scraper/notify.py` | console, Discord, Telegram, ntfy, generic webhook |
| `vinted_scraper/config.py` | YAML loading, `${VAR}` / `${VAR:-default}` expansion, search URL → params |
| `vinted_scraper/webui.py` + `webui.html` | stdlib-only config editor, no CDN, works offline |
| `deploy/`, `Dockerfile`, `docker-compose.yml` | systemd timer (`once` every 30 min), Docker (`run`; `--profile ui` for the editor) |

## How scraping works (and what broke before)

- **The JSON API is gone.** `/api/v2/catalog/items` now returns 404. Search results are
  server-rendered into the Next.js RSC payload of the `/catalog` HTML page.
  `parse_catalog_page` pulls the items out and `_normalize_item` maps them onto the *old
  API field names*, so `Listing.from_api` and everything downstream didn't change. The
  catalog page always renders 96 items per page.
- **Descriptions** come from the item page's JSON-LD (`parse_item_description`), because
  `/api/v2/items/{id}/details` now returns 403. Search results don't include descriptions,
  so the runner fetches up to `fetch_details` of them per run, only for listings whose
  title lacks a CPU or RAM, and caches them in SQLite.
- **Anti-bot:** curl_cffi impersonates real browser TLS/HTTP2 fingerprints. The User-Agent
  is never overridden, because it must match the fingerprint. The client:
  - loads the homepage first to collect cookies, and renews the session on 401
  - waits a random 2–6 s between requests; each search's schedule has ±4 min jitter (≤ 1/4 interval)
  - on 403/429/challenge, backs off exponentially, then rotates fingerprint (and proxy)
  - stops the whole run on `BlockedError` instead of hammering on
- **Expect Vinted to change its site again.** When it does, the catalog parsing in
  `client.py` is the place to fix. The client tests build small fake catalog pages
  (`catalog_html` in `tests/test_client.py`) in the format seen at the time, so passing tests
  don't prove it works live. Verify against the live site with `probe`. The cloud sandbox
  this was first written in could not reach vinted.de at all, so the scraping code was fixed
  from live responses captured on the user's machine.

## Kleinanzeigen (`kleinanzeigen.py`)

Second source. A search runs on `sites: [vinted, kleinanzeigen]` (or `site:` for one): `config._search`
turns every search term into one query per site, each query dict carrying its `domain`
(`params` = Vinted filters, `kleinanzeigen` = Kleinanzeigen filters; a Kleinanzeigen-only search may
use `params`). The runner picks the client per query (`make_client` by domain) and per listing
(`client_for` by the listing URL's host) for descriptions and availability checks. All results of a
search share one ranking. Kleinanzeigen ids are stored as `ID_OFFSET` (10^15) + ad id so they can't
collide with Vinted ids; `item_page` strips it. `crawled` keys leave out the domain for the search's
main site, so queries crawled before multi-site support aren't crawled deep again. Verified live on 2026-10-24:

- Search results are an Astro page. The `astro-island` whose `props` contain `resultAds` holds every
  listing (id, title, description snippet ending in "...", seoLink, price text like "120 € VB",
  locationName/parentLocationName, shippingAvailableValue, posterType, imageList), encoded as
  `[type, value]` pairs (type 1 = array). Sponsored slots have no `organicAdPreview`.
- Paths: `/s-<filters>/<query>/k0c<cat>[l<loc>r<km>]`, filters `anzeige:angebote` (no "Gesuche"),
  `preis:min:max`, `versand:ja`, `anbieter:privat`, `seite:N`. Newest first is the default.
  25 listings per page plus top ads. Pasted URLs keep their path; `seite:N` goes before the query.
- `/s-anzeige/<id>` works without the slug; a deleted listing redirects to `/`. Full description is
  `#viewad-description-text`. Reserved listings are detected by "Reserviert" in the h1.
- `Listing.partial_description` marks the snippet: the runner fetches the full text when specs are
  missing, and a longer stored description wins over the snippet afterwards.
- `Listing.extra` ("06112 Halle · nur Abholung · VB") is stored and shown in alerts.
- `buyNowEnabled` on a result = the site's own checkout; `_normalize` adds "Direkt kaufen" to `extra`.
- The user's config: mini-pc (14 terms), desktops (Vinted category 3581 + Kleinanzeigen 228) and
  servers (13 terms incl. workstations), all on both sites, Kleinanzeigen with `shipping: true`.

## eBay (`ebay.py`)

Third source, `sites: [..., ebay]`, filters under `ebay:` (`category`, `seller`, `country`, `auctions`,
`conditions`). Uses the Browse API (`api.ebay.com/buy/browse/v1`), not the website: www.ebay.de answers
scripts with 403. Keys: `client.ebay_client_id` / `ebay_client_secret` or `EBAY_CLIENT_ID` /
`EBAY_CLIENT_SECRET`; missing or rejected keys are logged once and the site is skipped (not a block, so
podiums still update). 429 = daily call limit -> `BlockedError`. Search: `sort=newlyListed`, 100 per
page, `deliveryCountry:DE` + `itemLocationCountry:DE` by default, fixed price/best offer only, no
"for parts" condition. Ids are `ID_OFFSET` (2·10^15) + legacy item id; URLs are rebuilt as
`www.ebay.de/itm/<id>` without tracking params, so `client_for` finds the client by host.
`Listing.shipping` (stored column `shipping`) is the quoted cost and replaces `shipping_estimate` in
every scorer (`Listing.shipping_or`). Category ids were checked on ebay.de 2026-10-28: 179 PC Desktops & All-in-Ones, 11211
Computerserver, 170083 Arbeitsspeicher, 56083 Interne Festplatten, 106273 Netzgebundener Speicher.
The API code is tested against fake responses only, built from eBay's documented format; the
first live check needs the user's keys (`probe` with an ebay.de search URL).

## Spec parsing rules (`specs.py`)

RAM vs. storage is the hard part. The current design (commit `d7b6afa`):

1. Find every size token (`16GB`, `512 Go`, `1TB`, `2x8GB` = 16) and every RAM/disk keyword,
   in many languages.
2. Pair each size with an adjacent keyword: at most 12 non-digit chars between them, no other
   keyword in between, each keyword used once. The most constrained size picks first, so
   "16GB RAM 256GB SSD" and "RAM 16GB SSD 256GB" both come out right.
3. `8/256`-style pairs and loose sizes only fill RAM when **no** size sits next to a RAM
   keyword. Otherwise "upgradable 16GB/64GB" or "max 8GB/32GB" would count as installed specs.
4. Storage fallback: an unassigned size ≥120 GB or in TB.

Missing parts: `_MISSING` matches "kein/ohne/without/no" followed by a list of parts, so
"Kein Ram, Festplatte und Netzteil" sets both `barebone` and `no_psu`. The user's desktops and
mini-pc searches reject both; servers only penalise barebones (often sold without disks).

Before any of that, `parse_specs` cleans the text:
- `_SURCHARGE_LINE` drops option lines ("➕64GB DDR4 RAM +439,00€", "gegen Aufpreis"); "(ohne
  Aufpreis)" marks the base configuration and stays.
- `_UPGRADE_LIMIT` drops "max. 64GB", "upgradable to 32GB", "bis zu", "erweiterbar auf", ... to the
  end of the clause, so limits never count as installed.
- `_VARIANTS` collapses dealer configuration lists to the base (smallest) option: "8/16/32GB",
  "256 / 512 / 1000GB" (3+ values, rising, max ≤ 8× min) and tight ranges "8-32GB", "128GB-1TB".
  Two values ("8/256", "4/64") stay a RAM/storage pair; " - " with spaces is a separator.
When several sizes still sit next to RAM keywords, the largest wins.

`cpu_only` flags parts rather than machines: boards/controllers (`PART`) unless the title names a
machine noun or model line; processors (socket, sSpec code, "Prozessor" first, or a title that is
only a CPU model) unless it names a machine, model line, maker or RAM/storage size. Makers alone
don't count for boards ("Supermicro scheda madre"). Cases: `test_loose_cpu`.

Servers: `cpu_db._server_cpu` runs before the Core/Ryzen patterns (Xeon E3/E5 with version,
E-2xxx, W-2xxx, Scalable metal names, D, X56xx, EPYC; bare "Xeon 10 Core" is estimated by cores).
`cpu_count` → `Specs.cpus` = 2 for "2x Xeon"/"Dual CPU" (not "2x8GB"). Form factor `server`
(rack, 1U/2U, PowerEdge, ProLiant, Primergy, Supermicro, ...) is checked first and adds
`extra_watts` idle draw. Plain "server" doesn't count: "Mini PC Server" is a home server.

GPUs: `gpu_db.find_gpus` only returns cards in its table (so "Primergy RX 100" and "Vega 11"
iGPUs don't count). The title's card wins over ones in the description. A size right after a card
(≤12 GB, "GTX 1060 6GB") or followed by VRAM/GDDR is dropped before RAM/storage parsing. Points
come from `weights.gpu` (default 0); the card's idle watts are always added to the energy cost.
A card alone ("Grafikkarte ...", or a card with no CPU or machine word) is `cpu_only`.
`specs.payment_reject(listing)` runs for `reject: offsite`, first in every scorer but `rules`. PayPal F&F
(`FRIENDS_FAMILY`) without anything protected (`SAFE_PAYMENT`, "Direkt kaufen" in `extra`) gives
`reject="paypal f&f"` on Vinted and Kleinanzeigen; negations don't count ("kein F&F", "kein Käuferschutz").
On Vinted any `OFFSITE` wording also rejects unless buying through Vinted is on offer; eBay is exempt.
Cases: `test_payment_reject`.

Vinted's text search is fuzzy and ignores the text almost completely with a category filter, so
the user's `desktops` search is the whole category 3581 (newest first) and `servers` uses
`params: {order: relevance}`. Category ids are in the catalog page payload (`catalogIds`).

Drives (`drives.py`): sizes need TB/To/GB/Go (no bare "T": "DMA Kingdom 75T"); GB below 100 is
ignored ("6 GB/s", "8GB RAM"); several sizes without a total relation = `mixed`, rejected. NAS
(`nas.py`): Synology only with its name or a suffix ("DS437" is a Shuttle PC); "getestet mit 2x 8TB"
means no drives included. Cases: `tests/test_drives.py`, `tests/test_nas.py`.

RAM listings (`memory.py`) are parsed from the title only for the size (descriptions mention upgrade
limits and other machines); generation and module type fall back to the description, and a
`"... unknown"` reject makes the runner fetch it. Ambiguous sizes take the smaller reading: only a
leading "2x Samsung 8GB" multiplies, "Lot de 2 / 2 barrettes / Paar" + one size = that size in total,
two sizes without a kit = the smaller, "200€ für 4 Stück" = the listing is one stick. Cases:
`tests/test_memory.py`.

When you change the parser, add the listing text as a case to `test_ram_and_storage` in
`tests/test_specs.py`. That table is the regression suite. CPU scores in `cpu_db.py` are
deliberately rough (rankings, not benchmarks). Users correct them with `cpu_overrides`.

## Schedule

Per search `interval_minutes` (else the global one) and global `quiet_hours` ([start, end] local
time, may wrap midnight). `runs` table keeps each search's last start; `Runner.run_due` runs what's
due, `run_forever` sleeps until the next search is due or quiet hours end. `once --if-due` (systemd
timer every 10 min) allows up to 5 min slack so a 20-min search doesn't slip to 30. The user runs
mini-pc every 20 min, desktops 30, servers 120, quiet 1–7. `enabled: false` pauses a search
(`Runner.active`); `once <name>` still runs it. Since 2026-10-25 (PC bought) only `sodimm-ram` (60 min)
is enabled, plus `dimm-ram` (desktop sticks), `hdd` and `nas` (60 min each); the three PC searches are paused.

## Runner behaviour worth keeping

- A listing that fails to score is still stored, as rejected (`reject="scoring error"`), so
  its fetched description stays cached. If **every** listing fails, `run_search` raises and
  `run_once` logs "search crashed" with the real error.
- A listing whose fetched description matches `exclude` is stored with `reject="excluded"`
  for the same reason.
- **Paging:** the catalog renders 96 items per page. Each query is fetched `pages` deep (default
  5) once, recorded in the `crawled` table; after that `client.search(until=...)` stops at the
  first page that is mostly listings already stored. Listings dropped by the search filters are
  stored too (`reject="excluded"`), so they count as known and a looser filter later picks them up.
- Notifications: an item is notified once, and again only when its price drops below the
  last notified price. `notify.top` caps alerts per search per run.
- **Rescoring:** every run (and `top`, `podium` and the web UI Deals tab) scores the stored listings
  from the last `podium_hours` again with the current config and filters, so stale scores from
  older configs or parser bugs never rank. Rows store `total_price`, `brand` and `condition` so the
  rebuilt `Listing` matches a fresh one. Older rows lack `total_price`, so the buyer fee is estimated.
- **Podium mode** (`notify.podium: N`, the user runs it with 3): replaces per-deal alerts. After
  scoring, the best N (`min_value` applies) are compared with the last podium saved in the `podium`
  table, and a `Podium` is sent via `Notifier.send_podium` only when the ids/order differ, a member
  got dearer (always: sellers on Kleinanzeigen often fix the price minutes after posting), or (with
  `price_drop`) a member got cheaper. Members missing from this run's results (or known only from a
  snippet or without a description, which may say "nur F&F") are checked with `client.item_page`. On Vinted a sold/reserved listing has **no JSON-LD
  at all**, but the RSC payload carries a `buyer_item_status` block (`"title":"Verkauft"`) and
  `"can_buy":false`; an available one has `"can_buy":true` (checked 2026-10-25). A 404 means
  deleted. `ItemPage.price` (Vinted JSON-LD `offers.price`, Kleinanzeigen `#viewad-price`) updates
  the stored price; `Store.set_price` scales `total_price` with it. Sold ones get `gone` set and
  are skipped until they show up in results again; pages that can't be classified are kept and logged as a warning. A blocked run leaves the podium unchanged.

## Web UI security (`webui.py`)

It shows and writes a file that can contain tokens, so:
- it binds to 127.0.0.1 by default (`--host 0.0.0.0` prints a warning)
- POSTs must be `application/json`, and the Host/Origin checks block cross-site requests and
  DNS rebinding
- configs are validated (`load_config` plus building every scorer and notifier) before saving
- the previous file is kept as `config.yaml.bak`

Saving rewrites the YAML through `yaml.safe_dump`, which drops comments. That's expected.

## Conventions

- Python ≥3.10. Runtime dependencies: `curl_cffi` and `PyYAML` only. Keep it that way; the web
  UI is deliberately stdlib + vanilla JS.
- Match the existing style: small dataclasses, comments only where the *why* isn't obvious,
  every tunable exposed in `config.example.yaml` and the web UI.
- Tests must stay offline (fake clients and fixtures in `tests/`).
