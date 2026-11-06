// NODE_PATH=.cache/ui-test/node_modules node tests/test_arc_temperature_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window,now=Date.parse('2026-06-01T12:00:00Z');w.Date.now=()=>now;w.confirm=()=>true;
const station={id:1,name:'Test',station_code:'EGLL',station_type:'metar',enabled:0};
const stamp=s=>Date.parse('2026-06-'+s+'Z')/1000;
// Half-hour, very close consecutive changes, an unchanged value, and a change
// immediately beside the live marker. The previous-day value defines the first arrow.
const history=[['01T00:10:00',-5],['01T10:30:00',10],['01T11:00:00',11],['01T11:05:00',12],['01T11:10:00',12],['01T11:55:00',13]].map(([t,v])=>[stamp(t),v]);
history.unshift([Date.parse('2026-05-31T23:30:00Z')/1000,-4]);
const weather={station,metar:{temp:13},history,metar_history:history,metar_archive:{status:'ok',reports:[]},forecast:{timezone:'UTC',daily:{time:['2026-05-31','2026-06-01'],sunrise:['2026-05-31T06:00','2026-06-01T06:00'],sunset:['2026-05-31T18:00','2026-06-01T18:00']},hourly:{time:['2026-06-01T00:00','2026-06-01T11:00'],temperature_2m:[-5,15]}}};
w.fetch=async(url,options={})=>{let data;
 if(url==='/api/session')data={csrf:'test'};
 else if(url==='/api/settings')data=options.method==='POST'?{units:JSON.parse(options.body).units}:{units:'C'};
 else if(url==='/api/security-info')data={tunnel:false,trading_enabled:false};
 else if(url==='/api/stations')data=[station];
 else if(url==='/api/stations/1/weather?view=1')data=weather;
 else if(url==='/api/stations/1/bindings')data=[];
 else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
 await tick();await tick();
 const root=w.document.querySelector('#arc-observations'),arrows=[...root.querySelectorAll('.arc-trend')],labels=[...root.querySelectorAll('.arc-temp-label')];
 assert.equal(arrows.length,5);assert.equal(labels.length,arrows.length);
 assert.equal(labels[0].querySelectorAll('tspan').length,2);assert.equal(labels[0].children[0].textContent,'-5°C');assert.equal(labels[0].children[1].textContent,'23°F');
 labels.forEach((label,i)=>{assert.equal(label.getAttribute('x'),arrows[i].getAttribute('x'));assert(Number(label.children[1].getAttribute('dy'))>0);assert(Number(label.getAttribute('y'))<Number(arrows[i].getAttribute('y')))});
 assert.equal(root.querySelectorAll('circle').length,6); // unchanged point remains, without a redundant label
 assert(Number(root.ownerSVGElement.getAttribute('viewBox').split(' ')[1])<0); // crowded labels are not clipped
 assert(w.document.querySelector('.arc-scroll').contains(w.document.querySelector('#arc-time-axis')));
 assert(w.document.querySelector('#arc-note').textContent.includes('°C и °F'));
 // The arc explicitly shows both scales while the rest of the interface still
 // follows the global C/F/both setting.
 const form=w.document.querySelector('#settings-form');
 for(const [units,expected] of [['F','55.4°F'],['B','13°C / 55.4°F'],['C','13°C']]){
   form.elements.units.value=units;form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
   assert.equal(w.document.querySelector('#temperature').textContent,expected);
   assert.equal(root.querySelectorAll('.arc-temp-label').length,5);
   assert.equal(root.querySelector('.arc-temp-label').children[0].textContent,'-5°C');
   assert.equal(root.querySelector('.arc-temp-label').children[1].textContent,'23°F');
 }

 w.document.querySelector('#arc-prev').click();assert.equal(root.querySelectorAll('.arc-temp-label').length,0);
 assert.equal(root.ownerSVGElement.getAttribute('viewBox'),'0 0 800 220');
 w.document.querySelector('#arc-next').click();assert.equal(root.querySelectorAll('.arc-temp-label').length,5);
 // If solar data disappears after a rerender, stale labels and expanded viewport reset.
 weather.forecast.daily.sunrise[1]=null;w.document.dispatchEvent(new w.Event('visibilitychange'));await tick();
 assert.equal(root.querySelectorAll('.arc-temp-label').length,0);assert.equal(root.ownerSVGElement.getAttribute('viewBox'),'0 0 800 220');
 console.log('Arc UI: one stacked °C/°F label per arrow, dense labels, date navigation OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
