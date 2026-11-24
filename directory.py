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
    sanitize_directory(directory)
    return notes


def values(entity, prop):
    return [x['mainsnak'].get('datavalue', {}).get('value') for x in sorted(entity.get('claims', {}).get(prop, []), key=lambda x:x.get('rank')=='preferred', reverse=True) if not (prop=='P159' and 'P582' in x.get('qualifiers', {})) and x.get('rank') != 'deprecated' and x.get('mainsnak', {}).get('snaktype') == 'value']


def wikidata(qid):
    return get('https://www.wikidata.org/wiki/Special:EntityData/' + qid + '.json')['entities'][qid]




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


POLICY = json.loads((BASE / 'entity-policy.json').read_text())
POLICY_VERSION = POLICY['version']
BLOCKED_TERMS = set(POLICY['blocked_terms'])
CONTEXTUAL_TERMS = set(POLICY['contextual_terms'])
BUNDLED = json.loads((BASE / 'companies.json').read_text())


def normalized_name(value):
    return ' '.join(str(value or '').replace('’', "'").split()).lower()


def usable_alias(value):
    return len(value) >= 3 and normalized_name(value) not in BLOCKED_TERMS


def official_website(value):
    try:
        url = urllib.parse.urlparse(value)
        if url.scheme != 'https' or not url.hostname: return None
        if any(url.hostname == h or url.hostname.endswith('.' + h) for h in POLICY['archive_hosts']): return None
        return value
    except (ValueError, TypeError): return None


def affected_mention(title, alias):
    name = r'(?<![a-z0-9])' + re.escape(alias) + r"(?:['’]s)?(?![a-z0-9])"
    gap = r'(?:[\s,:–—-]+(?:global|widespread|system|systems|service|services|server|servers|network|networks|app|apps|website|platform|payment|payments|phone|cloud|banking|flight|flights|email|login|major|brief|reported|technical|computer|software|communications)){0,4}[\s,:–—-]+'
    event = r'(?:outages?|disruptions?|downtime|offline|goes down|went down|technical (?:issues?|problems?))'
    patterns = [name + gap + event, name + r'[\s,:–—-]+(?:reports?|suffers?|experiences?|faces?|sees|restores?|resolves?|hit by|affected by|customers report|users report)\b.{0,70}\b' + event, r'\b' + event + r'[\s,:–—-]+(?:at|hits?|affects?|disrupts?|for|on)\s+' + name]
    return any(re.search(p, title, re.I) for p in patterns)


def make_matcher(companies):
    compiled = [(c['id'], a, re.compile(r'(?<![a-z0-9])' + re.escape(a) + r'(?![a-z0-9])', re.I)) for c in companies for a in c['aliases'] if usable_alias(a)]
    def identify_matches(title, kind='news'):
        found = set()
        for ident, alias, pattern in compiled:
            if not pattern.search(title): continue
            if kind != 'official' and normalized_name(alias) in CONTEXTUAL_TERMS:
                styled = alias[0].upper() + alias[1:]
                if not re.search(r'(?<![a-z0-9])(?:' + re.escape(styled) + '|' + re.escape(alias.upper()) + r')(?![a-z0-9])', title): continue
                if not affected_mention(title, alias): continue
            found.add(ident)
        return sorted(found)
    return identify_matches


def sanitize_directory(directory):
    audited = {c['id']: c for c in BUNDLED if c['id'] in POLICY['audited_legacy_ids']}
    for ident, original in list(directory.items()):
        c = original
        if c.get('origin') == 'Automatically discovered' and c.get('identity_policy') != POLICY_VERSION:
            if ident not in audited:
                del directory[ident]
                continue
            c = {**c, **audited[ident]}
            directory[ident] = c
        if c.get('origin') == 'Automatically discovered' and (not c.get('identity_verified') or not official_website(c.get('official_url')) or not usable_alias(c.get('discovery_evidence', {}).get('matched_name', ''))):
            del directory[ident]


@__import__('functools').lru_cache(maxsize=1024)
def organization_type(qid, path=()):
    if qid in POLICY['organization_roots']: return True
    if qid in path or len(path) >= 5: return False
    try:
        return any(organization_type(parent['id'], path + (qid,)) for parent in values(wikidata(qid), 'P279')[:3] if isinstance(parent, dict) and parent.get('id'))
    except Exception: return False


def current_organization(entity):
    year = datetime.now(timezone.utc).year
    if any(isinstance(v, dict) and v.get('time') and int(v['time'][1:5]) <= year for v in values(entity, 'P576')): return False
    return any(organization_type(v['id']) for v in values(entity, 'P31')[:5] if isinstance(v, dict) and v.get('id'))


def resolve(term):
    if not usable_alias(term): return None
    result = get('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode({'action': 'wbsearchentities', 'search': term, 'language': 'en', 'format': 'json', 'limit': 3}))
    for hit in result.get('search', []):
        entity = wikidata(hit['id'])
        name = entity.get('labels', {}).get('en', {}).get('value', hit['label'])
        names = [name] + [a['value'] for a in entity.get('aliases', {}).get('en', [])]
        if not any(usable_alias(a) and (normalized_name(term) == normalized_name(a) or normalized_name(clean_name(term)) == normalized_name(clean_name(a))) for a in names): continue
        if not current_organization(entity): continue
        websites = [u for u in values(entity, 'P856') if isinstance(u, str) and official_website(u)]
        if not websites: continue
        c = {'name': name, 'wikidata': hit['id'], 'official_url': websites[0], 'domain': urllib.parse.urlparse(websites[0]).hostname, 'entity_source': ENTITY + hit['id'], 'identity_verified': True, 'identity_policy': POLICY_VERSION, 'identity_verified_at': datetime.now(timezone.utc).isoformat(), 'entity_types': [v['id'] for v in values(entity, 'P31') if isinstance(v, dict) and v.get('id')]}
        headquarters = [v for v in values(entity, 'P159') if isinstance(v, dict) and v.get('id')]
        if headquarters:
            hq = wikidata(headquarters[0]['id'])
            coords = values(hq, 'P625')
            if coords and isinstance(coords[0], dict) and coords[0].get('globe', '').endswith('Q2'):
                c.update(coordinates=[coords[0]['latitude'], coords[0]['longitude']], city=hq.get('labels', {}).get('en', {}).get('value', 'Headquarters'), hq_source=ENTITY + hit['id'] + '#P159')
        c['metadata_checked_at'] = datetime.now(timezone.utc).isoformat()
        return c
    return None


def candidates(title):
    proper = r"\b[A-Z][A-Za-z0-9&.'’-]+(?:\s+(?:[A-Z][A-Za-z0-9&.'’-]+|of|the|and|&|plc)){0,5}"
    for match_ in re.finditer(proper, title):
        term = re.split(r'\s+(?:Outages?|Disruptions?|Down|Faces|Reports|Users|Services|Hit|Experiences|Suffers|Customers)\b', match_.group(), flags=re.I)[0].strip()
        if not usable_alias(term) or normalized_name(term) in CONTEXTUAL_TERMS: continue
        if normalized_name(term.split()[0]) in POLICY['non_name_prefixes']: continue
        if affected_mention(title, term): yield term


def discover(directory, records, limit=12):
    evidence = {}
    match_ = make_matcher(directory.values())
    for record in records:
        if record['kind'] != 'news': continue
        title = record.get('full_headline', record['headline'])
        for term in candidates(title):
            if len(evidence) < limit and not match_(term) and term not in evidence:
                evidence[term] = {'headline': title, 'url': record.get('url'), 'matched_name': term, 'rule': 'affected-organization-name'}
        if len(evidence) == limit: break
    def attempt(term):
        try: return resolve(term)
        except Exception: return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(attempt, evidence))
    added = []
    for term, profile in zip(evidence, results):
        if not profile or not profile.get('identity_verified'): continue
        existing = next((c for c in directory.values() if c.get('wikidata') == profile['wikidata'] or normalized_name(c['name']) == normalized_name(profile['name'])), None)
        if existing:
            for key, value in profile.items():
                if not existing.get(key): existing[key] = value
            continue
        ident = 'discovered-' + profile['wikidata'].lower()
        directory[ident] = {'id': ident, 'category': 'Discovered in reporting', 'aliases': sorted(set(a for a in [term, profile['name']] if usable_alias(a))), 'origin': 'Automatically discovered', 'discovered_at': datetime.now(timezone.utc).isoformat(), 'discovery_evidence': evidence[term], **profile}
        added.append(ident)
    return added


def save(directory):
    dest = BASE / 'companies.json'
    dest.with_suffix('.tmp').write_text(json.dumps(sorted(directory.values(), key=lambda c:c['name'].lower()), ensure_ascii=False, separators=(',', ':')) + '\n')
    dest.with_suffix('.tmp').replace(dest)
