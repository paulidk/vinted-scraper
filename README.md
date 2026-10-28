# vinted-scraper

Watches Vinted (and Kleinanzeigen.de and eBay) searches every 30 minutes, scores each listing by **value for money**, and
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

**Graphics cards** (GeForce GT/GTX/RTX, Quadro, RTX A-series, Radeon RX/R9, Arc; ~120 cards with
an approximate G3D Mark) are recognised in every search. A card always adds its idle draw (6–20 W)
to the running cost; it only earns points where you set `weights.gpu`, e.g. `0.5` per 1000 G3D in
the desktops search (a GTX 1070 is then worth about as much as an i5-8500). Video memory such as
"GTX 1060 6GB" or "8GB GDDR6" is never counted as RAM.

Loose CPUs, mainboards, RAID controllers and graphics cards on their own are skipped
(`reject: cpu_only`), and so are listings that ask you to pay outside Vinted (`reject: offsite`:
"no payment by Vinted", PayPal, WhatsApp, bank transfer). Those come with no buyer protection and
are the usual bait behind a too-good price like an RTX 4080 PC for 400 €.

## RAM upgrades

`type: ram` ranks memory sticks by **GB per 100 €** (price, fees and shipping). It reads kits
("32GB (2x16GB)", "2 × 8 GB", "Lot de 2 barrettes 8 Go", "8/16/32GB" = the smallest), the DDR
generation (also from "PC4-2666" or a bare speed like "3200"), SO-DIMM vs. desktop DIMM and the
speed, and skips what doesn't fit: the wrong generation or module type, fewer than `min_gb`, sticks
smaller than `min_stick_gb` (16 keeps a single 16 GB stick and 2×16 kits but not 2×8), more sticks
than `max_sticks`, server ECC/registered sticks, and whole laptops that just list their RAM.
A title that doesn't say DDR4 or SO-DIMM gets its description fetched (within `fetch_details`).
See the `sodimm-ram` example in `config.example.yaml`; `module: dimm` (and `max_sticks: 4`) does
the same for desktop sticks. When a title is ambiguous ("Lot de 2 barrettes 16 Go" = 2×8 or 2×16?,
"16gb ... ram 8gb") the smaller reading counts, so a misread listing never tops the alert.

## Hard drives and NAS

`type: drive` ranks drives by **TB per 100 €**. The cost includes each drive's idle power over
`years` (5 W per 3.5" drive by default), so one 8 TB drive beats two 4 TB drives at the same price
and a 1 TB drive for 10 € isn't a bargain. It reads sets ("4 Stück 2TB", "3x 4TB (12TB)", "Coppia
dischi"), per-drive prices ("(4 verfügbar)"), power-on hours and bad SMART values, and skips SSDs,
2.5" drives, SAS drives and whole NAS boxes unless you allow them.

`type: nas` reads the model: Synology "DS918+" is a 4-bay 2018 model with an x86 CPU ("+"; "j",
"play" and no suffix are ARM), QNAP "TS-453D" a 4-bay x86 (x39 generation and up), plus Asustor,
TerraMaster, Ugreen and WD My Cloud. "4-Bay"/"4 baies" in the text wins. Points come from bays, x86,
model year, RAM and included drives (per TB; "ohne HDD" = none). `min_year` skips models that no
longer get updates; `require_model` skips boxes it can't identify (old Netgear, Thecus, no-name
cases, whose age it can't tell); `require_x86` keeps only the ones that run Docker and Plex well. Routers, RAM,
fans and expansion units from the same brands are skipped. Examples: `hdd` and `nas` in
`config.example.yaml`.

## Kleinanzeigen

Any search can run on both sites: with `sites: [vinted, kleinanzeigen]` every search term runs on
Vinted first, then on Kleinanzeigen, and all results share one ranking and one top-3 alert.
`params` holds Vinted's filters, `kleinanzeigen` holds Kleinanzeigen's:

```yaml
- name: mini-pc
  sites: [vinted, kleinanzeigen]
  queries: [optiplex micro, thinkcentre m920q, elitedesk mini]
  kleinanzeigen: {category: 228, shipping: true}   # 228 = PCs
```

Without search terms it takes the whole category on Kleinanzeigen. `site: kleinanzeigen` searches
there only (its filters may then go in `params`).

- `shipping: true` keeps only listings with "Versand möglich"; most others are pickup only. To
  search around your town instead, set your location and radius on the website and paste the URL
  (`url: https://www.kleinanzeigen.de/s-pcs/halle/c228l2409r50`).
- `seller: privat` skips commercial sellers.
- Alerts show where the item is, whether it ships, and "VB" (price negotiable).
- There's no Vinted buyer fee. Search results only include the start of a description, so the
  rest is fetched (within `fetch_details`) when specs are missing.
- The `offsite` check (pay outside Vinted) only applies to Vinted listings: PayPal is the normal
  way to pay on Kleinanzeigen.
- Every term costs one request per site and run, so 14 terms on both sites are 28 requests every
  30 minutes. Kleinanzeigen ids are stored with an offset (10^15) so they never clash with Vinted's.
- Kleinanzeigen's terms of use don't allow automated access, just like Vinted's. Keep the run
  interval and page counts low, for personal use only.

```bash
python -m vinted_scraper probe "https://www.kleinanzeigen.de/s-pcs/anzeige:angebote/versand:ja/c228"
```

## eBay

eBay is searched through its official **Browse API**, not by scraping, so there's no Cloudflare and
no blocking. It needs free API keys:

1. Sign up at [developer.ebay.com](https://developer.ebay.com) (your normal eBay account works) and
   open **Application Keys**.
2. Create a **Production** keyset. eBay asks you to either set up a notification endpoint for
   account deletions or opt out of it; this tool stores no eBay user data, so opt out.
3. Put the **App ID** and **Cert ID** in `.env` (or the web UI's General tab):

   ```
   EBAY_CLIENT_ID=YourName-scraper-PRD-...
   EBAY_CLIENT_SECRET=PRD-...
   ```

Then add `ebay` to a search's `sites`. Its filters go in `ebay:`:

```yaml
- name: sodimm-ram
  sites: [vinted, kleinanzeigen, ebay]
  queries: [ddr4 sodimm, so-dimm ddr4]
  kleinanzeigen: {shipping: true}
  ebay: {condition: used}       # category, condition (used/new), seller (privat/gewerblich), auctions, order
```

- Only "Sofort-Kaufen" (Buy It Now) listings, Best Offer ("Preisvorschlag") ones included, from
  sellers in Germany who ship to Germany, newest first. `auctions: true` adds auctions, ranked by
  the current bid, which is rarely the final price. `country: ""` includes sellers abroad.
- eBay returns each listing's **shipping cost**, which replaces the scorer's `shipping_estimate`.
  Set `client.ebay.zip` to your postcode to also get costs that depend on the distance.
  eBay.de charges buyers no fee.
- The item specifics (Prozessor, Arbeitsspeichergröße, Kapazität, ...) are read along with the
  description, so vague titles usually still score correctly.
- Search URLs from the browser work too (`https://www.ebay.de/sch/i.html?_nkw=optiplex&_udhi=150`):
  the search text, category, price range, condition and auction filter are taken over.
- The API allows 5000 calls a day, about one per search term and run. Without keys, eBay is skipped
  with an error in the log and the other sites run as usual.

```bash
python -m vinted_scraper probe "https://www.ebay.de/sch/i.html?_nkw=optiplex+micro&LH_BIN=1"
```

## Schedule

`interval_minutes` (default 30) applies to every search, and a search can set its own: check what
sells fast often, rare listings seldom. `quiet_hours: [1, 7]` pauses everything at night, when
hardly anything gets listed and a 24/7 rhythm looks like a bot. When each search last ran is kept in
the database, so restarting doesn't run everything again at once.

- `enabled: false` pauses a search but keeps its settings (web UI: the "Enabled" box). `run` and
  `once` skip it; `once <name>` still runs it.
- `run` stays up and starts each search when it's due.
- `once` runs every search now. `once --if-due` only runs what's due and respects `quiet_hours`;
  that's what the systemd timer calls every 10 minutes.
- `once --full` pages every search term deep again instead of stopping at listings seen before, e.g.
  after a break or after widening the filters. `--pages 10` goes deeper than each search's `pages`;
  names limit it to some searches: `once --full --pages 10 mini-pc`. That's many requests (about
  one per page and term), so use it now and then, not on a schedule.

Shorter isn't better: below about 15 minutes the extra speed isn't worth the higher risk of being
blocked for hours.

## Top-3 alerts

With `notify: {podium: 3}` a search keeps a ranking of its best 3 listings and sends it as one
message only when that ranking changes: a better listing enters, the order changes, a listing
on it gets cheaper or more expensive, or one is sold. Each place says what changed (`new`,
`up from #3`, `price drop`, `price up from 120`) and the message lists what dropped out. A raised
price is always sent, so an alert never keeps showing a price the seller has changed since.

- **Every run re-scores the stored listings** with the current config, so a config change or a
  parser fix also applies to listings that were scraped earlier.
- Searches are sorted newest first, so a good listing soon falls off the first result page while
  still being for sale. It stays ranked for `podium_hours` (default 72). A listing on the podium
  that wasn't in this run's results is checked on its item page, and dropped if it's sold or
  deleted, and it also picks up a changed price. That's at most 2 × `podium` extra page loads
  per run.
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
- Waits a random 2–6 s between requests, and adds ±4 min of jitter to every search's schedule.
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
  `deploy/vinted-scraper.timer` run `once --if-due` every 10 minutes, which starts only the searches
  whose interval is up.
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
