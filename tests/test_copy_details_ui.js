// NODE_PATH=.cache/ui-test/node_modules node tests/test_copy_details_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,requests=[],intervals=[];w.setInterval=(fn,ms)=>{intervals.push({fn,ms});return intervals.length};
const wallet='0x'+'a'.repeat(40),condition='0x'+'b'.repeat(64),tx='0x'+'c'.repeat(64),key='d'.repeat(64)+':1';
let seq=0,epoch='test-epoch',failure=false,deferStatus=false,releaseStatus=null,deferNotice=false,releaseNotice=null;
const signal={leader_id:1,event_key:key,token_id:'123',condition_id:condition,wallet,label:'<img id=xss1>',title:'<img id=xss2>',side:'BUY',outcome:'YES',size:'10',price:'0.4',timestamp:1700000000,observed_at:1700000002,transaction_hash:tx,event_slug:'',estimate:{desired_shares:'2',desired_value:'0.8',available_shares:'8',best_price:'0.4',book_at:1700000001,book_levels:{bids:[{price:'0.3',size:'4'}],asks:[{price:'0.4',size:'5'}]}}};
const bad={...signal,event_key:'e'.repeat(64)+':1',token_id:'456',event_slug:'javascript:alert(1)',transaction_hash:'0x"><img id=xss3>'};
const detail={metadata:{token_id:'123',condition_id:condition,event_slug:'good-event'},history:{fetched_at:1700000003,partial:true,trades:[{side:'BUY',timestamp:1700000000,size:'10',price:'0.4',transaction_hash:tx},{side:'SELL',timestamp:1700000001,size:'3',price:'0.5',transaction_hash:'<img id=xss4>'}]},positions:{OPEN:{rows:[{status:'<img id=xss5>',current_size:'7',avg_price:'0.4',realized_pnl:'0.3',unrealized_pnl:'0.7',total_pnl:'1'}],partial:false,fetched_at:1700000003},CLOSED:{error:'<img id=xss6>'}}};
w.fetch=async(url,o={})=>{
 requests.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'test'};
 else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:true,trading_enabled:false};
 else if(url==='/api/trading/account-config')data={configured:false};
 else if(url==='/api/stations')data=[];
 else if(url==='/api/copy-observer/notification'){
  if(failure)return {status:503,ok:false,json:async()=>({error:'fail'})};
  data={epoch,seq};if(deferNotice){deferNotice=false;await new Promise(resolve=>releaseNotice=resolve)}
 }
 else if(url==='/api/copy-observer'){
  data={service:'running',leaders:[],signals:[signal,bad],notification:{epoch,seq}};
  if(deferStatus){deferStatus=false;await new Promise(resolve=>releaseStatus=resolve)}
 }
 else if(url.startsWith('/api/copy-observer/metadata?'))data={'123':{token_id:'123',condition_id:condition,event_slug:'good-event'},'456':{token_id:'456',condition_id:'0x'+'f'.repeat(64),event_slug:'wrong-event'}};
 else if(url.includes('/signal/'))data=detail;
 else throw Error('unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,15));
(async()=>{await tick();await tick();
 const nav=w.document.querySelector('[data-page="copy"]');
 assert(!nav.classList.contains('copy-unread'));assert(intervals.some(x=>x.ms===15000));
 seq=1;await w.pollCopyNotice();assert(nav.classList.contains('copy-unread'));assert(nav.getAttribute('aria-label').includes('новые сигналы'));
 failure=true;await w.pollCopyNotice();assert(nav.classList.contains('copy-unread'));assert(nav.classList.contains('copy-notice-error'));failure=false;
 nav.click();await tick();await tick();assert(!nav.classList.contains('copy-unread'));assert(!nav.classList.contains('copy-notice-error'));
 assert.equal(JSON.parse(w.localStorage.getItem('gradus.copy.notification.v1')).seen,1);
 const cards=w.document.querySelectorAll('.copy-signal');assert.equal(cards.length,2);
 assert.equal(cards[0].querySelector('.copy-event-link a').href,'https://polymarket.com/event/good-event');
 assert(cards[1].querySelector('.copy-link-missing'));assert(!cards[1].querySelector('.copy-event-link a'));
 assert.equal(cards[0].querySelectorAll('.copy-links a').length,3);
 assert(cards[0].textContent.includes('BID: 0.3 × 4'));assert(cards[0].textContent.includes('задержка: 2 с'));
 assert(!w.document.querySelector('#xss1,#xss2,#xss3'));
 cards[0].querySelector('.copy-detail-toggle').click();await tick();await tick();
 assert(w.document.querySelector('.copy-detail-content').textContent.includes('SELL · продажа'));
 assert(w.document.querySelector('.copy-detail-content').textContent.includes('Реализованный PnL: 0,3'));
 assert(w.document.querySelector('.copy-detail-content').textContent.includes('история обрезана')||w.document.querySelector('.copy-detail-content').textContent.includes('История обрезана'));
 assert(!w.document.querySelector('#xss4,#xss5,#xss6'));
 const countDetail=()=>requests.filter(([url])=>url.includes('/signal/')).length;const beforeRefresh=countDetail();w.document.querySelector('.copy-detail-refresh').click();await tick();assert.equal(countDetail(),beforeRefresh+1);
 await w.loadCopyObserver();assert(w.document.querySelector('.copy-detail-content').textContent.includes('SELL · продажа'));
 seq=2;await w.pollCopyNotice();assert(nav.classList.contains('copy-unread')); // even while page is open
 // Clicking acknowledges only the snapshot read; newer concurrent signals remain unread.
 deferStatus=true;nav.click();await tick();assert(releaseStatus);seq=3;await w.pollCopyNotice();assert(nav.classList.contains('copy-unread'));
 releaseStatus();await tick();assert(nav.classList.contains('copy-unread'));assert.equal(JSON.parse(w.localStorage.getItem('gradus.copy.notification.v1')).seen,2);
 nav.click();await tick();assert(!nav.classList.contains('copy-unread'));
 // An older in-flight notification cannot roll back the newer one.
 seq=4;deferNotice=true;const older=w.pollCopyNotice();await tick();seq=5;await w.pollCopyNotice();releaseNotice();await older;
 nav.click();await tick();assert.equal(JSON.parse(w.localStorage.getItem('gradus.copy.notification.v1')).seen,5);
 // Database recreation starts a new epoch even if its counter is smaller.
 epoch='new-epoch';seq=1;await w.pollCopyNotice();assert(nav.classList.contains('copy-unread'));
 nav.click();await tick();assert(!nav.classList.contains('copy-unread'));
 seq=2;await w.pollCopyNotice();assert(nav.classList.contains('copy-unread'));w.localStorage.setItem('gradus.copy.notification.v1',JSON.stringify({epoch,seen:2}));w.dispatchEvent(new w.StorageEvent('storage',{key:'gradus.copy.notification.v1'}));assert(!nav.classList.contains('copy-unread'));
 assert(!requests.some(([url,o])=>url.startsWith('/api/trading/')&&o.method==='POST'));
 dom.window.close();console.log('Copy details UI: OK');
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
