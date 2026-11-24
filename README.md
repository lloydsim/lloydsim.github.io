# Outage Watch

Public dashboard: https://lloydsim.github.io/

## What it does
- Searches a broad organization directory, including public exchange-listed companies, familiar brands and 33 public agencies/services. This is not a complete or licensed Fortune ranking.
- Checks eight official provider feeds and five US-English Google News searches, including government services. Reports cover the last 48 hours. News coverage does not establish an active incident.
- Refreshes company names from Nasdaq's public symbol directories and a community S&P 500 reference.
- Automatically considers new organization names in collected headlines, accepts exact identity matches against Wikidata organization profiles, and publishes new searchable entries. A bounded number are considered per collection. Ambiguous names are left out rather than guessed.
- Enriches a bounded queue of profiles with websites and headquarters references each run. Pins require coordinates; searchable entries can exist without pins. Wikidata and listing data may lag real-world changes. Headquarters pins are not affected-user locations.
- Shows brand icons using Google’s public favicon service for known official domains; initials are retained as fallback. No API keys are needed.

## Updates and persistence
The dashboard reads its primary feed from https://outage-watch-updates.nearsmile4.chatgpt.site/api/snapshot. An enabled cron-job.org job requests /api/tick at minutes 25 and 55 every hour (America/Los_Angeles). The updater stores records and profiles in R2; page visits also request a collection if the feed is overdue. The GitHub workflow remains a backup and runs on pushes or manual requests, with cron `28,58 * * * *` configured on `master`. It runs `python update_signals.py`, then deploys HTML and generated JSON directly using the GitHub Pages artifact actions. It avoids the GITHUB_TOKEN branch-push Pages trigger limitation. GitHub cron created no scheduled events during the initial investigation, so unattended primary updates use cron-job.org. Consult the updater /api/health endpoint and the external job history for scheduled-run evidence.

Each collection first merges the previously published `companies.json` from this site's fixed HTTPS URL into the bundled directory, so discovered profiles survive subsequent runs without repository commits. If that URL is unavailable, the bundled catalog is retained and a directory note is recorded; unbundled discoveries from the unavailable publication may be absent from that run. Listing failures retain existing records. If every incident/news feed fails, deployment stops instead of publishing an empty feed.

The page checks the independent feed every five minutes and falls back to the GitHub publication if the feed is unreachable. Its refresh button checks that feed, which can collect fresh sources when overdue; it does not perform an on-demand company investigation. Search filters company profiles and collected articles. External news links can investigate names outside the collection.

## Data references
- Nasdaq symbol directories: https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs
- Large-company snapshot: https://github.com/datasets/s-and-p-500-companies
- Public agencies: https://www.usa.gov/agency-index
- Organization and HQ references: https://www.wikidata.org/
- Official provider feeds and Google News RSS sources are linked inside the dashboard.

Run locally with Python 3.12+: `python update_signals.py`, then serve this directory with a static web server. `directory.py` must remain next to `update_signals.py`. Collection uses the Python standard library only. No credentials belong in these files.

Discovery identity policy v2: entity-policy.json must accompany directory.py. Generic headline words cannot become company profiles. New profiles require exact identity, organization type, current status, a non-archive official website and affected-organization headline context. Prior unaudited automatic profiles are removed.
