// NODE_PATH=.cache/ui-test/node_modules node tests/test_book_trade_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,calls=[],messages=[],timers=[];w.setInterval=(fn,ms)=>{timers.push({fn,ms});return 1};w.confirm=m=>{messages.push(m);return true};
const stations=[{id:1,name:'Лондон <img id="inject">',station_code:'EGLL',station_type:'metar',enabled:0},{id:2,name:'Париж',station_code:'LFPG',station_type:'metar',enabled:1}];
const opts=[{label:'20°C',token_yes:'101',token_no:'102'},{label:'21°C',token_yes:'201',token_no:'202'}];
const book={bids:[{price:'0.40',size:'50'}],asks:[{price:'0.45',size:'30'}],fetched_at:Date.now()/1000};
const positions=[{asset:'101',event_slug:'first',title:'L',size:3.456,avg_price:.31},{asset:'102',event_slug:'first',title:'L',size:2.5,avg_price:.73},{asset:'201',event_slug:'first',title:'L',size:7}];
let release=null,mode='ok',unknown=false,tradeEnabled=true;let releaseEnsemble=null;const now=Date.now()/1000;
const model=(name,yes,n)=>({name,yes_count:yes,no_count:n-yes,members:n,fetched_at:now,local_start:now-3600,local_end:now+3600});
const observation={temp:31,extreme:'max',extreme_at:now-900,last_observation:now-300,fetched_at:now};
w.fetch=async(url,o={})=>{calls.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'x'};else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:true,trading_enabled:tradeEnabled};else if(url==='/api/trading/account-config')data={configured:true};
 else if(url==='/api/trading/account')data={wallet:'0x111',collateral_balance:'10',orders:[],positions,positions_maybe_partial:false,errors:[]};
 else if(url==='/api/trading/control'){tradeEnabled=JSON.parse(o.body).enabled;data={enabled:tradeEnabled}}
 else if(url==='/api/trading/monitor')data={status:'ok'};else if(url==='/api/trading/auto-sell')data={running:true,plans:[]};else if(url.startsWith('/api/trading/intents?'))data={intents:[]};
 else if(url==='/api/stations')data=stations;
 else if(url.match(/^\/api\/stations\/\d+\/weather\?view=1$/)){const s=stations.find(s=>s.id===Number(url.split('/')[3]));data={station:s,forecast:{},metar:{}}}
 else if(url.match(/^\/api\/stations\/\d+\/bindings$/))data=[{id:1,market_slug:'first',market_name:'First'},{id:2,market_slug:'second',market_name:'Second'}];
 else if(url==='/api/market/first/ensemble'){await new Promise(r=>releaseEnsemble=r);data={slug:'first',station:'EGLL',date:'2030-01-01',timezone:'UTC',control:{last_run:now},outcomes:[{token:'102',token_yes:'101',label:'20°C',models:[model('ECMWF IFS',0,51),model('GEFS',1,31)]}]};}
 else if(url==='/api/market/second/ensemble')data={slug:'second',station:'EGLL',date:'2030-01-01',timezone:'UTC',control:{last_run:now},outcomes:[{token:'302',token_yes:'301',label:'Other',station:'EGLL',date:'2030-01-01',observed_at:now,observation,wu:{kind:'max',temp:31,match:true,with_metar:true,fetched_at:now,local_start:now-3600,local_end:now+3600},models:[model('ECMWF IFS',0,51),model('GEFS <img id=bad>',2,31)]}]};
 else if(url.includes('/price-history?'))data={history:[],quotes:[],fidelity_minutes:15};
 else if(url.endsWith('/books'))data={pairs:url.includes('first')?[{yes:'101',no:'102'},{yes:'201',no:'202'}]:[{yes:'301',no:'302'}],books:{'101':book,'102':book,'201':book,'202':book,'301':book,'302':book},requested_at:Date.now()/1000};
 else if(url==='/api/market/first')data={market:{title:'First',closed:false,options:opts},positions:[]};
 else if(url==='/api/market/second')data={market:{title:'Second',closed:false,options:[{label:'Other',token_yes:'301',token_no:'302'}]},positions:[]};
 else if(url==='/api/trading/orders'){
   if(mode==='defer'){mode='ok';await new Promise(r=>release=r)}
   if(unknown&&!JSON.parse(o.body).acknowledged_unknown)return {status:409,ok:false,json:async()=>({code:'unknown_orders',nonces:['a'.repeat(32)],error:'unknown'})};
   data={state:'accepted',order_id:'0x'+'b'.repeat(64)};
 }else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,25)),get=s=>w.document.querySelector(s),all=s=>[...w.document.querySelectorAll(s)],posts=()=>calls.filter(([u,o])=>u==='/api/trading/orders'&&o.method==='POST');
function set(field,value){const input=get(`.book-trade [data-field="${field}"]`);input.value=value;input.dispatchEvent(new w.Event('input',{bubbles:true}))}
(async()=>{
 await tick();await tick();await tick();assert(releaseEnsemble);
 // Switch markets before the first response; it must not overwrite the new one.
 all('.market-tab')[1].click();await tick();await tick();
 let slot=get('.market-ensemble-row');assert(slot.textContent.includes('ECMWF IFS: YES 0% (0/51), NO 100% (51/51)'));
 assert(slot.textContent.includes('METAR · максимум 31°C / 87.8°F'));assert(slot.textContent.includes('GEFS <img id=bad>'));assert(!get('#bad'));assert(slot.textContent.includes('WU + METAR: максимум 31°C · в этом диапазоне · не вероятность'));assert(slot.querySelector('.market-wu-marker.match'));
 releaseEnsemble();await tick();assert(get('.market-ensemble-row').textContent.includes('GEFS <img id=bad>'));
 // Stale or already-started local day can no longer look like a current estimate.
 w.Date.now=()=>Date.now()+3601000;w.renderOverviewEnsemble();assert(get('.market-ensemble-row').textContent.includes('сутки закончились'));assert(!get('.market-ensemble-row').textContent.includes('WU + METAR:'));
 w.Date.now=()=>Date.now()+21601000;w.renderOverviewEnsemble();assert(get('.market-ensemble-row').textContent.includes('устарели'));
 assert.equal(posts().length,0);assert(get('#market-ensemble-note').textContent.includes('0% не означает невозможность'));
 console.log('Market ensemble UI: display, stale data, escaping, race and read-only safety OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
