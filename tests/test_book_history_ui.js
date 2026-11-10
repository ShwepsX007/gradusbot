// NODE_PATH=.cache/ui-test/node_modules node tests/test_book_history_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,calls=[],timers=[];w.setInterval=(fn,ms)=>{timers.push({fn,ms});return timers.length};
const station={id:1,name:'London',station_code:'EGLL',station_type:'metar',enabled:0};
const pairs=[{yes:'1',no:'2'},{yes:'3',no:'4'}],options=[{label:'20°C <img id="xss">',token_yes:'1',token_no:'2'},{label:'21°C',token_yes:'3',token_no:'4'}];
const now=Math.floor(Date.now()/1000),book={bids:[{price:'.4',size:'5'}],asks:[{price:'.6',size:'10'}],fetched_at:now};
let pending=null,defer=false,fail=false,empty=false;
w.fetch=async(url,o={})=>{calls.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'t'};else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};else if(url==='/api/stations')data=[station];
 else if(url==='/api/stations/1/weather?view=1')data={station,forecast:{},metar:{}};
 else if(url==='/api/stations/1/bindings')data=[{market_slug:'london',market_name:'London'}];
 else if(url==='/api/market/london')data={market:{title:'London',options,closed:false},positions:[]};
 else if(url==='/api/market/london/books')data={pairs,books:{'1':book,'2':book,'3':book,'4':book},requested_at:now};
 else if(url.includes('/price-history?')){
   if(defer){defer=false;await new Promise(r=>pending=r)}
   if(fail)return {ok:false,status:502,json:async()=>({error:'История недоступна'})};
   data={history:empty?[]:[{t:now-3600,p:.4},{t:now,p:.6}],fidelity_minutes:15,fetched_at:now};
 }else throw Error('Unexpected '+url);
 return {ok:true,status:200,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,25)),get=s=>w.document.querySelector(s),history=()=>calls.filter(([u])=>u.includes('/price-history?'));
(async()=>{
 await tick();await tick();assert.equal(history().length,0);
 get('.book-toggle').click();await tick();assert.equal(history().length,1);assert(history()[0][0].includes('token=1&side=yes&period=1d'));
 assert(get('.book-history-svg'));assert(get('.book-history').compareDocumentPosition(get('.book-side'))&w.Node.DOCUMENT_POSITION_FOLLOWING);
 assert(get('.book-history-note').textContent.includes('BID 40¢ / ASK 60¢'));assert(get('.book-history-note').textContent.includes('не история BID/ASK'));assert(!get('#xss'));
 const svg=get('.book-history-svg');svg.getBoundingClientRect=()=>({left:0,width:800});svg.dispatchEvent(new w.MouseEvent('pointermove',{clientX:0,bubbles:true}));assert(get('.book-history-value').textContent.includes('40¢'));svg.dispatchEvent(new w.Event('pointerleave'));assert(get('.book-history-value').textContent.includes('60¢'));
 defer=true;get('.history-side[data-side="no"]').click();await tick();assert(pending);assert(history()[1][0].includes('token=2&side=no'));
 get('.history-period[data-period="6h"]').click();await tick();assert(history()[2][0].includes('token=2&side=no&period=6h'));pending();await tick();assert(get('.history-period.active').textContent==='6ч');assert(get('.book-history-svg').getAttribute('aria-label').includes('NO'));
 fail=true;get('.history-period[data-period="1w"]').click();await tick();assert(get('.book-chart-empty').textContent.includes('История недоступна'));fail=false;empty=true;get('.history-retry').click();await tick();assert(get('.book-chart-empty').textContent.includes('история отсутствует'));empty=false;
 get('.book-toggle[data-index="1"]').click();await tick();assert(history().at(-1)[0].includes('token=3&side=yes'));assert(get('.book-history-head').textContent.includes('21°C'));
 let count=history().length;timers.filter(x=>x.ms===60000).forEach(x=>x.fn());await tick();assert.equal(history().length,count+1);
 w.document.querySelector('[data-page="stations"]').click();count=history().length;timers.filter(x=>x.ms===60000).forEach(x=>x.fn());await tick();assert.equal(history().length,count);
 w.document.querySelector('[data-page="weather"]').click();await tick();await tick();get('.book-toggle[data-index="1"]').click();count=history().length;timers.filter(x=>x.ms===60000).forEach(x=>x.fn());await tick();assert.equal(history().length,count);
 assert(!calls.some(([u,o])=>o.method==='POST'&&u.startsWith('/api/trading/')));
 dom.window.close();console.log('Order book price history UI: lazy loading, periods, outcome, stale requests, errors and chart OK');
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
