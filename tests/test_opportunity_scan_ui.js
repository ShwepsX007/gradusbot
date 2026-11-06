// NODE_PATH=.cache/ui-test/node_modules node tests/test_opportunity_scan_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window;w.confirm=()=>true;let requests=[],releaseSlow=null,slow=false;
const now=Date.now()/1000, event={slug:'highest-temperature-in-london-on-november-6-2026',title:'London <img id="evil">',station_code:'EGLL',date:'2026-11-06',timezone:'Europe/London',quotes_at:now,station:{status:'found',candidate:{code:'EGLL',kind:'Weather Underground',url:'https://www.wunderground.com/history/daily/gb/london/EGLL'}}};
const row={event,label:'29°C',side:'no',gap:2,gap_units:'C',leader:{label:'27°C',yes_price:.98},reference:{kind:'leader'},bid:{price:'0.95',size:'20'},ask:{price:'0.99',size:'5'},book_at:now,ask_entry:false};
w.fetch=async(url,o={})=>{
 requests.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'csrf'};
 else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};
 else if(url==='/api/stations'||url==='/api/markets')data=[];
 else if(url.startsWith('/api/temperature-opportunities?')){
   if(slow)await new Promise(r=>{releaseSlow=r});
   const params=new URL('http://example'+url).searchParams;
   if(params.get('session'))data={rows:[{...row,label:'30°C',ask:{price:'0.97',size:'8'},ask_entry:true}],session:'session',next_offset:null,checked:1,total_candidates:2,unavailable:0,stale_leader:0,skipped:{},catalog_incomplete:false};
   else data={rows:[row],session:'session',next_offset:100,checked:1,total_candidates:2,unavailable:0,stale_leader:0,skipped:{no_observation:1},catalog_incomplete:true};
 }
 else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,25));
(async()=>{
 await tick();w.document.querySelector('[data-page="markets"]').click();await tick();
 const form=w.document.querySelector('#opportunity-form');form.elements.side.value='both';form.elements.quote.value='either';form.elements.price.value='97';
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 const call=requests.find(([u])=>u.startsWith('/api/temperature-opportunities?'))[0];let q=new URL('http://example'+call).searchParams;
 assert.equal(q.get('side'),'both');assert.equal(q.get('quote'),'either');assert.equal(q.get('price'),'97');
 assert.equal(w.document.querySelectorAll('.opportunity-card').length,1);const card=w.document.querySelector('.opportunity-card');
 assert(card.textContent.includes('29°C'));assert(card.textContent.includes('NO'));assert(card.textContent.includes('95¢'));assert(card.textContent.includes('99¢'));assert(card.textContent.includes('подходящего ASK'));
 assert(!w.document.querySelector('#evil'));assert(w.document.querySelector('#opportunity-status').textContent.includes('Каталог неполный'));
 assert(!w.document.querySelector('#opportunity-more').classList.contains('hidden'));
 w.document.querySelector('#opportunity-more').click();await tick();assert.equal(w.document.querySelectorAll('.opportunity-card').length,2);assert(w.document.querySelector('#opportunity-more').classList.contains('hidden'));
 assert(requests.some(([u])=>u.includes('session=session')&&u.includes('offset=100')));
 assert(!requests.some(([u,o])=>o.method==='POST'&&/trading|orders/.test(u)));
 form.elements.gap.value='2';form.elements.gap.dispatchEvent(new w.Event('input',{bubbles:true}));assert.equal(w.document.querySelectorAll('.opportunity-card').length,0);
 // Changes while a request is in flight invalidate that result and do not show a misleading match.
 slow=true;form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert(releaseSlow);
 form.elements.price.value='90';form.elements.price.dispatchEvent(new w.Event('input',{bubbles:true}));releaseSlow();await tick();
 assert(w.document.querySelector('#opportunity-status').textContent.includes('Фильтр изменён'));assert.equal(w.document.querySelectorAll('.opportunity-card').length,0);
 console.log('Opportunity scan UI: filters, pagination, bid/ask and stale responses OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
