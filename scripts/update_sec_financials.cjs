'use strict';
// SEC Company Facts: no credentials needed; declare a real contact in SEC_USER_AGENT.
const CIK={mu:723125,dell:1571996,hpe:1645590,smci:1375365,amat:6951,lrcx:707549,klac:319201,ter:97210};
const REVENUE=['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','RevenueFromContractWithCustomerIncludingAssessedTax'];
const COST=['CostOfRevenue','CostOfGoodsAndServicesSold','CostOfSales','CostOfGoodsSold'];
const DAY=86400000,days=(a,b)=>Math.round((Date.parse(b)-Date.parse(a))/DAY)+1;
const next=a=>new Date(Date.parse(a)+DAY).toISOString().slice(0,10);
const formOK=f=>/^(10-Q|10-K)(\/A)?$/.test(f||'');
const finite=Number.isFinite;
function extract(json,cik,asof){
 const facts=json.facts?.['us-gaap']||{};
 const read=tag=>(facts[tag]?.units?.USD||[]).filter(r=>formOK(r.form)&&r.filed<=asof&&r.end<=asof&&r.end>='2018-01-01'&&finite(r.val)&&r.accn).map(r=>({...r,tag}));
 const inv=read('InventoryNet').filter(r=>!r.start&&r.val>=0);
 const allTags=[...new Set([...REVENUE,...COST,'GrossProfit'])],flows=new Map(allTags.map(t=>[t,read(t).filter(r=>r.start)]));
 const byAcc=new Map();for(const r of inv){const old=byAcc.get(r.accn);if(!old||r.end>old.end)byAcc.set(r.accn,r)}
 const filings=new Map();for(const r of byAcc.values()){
  const q=r.fp==='FY'&&r.form.startsWith('10-K')?4:/^Q[123]$/.test(r.fp||'')&&r.form.startsWith('10-Q')?Number(r.fp[1]):null;
  if(!q||!Number.isInteger(r.fy))continue;
  const key=r.fy+'-'+q,old=filings.get(key);
  if(!old||r.filed>old.filed){filings.set(key,{...r,quarter:q})}
 }
 const ordered=[...filings.values()].sort((a,b)=>a.end.localeCompare(b.end));
 const source=r=>'https://www.sec.gov/Archives/edgar/data/'+cik+'/'+r.accn.replace(/-/g,'')+'/'+r.accn+'-index.htm';
 // Select a single unambiguous value for this filing and interval. Do not use
 // later comparative facts to silently restate previously reported quarters.
 function unique(tag,filing,pred){
  const candidates=(flows.get(tag)||[]).filter(r=>r.accn===filing.accn&&r.end===filing.end&&pred(r));
  const vals=new Set(candidates.map(r=>r.val));return vals.size===1?candidates[0]:null;
 }
 function flow(tags,f,prev){
  const isQuarter=r=>{const d=days(r.start,r.end);return d>=70&&d<=110&&(!prev||r.start===next(prev.end))};
  for(const tag of tags){
   const direct=unique(tag,f,isQuarter);if(direct)return {val:direct.val,start:direct.start,tag,method:'reported-quarter',refs:[direct]};
   if(!prev||f.quarter===1)continue;
   // Same-tag fiscal YTD difference, including FY minus nine months for Q4.
   const total=unique(tag,f,r=>{const d=days(r.start,r.end);return d>=70*f.quarter&&d<=110*f.quarter});
   if(!total)continue;
   const prior=unique(tag,prev,r=>r.start===total.start&&days(r.start,r.end)>=70*(f.quarter-1)&&days(r.start,r.end)<=110*(f.quarter-1));
   if(prior&&total.val>=prior.val){const start=next(prev.end),d=days(start,f.end);if(d>=70&&d<=110)return {val:total.val-prior.val,start,tag,method:'same-tag-YTD-difference',refs:[total,prior]}}
  }return null;
 }
 const rows=[],issues=[];
 for(const f of ordered){if(f.end<'2019-01-01')continue;
  const prior=filings.get(f.fy+'-'+(f.quarter-1)),prev=f.quarter>1?prior:null;
  const invValues=new Set(inv.filter(r=>r.accn===f.accn&&r.end===f.end).map(r=>r.val));
  if(invValues.size!==1){issues.push(f.end+' conflicting inventory');continue}
  const rev=flow(REVENUE,f,prev);let cogs=flow(COST,f,prev);
  const gp=flow(['GrossProfit'],f,prev);
  // Where gross profit is reported, require the cost to reconcile or use the
  // reported revenue-minus-gross-profit subtotal (same quarter and units).
  if(rev&&gp&&rev.start===gp.start){const cost=rev.val-gp.val;if(cost>0&&(!cogs||Math.abs(cogs.val-cost)>Math.max(1e4,Math.abs(rev.val)*.00001)))cogs={val:cost,start:rev.start,tag:rev.tag+' minus GrossProfit',method:'revenue-minus-gross-profit',refs:[...rev.refs,...gp.refs]}}
  if(!rev||!cogs||rev.start!==cogs.start||rev.val<=0||cogs.val<=0){issues.push(f.end+' incomplete or inconsistent quarter');continue}
  const n=days(rev.start,f.end);if(n<70||n>110){issues.push(f.end+' invalid period length');continue}
  const previousYearEnd=filings.get((f.fy-1)+'-4');if(f.quarter===1&&previousYearEnd&&rev.start!==next(previousYearEnd.end)){issues.push(f.end+' first quarter start mismatch');continue}
  rows.push({date:f.end,fiscalYear:f.fy,quarter:f.quarter,inventory:f.val/1e6,revenue:rev.val/1e6,cogs:cogs.val/1e6,days:n,periodStart:rev.start,sourceType:'SEC Company Facts JSON / consolidated US GAAP',sourceUrls:[...new Set([f,...rev.refs,...cogs.refs].map(source))],sourceTags:{inventory:'InventoryNet',revenue:rev.tag,cogs:cogs.tag},derivation:{revenue:rev.method,cogs:cogs.method},filed:f.filed});
 }
 return {rows,issues};
}
function merge(company,result){
 const old=new Map(company.rows.map(r=>[r.date,r])),changed=[];
 for(const row of result.rows){
  const prev=old.get(row.date);
  // Preserve differences for review; prefer SEC original reported values for
  // a complete, consistently dated quarter, never replace with missing values.
  if(prev&&['inventory','revenue','cogs'].some(k=>finite(prev[k])&&Math.abs(prev[k]-row[k])>Math.max(.01,Math.abs(row[k])*.00001))){
   row.previousValues={inventory:prev.inventory,revenue:prev.revenue,cogs:prev.cogs,sourceType:prev.sourceType||'legacy',sourceUrls:prev.sourceUrls||[]};changed.push(row.date)
  }else if(prev?.previousValues){row.previousValues=prev.previousValues}
  // Remove another row with the same fiscal period only if dates are close;
  // retain evidence of the previous date, and leave unrelated periods alone.
  for(const [key,r] of old)if(key!==row.date&&r.fiscalYear===row.fiscalYear&&r.quarter===row.quarter&&Math.abs(Date.parse(key)-Date.parse(row.date))<=10*DAY){row.previousDate=key;old.delete(key)}
  old.set(row.date,row)
 }
 const rows=[...old.values()].sort((a,b)=>a.date.localeCompare(b.date));
 const fy=new Map();for(const r of rows){const k=r.fiscalYear+'-'+r.quarter;if(fy.has(k))fy.set(k,null);else fy.set(k,r)}
 const comparable=(a,b,k)=>finite(a?.[k])&&finite(b?.[k])&&b[k]>0&&a[k]>=0&&Math.abs(days(b.date,a.date)-366)<=16;
 for(const r of rows){const p=fy.get((r.fiscalYear-1)+'-'+r.quarter);for(const [key,out] of [['inventory','inventoryYoy'],['revenue','revenueYoy']]){
   if(comparable(r,p,key))r[out]=(r[key]/p[key]-1)*100;
   else if(r.sourceType?.startsWith('SEC Company Facts'))r[out]=null;
  }if(finite(r.days)&&r.days>=70&&r.days<=110&&finite(r.inventory)&&r.cogs>0)r.dio=r.inventory/r.cogs*r.days;else if(r.sourceType?.startsWith('SEC Company Facts'))r.dio=null}
 company.rows=rows;return changed;
}
async function main(){
 const fs=require('node:fs'),path=require('node:path'),ua=process.env.SEC_USER_AGENT;
 if(!ua||!/@[^\s]+\.[^\s]+/.test(ua))throw Error('Set SEC_USER_AGENT to project name and valid contact email (not an API key).');
 const file=path.resolve(__dirname,'../data.json'),data=JSON.parse(fs.readFileSync(file,'utf8')),asof=new Date().toISOString().slice(0,10),report=[];
 let any=false;
 for(const c of data.memoryCycle.companies){if(!CIK[c.id])continue;const cik=CIK[c.id],url='https://data.sec.gov/api/xbrl/companyfacts/CIK'+String(cik).padStart(10,'0')+'.json';
  if(c.currency!=='USD million'){report.push({id:c.id,error:'currency mismatch'});continue}
  try{const res=await fetch(url,{headers:{'User-Agent':ua,'Accept':'application/json'},signal:AbortSignal.timeout(45000)});if(!res.ok)throw Error('HTTP '+res.status);const facts=await res.json();if(Number(facts.cik)!==cik)throw Error('CIK mismatch');
   const result=extract(facts,cik,asof);if(!result.rows.length)throw Error('No complete quarterly facts');
   const before=JSON.stringify(c.rows),revisions=merge(c,result);c.sources=c.sources||[];if(!c.sources.some(s=>s.url===url))c.sources.unshift({label:'SEC Company Facts JSON',url});c.note='SEC JSON으로 확인된 분기는 원문 기준 갱신. 기존 자료와 차이 및 출처를 행별 보존. 미확보 구간은 공란.';any=any||JSON.stringify(c.rows)!==before;
   report.push({id:c.id,completeApiQuarters:result.rows.length,first:result.rows[0].date,last:result.rows.at(-1).date,revisions,issues:result.issues});
  }catch(e){report.push({id:c.id,error:String(e.message)});console.error(c.id,e.message)}
  await new Promise(r=>setTimeout(r,300));
 }
 console.log(JSON.stringify(report,null,2));if(!report.some(r=>r.completeApiQuarters>0))throw Error('No SEC API retrieval succeeded; existing dashboard retained.');
 if(any){data.memoryCycle.as_of=asof;data.memoryCycle.secApiReport={as_of:asof,companies:report};data.release={version:'BETA 1.3',date:asof,note:'SEC Company Facts JSON 갱신 · 확인된 분기만 반영'};fs.writeFileSync(file+'.tmp',JSON.stringify(data,null,2)+'\n');fs.renameSync(file+'.tmp',file)}
}
if(typeof module!=='undefined')module.exports={extract,merge};
if(typeof require!=='undefined'&&require.main===module)main().catch(e=>{console.error(e.message);process.exitCode=1});
