"""EDINET v2 official filing index and consolidated XBRL. Keys never persisted.
Japanese Q1/Q3 statutory filings discontinued from FY beginning April 2024;
missing TDnet quarters remain explicitly missing.
"""
import argparse, calendar, datetime as dt, decimal, io, json, os, pathlib, re, time, zipfile, xml.etree.ElementTree as ET
import requests
D=decimal.Decimal
API="https://api.edinet-fsa.go.jp/api/v2/"
TARGETS={"4062":"ibiden","6981":"murata","3110":"nittobo","2802":"ajinomoto","8035":"tel","6857":"advantest"}
TAGS={"inventory":{"Inventories","InventoriesIFRS","InventoryNet"},"inventory_finished":{"MerchandiseAndFinishedGoods","FinishedGoods"},"inventory_wip":{"WorkInProcess","WorkInProgress"},"inventory_raw":{"RawMaterialsAndSupplies","RawMaterials"},"revenue":{"NetSales","Revenue","RevenueIFRS","OperatingRevenue","RevenueFromContractsWithCustomers"},"cogs":{"CostOfSales","CostOfSalesIFRS","CostOfRevenue","CostOfGoodsSold"},"gross":{"GrossProfit","GrossProfitIFRS","GrossProfitLoss"}}
def local(t):return t.rsplit("}",1)[-1].split(":")[-1]
def get1(n,tag):
    a=[x.text for x in n.iter() if local(x.tag)==tag]
    return a[0] if len(a)==1 else None
def period(end):
    e=dt.date.fromisoformat(end)
    if e.month not in (3,6,9,12) or e.day!=calendar.monthrange(e.year,e.month)[1]:raise ValueError("nonstandard quarter end")
    return e.year+(e.month!=3),{6:1,9:2,12:3,3:4}[e.month]
def qstart(end):
    e=dt.date.fromisoformat(end)
    return str(dt.date(e.year,e.month-2,1))
def parse(body,end):
    fy,q=period(end);start=qstart(end);fystart=f"{fy-1}-04-01";facts={}
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        files=[x for x in z.infolist() if "/PublicDoc/" in x.filename and x.filename.lower().endswith(".xbrl") and x.file_size<=40_000_000]
        if not files:raise ValueError("no public XBRL instance")
        for f in files:
            root=ET.fromstring(z.read(f));ctx={};units=set()
            for n in root:
                typ=local(n.tag);ident=n.get("id")
                if typ=="context" and ident:
                    dims=[x for x in n.iter() if local(x.tag) in ("explicitMember","typedMember")]
                    if "nonconsolidated" in ident.lower() or any(local(x.text or "")!="ConsolidatedMember" for x in dims):continue
                    ctx[ident]=(get1(n,"instant"),get1(n,"startDate"),get1(n,"endDate"))
                if typ=="unit" and ident and local(get1(n,"measure") or "")=="JPY" and not any(local(x.tag)=="divide" for x in n.iter()):units.add(ident)
            for n in root:
                name=local(n.tag);key=next((k for k,v in TAGS.items() if name in v),None)
                if not key or n.get("unitRef") not in units or n.get("contextRef") not in ctx:continue
                instant,a,b=ctx[n.get("contextRef")]
                if key.startswith("inventory"):
                    if instant!=end:continue
                    mode="instant"
                else:
                    if b!=end or a not in (start,fystart):continue
                    mode="quarter" if a==start else "ytd"
                try:v=D(n.text or "")
                except decimal.InvalidOperation:continue
                if v.is_finite():facts.setdefault((key,mode),set()).add(v)
    def pick(k,m):
        a=facts.get((k,m),set())
        return next(iter(a)) if len(a)==1 else None
    def flow(m):
        r=pick("revenue",m);c=pick("cogs",m);g=pick("gross",m)
        if c is not None:c=abs(c)
        if r is not None and g is not None:
            implied=r-g
            if c is None:c=implied
            elif abs(c-implied)>max(D(1000),abs(r)*D(".000001")):return None,None
        return (r,c) if r is not None and c is not None and r>0 and c>0 else (None,None)
    inv=pick("inventory","instant")
    # Japanese GAAP often discloses inventories as three separate balance sheet lines.
    # Do not add components to a reported total or infer missing components as zero.
    components=[pick(k,"instant") for k in ("inventory_finished","inventory_wip","inventory_raw")]
    if inv is None and all(v is not None and v>=0 for v in components):inv=sum(components)
    r,c=flow("quarter");a,b=flow("ytd")
    if q==1:a,b=r,c
    if inv is None or inv<0 or (r is None and a is None):raise ValueError("missing/conflicting consolidated inventory or income facts")
    def m(x):return float(x/1_000_000) if x is not None else None
    return {"inventory":m(inv),"quarterRevenue":m(r),"quarterCogs":m(c),"ytdRevenue":m(a),"ytdCogs":m(b)}
def make_row(end,raw,prior):
    fy,q=period(end);r,c=raw["quarterRevenue"],raw["quarterCogs"];refs=[raw["docID"]];method="reported quarter"
    if r is None or c is None:
        if q==1 or not prior or any(x is None for x in (raw["ytdRevenue"],raw["ytdCogs"],prior["ytdRevenue"],prior["ytdCogs"])):return None
        r=raw["ytdRevenue"]-prior["ytdRevenue"];c=raw["ytdCogs"]-prior["ytdCogs"];refs.append(prior["docID"]);method="same fiscal year YTD difference"
    if r<=0 or c<=0:return None
    days=(dt.date.fromisoformat(end)-dt.date.fromisoformat(qstart(end))).days+1
    return {"date":end,"fiscalYear":fy,"quarter":q,"inventory":raw["inventory"],"revenue":r,"cogs":c,"days":days,"dio":raw["inventory"]/c*days,"sourceType":"EDINET consolidated XBRL (official)","sourceUrls":["https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?"+x for x in refs],"edinetDocIDs":refs,"derivation":method,"edinetRaw":raw}
def self_test():
    raw={"inventory":100,"quarterRevenue":None,"quarterCogs":None,"ytdRevenue":1000,"ytdCogs":800,"docID":"SYNTHETIC"}
    prev={"ytdRevenue":700,"ytdCogs":550,"docID":"SYNTHETIC2"}
    r=make_row("2026-03-31",raw,prev)
    assert r["revenue"]==300 and r["cogs"]==250 and r["days"]==90 and r["fiscalYear"]==2026
    print("EDINET synthetic YTD and fiscal-calendar check passed")
def main():
    p=argparse.ArgumentParser();p.add_argument("--self-test",action="store_true");p.add_argument("--backfill-days",type=int,default=200);args=p.parse_args();self_test()
    if args.self_test:return
    key=os.environ.get("EDINET_API_KEY","").strip()
    if not key:raise RuntimeError("EDINET_API_KEY missing")
    s=requests.Session();s.headers["User-Agent"]="AI-Bubble-Monitor public financial research"
    def get(path,params):
        try:r=s.get(API+path,params={**params,"Subscription-Key":key},timeout=20)
        except requests.RequestException:raise RuntimeError("EDINET transport failure") from None
        if r.status_code!=200:raise RuntimeError("EDINET HTTP "+str(r.status_code))
        return r
    file=pathlib.Path(__file__).resolve().parents[1]/"data.json";d=json.loads(file.read_text(encoding="utf-8"));cycle=d["memoryCycle"];today=dt.date.today()
    state=cycle.get("japanEdinetState",{});index=state.get("index",{});scanned=set(state.get("scannedDates",[]))
    # Recent dates every run, then bounded historical batches. No giant full-index dump.
    recent=[today-dt.timedelta(days=i) for i in range(14)]
    history=[today-dt.timedelta(days=i) for i in range(14,(today-dt.date(2019,1,1)).days+1)]
    targets=recent+[x for x in history if str(x) not in scanned and x.weekday()<5][:max(0,min(args.backfill_days,300))]
    errors=[];consecutive=0
    for i,day in enumerate(targets):
        try:
            j=get("documents.json",{"date":str(day),"type":2}).json()
            if str(j.get("metadata",{}).get("status",""))!="200" or not isinstance(j.get("results"),list):raise RuntimeError("unexpected EDINET index response")
            for v in j["results"]:
                ticker=str(v.get("secCode") or "")[:4];doc=v.get("docID")
                if ticker not in TARGETS or not doc or str(v.get("docTypeCode")) not in {"120","130","140","150","160","170"}:continue
                if str(v.get("withdrawalStatus","0"))!="0" or str(v.get("disclosureStatus","0"))!="0":
                    index.pop(doc,None);continue
                if str(v.get("xbrlFlag"))!="1" or not v.get("periodEnd"):continue
                try:fy,q=period(v["periodEnd"])
                except ValueError:continue
                index[doc]={k:v.get(k) for k in ("docID","periodStart","periodEnd","submitDateTime","docTypeCode","secCode")};index[doc]["id"]=TARGETS[ticker]
            scanned.add(str(day));consecutive=0
        except Exception as e:
            errors.append(str(day)+": "+str(e)[:120]);consecutive+=1
            if consecutive>=5:break
        finally:time.sleep(.25)
        if i%40==0:print("EDINET index scan",i+1,"/",len(targets),flush=True)
    if not scanned:raise RuntimeError("No EDINET listing retrieved; verify credentials")
    reports=[];fetched=0
    for c in cycle["companies"]:
        if c["id"] not in TARGETS.values():continue
        if c["currency"]!="JPY million":raise RuntimeError("issuer currency mismatch")
        old={r["date"]:r for r in c["rows"]};raw=dict(state.get("raw",{}).get(c["id"],{}));issues=[]
        candidates={}
        for v in index.values():
            if v["id"]!=c["id"]:continue
            end=v["periodEnd"]
            if end not in candidates or (v.get("submitDateTime") or "")>(candidates[end].get("submitDateTime") or ""):candidates[end]=v
        for end,v in sorted(candidates.items(),reverse=True):
            if raw.get(end,{}).get("docID")==v["docID"]:continue
            try:
                r=parse(get("documents/"+v["docID"],{"type":1}).content,end);r["docID"]=v["docID"];r["filed"]=v.get("submitDateTime");raw[end]=r;fetched+=1
            except Exception as e:issues.append(end+" "+v["docID"]+": "+str(e)[:100])
            finally:time.sleep(.4)
        state.setdefault("raw",{})[c["id"]]=raw
        byperiod={period(end):v for end,v in raw.items()}
        for end,v in sorted(raw.items()):
            fy,q=period(end);r=make_row(end,v,byperiod.get((fy,q-1)))
            if not r:continue
            p=old.get(end)
            if p and p.get("previousValues"):r["previousValues"]=p["previousValues"]
            if p and any(p.get(k)!=r[k] for k in ("inventory","revenue","cogs")):r["previousValues"]={k:p.get(k) for k in ("inventory","revenue","cogs","sourceType","sourceUrls")}
            old[end]=r
        c["rows"]=sorted(old.values(),key=lambda x:x["date"]);rows={(r["fiscalYear"],r["quarter"]):r for r in c["rows"]}
        for r in c["rows"]:
            p=rows.get((r["fiscalYear"]-1,r["quarter"]))
            # Japan fiscal year normalized to end-year. Use matching dates if legacy labels differ.
            if not p:
                try:p=next((x for x in c["rows"] if period(x["date"])==(period(r["date"])[0]-1,period(r["date"])[1])),None)
                except ValueError:pass
            for k,out in (("inventory","inventoryYoy"),("revenue","revenueYoy")):
                a=r.get(k);b=p.get(k) if p else None
                if a is not None and b is not None and b>0:r[out]=(a/b-1)*100
                elif r.get("edinetRaw"):r[out]=None
        n=sum(1 for r in c["rows"] if r.get("edinetRaw"))
        if n:
            c["note"]="EDINET 연결 XBRL 공시 기준. 회계연도 종료연도 표기. 2024년 제도 변경 후 1·3분기 TDnet/공식 IR 별도 보강 필요. 누적 차감에 필요한 이전 분기 미확보 시 공란."
            src=c.setdefault("sources",[]);url="https://disclosure2.edinet-fsa.go.jp/"
            if not any(x["url"]==url for x in src):src.insert(0,{"label":"EDINET 공식 공시","url":url})
        reports.append({"id":c["id"],"availableDocuments":len(candidates),"parsedDocuments":len(raw),"completeEdinetQuarters":n,"issues":issues})
    state.update({"index":index,"scannedDates":sorted(scanned)});cycle["japanEdinetState"]=state
    report={"as_of":str(today),"companies":reports,"indexDatesScanned":len(scanned),"historicalScanRemainingWeekdays":sum(1 for x in history if x.weekday()<5 and str(x) not in scanned),"newParsedDocuments":fetched,"errors":errors,"note":"EDINET historical filing index scanned incrementally; Q1/Q3 after 2024 require TDnet/IR. Missing quarters not invented."}
    print(json.dumps(report,ensure_ascii=False,indent=2));pathlib.Path("japan-collection-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    if consecutive>=5 and not fetched and not any(r["completeEdinetQuarters"] for r in reports):raise RuntimeError("EDINET unavailable; old data retained")
    cycle["japanApiReport"]=report;cycle["japanApiCollection"]={"status":"partial" if any(r["completeEdinetQuarters"] for r in reports) else "index_only","lastRunDate":str(today),"source":"EDINET v2"}
    if fetched:cycle["as_of"]=str(today)
    # Keep the current dashboard release; report collection status separately in memoryCycle.
    temp=file.with_suffix(".tmp");temp.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8");temp.replace(file)
if __name__=="__main__":main()
