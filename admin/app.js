"use strict";
const $ = id => document.getElementById(id);
let token = "";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const count = n => Number(n || 0).toLocaleString();
const date = v => v ? new Date(v.endsWith("Z") ? v : v + "Z").toLocaleString(undefined,{dateStyle:"short",timeStyle:"short",timeZone:"UTC"}) : "—";
async function api(path) {
 const r = await fetch(path, {headers:{"Authorization":"Bearer "+token},cache:"no-store"});
 if(r.status===401) throw Error("Invalid ADMIN_TOKEN. Check Render → Environment.");
 if(!r.ok) throw Error("API request failed ("+r.status+")");
 return r.json();
}
function table(id, rows, n) {
 $(id).innerHTML = rows.length ? rows.join("") : '<tr><td class="empty" colspan="'+n+'">No data recorded yet</td></tr>';
}
function update(data, runs) {
 $("visitsCount").textContent=count(data.visits);
 $("visitorsCount").textContent=count(data.unique_visitors);
 $("questionsCount").textContent=count(data.questions);
 $("latency").textContent=data.avg_ms ? (data.avg_ms/1000).toFixed(2)+"s" : "—";
 $("answered").textContent=count(data.answered);
 $("sessions").textContent=count(data.question_sessions);
 $("cached").textContent=count(data.cached);
 const highest=Math.max(1,...data.daily.flatMap(d=>[d.visits,d.questions]));
 $("chart").innerHTML=data.daily.map(d=>'<div class="barpair" title="'+esc(d.day)+' · '+count(d.visits)+' visits, '+count(d.questions)+' questions"><span class="bar" style="height:'+Math.max(1,Math.round(d.visits/highest*100))+'%"></span><span class="bar q" style="height:'+Math.max(1,Math.round(d.questions/highest*100))+'%"></span></div>').join("");
 const d=data.daily;
 $("chartlabels").innerHTML='<span>'+esc(d[0]?.day||"")+'</span><span>'+esc(d[Math.floor(d.length/2)]?.day||"")+'</span><span>'+esc(d[d.length-1]?.day||"")+'</span>';
 $("statuses").innerHTML=(data.statuses.length?data.statuses:[{name:"No responses",count:0}]).map(x=>'<div class="metric"><span>'+esc(x.name)+'</span><strong>'+count(x.count)+'</strong></div>').join("");
 $("referrers").innerHTML=(data.referrers.length?data.referrers:[{name:"No visits yet",count:0}]).map(x=>'<div class="source"><span>'+esc(x.name)+'</span><b>'+count(x.count)+'</b></div>').join("");
 table("questionRows",runs.map(x=>'<tr><td>'+esc(date(x.created_at))+'</td><td class="question">'+esc(x.question)+'</td><td><span class="chip">'+esc(x.status)+'</span></td><td>'+esc(x.llm)+'</td><td>'+esc(x.total_ms)+" ms"+'</td></tr>'),5);
 table("visitRows",data.recent_visits.map(x=>'<tr><td>'+esc(date(x.created_at))+'</td><td><code>'+esc(x.visitor)+'</code></td><td>'+esc(x.referrer)+'</td></tr>'),3);
 $("status").textContent="Updated "+new Date().toLocaleTimeString()+" · UTC data · "+data.days+" days";
}
async function refresh(){
 if(!token)return;
 $("refresh").disabled=true;$("status").textContent="Refreshing…";
 try{
  const [data,runs]=await Promise.all([api("/admin/api/overview?days="+$("range").value),api("/runs?limit=100")]);
  update(data,runs);
  $("signin").hidden=true;$("dashboard").hidden=false;$("lock").hidden=false;$("loginError").textContent="";
 }catch(e){
  $("status").textContent=String(e.message||e);
  if(String(e.message).includes("ADMIN_TOKEN")){$("loginError").textContent=e.message;$("signin").hidden=false;$("dashboard").hidden=true;}
 }finally{$("refresh").disabled=false;}
}
$("login").addEventListener("submit",e=>{e.preventDefault();token=$("token").value.trim();$("token").value="";refresh();});
$("refresh").addEventListener("click",refresh);
$("range").addEventListener("change",refresh);
$("lock").addEventListener("click",()=>{token="";$("dashboard").hidden=true;$("signin").hidden=false;$("lock").hidden=true;$("status").textContent="Locked";});
window.addEventListener("pagehide",()=>{token="";});
