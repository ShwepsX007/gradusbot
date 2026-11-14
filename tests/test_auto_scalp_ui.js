// NODE_PATH=.cache/ui-test/node_modules node tests/test_auto_scalp_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const initial={initial_balance:'1000',trade_cap:'50',total_cap:'500',min_shares:'5',min_price:'0.85',max_price:'0.985',target_cents:'5',min_profit:'0.03',fee_rate:'0.05',max_positions:5,daily_loss:'5',interval_seconds:30,forecast_mode:'off',forecast_gap:'3',forecast_gap_units:'C'};
let saved={...initial},enabled=false,holdPost=null,holdGet=null,failNextPost=false;const requests=[];
const state=()=>({config:{settings:{...saved},balance:'950',enabled,service:'running',scan_at:1700000000,message:'<img id=bad>'},real_available:false,positions:[{id:1,title:'<img id=xss>',label:'20°C',remaining:'50',original:'50',entry_price:'0.9',target:'0.95',realized:'0',event_slug:'javascript:evil',forecast_info:{mode:'fallback',reason:'нет <img id=bad3>'}}],journal:[{position_id:1,kind:'SELL',timestamp:1700000000,size:'5',price:'0.95',fee:'0.01',pnl:'0.1',note:'<script id=bad2>'}]});
const tick=()=>new Promise(r=>setTimeout(r,30));
async function open(){
 const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
 const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
 const w=dom.window;w.setInterval=()=>0;w.confirm=()=>true;
 w.fetch=async(url,o={})=>{requests.push([url,o]);let data,status=200;
  if(url==='/api/session')data={csrf:'test'};else if(url==='/api/settings')data={units:'C'};else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};else if(url==='/api/stations')data=[];
  else if(url==='/api/auto-scalp'&&o.method==='POST'){
    const body=JSON.parse(o.body);assert.equal(body.mode,'demo');assert.equal(body.confirm,true);
    if(holdPost){const h=holdPost;holdPost=null;await h.promise}
    if(failNextPost){failNextPost=false;data={error:'Тестовая ошибка'};status=400}
    else if(body.action==='settings'){saved={...body.settings};data={ok:true,settings:{...saved}}}
    else {if(body.action==='start')enabled=true;if(body.action==='stop')enabled=false;data={ok:true}}
  }else if(url==='/api/auto-scalp'){
    const snapshot=state();if(holdGet){const h=holdGet;holdGet=null;await h.promise}data=snapshot;
  }else throw Error('Unexpected '+url);
  return {status,ok:status===200,json:async()=>data};
 };
 w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
 await tick();w.document.querySelector('[data-page="scalp"]').click();await tick();return {dom,w,form:w.document.querySelector('#scalp-form')};
}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
(async()=>{
 let {dom,w,form}=await open();
 assert(w.document.querySelector('#scalp-page').textContent.includes('Реальная автоторговля пока не реализована'));
 assert(w.document.querySelector('.scalp-mode button[disabled]'));assert(w.document.querySelector('#scalp-chart svg'));
 assert(!w.document.querySelector('#xss,#bad,#bad2,#bad3'));assert(w.document.querySelector('#scalp-positions').textContent.includes('Без прогноза'));assert(!w.document.querySelector('#scalp-positions a'));
 assert(form.checkValidity());assert.equal(form.elements.forecast_mode.value,'off');
 // A select that emits only change must not be reset by the next poll.
 form.elements.forecast_mode.value='forecast';form.elements.forecast_mode.dispatchEvent(new w.Event('change',{bubbles:true}));
 form.elements.forecast_gap.value='7';form.elements.forecast_gap.dispatchEvent(new w.Event('input',{bubbles:true}));
 form.elements.forecast_gap_units.value='F';form.elements.forecast_gap_units.dispatchEvent(new w.Event('change',{bubbles:true}));
 form.elements.target_cents.value='6';form.elements.target_cents.dispatchEvent(new w.Event('input',{bubbles:true}));
 assert(w.document.querySelector('#scalp-start').disabled);await w.loadScalp();assert.equal(form.elements.forecast_mode.value,'forecast');assert.equal(form.elements.forecast_gap.value,'7');
 // Actual button click runs native constraint validation, not an artificial submit event.
 form.querySelector('button[type=submit]').click();await tick();assert.equal(saved.forecast_mode,'forecast');assert.equal(saved.forecast_gap,'7');assert.equal(saved.forecast_gap_units,'F');assert.equal(saved.target_cents,'6');
 assert(w.document.querySelector('#scalp-form-status').textContent.includes('сохранены на сервере'));assert(!w.document.querySelector('#scalp-start').disabled);
 const firstRow=w.document.querySelector('#scalp-positions .scalp-row');const firstLog=w.document.querySelector('#scalp-journal .scalp-row');await w.loadScalp();
 assert.strictEqual(w.document.querySelector('#scalp-positions .scalp-row'),firstRow);assert.strictEqual(w.document.querySelector('#scalp-journal .scalp-row'),firstLog);
 dom.window.close();({dom,w,form}=await open());assert.equal(form.elements.forecast_mode.value,'forecast');assert.equal(form.elements.forecast_gap.value,'7');assert.equal(form.elements.forecast_gap_units.value,'F');assert.equal(form.elements.target_cents.value,'6');
 // Slow polling is coalesced rather than creating a new request every interval.
 const slow=deferred();holdGet=slow;const before=requests.filter(([u,o])=>u==='/api/auto-scalp'&&!o.method).length;
 const read1=w.loadScalp(),read2=w.loadScalp(),read3=w.loadScalp();await tick();assert.equal(requests.filter(([u,o])=>u==='/api/auto-scalp'&&!o.method).length,before+1);
 slow.resolve();await Promise.all([read1,read2,read3]);
 // Stale read in flight at submission cannot overwrite the confirmed result.
 const stale=deferred();holdGet=stale;const oldRead=w.loadScalp();await tick();form.elements.forecast_gap.value='9';form.elements.forecast_gap.dispatchEvent(new w.Event('input',{bubbles:true}));form.querySelector('button[type=submit]').click();await tick();
 assert.equal(saved.forecast_gap,'9');stale.resolve();await oldRead;await tick();assert.equal(form.elements.forecast_gap.value,'9');
 // Changes made while POST is in flight remain unsaved and block starting.
 const pending=deferred();holdPost=pending;form.elements.forecast_gap.value='10';form.elements.forecast_gap.dispatchEvent(new w.Event('input',{bubbles:true}));form.querySelector('button[type=submit]').click();await tick();
 form.elements.forecast_gap.value='11';form.elements.forecast_gap.dispatchEvent(new w.Event('input',{bubbles:true}));pending.resolve();await tick();assert.equal(saved.forecast_gap,'10');assert.equal(form.elements.forecast_gap.value,'11');assert(w.document.querySelector('#scalp-start').disabled);assert(w.document.querySelector('#scalp-form-status').textContent.includes('ещё раз'));
 // Failed save leaves edited value intact and never displays a false success.
 failNextPost=true;form.querySelector('button[type=submit]').click();await tick();assert.equal(saved.forecast_gap,'10');assert.equal(form.elements.forecast_gap.value,'11');assert(w.document.querySelector('#scalp-form-status').textContent.includes('Тестовая ошибка'));
 form.querySelector('button[type=submit]').click();await tick();assert.equal(saved.forecast_gap,'11');
 // Native invalid value never posts and shows a visible reason.
 const count=requests.filter(([u,o])=>u==='/api/auto-scalp'&&o.method==='POST').length;form.elements.forecast_gap.value='0';form.elements.forecast_gap.dispatchEvent(new w.Event('input',{bubbles:true}));form.querySelector('button[type=submit]').click();await tick();assert.equal(requests.filter(([u,o])=>u==='/api/auto-scalp'&&o.method==='POST').length,count);assert(w.document.querySelector('#scalp-form-status').textContent.includes('Проверьте'));
 form.elements.forecast_gap.value='11';form.elements.forecast_gap.dispatchEvent(new w.Event('input',{bubbles:true}));form.querySelector('button[type=submit]').click();await tick();
 w.document.querySelector('#scalp-start').click();await tick();assert(enabled);assert(w.document.querySelector('#scalp-start').disabled);w.document.querySelector('#scalp-stop').click();await tick();assert(!enabled);
 assert(!requests.some(([u,o])=>u.startsWith('/api/trading/')&&o.method==='POST'));dom.window.close();console.log('Auto scalping demo UI: persistent settings, races, validation and efficient rendering OK');
})().catch(e=>{console.error(e);process.exitCode=1});
