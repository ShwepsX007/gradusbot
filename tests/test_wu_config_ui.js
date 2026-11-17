const assert=require('assert'),fs=require('fs'),{JSDOM,VirtualConsole}=require('jsdom');
const vc=new VirtualConsole();vc.on('jsdomError',e=>{throw e});
const dom=new JSDOM(fs.readFileSync('web/static/index.html','utf8'),{url:'http://127.0.0.1:8108',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
const w=dom.window;w.confirm=()=>true;w.prompt=()=> 'terminal-password';let requests=[],keys=[];
w.fetch=async(url,o={})=>{requests.push([url,o]);let data;
 if(url==='/api/session')data={csrf:'test'};else if(url==='/api/settings')data={units:'C'};else if(url==='/api/security-info')data={tunnel:true,trading_enabled:false};else if(url==='/api/trading/account-config')data={configured:false};else if(url==='/api/trading/monitor-interval')data={interval_seconds:30};else if(url==='/api/stations')data=[];else if(url==='/api/ai/config')data={model:'gemini-3.5-flash-lite',prompt:'test',keys:[]};
 else if(url==='/api/wu/config') {if(o.method==='POST'){const b=JSON.parse(o.body);if(b.action==='add_key')keys=[{id:'abc123'}];if(b.action==='delete_key')keys=[]}data={keys};}
 else throw Error('Unexpected '+url);
 return {status:200,ok:true,json:async()=>data};
};
w.eval(fs.readFileSync('web/static/weather.js','utf8'));w.eval(fs.readFileSync('web/static/app.js','utf8'));
const tick=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
 await tick();await tick();w.document.querySelector('[data-page="settings"]').click();await tick();await tick();
 const form=w.document.querySelector('#wu-key-form'),key='wu-test-secret-12345';
 assert(!form.querySelector('button').disabled);form.elements.key.value=key;form.elements.current_password.value='terminal-password';form.elements.authorized.checked=true;
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();
 assert.equal(form.elements.key.value,'');assert(!w.document.querySelector('#wu-key-list').textContent.includes(key));assert(w.document.querySelector('#wu-key-list').textContent.includes('Ключ 1'));
 const post=requests.find(([u,o])=>u==='/api/wu/config'&&o.method==='POST');assert.equal(JSON.parse(post[1].body).authorized,true);
 w.document.querySelector('.wu-key-delete').click();await tick();assert(w.document.querySelector('#wu-key-list').textContent.includes('не добавлены'));
 w.setWuAccess(false);assert(form.querySelector('button').disabled);assert(w.document.querySelector('#wu-config-status').textContent.includes('SSH'));
 console.log('WU settings UI: tunnel, authorization, no displayed secrets, add/delete OK');w.close();
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
