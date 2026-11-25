"""Run with Python 3; no API key required for public FRED CSV."""
import csv, datetime as dt, io, json, pathlib, urllib.request, urllib.parse, math
p=pathlib.Path(__file__).with_name('data.json'); data=json.loads(p.read_text()); today=dt.date.today(); start=today-dt.timedelta(days=370)
def fetch(s):
 url='https://fred.stlouisfed.org/graph/fredgraph.csv?'+urllib.parse.urlencode({'id':s,'cosd':start.isoformat()})
 with urllib.request.urlopen(url, timeout=30) as r: rows=csv.DictReader(io.StringIO(r.read().decode('utf-8-sig')))
 out={}
 for row in rows:
  try:
   date=dt.date.fromisoformat(next(iter(row.values()))); value=float(row[s])
   if math.isfinite(value) and date<=today: out[date.isoformat()]=value
  except (ValueError,KeyError,TypeError): continue
 if not out: raise ValueError('No valid observations: '+s)
 return out
jobs={'spread':lambda:{d:round((v-i[d])*100,4) for d,v in s.items() if d in i},'vix':lambda:fetch('VIXCLS'),'tga':lambda:{d:v/1000 for d,v in fetch('WTREGEN').items()},'hy':lambda:fetch('BAMLH0A0HYM2')}
updated=0
for m in data['metrics']:
 if m['id'] not in jobs: continue
 try:
  if m['id']=='spread': s=fetch('SOFR'); i=fetch('IORB')
  h=jobs[m['id']]()
  if not h: raise ValueError('No matched observations')
  old=dict(m['history']);old.update(h);m['history']=sorted(old.items());updated+=1
 except Exception as e: print(m['id'], 'FAILED; retained previous data:',e)
if not updated: raise SystemExit('No data updated')
data['as_of']=today.isoformat();tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(p)
print('Updated',updated,'series; commit data.json to publish.')
