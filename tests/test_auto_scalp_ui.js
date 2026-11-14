// NODE_PATH=.cache/ui-test/node_modules node tests/test_auto_scalp_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,requests=[];w.setInterval=()=>0;w.confirm=()=>true;
let enabled=false;const settings={initial_balance:'1000',trade_cap:'50',total_cap:'500',min_shares:'5',min_price:'0.85',max_price:'0.985',target_cents:'5',min_profit:'0.03',fee_rate:'0.05',max_positions:5,daily_loss:'5',interval_seconds:30,forecast_mode:'forecast',forecast_gap:'3',forecast_gap_units:'F'};
const state=()=>({config:{settings,balance:'950',enabled,service:'running',scan_at:1700000000,message:'<img id=bad>'},real_available:false,positions:[{id:1,title:'<img id=xss>',label:'20°C',remaining:'50',original:'50',entry_price:'0.9',target:'0.95',realized:'0',event_slug:'javascript:evil',forecast_info:{mode:'fallback',reason:'нет <img id=bad3>'}}],journal:[{position_id:1,kind:'SELL',timestamp:1700000000,size:'5',price:'0.95',fee:'0.01',pnl:'0.1',note:'<script id=bad2>'}]});
w.fetch=async(url,o={})=>{requests.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'test'};else if(url==='/api/settings')data={units:'C'};else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};else if(url==='/api/stations')data=[];
 else if(url==='/api/auto-scalp'&&o.method==='POST'){let body=JSON.parse(o.body);assert.equal(body.mode,'demo');assert.equal(body.confirm,true);if(body.action==='start')enabled=true;if(body.action==='stop')enabled=false;data={ok:true}}
 else if(url==='/api/auto-scalp')data=state();else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,20));
(async()=>{await tick();w.document.querySelector('[data-page="scalp"]').click();await tick();
 assert(w.document.querySelector('#scalp-page').textContent.includes('Реальная автоторговля пока не реализована'));
 assert(w.document.querySelector('.scalp-mode button[disabled]'));assert(w.document.querySelector('#scalp-chart svg'));
 assert(!w.document.querySelector('#xss,#bad,#bad2,#bad3'));assert(w.document.querySelector('#scalp-positions').textContent.includes('Без прогноза'));assert(w.document.querySelector('#scalp-positions').textContent.includes('только по цене'));assert(!w.document.querySelector('#scalp-positions a'));
 const form=w.document.querySelector('#scalp-form');assert.equal(form.elements.forecast_mode.value,'forecast');assert.equal(form.elements.forecast_gap_units.value,'F');assert.equal(form.elements.forecast_gap.value,'3');assert.equal(form.elements.target_cents.value,'5');form.elements.target_cents.value='6';form.elements.target_cents.dispatchEvent(new w.Event('input',{bubbles:true}));await w.loadScalp();assert.equal(form.elements.target_cents.value,'6');
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();assert(requests.some(([u,o])=>u==='/api/auto-scalp'&&o.method==='POST'&&JSON.parse(o.body).settings.target_cents==='6'&&JSON.parse(o.body).settings.forecast_mode==='forecast'&&JSON.parse(o.body).settings.forecast_gap_units==='F'));
 w.document.querySelector('#scalp-start').click();await tick();assert(w.document.querySelector('#scalp-start').disabled);w.document.querySelector('#scalp-stop').click();await tick();assert(!enabled);
 assert(!requests.some(([u,o])=>u.startsWith('/api/trading/')&&o.method==='POST'));dom.window.close();console.log('Auto scalping demo UI: OK');
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
