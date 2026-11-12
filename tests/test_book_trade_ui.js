// NODE_PATH=.cache/ui-test/node_modules node tests/test_book_trade_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,calls=[],messages=[],timers=[];w.setInterval=(fn,ms)=>{timers.push({fn,ms});return 1};w.confirm=m=>{messages.push(m);return true};
const stations=[{id:1,name:'Лондон <img id="inject">',station_code:'EGLL',station_type:'metar',enabled:0},{id:2,name:'Париж',station_code:'LFPG',station_type:'metar',enabled:1}];
const opts=[{label:'20°C',token_yes:'101',token_no:'102'},{label:'21°C',token_yes:'201',token_no:'202'}];
const book={bids:[{price:'0.40',size:'50'}],asks:[{price:'0.45',size:'30'}],fetched_at:Date.now()/1000};
const positions=[{asset:'101',event_slug:'first',title:'L',size:3.456,avg_price:.31},{asset:'102',event_slug:'first',title:'L',size:2.5,avg_price:.73},{asset:'201',event_slug:'first',title:'L',size:7}];
let release=null,mode='ok',unknown=false,tradeEnabled=true;
w.fetch=async(url,o={})=>{calls.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'x'};else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:true,trading_enabled:tradeEnabled};else if(url==='/api/trading/account-config')data={configured:true};
 else if(url==='/api/trading/account')data={wallet:'0x111',collateral_balance:'10',orders:[],positions,positions_maybe_partial:false,errors:[]};
 else if(url==='/api/trading/control'){tradeEnabled=JSON.parse(o.body).enabled;data={enabled:tradeEnabled}}
 else if(url==='/api/trading/monitor')data={status:'ok'};else if(url.startsWith('/api/trading/intents?'))data={intents:[]};
 else if(url==='/api/stations')data=stations;
 else if(url.match(/^\/api\/stations\/\d+\/weather\?view=1$/)){const s=stations.find(s=>s.id===Number(url.split('/')[3]));data={station:s,forecast:{},metar:{}}}
 else if(url.match(/^\/api\/stations\/\d+\/bindings$/))data=[{id:1,market_slug:'first',market_name:'First'},{id:2,market_slug:'second',market_name:'Second'}];
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
 await tick();await tick();await tick();
 assert(get('#market-position-note').textContent.includes('Отсутствие отметки'));assert(get('.market-tab[data-slug=first]').classList.contains('has-position'));assert(!get('.market-tab[data-slug=second]').classList.contains('has-position'));
 // Dropdown is searchable by name/ICAO; selecting uses the same safe weather navigation.
 get('#station-picker').open=true;get('#station-picker-search').value='lfpg';get('#station-picker-search').dispatchEvent(new w.Event('input'));
 assert.equal(all('.station-picker-item').length,1);get('.station-picker-item').click();await tick();assert.equal(get('#station-code').textContent,'LFPG');assert(!get('#station-picker').open);assert(get('.station-tab[data-id="2"]').classList.contains('active'));
 get('#station-picker-search').value='';get('#station-picker-search').dispatchEvent(new w.Event('input'));assert(!get('#inject'));get('.station-picker-item[data-id="1"]').click();await tick();await tick();assert.equal(get('#station-code').textContent,'EGLL');
 get('.book-toggle[data-index="0"]').click();await tick();assert(get('.book-trade'));assert(get('.book-trade-positions').textContent.includes('средний вход 31¢'));assert(get('.book-trade-positions').textContent.includes('средний вход 73¢'));assert.equal(all('.book-prepare-sale').length,2);assert.equal(posts().length,0);
 // Sale fills only a local, confirmed draft; never sends and respects quantity precision.
 get('.book-prepare-sale[data-asset="102"]').click();assert.equal(posts().length,0);assert.equal(get('.book-trade [data-field="token"]').value,'no');assert.equal(get('.book-trade [data-field="action"]').value,'SELL');assert.equal(get('.book-trade [data-field="size"]').value,'2.5');assert.equal(get('.book-trade [data-field="price"]').value,'40');
 assert.equal(get('#trade-action').value,'BUY');
 // A fresh book/account rerender must preserve the user's unsent draft and focus.
 const input=get('.book-trade [data-field="size"]');input.value='1.25';input.dispatchEvent(new w.Event('input'));input.focus();w.renderAccountSnapshot();assert.equal(get('.book-trade [data-field="size"]').value,'1.25');assert.equal(w.document.activeElement,get('.book-trade [data-field="size"]'));
 // Amount units are never silently reinterpreted between shares and pUSD.
 set('action','BUY');assert.equal(get('.book-trade [data-field="size"]').value,'');set('size','3');set('type','FAK');assert.equal(get('.book-trade [data-field="size"]').value,'');assert(get('.book-trade-size-label').textContent.includes('pUSD'));
 set('type','GTC');set('price','44');set('size','3');
 get('.book-toggle[data-index="1"]').click();assert.equal(get('.book-trade [data-field="price"]').value,'');set('price','50');set('size','6');
 get('.book-toggle[data-index="0"]').click();assert.equal(get('.book-trade [data-field="price"]').value,'44');assert.equal(get('.book-trade [data-field="size"]').value,'3');
 // Quote click in the expanded book fills local and existing top draft but never submits.
 get('.book-details .quote-pick[data-token="yes"][data-side="ask"]').click();assert.equal(get('.book-trade [data-field="token"]').value,'yes');assert.equal(get('.book-trade [data-field="price"]').value,'45');assert.equal(get('.book-trade [data-field="size"]').value,'');assert.equal(get('#trade-price').value,'45');assert.equal(posts().length,0);
 set('size','3');mode='defer';get('.book-trade-submit').click();await tick();assert(release);assert(get('.book-trade-submit').disabled);assert(get('#trade-submit').disabled);get('.book-trade-submit').click();assert.equal(posts().length,1);
 const body=JSON.parse(posts()[0][1].body);assert.equal(body.asset,'101');assert.equal(body.slug,'first');assert.equal(body.side,'BUY');assert.equal(body.selection,'yes');assert.equal(body.price,'0.450000');assert.equal(body.size,'3');assert.equal(body.order_type,'GTC');assert(messages[0].includes('20°C'));
 release();await tick();await tick();assert(get('.book-trade-status').textContent.includes('не подтверждение исполнения'));
 // Explicit unknown warning remains mandatory for local requests.
 unknown=true;get('.book-trade-submit').click();await tick();await tick();assert(messages.some(m=>m.includes('ВНИМАНИЕ')&&m.includes('20°C')));assert(JSON.parse(posts().at(-1)[1].body).acknowledged_unknown);unknown=false;
 // A delayed unknown-orders response must not retry if another book was opened.
 mode='defer';unknown=true;const beforePosts=posts().length,beforeMessages=messages.length;get('.book-trade-submit').click();await tick();get('.book-toggle[data-index="1"]').click();release();await tick();await tick();assert.equal(posts().length,beforePosts+1);assert.equal(messages.length,beforeMessages+1);unknown=false;get('.book-toggle[data-index="0"]').click();
 // Trading switch applies to both forms and is enforced without sending a request.
 w.document.querySelector('[data-page="settings"]').click();await tick();const ctl=get('#trade-control-form');ctl.elements.enabled.value='false';ctl.elements.current_password.value='pw';ctl.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();w.document.querySelector('[data-page="weather"]').click();await tick();await tick();assert(get('.book-trade-submit').disabled);assert(get('#trade-submit').disabled);const stopped=posts().length;get('.book-trade-submit').click();assert.equal(posts().length,stopped);
 // Stale snapshot prevents a one-click sale draft.
 w.Date.now=()=>Date.now()+70000;w.renderAccountSnapshot();assert(all('.book-prepare-sale').every(b=>b.disabled));assert(get('.book-trade-positions').textContent.includes('УСТАРЕЛ'));assert(get('.market-tab[data-slug=first]').classList.contains('position-stale'));assert(get('#market-position-note').textContent.includes('УСТАРЕЛА'));w.Date.now=Date.now;
 // Different event and range cannot inherit the old draft or its selected token.
 all('.market-tab')[1].click();await tick();await tick();get('.book-toggle').click();await tick();assert.equal(get('.book-trade [data-field="price"]').value,'');assert.equal(get('.book-trade [data-field="size"]').value,'');assert(get('.book-trade-heading').textContent.includes('Other'));
 assert(!calls.some(([u,o])=>/^\/api\/stations\/\d+$/.test(u)&&o.method==='PATCH'));
 console.log('Station picker and book trade UI: identity, drafts, sale, safety and submission OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
