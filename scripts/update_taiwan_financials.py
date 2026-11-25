"""Taiwan TWSE/MOPS: official JSON snapshot plus historical consolidated iXBRL.
No API token; keep all amounts in TWD million. No synthetic observations.
"""
import argparse, calendar, datetime as dt, decimal, io, json, pathlib, re, time, zipfile
import requests
from bs4 import BeautifulSoup
D=decimal.Decimal
TICKERS={"quanta":"2382","wistron":"3231","inventec":"2356","foxconn":"2317","unimicron":"3037"}
BASE="https://mopsov.twse.com.tw"
DOWNLOAD=BASE+"/server-java/FileDownLoad"
SNAPSHOT="https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
BS_SNAPSHOT="https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"
TAGSETS={"inventory":("inventories",),"revenue":("revenue","operatingrevenue","revenuefromcontractswithcustomers"),"cogs":("costofsales","costofrevenue","operatingcosts","costofgoodsandsold","costofgoodssold"),"gross":("grossprofit","grossprofitloss")}
def enddate(y,q): return dt.date(y,q*3,calendar.monthrange(y,q*3)[1])
def startdate(y,q): return dt.date(y,(q-1)*3+1,1)
def local(s): return str(s or "").split(":")[-1].lower()
def children(node,name): return [x for x in node.find_all(True) if local(x.name)==name]
def text1(node,name):
    v=children(node,name)
    return v[0].get_text(strip=True) if len(v)==1 else None
def amount(node):
    if node.get("xsi:nil")=="true": return None
    raw=node.get_text(strip=True).replace(",","").replace(" ","").replace("\u00a0","").replace("−","-")
    if raw in ("","-","—"): return None
    if raw.startswith("(") and raw.endswith(")"): raw="-"+raw[1:-1]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?",raw): return None
    scale=int(node.get("scale","0"))
    if not -6<=scale<=9: return None
    v=D(raw)*(D(10)**scale)
    if node.get("sign")=="-": v=-abs(v)
    return v
def documents(body,ticker):
    if body[:2]==b"PK":
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            members=[i for i in z.infolist() if i.filename.lower().endswith((".html",".htm",".xhtml",".xml")) and i.file_size<=30_000_000 and "cr-"+ticker in i.filename.lower()]
            if not members: members=[i for i in z.infolist() if i.filename.lower().endswith((".html",".htm",".xhtml")) and i.file_size<=30_000_000]
            if len(members)>30: raise ValueError("too many report members")
            return [z.read(i) for i in members]
    return [body]
def parse(body,ticker,y,q):
    end=str(enddate(y,q)); start=str(startdate(y,q)); jan=f"{y}-01-01"
    facts={}
    for raw in documents(body,ticker):
        soup=BeautifulSoup(raw,"html.parser")
        contexts={}
        units={}
        for n in soup.find_all(True):
            kind=local(n.name)
            if kind=="context" and n.get("id"):
                entity=text1(n,"identifier")
                # Reject segment/product facts; allow explicit consolidated scope only.
                dims=children(n,"explicitmember")+children(n,"typedmember")
                if any("consolidatedmember"!=local(x.get_text(strip=True)) for x in dims): continue
                if not entity: continue
                instant=text1(n,"instant"); a=text1(n,"startdate"); b=text1(n,"enddate")
                contexts[n["id"]]=(entity,instant,a,b)
            elif kind=="unit" and n.get("id"):
                if local(text1(n,"measure"))=="twd" and not children(n,"divide"): units[n["id"]]=True
        for n in soup.find_all(True):
            kind=local(n.name)
            name=local(n.get("name")) if kind=="nonfraction" else kind
            key=next((k for k,t in TAGSETS.items() if name in t),None)
            if not key: continue
            ctx=contexts.get(n.get("contextref")); unit=n.get("unitref")
            if not ctx or unit not in units: continue
            entity,instant,a,b=ctx
            if key=="inventory":
                if instant!=end: continue
                period="instant"
            else:
                if b!=end or a not in (start,jan): continue
                period="quarter" if a==start else "ytd"
            v=amount(n)
            if v is not None: facts.setdefault((entity,key,period),set()).add(v)
    # Entity ID can be the ticker or a legal identifier in the single-company report.
    entities={e for e,k,p in facts}
    matching=[e for e in entities if e==ticker or e.endswith(":"+ticker)]
    if len(matching)==1: entity=matching[0]
    elif len(entities)==1: entity=next(iter(entities))
    else: raise ValueError("missing/ambiguous report entity")
    def get(k,p):
        vals=facts.get((entity,k,p),set())
        return next(iter(vals)) if len(vals)==1 else None
    inventory=get("inventory","instant")
    def flow(p):
        rev=get("revenue",p); cost=get("cogs",p); gross=get("gross",p)
        if cost is not None: cost=abs(cost)
        if rev is not None and gross is not None:
            implied=rev-gross
            if implied>0 and (cost is None or abs(cost-implied)<=max(D(1000),abs(rev)*D("0.00001"))):
                cost=implied if cost is None else cost
            elif cost is not None: return None,None
        if rev is None or cost is None or rev<=0 or cost<=0: return None,None
        return rev,cost
    qr,qc=flow("quarter"); yr,yc=flow("ytd")
    if q==1: yr,yc=qr,qc
    if q==4 and yr is None: yr,yc=flow("ytd")
    if inventory is None or inventory<0 or (qr is None and yr is None): raise ValueError("inventory or duration facts unavailable/conflicting")
    def convert(x): return float(x/1_000_000) if x is not None else None
    return {"inventory":convert(inventory),"quarterRevenue":convert(qr),"quarterCogs":convert(qc),"ytdRevenue":convert(yr),"ytdCogs":convert(yc)}
def snapshot(session):
    result={}
    for name,url in (("income",SNAPSHOT),("balance",BS_SNAPSHOT)):
        try:
            r=session.get(url,timeout=25);r.raise_for_status()
            rows=r.json()
            if not isinstance(rows,list): raise ValueError("not a list")
            result[name]=[{"ticker":str(x.get("公司代號","")).strip(),"year":x.get("年度"),"quarter":x.get("季別"),"revenue":x.get("營業收入"),"cogs":x.get("營業成本"),"inventory":x.get("存貨")} for x in rows if str(x.get("公司代號","")).strip() in TICKERS.values()]
        except Exception as e: result[name+"Error"]=str(e)[:200]
    return result
def fetch_report(session,ticker,y,q):
    params={"functionName":"t164sb01","step":"9","co_id":ticker,"year":y,"season":q,"report_id":"C"}
    r=session.get(DOWNLOAD,params=params,timeout=30,headers={"Referer":BASE+"/mops/web/t203sb01"})
    r.raise_for_status()
    if len(r.content)>30_000_000: raise ValueError("report too large")
    f=parse(r.content,ticker,y,q); f["sourceUrl"]=r.url
    return f
def complete(raw,prev,y,q):
    rv,co=raw.get("quarterRevenue"),raw.get("quarterCogs")
    method="reported single quarter"
    urls=[raw["sourceUrl"]]
    if rv is None or co is None:
        if q==1 or not prev: return None
        if any(v is None for v in [raw.get("ytdRevenue"),raw.get("ytdCogs"),prev.get("ytdRevenue"),prev.get("ytdCogs")]): return None
        rv=raw["ytdRevenue"]-prev["ytdRevenue"];co=raw["ytdCogs"]-prev["ytdCogs"]
        method="current YTD minus previous quarter YTD";urls.append(prev["sourceUrl"])
    if rv<=0 or co<=0: return None
    days=(enddate(y,q)-startdate(y,q)).days+1
    return {"date":str(enddate(y,q)),"fiscalYear":y,"quarter":q,"inventory":raw["inventory"],"revenue":rv,"cogs":co,"days":days,"dio":raw["inventory"]/co*days,"sourceType":"MOPS consolidated iXBRL (official)","sourceUrls":urls,"derivation":method,"mopsRaw":raw}
def self_test():
    # Test exact contexts, TWD units, scale, and annual-minus-Q3.
    x=b'''<html xmlns:ix="x"><xbrli:context id="i"><xbrli:entity><xbrli:identifier>2317</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context><xbrli:context id="a"><xbrli:entity><xbrli:identifier>2317</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context><xbrli:unit id="t"><xbrli:measure>iso4217:TWD</xbrli:measure></xbrli:unit><ix:nonFraction contextRef="i" unitRef="t" name="ifrs:Inventories" scale="6">100</ix:nonFraction><ix:nonFraction contextRef="a" unitRef="t" name="ifrs:Revenue" scale="6">1000</ix:nonFraction><ix:nonFraction contextRef="a" unitRef="t" name="ifrs:CostOfSales" scale="6">800</ix:nonFraction></html>'''
    row=parse(x,"2317",2025,4);row["sourceUrl"]="https://mopsov.twse.com.tw/test"
    result=complete(row,{"ytdRevenue":700,"ytdCogs":550,"sourceUrl":"https://mopsov.twse.com.tw/test-q3"},2025,4)
    assert result["revenue"]==300 and result["cogs"]==250 and result["days"]==92
    assert result["dio"]==36.8
    print("Synthetic iXBRL and YTD subtraction checks passed")
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");args=ap.parse_args()
    if args.self_test: self_test();return
    self_test()
    file=pathlib.Path(__file__).resolve().parents[1]/"data.json"
    data=json.loads(file.read_text(encoding="utf-8"))
    today=dt.date.today()
    periods=[(y,q) for y in range(2019,today.year+1) for q in range(1,5) if enddate(y,q)<today]
    session=requests.Session()
    session.headers.update({"User-Agent":"AI-Bubble-Monitor public quarterly research dashboard/1.0","Accept":"*/*"})
    snap=snapshot(session)
    reports=[];success=0;new_count=0
    # Stop after repeated transport failures, rather than hammer a blocked service.
    consecutive_transport=0
    for c in data["memoryCycle"]["companies"]:
        ticker=TICKERS.get(c["id"])
        if not ticker: continue
        if c.get("currency")!="TWD million": raise ValueError("unexpected company currency")
        previous={r["date"]:r for r in c["rows"]}
        raw_by={(r["fiscalYear"],r["quarter"]):r["mopsRaw"] for r in c["rows"] if r.get("mopsRaw")}
        errors=[];revised=[];count=0;verified_this_run=0
        # Latest first, so current quarter is available even if historical requests fail.
        for y,q in reversed(periods):
            if (y,q) in raw_by and (y,q) not in periods[-2:]: continue
            if consecutive_transport>=5: errors.append("Stopped: repeated network errors; existing data preserved");break
            try:
                raw_by[(y,q)]=fetch_report(session,ticker,y,q);consecutive_transport=0;verified_this_run+=1
            except requests.RequestException as e:
                consecutive_transport+=1;errors.append(f"{y}Q{q}: network/HTTP error {str(e)[:100]}")
            except Exception as e:
                consecutive_transport=0;errors.append(f"{y}Q{q}: {str(e)[:120]}")
            finally: time.sleep(1.2)
        for y,q in periods:
            raw=raw_by.get((y,q))
            if not raw: continue
            row=complete(raw,raw_by.get((y,q-1)),y,q)
            if not row: continue
            old=previous.get(row["date"])
            if old and old.get("previousValues"): row["previousValues"]=old["previousValues"]
            if old and any(old.get(k)!=row.get(k) for k in ("inventory","revenue","cogs")):
                row["previousValues"]={k:old.get(k) for k in ("inventory","revenue","cogs","sourceType","sourceUrls")}
                revised.append(row["date"])
            if old!=row: count+=1
            previous[row["date"]]=row
        if verified_this_run and any(r.get("mopsRaw") for r in previous.values()): success+=1
        rows=sorted(previous.values(),key=lambda r:r["date"])
        by={(r["fiscalYear"],r["quarter"]):r for r in rows}
        for r in rows:
            p=by.get((r["fiscalYear"]-1,r["quarter"]))
            for field,out in (("inventory","inventoryYoy"),("revenue","revenueYoy")):
                a=r.get(field);b=p.get(field) if p else None
                if a is not None and b is not None and b>0: r[out]=(a/b-1)*100
                elif r.get("mopsRaw"): r[out]=None
        c["rows"]=rows
        api_count=sum(1 for r in rows if r.get("mopsRaw"))
        if api_count:
            c["note"]="MOPS 연결 iXBRL 공식 원문 기준. 분기 손익은 단일 분기 또는 YTD 차감. 자료 미확보 구간은 기존 검증 자료 유지 또는 공란. 단위 TWD million."
            src=c.setdefault("sources",[])
            for label,url in [("MOPS XBRL 원본 조회",BASE+"/mops/web/t203sb01"),("TWSE 공개 JSON 손익 요약",SNAPSHOT)]:
                if not any(s["url"]==url for s in src): src.append({"label":label,"url":url})
        reports.append({"id":c["id"],"ticker":ticker,"completeMopsQuarters":api_count,"newOrRevised":count,"revisedDates":revised,"fetchedReports":verified_this_run,"issues":errors})
        new_count+=count
    print(json.dumps({"companies":reports,"twseSnapshot":snap},ensure_ascii=False,indent=2))
    if not success: raise RuntimeError("No newly fetched complete MOPS report; existing data retained")
    data["memoryCycle"]["taiwanApiReport"]={"as_of":str(today),"companies":reports,"twseSnapshot":snap,"source":"MOPS consolidated iXBRL; TWSE JSON latest reference","note":"TWSE latest snapshot is diagnostic only; quarterly numbers originate in MOPS tagged facts. No credentials."}
    data["memoryCycle"]["as_of"]=str(today)
    data["release"]={"version":"BETA 1.4","date":str(today),"note":"Taiwan MOPS quarterly collection: "+str(success)+" issuers with successful fetch; inspect coverage."}
    temp=file.with_suffix(".tmp")
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    temp.replace(file)
if __name__=="__main__": main()
