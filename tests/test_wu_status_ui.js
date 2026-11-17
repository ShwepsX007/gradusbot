// NODE_PATH=.cache/ui-test/node_modules node tests/test_wu_status_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window, realTimeout=w.setTimeout.bind(w), realClear=w.clearTimeout.bind(w),scheduled=new Map();let timer=1000,hidden=false,requests=[],releaseOld;
Object.defineProperty(w.document,'hidden',{configurable:true,get:()=>hidden});
w.setTimeout=(fn,ms)=>{if(ms!==5000)return realTimeout(fn,ms);const id=++timer;scheduled.set(id,fn);return id};
w.clearTimeout=id=>{scheduled.delete(id);realClear(id)};
const stations=[{id:1,name:'London',station_code:'EGLL',station_type:'metar',enabled:0},{id:2,name:'Paris',station_code:'LFPG',station_type:'metar',enabled:0}];
const future=Math.floor(Date.now()/1000)+3600;
const result={status:'ok',timezone:'UTC',day:'2026-01-01',complete_remaining:false,fetched_at:Math.floor(Date.now()/1000),points:[{time:future,temp:22,condition:'Ясно'}]};
w.fetch=async(url,o={})=>{requests.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'test'};else if(url==='/api/settings')data={units:'C'};else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};else if(url==='/api/stations')data=stations;
 else if(/^\/api\/stations\/\d+\/weather\?view=1$/.test(url)){const s=stations.find(s=>s.id===Number(url.split('/')[3]));data={station:s,metar:{temp:s.id},history:[],metar_history:[],forecast:{timezone:'UTC'},forecast_ensemble:{timezone:'UTC',points:[]},wu_forecast:{status:'loading',points:[]},metar_archive:{reports:[]},forecast_status:{reason:'ok'}};}
 else if(url==='/api/stations/1/wu-forecast?view=1&tz=UTC'){await new Promise(r=>releaseOld=r);data={...result,points:[{time:future,temp:99}]};}
 else if(url==='/api/stations/2/wu-forecast?view=1&tz=UTC')data=result;
 else if(url.endsWith('/bindings'))data=[];else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,25));
function runPoll(){assert.equal(scheduled.size,1);const [id,fn]=scheduled.entries().next().value;scheduled.delete(id);fn()}
(async()=>{
 await tick();await tick();assert.equal(scheduled.size,1);runPoll();await tick();assert(releaseOld);
 const old=requests.find(([u])=>u.includes('/1/wu-forecast'));
 w.document.querySelector('.station-tab[data-id="2"]').click();await tick();assert(old[1].signal.aborted);assert.equal(scheduled.size,1);
 releaseOld();await tick();assert.equal(w.document.querySelector('#station-code').textContent,'LFPG');assert(!w.document.querySelector('#wu-strip-list').textContent.includes('99°C'));
 runPoll();await tick();assert(w.document.querySelector('#wu-strip-list').textContent.includes('22°C'));assert(w.document.querySelector('#wu-strip-list').textContent.includes('Ясно'));assert.equal(scheduled.size,0);
 // When the view is hidden, pending local status checks are cancelled.
 w.document.dispatchEvent(new w.Event('visibilitychange'));await tick();assert.equal(scheduled.size,1);
 hidden=true;w.document.dispatchEvent(new w.Event('visibilitychange'));assert.equal(scheduled.size,0);
 const count=requests.filter(([u])=>u.includes('/wu-forecast')).length;await tick();assert.equal(requests.filter(([u])=>u.includes('/wu-forecast')).length,count);
 console.log('WU status UI: fast local polling, station race, abort and hidden view OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
