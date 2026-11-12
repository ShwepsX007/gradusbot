// NODE_PATH=.cache/ui-test/node_modules node tests/test_portfolio_redesign_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window;let calls=[],mode='normal';w.confirm=()=>false;
const portfolio={wallet:'0x123',balance:'3.83',value:9.18,errors:['История частично недоступна.'],orders_partial:true,positions_partial:true,activity_partial:false,
 positions:[{asset:'a',event_slug:'london',title:'London <img id="bad">',outcome:'No',size:12,value:9.18,pnl:-2.58,avg_price:.2,bid:.4,bid_size:2,bid_at:1234},{asset:'b',event_slug:'paris',title:'Paris',outcome:'Yes',size:5,value:null,pnl:null}],
 orders:[{asset_id:'a',id:'0x'+'a'.repeat(64),side:'SELL',price:.99,size_matched:2,original_size:12,status:'LIVE'},{asset_id:'unmapped',id:'0x'+'b'.repeat(64),side:'BUY',price:null,size_matched:0,original_size:10,status:'LIVE'}],
 intents:[],activity:[{event_slug:'london',title:'London',outcome:'No',type:'TRADE',side:'BUY',price:.2,size:12,value:11.77,timestamp:1234},{event_slug:'paris',type:'TRADE',side:'SELL',price:.6,size:2,timestamp:1234}],pnl:[{timestamp:100,value:1},{timestamp:200,value:null},{timestamp:300,value:-2}]};
const markets=[{name:'London weather',slug:'london'},{name:'Paris <img id="bad2">',slug:'paris'}];
w.fetch=async(url,options={})=>{calls.push([url,options]);let data;
 if(url==='/api/session')data={csrf:'x'};else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};else if(url==='/api/stations')data=[];
 else if(url==='/api/markets')data=markets;
 else if(url==='/api/trading/portfolio')data=mode==='normal'?portfolio:{wallet:'0x123',balance:null,value:9,positions:null,orders:null,activity:null,intents:null,pnl:[{timestamp:100,value:1}],errors:['CLOB недоступен.']};
 else throw Error('Unexpected '+url);
 return {ok:true,status:200,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
 await tick();await tick();w.document.querySelector('[data-page="portfolio"]').click();await tick();await tick();
 const get=id=>w.document.querySelector('#'+id), rows=id=>[...get(id).querySelectorAll('.portfolio-row')];
 assert(get('portfolio-balances').textContent.includes('13,01'));assert(get('portfolio-status').textContent.includes('усечён'));assert(get('portfolio-status').textContent.includes('500'));assert(get('portfolio-status').textContent.includes('История частично недоступна'));
 assert.equal(rows('portfolio-positions').length,2);assert.equal(rows('portfolio-orders').length,2);assert(!get('bad'));assert.equal(rows('portfolio-positions')[0].querySelector('.portfolio-pill').textContent,'No');
 assert(rows('portfolio-positions')[0].textContent.includes('-2,58'));assert(rows('portfolio-positions')[1].textContent.includes('—'));
 assert(rows('portfolio-orders')[0].textContent.includes('London'));assert.equal(rows('portfolio-orders')[0].querySelector('.portfolio-market-copy small').title,'0x'+'a'.repeat(64));assert(rows('portfolio-orders')[0].textContent.includes('2 / 12'));assert(rows('portfolio-orders')[0].textContent.includes('99¢'));assert.equal(rows('portfolio-orders')[0].querySelector('.portfolio-open').dataset.slug,'london');assert.equal(rows('portfolio-orders')[1].querySelector('.portfolio-open').dataset.slug,'');
 assert(rows('portfolio-positions')[0].textContent.includes('20¢ / 40¢'));assert(rows('portfolio-positions')[0].textContent.includes('2 долей'));assert(rows('portfolio-activity')[0].textContent.includes('Вход 20¢'));assert(rows('portfolio-activity')[1].textContent.includes('Вход — · выход 60¢'));
 assert(get('portfolio-panel-orders').textContent.includes('не обязательно рассчитано'));assert(get('portfolio-activity').textContent.includes('Покупка'));assert.equal(get('portfolio-pnl').querySelectorAll('polyline').length,2);
 assert(!w.document.querySelector('#portfolio-page .account-cancel'));assert(!w.document.querySelector('#portfolio-page #trade-submit'));
 const before=calls.length,search=get('portfolio-search');search.value='london';search.dispatchEvent(new w.Event('input',{bubbles:true}));
 assert.equal(rows('portfolio-positions').filter(r=>!r.hidden).length,1);assert.equal(rows('portfolio-orders').filter(r=>!r.hidden).length,1);assert.equal(rows('portfolio-activity').filter(r=>!r.hidden).length,1);
 get('portfolio-tab-orders').click();assert.equal(calls.length,before);search.value='несуществующий';search.dispatchEvent(new w.Event('input',{bubbles:true}));assert(rows('portfolio-orders').every(r=>r.hidden));assert.equal(get('portfolio-orders').querySelector('.portfolio-filter-empty').hidden,false);
 search.value='';search.dispatchEvent(new w.Event('input',{bubbles:true}));assert(rows('portfolio-orders').every(r=>!r.hidden));
 mode='partial';get('portfolio-refresh').click();await tick();assert(get('portfolio-balances').textContent.includes('—'));assert(!get('portfolio-balances').textContent.includes('13,01'));assert(get('portfolio-positions').textContent.includes('недоступны'));assert(get('portfolio-orders').textContent.includes('недоступны'));assert(get('portfolio-status').textContent.includes('CLOB недоступен'));assert(get('portfolio-pnl').textContent.includes('недостаточно данных'));assert(get('portfolio-pnl').textContent.includes('1'));
 w.document.querySelector('[data-page="markets"]').click();await tick();assert(!get('bad2'));const saved=[...get('all-markets').querySelectorAll('.saved-market-row')];assert.equal(saved.length,2);assert(saved[0].querySelector('.resolve-managed-market'));assert(saved[0].querySelector('.rename-managed-market'));assert(saved[0].querySelector('.delete-managed-market'));
 const msearch=get('saved-markets-search');msearch.value='paris';msearch.dispatchEvent(new w.Event('input',{bubbles:true}));assert(saved[0].hidden);assert(!saved[1].hidden);msearch.value='nothing';msearch.dispatchEvent(new w.Event('input',{bubbles:true}));assert.equal(get('saved-markets-filter-empty').hidden,false);
 assert(!calls.some(([u,o])=>o.method==='POST'&&/trading\/(orders|cancel)/.test(u)));
 console.log('Portfolio and markets UI: structured snapshots, search, partial data, escaping and read-only safety OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
