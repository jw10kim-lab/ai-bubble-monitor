"""OpenDART consolidated financials. Never expose the API key in logs or output."""
import argparse, calendar, datetime as dt, io, json, os, pathlib, re, time, zipfile, xml.etree.ElementTree as ET
import requests
BASE="https://opendart.fss.or.kr/api/"
TARGETS={"sk":"000660","semco":"009150"}
REPORT={1:"11013",2:"11012",3:"11014",4:"11011"}
IDS={"inventory":{"ifrs-full_Inventories","ifrs_Inventories"},"revenue":{"ifrs-full_Revenue","ifrs_Revenue"},"cogs":{"ifrs-full_CostOfSales","ifrs_CostOfSales"},"gross":{"ifrs-full_GrossProfit","ifrs_GrossProfit"}}
NAMES={"inventory":{"재고자산"},"revenue":{"매출액","매출","수익(매출액)","영업수익","매출수익"},"cogs":{"매출원가"},"gross":{"매출총이익","매출총손익"}}
def num(x):
    s=str(x or "").replace(",","").strip()
    if s.startswith("(") and s.endswith(")"):s="-"+s[1:-1]
    return int(s) if re.fullmatch(r"-?\d+",s) else None
def pick(rows,key,field):
    scope=[r for r in rows if r.get("sj_div") in ({"BS"} if key=="inventory" else {"IS","CIS"}) and r.get("currency")=="KRW"]
    for pred in (lambda r:r.get("account_id") in IDS[key],lambda r:re.sub(r"\s+","",r.get("account_nm","")) in NAMES[key]):
        matched=[r for r in scope if pred(r)]
        if not matched:continue
        vals={num(r.get(field)) for r in matched}-{None}
        if len(vals)>1:raise ValueError("conflicting "+key+" "+field)
        if len(vals)==1:return vals.pop()/1e6
    return None
def extract(rows,y,q):
    if not rows:raise ValueError("empty consolidated report")
    if any(r.get("fs_div") not in (None,"","CFS") for r in rows):raise ValueError("standalone report")
    inv=pick(rows,"inventory","thstrm_amount")
    def flow(field):
        rev=pick(rows,"revenue",field);co=pick(rows,"cogs",field);gp=pick(rows,"gross",field)
        if rev is None:return None,None
        if co is not None:co=abs(co)
        if gp is not None:
            implied=rev-gp
            if co is None:co=implied
            elif abs(implied-co)>max(.01,abs(rev)*.000001):raise ValueError("gross profit reconciliation failed")
        if rev<=0 or co is None or co<=0:return None,None
        return rev,co
    a,b=flow("thstrm_amount")
    c,d=flow("thstrm_add_amount") if q in (2,3) else (a,b)
    if q==1:c,d=a,b
    if q==4:a,b=None,None
    rec={r.get("rcept_no") for r in rows if r.get("rcept_no")}
    if len(rec)!=1:raise ValueError("ambiguous receipt")
    if inv is None or inv<0 or (a is None and c is None):raise ValueError("required consolidated facts missing")
    return {"inventory":inv,"quarterRevenue":a,"quarterCogs":b,"ytdRevenue":c,"ytdCogs":d,"receipt":rec.pop()}
def row(raw,prev,y,q):
    a,b=raw["quarterRevenue"],raw["quarterCogs"];refs=[raw["receipt"]];method="reported single quarter"
    if a is None or b is None:
        if not prev or q==1 or any(x is None for x in (raw["ytdRevenue"],raw["ytdCogs"],prev["ytdRevenue"],prev["ytdCogs"])):return None
        a=raw["ytdRevenue"]-prev["ytdRevenue"];b=raw["ytdCogs"]-prev["ytdCogs"];refs.append(prev["receipt"]);method="YTD subtraction"
    if a<=0 or b<=0:return None
    end=dt.date(y,q*3,calendar.monthrange(y,q*3)[1]);start=dt.date(y,(q-1)*3+1,1);days=(end-start).days+1
    return {"date":str(end),"fiscalYear":y,"quarter":q,"inventory":raw["inventory"],"revenue":a,"cogs":b,"days":days,"dio":raw["inventory"]/b*days,"sourceType":"OpenDART consolidated JSON (official)","sourceUrls":["https://dart.fss.or.kr/dsaf001/main.do?rcpNo="+s for s in refs],"derivation":method,"dartRaw":raw}
def self_test():
    def r(a,v,acc=None,sj="IS"):return {"account_id":a,"sj_div":sj,"currency":"KRW","thstrm_amount":str(v),"thstrm_add_amount":str(acc) if acc is not None else "", "rcept_no":"20260501000001"}
    data=[r("ifrs-full_Inventories",100000000,sj="BS"),r("ifrs-full_Revenue",1000000000),r("ifrs-full_CostOfSales",800000000)]
    a=extract(data,2025,4);res=row(a,{"ytdRevenue":700,"ytdCogs":550,"receipt":"20251101000001"},2025,4)
    assert res["revenue"]==300 and res["cogs"]==250 and res["days"]==92
    print("OpenDART parser synthetic test passed")
def main():
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");args=ap.parse_args();self_test()
    if args.self_test:return
    key=os.environ.get("DART_API_KEY","").strip()
    if not re.fullmatch(r"[A-Za-z0-9]{40}",key):raise RuntimeError("DART_API_KEY missing or invalid format")
    s=requests.Session()
    def get(endpoint,params):
        try:
            r=s.get(BASE+endpoint,params={"crtfc_key":key,**params},timeout=30)
        except requests.RequestException:raise RuntimeError("OpenDART transport failed") from None
        if r.status_code!=200:raise RuntimeError("OpenDART HTTP "+str(r.status_code))
        return r
    with zipfile.ZipFile(io.BytesIO(get("corpCode.xml",{}).content)) as z:
        root=ET.fromstring(z.read(next(n for n in z.namelist() if n.lower().endswith(".xml"))))
    mapping={x.findtext("stock_code","").strip():x.findtext("corp_code") for x in root.findall("list")}
    file=pathlib.Path(__file__).resolve().parents[1]/"data.json";d=json.loads(file.read_text(encoding="utf-8"));today=dt.date.today()
    periods=[(y,q) for y in range(2019,today.year+1) for q in range(1,5) if dt.date(y,q*3,calendar.monthrange(y,q*3)[1])<today]
    report=[];success=0
    for c in d["memoryCycle"]["companies"]:
        ticker=TARGETS.get(c["id"])
        if not ticker:continue
        if c.get("currency")!="KRW million" or not mapping.get(ticker):raise RuntimeError("issuer mapping/currency mismatch")
        raw={(r["fiscalYear"],r["quarter"]):r["dartRaw"] for r in c["rows"] if r.get("dartRaw")};old={r["date"]:r for r in c["rows"]};errors=[];fetched=0
        for y,q in reversed(periods):
            if (y,q) in raw and (y,q) not in periods[-2:]:continue
            try:
                j=get("fnlttSinglAcntAll.json",{"corp_code":mapping[ticker],"bsns_year":y,"reprt_code":REPORT[q],"fs_div":"CFS"}).json()
                if j.get("status") in ("010","011","012","020","021","901"):raise RuntimeError("DART fatal status "+j["status"])
                if j.get("status")!="000":errors.append(f"{y}Q{q}: DART status {j.get('status')}");continue
                raw[y,q]=extract(j["list"],y,q);fetched+=1
            except RuntimeError:raise
            except Exception as e:errors.append(f"{y}Q{q}: {type(e).__name__} / financial fields missing or inconsistent")
            finally:time.sleep(.25)
        revised=[]
        for y,q in periods:
            if (y,q) not in raw:continue
            r=row(raw[y,q],raw.get((y,q-1)),y,q)
            if not r:continue
            p=old.get(r["date"])
            if p and p.get("previousValues"):r["previousValues"]=p["previousValues"]
            if p and any(p.get(k)!=r[k] for k in ("inventory","revenue","cogs")):
                r["previousValues"]={k:p.get(k) for k in ("inventory","revenue","cogs","sourceType","sourceUrls")};revised.append(r["date"])
            old[r["date"]]=r
        c["rows"]=sorted(old.values(),key=lambda r:r["date"]);by={(r["fiscalYear"],r["quarter"]):r for r in c["rows"]}
        for r in c["rows"]:
            p=by.get((r["fiscalYear"]-1,r["quarter"]))
            for field,out in (("inventory","inventoryYoy"),("revenue","revenueYoy")):
                a=r.get(field);b=p.get(field) if p else None
                if a is not None and b is not None and b>0:r[out]=(a/b-1)*100
                elif r.get("dartRaw"):r[out]=None
        n=sum(1 for r in c["rows"] if r.get("dartRaw"))
        if n:
            c["note"]="OpenDART 연결 재무제표 JSON 원문. 4분기 연간−9개월 누적. 단위 백만 원. 미확보 값은 기존 자료 유지 또는 공란."
            src=c.setdefault("sources",[]);url="https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020"
            if not any(x["url"]==url for x in src):src.insert(0,{"label":"OpenDART 단일회사 전체 재무제표","url":url})
        success+=int(fetched>0 and n>0);report.append({"id":c["id"],"completeDartQuarters":n,"fetched":fetched,"revised":revised,"issues":errors})
    print(json.dumps(report,ensure_ascii=False,indent=2))
    pathlib.Path("korea-collection-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    if not success:raise RuntimeError("No complete OpenDART quarters; old data preserved")
    d["memoryCycle"]["koreaApiReport"]={"as_of":str(today),"companies":report}
    d["memoryCycle"]["koreaApiCollection"]={"status":"success" if success==len(TARGETS) else "partial","lastRunDate":str(today),"source":"OpenDART consolidated JSON"}
    d["memoryCycle"]["as_of"]=str(today)
    d["release"]={"version":"BETA 1.5","date":str(today),"note":"OpenDART quarterly API update; see coverage report."}
    temp=file.with_suffix(".tmp");temp.write_text(json.dumps(d,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8");temp.replace(file)
if __name__=="__main__":main()
