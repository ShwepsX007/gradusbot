// NODE_PATH=.cache/ui-test/node_modules node tests/test_quick_market_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,calls=[],intervals=[];w.setInterval=(fn,ms)=>{intervals.push({fn,ms});return 1};
w.HTMLDialogElement.prototype.showModal=function(){this.open=true};w.HTMLDialogElement.prototype.close=function(){this.open=false};
const slug='highest-temperature-in-london-on-may-1-2026',condition='0x'+'a'.repeat(64),wallet='0x'+'b'.repeat(40),key='d'.repeat(64)+':1';
const copy={leader_id:1,event_key:key,token_id:'123',condition_id:condition,wallet,label:'Trader',title:'London',side:'BUY',size:'10',price:'0.9',timestamp:1700000000,observed_at:1700000002,transaction_hash:'0x'+'c'.repeat(64),event_slug:'',estimate:{}};
const station={id:1,name:'London',station_code:'EGLL',station_type:'metar',enabled:0},position={id:1,title:'London',label:'20°C',event_slug:slug,condition_id:condition,token:'123',remaining:'10',original:'10',entry_price:'0.9',target:'0.95',realized:'0'};
let stations=[],bindings=[],deferWeather=true,releaseWeather=null,review=false,fail=false,deferOpen=false,releaseOpen=null;
const settings={initial_balance:'1000',trade_cap:'50',total_cap:'500',min_shares:'5',min_price:'0.85',max_price:'0.985',target_cents:'5',min_profit:'0.03',fee_rate:'0.05',max_positions:5,daily_loss:'5',interval_seconds:30};
w.fetch=async(url,o={})=>{calls.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'test'};
 else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};
 else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/stations')data=stations;
 else if(url==='/api/copy-observer')data={service:'running',leaders:[],signals:[copy],notification:{epoch:'test',seq:0}};
 else if(url.startsWith('/api/copy-observer/metadata'))data={};
 else if(url==='/api/auto-scalp')data={config:{settings,balance:'950',enabled:false,service:'running'},positions:[position],journal:[{position_id:1,kind:'BUY',timestamp:1700000000,size:'10',price:'0.9',fee:'0',pnl:null,note:'demo'},{position_id:999,kind:'BUY',timestamp:1700000000,size:'1',price:'0.9',fee:'0',note:'older',event_slug:slug,condition_id:condition,token:'123',title:'Old',label:'20°C'}]};
 else if(url==='/api/market-station/quick-open'){
   const body=JSON.parse(o.body);assert.equal(body.token,'123');assert.equal(body.condition_id,condition);assert.equal(body.confirm,true);
   if(fail)return {status:502,ok:false,json:async()=>({error:'Ошибка получения события'})};
   if(deferOpen){deferOpen=false;await new Promise(resolve=>releaseOpen=resolve)}
   if(review)data={needs_review:true,slug,resolution:{slug,title:'London',status:'unknown',candidate:null,candidates:[]}};
   else{stations=[station];bindings=[{market_slug:slug,market_name:'London'}];data={needs_review:false,station,slug,token:'123',selection:'no'}}
 }
 else if(url==='/api/stations/1/bindings')data=bindings;
 else if(url==='/api/stations/1/weather?view=1'){
   if(deferWeather){deferWeather=false;await new Promise(resolve=>releaseWeather=resolve)}
   data={station,forecast:{},metar:{}};
 }
 else if(url===`/api/market/${slug}/books`)data={pairs:[{yes:'456',no:'123'}],books:{},requested_at:Date.now()/1000};
 else if(url===`/api/market/${slug}`)data={market:{title:'London',closed:false,options:[{label:'19°C',token_yes:'789',token_no:'987'},{label:'20°C',token_yes:'456',token_no:'123'}]},positions:[]};
 else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,20));
(async()=>{await tick();await tick();
 w.document.querySelector('[data-page="copy"]').click();await tick();
 const button=w.document.querySelector('#copy-signals .quick-market-open');assert(button&&!button.disabled);assert.equal(button.dataset.slug,'');
 button.click();button.click();await tick();await tick();
 assert.equal(calls.filter(([u])=>u==='/api/market-station/quick-open').length,1);
 assert(releaseWeather,'weather is still loading');assert(!w.document.querySelector('#weather-page').classList.contains('hidden'));
 assert.equal(w.document.querySelector('#market-title').textContent,'London');assert.equal(w.document.querySelector('#trade-option').value,'1');assert.equal(w.document.querySelector('#trade-token').value,'no');
 assert.equal(w.document.querySelector('#trade-price').value,'');assert.equal(w.document.querySelector('#trade-size').value,'');
 assert(w.document.querySelector('#trade-send-status').textContent.includes('ордер не отправлен'));
 releaseWeather();await tick();assert.equal(w.document.querySelector('#trade-option').value,'1');
 w.document.querySelector('[data-page="scalp"]').click();await tick();
 assert.equal(w.document.querySelectorAll('#scalp-positions .quick-market-open').length,1);assert.equal(w.document.querySelectorAll('#scalp-journal .quick-market-open').length,2);
 w.document.querySelector('#scalp-journal .quick-market-open').click();await tick();await tick();
 assert(!w.document.querySelector('#weather-page').classList.contains('hidden'));assert.equal(w.document.querySelector('#trade-token').value,'no');
 w.document.querySelector('[data-page="copy"]').click();await tick();review=true;w.document.querySelector('#copy-signals .quick-market-open').click();await tick();
 assert(w.document.querySelector('#market-first-dialog').open);assert(w.document.querySelector('#market-resolution').textContent.includes('Однозначный код станции'));w.document.querySelector('#market-first-dialog').close();review=false;
 fail=true;w.document.querySelector('#copy-signals .quick-market-open').click();await tick();assert(w.document.querySelector('#toast').textContent.includes('Ошибка получения события'));assert(!w.document.querySelector('#copy-signals .quick-market-open').disabled);fail=false;
 deferOpen=true;w.document.querySelector('#copy-signals .quick-market-open').click();await tick();assert(releaseOpen);w.document.querySelector('[data-page="scalp"]').click();releaseOpen();await tick();assert(!w.document.querySelector('#scalp-page').classList.contains('hidden'));
 assert(!calls.some(([u,o])=>u.startsWith('/api/trading/')&&o.method==='POST'));
 dom.window.close();console.log('Quick market opening UI: OK');
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
