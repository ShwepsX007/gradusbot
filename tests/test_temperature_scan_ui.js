// Run with NODE_PATH=.cache/ui-test/node_modules node tests/test_temperature_scan_ui.js
const assert = require('assert');
const fs = require('fs');
const {JSDOM,VirtualConsole} = require('jsdom');
const html=fs.readFileSync('web/static/index.html','utf8');
const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>{throw e});
const dom=new JSDOM(html,{url:'https://terminal.example/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});
const {window:w}=dom;
let stations=[],markets=[],bindings=[],calls=[],prompts=0;
const candidate={code:'RPLL',kind:'Aviation Weather Center',field:'event.resolutionSource',url:'https://aviationweather.gov/api/data/metar?ids=RPLL'};
const item={slug:'highest-temperature-in-manila-on-november-2-2026',title:'Manila',date:'2026-11-02',timezone:'Asia/Manila',station_code:'RPLL',station:{status:'found',candidate},quotes_at:Date.now()/1000-180,top_yes_prices:[{label:'20°C',price:0.35,accepting_orders:true},{label:'21°C',price:0.28,accepting_orders:true},{label:'<img src=x onerror=alert(1)>',price:0.2,accepting_orders:false}],max_odds_yes:{label:'17°C',price:0.05,accepting_orders:true}};
const hk={slug:'highest-temperature-in-hong-kong-on-november-2-2026',title:'Hong Kong',date:'2026-11-02',timezone:'Asia/Hong_Kong',station_code:null,station:{status:'unknown',candidate:null}};
w.confirm=()=>{prompts++;return true};
w.fetch=async (url,options={})=>{
  calls.push([url,options]); const body=options.body?JSON.parse(options.body):{};let data;
  if(url==='/api/session')data={csrf:'token'};
  else if(url==='/api/settings')data={units:'C'};
  else if(url==='/api/stations')data=stations;
  else if(url==='/api/markets'&&options.method==='POST'){markets.push({slug:body.slug,name:'Hong Kong'});data={ok:true}}
  else if(url==='/api/markets')data=markets;
  else if(url==='/api/temperature-scan')data={events:[item,hk],incomplete:true,unknown_timezones:0};
  else if(url==='/api/market-station/resolve')data=body.slug===item.slug?{slug:item.slug,title:item.title,status:'found',candidate}:{slug:hk.slug,title:hk.title,status:'unknown',candidate:null};
  else if(url==='/api/market-station/add'){
    assert.equal(body.code,'RPLL');assert.equal(body.confirm,true);
    stations=[{id:1,name:'Manila',station_code:'RPLL',station_type:'metar',enabled:false}];
    markets.push({slug:item.slug,name:'Manila'});bindings=[{market_slug:item.slug,market_name:'Manila'}];
    data={station:stations[0],slug:item.slug,created_station:true,already_bound:false};
  }
  else if(url==='/api/stations/1/bindings')data=bindings;
  else if(url==='/api/stations/1/weather')data={station:stations[0],forecast:{},metar:{}};
  else if(url.endsWith('/books'))data={books:{},requested_at:Date.now()/1000};
  else if(url.startsWith('/api/market/'))data={market:{title:'Manila',options:[]},positions:[]};
  else throw Error('Unexpected URL '+url);
  return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
  await tick();w.document.querySelector('[data-page="markets"]').click();await tick();
  assert(w.document.querySelector('#all-markets').compareDocumentPosition(w.document.querySelector('.scan-panel')) & w.Node.DOCUMENT_POSITION_FOLLOWING);
  w.document.querySelector('#scan-temperature').click();await tick();
  assert.equal(w.document.querySelectorAll('.scan-card').length,2);
  assert(w.document.querySelector('#scan-status').textContent.includes('не полностью'));
  assert(w.document.querySelector('#scan-device').textContent.includes('Ваше время'));
  const manila=w.document.querySelector(`[data-slug="${item.slug}"].scan-add`).closest('.scan-card');
  assert.equal(manila.querySelectorAll('.scan-quote').length,3);
  assert(manila.textContent.includes('20°C'));assert(manila.textContent.includes('35%'));assert(manila.textContent.includes('×2,86'));
  assert(manila.textContent.includes('Макс. расчётный коэффициент YES: ×20 · 17°C (5%)'));
  assert(manila.textContent.includes('снимок устарел'));assert(manila.textContent.includes('приём не подтверждён'));
  assert.equal(manila.querySelectorAll('img').length,0);
  assert(w.document.querySelector(`[data-slug="${hk.slug}"].scan-add`).closest('.scan-card').textContent.includes('Цены YES активных исходов недоступны'));
  w.document.querySelector(`[data-slug="${item.slug}"].scan-add`).click();await tick();await tick();
  assert.equal(prompts,0);assert.equal(stations.length,1);assert.equal(stations[0].enabled,false);
  assert(w.document.querySelector(`[data-slug="${item.slug}"].scan-add`).disabled);
  w.document.querySelector(`[data-slug="${hk.slug}"].scan-add`).click();await tick();await tick();
  assert.equal(stations.length,1);assert(markets.some(m=>m.slug===hk.slug));
  assert(calls.some(([url,o])=>url==='/api/markets'&&o.method==='POST'));
  assert(w.document.querySelector('#toast').textContent.includes('без станции'));
  w.document.querySelector('#scan-filter').value='RPLL';w.document.querySelector('#scan-filter').dispatchEvent(new w.Event('input'));
  assert.equal(w.document.querySelectorAll('.scan-card').length,1);
  console.log('Temperature scan UI: OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
