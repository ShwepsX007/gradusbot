// NODE_PATH=.cache/ui-test/node_modules node tests/test_ai_forecast_ui.js
const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window;w.confirm=()=>true;w.prompt=()=> 'terminal-password';let requests=[],releaseGenerate,releaseOld,oldPending=false;
const stations=[{id:1,name:'London',station_code:'EGLL',station_type:'metar',enabled:1},{id:2,name:'Paris',station_code:'LFPG',station_type:'metar',enabled:1}];
const now=Date.now()/1000, forecast={station_id:1,text:'<img id="unsafe">\nОтвет',model:'gemini-3.5-flash-lite',project:'project-alpha',created_at:now,total_count:42,truncated:0,input_summary:{metar_points:2,archive_status:'partial',archive_truncated:true,sources:{'Open-Meteo':1,'MET Norway':0,'OpenWeather':2},timezone:'Europe/London'}};
let cfg={model:'gemini-3.5-flash-lite',prompt:'Промпт',switch_authorized:false,keys:[]};
w.fetch=async(url,o={})=>{requests.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'test'};
 else if(url==='/api/settings')data={units:'C'};
 else if(url==='/api/security-info')data={tunnel:true,trading_enabled:false};
 else if(url==='/api/trading/account-config')data={configured:false};
 else if(url==='/api/trading/monitor-interval')data={interval_seconds:30};
 else if(url==='/api/stations')data=stations;
 else if(/^\/api\/stations\/\d+\/weather\?view=1$/.test(url)){const id=Number(url.split('/')[3]);data={station:stations[id-1],metar:{temp:13},metar_history:[],metar_archive:{status:'ok',reports:[]},forecast_status:{reason:'no_coordinates'},forecast_ensemble:null,forecast:null,history:[]}}
 else if(url.endsWith('/bindings'))data=[];
 else if(url==='/api/openweather/config'){data={configured:o.method!=='DELETE'};}
 else if(url==='/api/ai/config'){
  if(o.method==='POST'){
   const body=JSON.parse(o.body);
   if(body.action==='save')cfg={...cfg,model:body.model,prompt:body.prompt,switch_authorized:body.switch_authorized};
   if(body.action==='add_key')cfg={...cfg,keys:[{id:'abc',project:body.project}]};
   if(body.action==='delete_key')cfg={...cfg,keys:[]};
  }data=cfg;
 }
 else if(url.endsWith('/ai-forecast')){
  if(o.method==='POST'){await new Promise(r=>{releaseGenerate=r});data={forecast}}
  else if(oldPending&&url.includes('/1/')){await new Promise(r=>{releaseOld=r});data={forecast:{...forecast,text:'старый'}}}
  else data={forecast:null};
 }
 else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
 await tick();await tick();
 // The overview follows the same order visually and in DOM/keyboard navigation.
 const section=w.document.querySelector('.station-section'),card=section.querySelector('.weather-card'),lower=section.querySelector('.weather-lower'),aiPanel=section.querySelector('.ai-forecast-panel');
 assert.deepEqual([...card.children].map(x=>x.className),['weather-main','sun-panel']);
 assert.equal(card.querySelector('#raw-metar').closest('.weather-main'),card.children[0]);
 assert.equal(card.querySelector('#sun-dot').closest('.sun-panel'),card.children[1]);
 assert(lower.querySelector('.chart-panel'));assert(lower.querySelector('.forecast-panel'));
 assert.equal(card.nextElementSibling,lower);assert.equal(lower.nextElementSibling,aiPanel);
 assert(!requests.some(([u,o])=>u.endsWith('/ai-forecast')&&o.method==='POST'),'No automatic Gemini requests');
 const button=w.document.querySelector('#ai-generate');assert(!button.disabled);
 // An old GET must not replace a newer generated result.
 oldPending=true;w.document.querySelector('.station-tab[data-id="1"]').click();await tick();assert(releaseOld);
 button.click();await tick();assert(button.disabled);button.click();assert.equal(requests.filter(([u,o])=>u.endsWith('/ai-forecast')&&o.method==='POST').length,1);
 releaseGenerate();await tick();releaseOld();await tick();
 assert.equal(w.document.querySelector('#ai-forecast-output').textContent,forecast.text);
 assert(!w.document.querySelector('#unsafe'),'AI output must be text');
 assert(w.document.querySelector('#ai-forecast-status').textContent.includes('неполные данные'));
 assert(w.document.querySelector('#ai-forecast-status').textContent.includes('OpenWeather: 2'));
 assert(!button.disabled);
 w.document.querySelector('.station-tab[data-id="2"]').click();await tick();assert(w.document.querySelector('#ai-forecast-output').textContent.includes('ещё не'));
 w.document.querySelector('[data-page="settings"]').click();await tick();await tick();
 const form=w.document.querySelector('#ai-settings-form');assert.equal(form.elements.model.value,cfg.model);
 form.elements.prompt.value='Новый прогноз';form.elements.switch_authorized.checked=true;form.elements.current_password.value='terminal-password';
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 const saved=requests.filter(([u,o])=>u==='/api/ai/config'&&o.method==='POST').map(x=>JSON.parse(x[1].body))[0];assert.equal(saved.prompt,'Новый прогноз');assert.equal(saved.switch_authorized,true);assert.equal(form.elements.current_password.value,'');
 const keyForm=w.document.querySelector('#ai-key-form');keyForm.elements.project.value='project-alpha';keyForm.elements.key.value='AIza'+'x'.repeat(30);keyForm.elements.current_password.value='terminal-password';keyForm.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 assert.equal(keyForm.elements.key.value,'');assert(w.document.querySelector('#ai-key-list').textContent.includes('project-alpha'));assert(!w.document.querySelector('#ai-key-list').textContent.includes('AIza'));
 w.document.querySelector('.ai-key-delete').click();await tick();assert(w.document.querySelector('#ai-key-list').textContent.includes('не добавлены'));
 const ow=w.document.querySelector('#openweather-form');assert(!ow.querySelector('button').disabled);
 ow.elements.key.value='TESTKEY';ow.elements.current_password.value='terminal-password';ow.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 assert.equal(ow.elements.key.value,'');assert.equal(ow.elements.current_password.value,'');assert(!w.document.querySelector('#openweather-config-status').textContent.includes('TESTKEY'));
 ow.elements.current_password.value='terminal-password';w.document.querySelector('#openweather-delete').click();await tick();assert(requests.some(([u,o])=>u==='/api/openweather/config'&&o.method==='DELETE'));assert.equal(ow.elements.current_password.value,'');
 w.renderOpenWeatherHourly({forecast_openweather:{points:[{time:Date.now()/1000+3600,temp:17}]}});assert(!w.document.querySelector('#openweather-hourly').hidden);assert(w.document.querySelector('#openweather-hourly-list').textContent.includes('17°C'));
 w.renderOpenWeatherHourly({});assert(w.document.querySelector('#openweather-hourly').hidden);
 w.setAiAccess(false);assert(ow.querySelector('button').disabled);assert(button.disabled);assert(w.document.querySelector('#ai-config-status').textContent.includes('SSH'));
 console.log('Gemini UI manual requests, race, secrets and escaping: OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
