"""Public organization catalog, refreshed listings and conservative entity discovery.

Names come from Nasdaq's public symbol directories and the community S&P snapshot.
Wikidata supplies organization websites and sourced HQ coordinates, never outage status.
The previously published directory preserves discovered profiles without token commits.
"""
import csv
import concurrent.futures
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
UA = 'OutageWatch/3.0 (https://github.com/lloydsim/lloydsim.github.io)'
LISTINGS = 'https://www.nasdaqtrader.com/dynamic/SymDir/'
SP500 = 'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv'
ENTITY = 'https://www.wikidata.org/wiki/'


def get(url, text=False):
    request = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = response.read(8_000_001)
    if len(data) > 8_000_000:
        raise ValueError('Response too large')
    return data.decode('utf-8-sig') if text else json.loads(data)


def slug(name):
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def match(text, alias):
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(alias) + r'(?![a-z0-9])', text, re.I))


def clean_name(name):
    name = re.sub(r'\s*\(The\)$', '', name, flags=re.I)
    name = re.sub(r'^The\s+', '', name, flags=re.I)
    name = re.split(r' - |,?\s+(?:Common Stock|Common Shares|Ordinary Shares|Class [A-Z0-9]+\b|American Depositary|American Depository|ADS\b|Depositary Shares|Subordinate Voting)', name, flags=re.I)[0]
    return re.sub(r',?\s+(?:Inc\.?|Corporation|Company|Corp\.?|Ltd\.?|plc|N\.V\.|S\.A\.)$', '', name, flags=re.I).strip(' ,.-')


def aliases(name):
    # Short ambiguous names (e.g. "ON", "IT") are searchable, not automatic news matches.
    return [name] if len(name) >= 4 else []


def add_listing(directory, row, ticker, source):
    name = clean_name(row['Security Name'])
    if not name or re.search(r'\b(?:ETF|ETN|Warrants?|Rights|Notes|Preferred|Units|Acquisition|Trust|Bonds|Debentures|Securities|Fund)\b', row['Security Name'], re.I):
        return
    existing = next((c for c in directory.values() if ticker in c.get('tickers', []) or name.lower() in [c['name'].lower(), *[a.lower() for a in c['aliases']]]), None)
    if existing is None:
        ident = 'listed-' + slug(name)
        existing = directory.setdefault(ident, {'id': ident, 'name': name, 'category': 'Listed company', 'aliases': aliases(name), 'origin': 'Exchange directory', 'directory_source': source})
    existing['tickers'] = sorted(set(existing.get('tickers', []) + [ticker]))


def refresh(directory):
    notes = []
    # Restore newly discovered/enriched profiles from our own previous publication.
    try:
        previous = get('https://lloydsim.github.io/companies.json')
        if not isinstance(previous, list): raise ValueError('Bad catalog')
        for c in previous[:15000]:
            if isinstance(c, dict) and isinstance(c.get('aliases'), list) and re.fullmatch(r'[a-z0-9-]+', c.get('id', '')) and isinstance(c.get('name'), str):
                if c['id'] in directory:
                    directory[c['id']] = {**c, **directory[c['id']]}
                else: directory[c['id']] = c
    except Exception:
        notes.append('Previous published directory unavailable; using bundled catalog.')
    for filename, symbol in [('nasdaqlisted.txt', 'Symbol'), ('otherlisted.txt', 'ACT Symbol')]:
        url = LISTINGS + filename
        try:
            for row in csv.DictReader(io.StringIO(get(url, text=True)), delimiter='|'):
                if row.get('Test Issue') != 'N' or row.get('ETF') != 'N': continue
                if filename == 'otherlisted.txt' and row.get('Exchange') not in ('N', 'A', 'P', 'Z', 'V'): continue
                add_listing(directory, row, row[symbol], url)
        except Exception:
            notes.append(filename + ' unavailable; previous listing retained.')
    try:
        for row in csv.DictReader(io.StringIO(get(SP500, text=True))):
            ticker = row['Symbol']
            c = next((c for c in directory.values() if ticker in c.get('tickers', [])), None)
            if not c:
                ident = 'listed-' + slug(row['Security'])
                c = directory.setdefault(ident, {'id': ident, 'name': row['Security'], 'aliases': aliases(row['Security']), 'tickers': [ticker]})
            c['category'] = c.get('category') if c.get('origin') in ('Curated profile','Public agency') else row['GICS Sector']
            c['city'] = c.get('city') or row['Headquarters Location']
            c['directory_source'] = c.get('directory_source') or SP500
            # HQ city text is not a verified map coordinate.
    except Exception:
        notes.append('Large-company reference unavailable; previous entries retained.')
    for c in directory.values():
        if c['name'].lower() == 'popular': c['aliases'] = ['Popular Inc', 'Popular Bank', 'Banco Popular']
        if c['name'].lower() in ('twilio', 'github', 'cloudflare'): c['technical'] = True
    return notes


def values(entity, prop):
    return [x['mainsnak'].get('datavalue', {}).get('value') for x in sorted(entity.get('claims', {}).get(prop, []), key=lambda x:x.get('rank')=='preferred', reverse=True) if not (prop=='P159' and 'P582' in x.get('qualifiers', {})) and x.get('rank') != 'deprecated' and x.get('mainsnak', {}).get('snaktype') == 'value']


def wikidata(qid):
    return get('https://www.wikidata.org/wiki/Special:EntityData/' + qid + '.json')['entities'][qid]


def resolve(term):
    result = get('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode({'action': 'wbsearchentities', 'search': term, 'language': 'en', 'format': 'json', 'limit': 4}))
    for hit in result.get('search', []):
        description = hit.get('description', '')
        if not re.search(r'company|corporation|airline|bank|agency|department|organization|organisation|service|retailer|manufacturer|conglomerate|business|platform|social network|payment|subsidiary|telecommunication', description, re.I): continue
        entity = wikidata(hit['id'])
        name = entity.get('labels', {}).get('en', {}).get('value', hit['label'])
        names = [name] + [a['value'] for a in entity.get('aliases', {}).get('en', [])]
        # A fuzzy search hit alone must never establish identity.
        if not any(term.lower() == a.lower() or clean_name(term).lower() == clean_name(a).lower() for a in names): continue
        websites = [v for v in values(entity, 'P856') if isinstance(v, str) and v.startswith('https://')]
        if not websites: continue
        c = {'name': name, 'wikidata': hit['id'], 'official_url': websites[0], 'domain': urllib.parse.urlparse(websites[0]).hostname, 'entity_source': ENTITY + hit['id']}
        headquarters = [v for v in values(entity, 'P159') if isinstance(v, dict) and v.get('id')]
        if headquarters:
            hq = wikidata(headquarters[0]['id'])
            coords = values(hq, 'P625')
            if coords and isinstance(coords[0], dict) and coords[0].get('globe', '').endswith('Q2'):
                c['coordinates'] = [coords[0]['latitude'], coords[0]['longitude']]
                c['city'] = hq.get('labels', {}).get('en', {}).get('value', 'Headquarters')
                c['hq_source'] = ENTITY + hit['id'] + '#P159'
        c['metadata_checked_at'] = datetime.now(timezone.utc).isoformat()
        return c
    return None


def enrich_one(c):
    c['metadata_attempted_at'] = datetime.now(timezone.utc).isoformat()
    try:
        resolved = resolve(c.get('lookup_name', c['name']))
        if resolved:
            for key, value in resolved.items():
                if key not in ('name', 'coordinates', 'city', 'official_url', 'hq_source') or not c.get(key): c[key] = value
            if c.get('official_url'):
                c['domain'] = urllib.parse.urlparse(c['official_url']).hostname
            return True
    except Exception: pass
    return False


def candidates(title):
    # Only candidates; entity validation and exact name/alias matching happen next.
    title = re.sub(r'\b(?:Major|Massive|Global|Breaking|Latest|Widespread|Update|US|Today)\b', '', title)
    parts = re.findall(r'\b(?:[A-Z][A-Za-z0-9&.\'-]+)(?:\s+(?:[A-Z][A-Za-z0-9&.\'-]+|of|the|&)){0,3}', title)
    for p in parts:
        p = re.split(r'\s+(?:Outage|Disruption|Down|Faces|Reports|Users|Services|Hit|Experiences)\b', p)[0].strip()
        if len(p) >= 4 and p.lower() not in ('internet', 'outage', 'service', 'power', 'thousands', 'customers', 'technical', 'united states'):
            yield p


def discover(directory, records, limit=12):
    added, terms = [], []
    for record in records:
        if record['kind'] != 'news': continue
        title = record.get('full_headline', record['headline'])
        # A title can mention both an already known and a new organization.
        for term in candidates(title):
            if any(any(match(term, a) for a in c['aliases']) for c in directory.values()): continue
            if term not in terms: terms.append(term)
            if len(terms) >= limit: break
        if len(terms) >= limit: break
    def attempt(term):
        try: return resolve(term)
        except Exception: return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(attempt, terms))
    for term, profile in zip(terms, results):
        if not profile: continue
        qid = profile['wikidata']
        existing = next((c for c in directory.values() if c.get('wikidata') == qid or c['name'].lower() == profile['name'].lower() or term.lower() in [a.lower() for a in c['aliases']]), None)
        if existing:
            if term.lower() not in [a.lower() for a in existing['aliases']]: existing['aliases'].append(term)
            for key, value in profile.items():
                if not existing.get(key): existing[key] = value
            continue
        ident = 'discovered-' + qid.lower()
        directory[ident] = {'id': ident, 'category': 'Discovered in reporting', 'aliases': sorted(set([term, profile['name']])), 'origin': 'Automatically discovered', 'discovered_at': datetime.now(timezone.utc).isoformat(), **profile}
        added.append(ident)
    return added


def save(directory):
    dest = BASE / 'companies.json'
    dest.with_suffix('.tmp').write_text(json.dumps(sorted(directory.values(), key=lambda c:c['name'].lower()), ensure_ascii=False, separators=(',', ':')) + '\n')
    dest.with_suffix('.tmp').replace(dest)
