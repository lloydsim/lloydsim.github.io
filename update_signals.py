"""Collect official incidents and discover outage reporting; never infer health from failures."""
import concurrent.futures
import directory as catalog
import hashlib
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

DIRECTORY = json.loads(Path(__file__).with_name("companies.json").read_text()) if Path(__file__).with_name("companies.json").exists() else []

MATCHERS = []
def rebuild_matchers():
    global MATCHERS
    MATCHERS = [(c['id'], re.compile(r'(?<![a-z0-9])(?:' + '|'.join(re.escape(a) for a in c['aliases'] if len(a)>2) + r')(?![a-z0-9])', re.I)) for c in DIRECTORY if any(len(a)>2 for a in c['aliases'])]

def company_ids(title):
    return [ident for ident, pattern in MATCHERS if pattern.search(title)]

NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(hours=48)
STATUSPAGES = [
    ('GitHub', 'https://www.githubstatus.com'),
    ('Cloudflare', 'https://www.cloudflarestatus.com'),
    ('Discord', 'https://discordstatus.com'),
    ('Zoom', 'https://status.zoom.us'),
    ('Twilio', 'https://status.twilio.com'),
]
GOOGLE = [
    ('Google Workspace', 'https://www.google.com/appsstatus/dashboard/incidents.json', 'https://www.google.com/appsstatus/dashboard/'),
    ('Google Search', 'https://status.search.google.com/incidents.json', 'https://status.search.google.com/'),
    ('Google Cloud', 'https://status.cloud.google.com/incidents.json', 'https://status.cloud.google.com/'),
]
NEWS_QUERIES = [
    ('Company outages', '(outage OR "service disruption") (company OR app OR service) when:2d'),
    ('Banking & payments', '(bank OR banking OR payments) (outage OR "technical issues") when:2d'),
    ('Government services', '(FAA OR IRS OR SSA OR USPS OR NSA OR TSA OR FEMA OR NASA OR government) (outage OR \"system disruption\" OR \"website down\") when:2d'),
    ('Travel & connectivity', '(airline OR telecom OR internet OR Workday) outage when:2d'),
    ('Everyday apps', '(Apple OR Microsoft OR Netflix OR Spotify OR Amazon OR PayPal OR Instagram OR WhatsApp OR TikTok) (outage OR \"service disruption\") when:2d'),
]

def fetch(url, xml=False):
    req = urllib.request.Request(url, headers={'User-Agent': 'OutageWatch/2.0 public status dashboard', 'Accept': 'application/rss+xml, application/json, application/xml'})
    with urllib.request.urlopen(req, timeout=18) as response:
        raw = response.read(3_000_001)
    if len(raw) > 3_000_000:
        raise ValueError('Oversized response')
    return ET.fromstring(raw) if xml else json.loads(raw)

def date(value):
    try:
        d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except (ValueError, TypeError):
        return None

def safe_url(value, fallback):
    return value if isinstance(value, str) and urllib.parse.urlparse(value).scheme == 'https' else fallback

def source(name, url, kind, records=None, failed=False):
    return {'name': name, 'url': url, 'kind': kind, 'available': not failed,
            'summary': 'Unavailable / unverified' if failed else f'{len(records or [])} recent report(s)',
            'detail': 'Check the source directly.' if failed else 'Recent feed updates; coverage is limited to this source.'}

def official(task):
    kind, name, endpoint, home = task
    try:
        raw = fetch(endpoint)
        items = raw.get('incidents') if kind == 'statuspage' and isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise ValueError('Unexpected feed format')
        records = []
        for item in items:
            if not isinstance(item, dict): continue
            changed = date(item.get('updated_at') or item.get('modified') or item.get('begin'))
            if not changed or changed < CUTOFF or changed > NOW + timedelta(minutes=10): continue
            resolved = bool(item.get('end')) or item.get('status') in ('resolved', 'postmortem')
            headline = item.get('name') or item.get('external_desc') or item.get('service_name') or 'Service incident'
            headline = ' '.join(str(headline).split()[:16])[:160]
            url = safe_url(item.get('shortlink') or item.get('uri'), home)
            records.append({'id': name + ':' + str(item.get('id', hashlib.sha256(headline.encode()).hexdigest()[:14])),
                'name': name, 'companies': company_ids(name), 'headline': headline, 'state': 'resolved' if resolved else 'reported',
                'status_label': 'Resolved · official' if resolved else 'Open · official',
                'sector': 'Official status', 'kind': 'official', 'published_at': changed.isoformat(),
                'publisher': name + ' status', 'url': url,
                'description': 'Provider-published incident. Timestamp is the latest incident update. Coverage may include regions outside the U.S.'})
        return source(name, home, 'official', records), records
    except Exception:
        return source(name, home, 'official', failed=True), []

def news(task):
    name, query = task
    endpoint = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
    try:
        tree = fetch(endpoint, xml=True)
        if tree.tag != 'rss' or tree.find('channel') is None: raise ValueError('Unexpected news format')
        records = []
        for item in tree.findall('./channel/item')[:50]:
            try: changed = parsedate_to_datetime(item.findtext('pubDate', ''))
            except (ValueError, TypeError): continue
            if changed.tzinfo is None: changed = changed.replace(tzinfo=timezone.utc)
            if changed < CUTOFF or changed > NOW + timedelta(minutes=10): continue
            title = item.findtext('title', '')
            if not re.search(r'outage|disruption|technical (?:issue|problem)|goes down|went down', title, re.I): continue
            publisher = item.findtext('source', 'News publisher')
            title = title.rsplit(' - ' + publisher, 1)[0]
            if publisher.lower() in ('facebook', 'linkedin', 'youtube', 'instagram', 'tiktok'): continue
            if re.search(r'how to|how outages|what you should|ways to protect|prepare for|stock alert|weigh on shares|shares fall',title,re.I): continue
            words = title.split()
            headline = ' '.join(words[:14]) + ('…' if len(words) > 14 else '')
            url = safe_url(item.findtext('link'), endpoint)
            records.append({'id': 'news:' + hashlib.sha256(url.encode()).hexdigest()[:18],
                'name': publisher, 'companies': company_ids(title), 'full_headline': title, 'headline': headline, 'state': 'unknown', 'status_label': 'News report · unverified',
                'sector': name, 'kind': 'news', 'published_at': changed.isoformat(),
                'publisher': publisher, 'url': url,
                'description': 'Discovered through Google News. This is a linked article, not an independently confirmed active outage. Multiple articles may describe the same event; geographic scope varies.'})
        return source(name + ' · Google News', endpoint, 'news', records), records
    except Exception:
        return source(name + ' · Google News', endpoint, 'news', failed=True), []

def main():
    global DIRECTORY
    directory = {c['id']: c for c in DIRECTORY}
    directory_notes = catalog.refresh(directory)
    DIRECTORY = list(directory.values())
    rebuild_matchers()
    tasks = [('statuspage', n, base + '/api/v2/incidents.json', base) for n, base in STATUSPAGES]
    tasks += [('google', n, endpoint, home) for n, endpoint, home in GOOGLE]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(official, tasks)) + list(pool.map(news, NEWS_QUERIES))
    sources, dedup = [], {}
    for src, records in results:
        sources.append(src)
        for record in records: dedup[record['id']] = record
    official_records = [x for x in dedup.values() if x['kind'] == 'official']
    news_records = sorted([x for x in dedup.values() if x['kind'] == 'news'], key=lambda x:x['published_at'], reverse=True)[:150]
    records = sorted(official_records + news_records, key=lambda x:x['published_at'], reverse=True)
    if not any(s['available'] for s in sources):
        raise RuntimeError('All sources unavailable; preserve the last deployment')
    data = {'checked_at': NOW.isoformat(), 'window_hours': 48, 'sources': sources, 'incidents': records,
            'coverage': {'configured': len(sources), 'available': sum(s['available'] for s in sources)},
            'trigger': __import__('os').environ.get('GITHUB_EVENT_NAME', 'local'),
            'scope': 'Global provider feeds and US-English news discovery; not exhaustive. Articles are not unique outages.'}
    added = catalog.discover(directory, records)
    # Enrich organizations currently in reporting first, then a bounded background queue.
    relevant = {ident for r in records for ident in r.get('companies', [])}
    pending = sorted([c for c in directory.values() if not c.get('metadata_checked_at')], key=lambda c:(c['id'] not in relevant, c.get('origin') not in ('Public agency', 'Curated profile'), c.get('metadata_attempted_at',''), c['name']))[:16]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(catalog.enrich_one, pending))
    DIRECTORY = list(directory.values())
    rebuild_matchers()
    for record in records:
        record['companies'] = company_ids(record.get('full_headline', record['name'] if record['kind'] == 'official' else record['headline']))
    catalog.save(directory)
    data['directory'] = {'count': len(directory), 'mapped': sum(bool(c.get('coordinates')) for c in DIRECTORY), 'newly_discovered': len(added), 'notes': directory_notes}
    dest = Path(__file__).with_name('signals.json')
    dest.with_suffix('.tmp').write_text(json.dumps(data, indent=2) + '\n')
    dest.with_suffix('.tmp').replace(dest)
    print(f"Checked {len(sources)} sources; {data['coverage']['available']} available; {len(records)} recent records")

if __name__ == '__main__': main()
