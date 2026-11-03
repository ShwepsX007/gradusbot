// NODE_PATH=.cache/ui-test/node_modules node tests/test_station_auto_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window;w.confirm=()=>true;let hidden=false,requests=[],intervals=[],releaseFirst;
Object.defineProperty(w.document,'hidden',{configurable:true,get:()=>hidden});
w.setInterval=(fn,ms)=>{intervals.push({fn,ms});return intervals.length};
const stations=[{id:1,name:'London',station_code:'EGLL',station_type:'metar',enabled:0},{id:2,name:'Paris',station_code:'LFPG',station_type:'metar',enabled:1}];
w.fetch=async(url,o={})=>{requests.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'test'};
 else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:true,trading_enabled:false};
 else if(url==='/api/trading/account-config')data={configured:false};
 else if(url==='/api/stations')data=stations;
 else if(url==='/api/stations/1/weather?view=1'){
   if(!releaseFirst)await new Promise(r=>{releaseFirst=r}); // deliberately ignore abort to mimic server work already underway
   data={station:stations[0],metar:{temp:10},metar_history:[],metar_archive:{status:'ok',reports:[]},forecast_status:{reason:'no_coordinates'},history:[]};
 }
 else if(url==='/api/stations/2/weather?view=1')data={station:stations[1],metar:{temp:20},metar_history:[],metar_archive:{status:'ok',reports:[]},forecast_status:{reason:'no_coordinates'},history:[]};
 else if(url.endsWith('/bindings'))data=[];
 else if(url.endsWith('/ai-forecast'))data={forecast:null};
 else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,30));
const weatherRequests=()=>requests.filter(([u])=>u.includes('/weather?view=1'));
function runMinute(){for(const timer of intervals.filter(i=>i.ms===60000))timer.fn()}
(async()=>{
 await tick();await tick();assert(releaseFirst);assert.equal(weatherRequests().length,1);
 // A background tick while a request is still in flight must not duplicate it.
 runMinute();await tick();assert.equal(weatherRequests().length,1);
 w.document.querySelector('.station-tab[data-id="2"]').click();await tick();assert.equal(weatherRequests().length,2);
 assert(weatherRequests()[0][1].signal.aborted);releaseFirst();await tick();
 assert.equal(w.document.querySelector('#station-code').textContent,'LFPG');assert.equal(w.document.querySelector('#temperature').textContent,'20°C');
 assert(w.document.querySelector('#station-status').textContent.includes('АВТООПРОС'));
 assert(w.document.querySelector('#bot-poll-label').textContent.includes('РАЗРЕШЁН'));
 runMinute();await tick();assert.equal(weatherRequests().length,3);assert(weatherRequests().at(-1)[0].includes('/2/'));
 w.document.querySelector('[data-page="stations"]').click();await tick();const before=weatherRequests().length;
 runMinute();await tick();assert.equal(weatherRequests().length,before);
 // Entering the overview while the browser tab is hidden must not start a request.
 hidden=true;w.document.dispatchEvent(new w.Event('visibilitychange'));w.document.querySelector('[data-page="weather"]').click();await tick();runMinute();await tick();assert.equal(weatherRequests().length,before);
 hidden=false;w.document.dispatchEvent(new w.Event('visibilitychange'));await tick();assert.equal(weatherRequests().length,before+1);assert(weatherRequests().at(-1)[0].includes('/2/'));
 // Automatic navigation never writes global bot on/off state.
 assert(!requests.some(([u,o])=>/^\/api\/stations\/\d+$/.test(u)&&o.method==='PATCH'));
 console.log('Station auto-view: switch, hidden page, background and stale responses OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
