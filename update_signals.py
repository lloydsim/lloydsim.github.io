"""Produce conservative, sourced status signals. Never infer outages from failed fetches."""
import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

SOURCES = [
    ('Google Workspace', 'https://www.google.com/appsstatus/dashboard/incidents.json', 'https://www.google.com/appsstatus/dashboard/'),
    ('Google Search', 'https://status.search.google.com/incidents.json', 'https://status.search.google.com/'),
]

def fetch_json(url):
    req=urllib.request.Request(url, headers={'User-Agent':'OutageWatch/1.0 (personal public status tracker)','Accept':'application/json'})
    with urllib.request.urlopen(req, timeout=16) as response:
        return json.loads(response.read(1_000_000).decode('utf-8'))

def make_record(name,endpoint,official):
    try:
        raw=fetch_json(endpoint)
        if not isinstance(raw,list):
            raise ValueError('Unexpected status format')
        # These incident feeds contain history. Show only items with a recent update,
        # avoiding any assumption that a historical incident is still open.
        cutoff=datetime.now(timezone.utc)-timedelta(days=2)
        recent=[]
        for incident in raw:
            if not isinstance(incident,dict): continue
            stamp=incident.get('modified') or incident.get('begin') or incident.get('created') or ''
            try:
                at=datetime.fromisoformat(stamp.replace('Z','+00:00'))
                if at.tzinfo is None: at=at.replace(tzinfo=timezone.utc)
            except (ValueError,TypeError): continue
            if at>=cutoff: recent.append(incident)
        recent.sort(key=lambda x:x.get('modified') or x.get('begin') or '',reverse=True)
        if recent:
            item=recent[0]
            name_item=item.get('service_name') or item.get('external_desc') or item.get('id') or 'Recent incident'
            return {'name':name,'summary':f'{len(recent)} incident update(s) in last 48 hours','detail':str(name_item)[:160], 'url':official}
        return {'name':name,'summary':'No recent incidents listed in feed','detail':'Only covers the published feed. Does not guarantee every service is operational.','url':official}
    except Exception:
        return {'name':name,'summary':'Feed unavailable / unverified','detail':'Unable to verify automated data; check the official dashboard directly.','url':official}

sources=[make_record(*source) for source in SOURCES]
sources.extend([
 {'name':'Apple System Status','summary':'Open official dashboard','detail':'Automated extraction not configured; no live claim is made.','url':'https://www.apple.com/support/systemstatus/'},
 {'name':'Microsoft 365','summary':'Open official dashboard','detail':'Some service-health details require administrator sign-in.','url':'https://status.cloud.microsoft/'},
 {'name':'FAA','summary':'Open official FAA source','detail':'Investigations and operations are not represented by this automated feed.','url':'https://www.faa.gov/'},
])
Path(__file__).with_name('signals.json').write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'sources':sources},indent=2)+'\n')
