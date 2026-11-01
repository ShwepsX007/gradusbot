const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {stations:[], selected:null, bindings:[], market:null, weather:null, demo:false, units:'C', arcOffset:0, arcStation:null};
const demoStation = {id:0,name:'New York',station_code:'KLGA',station_type:'metar',enabled:1};
const demoBindings = [{id:0,market_slug:'demo',market_name:'New York · сегодня'}, {id:1,market_slug:'demo-tomorrow',market_name:'New York · завтра'}];
function shiftDate(date,days){let d=new Date(date+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10)}
const demoToday=localDate('America/New_York');
const demoWeather = {station:demoStation,metar:{temp:23,humidity:64,wind_speed:8,wind_ms:4.1,wind_dir:210,wx_phrase:'SCT',cloud:{description:'Переменная облачность',code:'SCT'},precipitation:{description:'Осадки не указаны'},raw_metar:'KLGA 151651Z 21008KT 10SM SCT045 23/16 A3012'},info:{name:'LaGuardia Airport'},forecast:{timezone:'America/New_York',daily:{sunrise:[-1,0,1].map(i=>shiftDate(demoToday,i)+'T05:24'),sunset:[-1,0,1].map(i=>shiftDate(demoToday,i)+'T20:30'),temperature_2m_max:[27,27,27],temperature_2m_min:[18,18,18]},hourly:{time:Array.from({length:48},(_,i)=>shiftDate(demoToday,Math.floor(i/24))+'T'+String(i%24).padStart(2,'0')+':00'),temperature_2m:Array.from({length:48},(_,i)=>Math.round(22+5*Math.sin(((i%24)-9)*Math.PI/12))),precipitation_probability:Array.from({length:48},(_,i)=>i%24>=15?65:20)}},history:[20,19,18,18,19,20,22,23,25,26,27,26,25,24,23].map((v,i)=>[Date.now()/1000-(14-i)*3600,v])};
const demoOptions = [{label:'20°C или ниже',prob:4},{label:'21–22°C',prob:12},{label:'23–24°C',prob:28},{label:'25–26°C',prob:36},{label:'27°C или выше',prob:20}];
let csrfToken = '';
let toastTimer;
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3500)}
async function api(url, options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken,...(options.headers||{})}});if(r.status===401){location.assign('/login');throw Error('Сессия завершена')}let data;try{data=await r.json()}catch{throw Error('Ошибка ответа сервера')}if(!r.ok)throw Error(data.error||'Ошибка запроса');return data}
function page(name){$$('.page').forEach(x=>x.classList.add('hidden'));$(`#${name}-page`).classList.remove('hidden');$$('.nav').forEach(x=>x.classList.toggle('active',x.dataset.page===name));$('#crumb').textContent=({weather:'Обзор',stations:'Станции',markets:'Рынки',settings:'Настройки'})[name];if(name==='stations')renderStations();if(name==='markets')renderAllMarkets();if(name==='settings')loadSettings()}
$$('.nav').forEach(x=>x.onclick=()=>page(x.dataset.page));
function showDialog(id){$(`#${id}`).showModal()}
$$('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#add-station-top').onclick=$('#demo-add').onclick=$('.add-station').onclick=()=>showDialog('station-dialog');
$('#add-market').onclick=$('#markets-find').onclick=()=>{if(state.demo||!state.selected){toast('Сначала добавьте реальную станцию');showDialog('station-dialog');return}$('#search-results').innerHTML='';$('#market-dialog .dialog-error').textContent='';showDialog('market-dialog');searchStationMarkets()};
$('#station-form').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{const s=await api('/api/stations',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(f)))});f.reset();f.closest('dialog').close();await loadStations(s.id);page('weather');toast('Станция добавлена. Включите её для получения данных.')}catch(err){f.querySelector('.dialog-error').textContent=err.message}};
$('#station-toggle').onclick=async()=>{if(state.demo){toast('В демонстрации станция не изменяется');return}let s=state.stations.find(x=>x.id===state.selected);if(!s)return;try{await api(`/api/stations/${s.id}`,{method:'PATCH',body:JSON.stringify({enabled:!s.enabled})});await loadStations(s.id)}catch(e){toast(e.message)}};
async function loadStations(select){try{state.stations=await api('/api/stations');state.demo=state.stations.length===0;$('#demo-banner').classList.toggle('hidden',!state.demo);if(state.demo){state.selected=0;state.weather=demoWeather;state.bindings=demoBindings;state.market='demo';renderAll();return}state.selected=state.stations.some(x=>x.id===(select??state.selected))?(select??state.selected):state.stations[0].id;renderStationTabs();await loadStation()}catch(e){toast(e.message)}}
function renderStationTabs(){const list=state.demo?[demoStation]:state.stations;$('#station-count').textContent=state.demo?'01':String(list.length).padStart(2,'0');$('#station-tabs').innerHTML=list.map(s=>`<button class="station-tab ${s.id===state.selected?'active':''}" data-id="${s.id}"><i class="tab-dot"></i>${esc(s.name)} <span style="opacity:.5;margin-left:7px">${esc(s.station_code)}</span></button>`).join('');$$('.station-tab').forEach(b=>b.onclick=async()=>{state.selected=Number(b.dataset.id);renderStationTabs();await loadStation()})}
async function loadStation(background=false){if(state.arcStation!==state.selected){state.arcStation=state.selected;state.arcOffset=0}const previousMarket=state.market;if(!background){state.market=null;state.weather=null;state.bindings=[];renderWeather();renderMarketTabs();renderMarket(null)}try{const [w,b]=await Promise.all([api(`/api/stations/${state.selected}/weather`),api(`/api/stations/${state.selected}/bindings`)]);if(w.station.id!==state.selected)return;state.weather=w;state.bindings=b;state.market=b.some(x=>x.market_slug===previousMarket)?previousMarket:(b[0]?.market_slug||null);renderWeather();renderMarketTabs();await loadMarket()}catch(e){toast(e.message)}}
function fmt(v,unit=''){return v===null||v===undefined||v===''?'—':`${esc(v)}${unit}`}
function tempNumber(v){return Number.isFinite(Number(v))&&v!==null&&v!==undefined&&v!==''?Number(v):null}
function prettyNumber(n){return String(Math.round((n+Number.EPSILON)*10)/10)}
function tempText(v){let c=tempNumber(v);if(c===null)return '—';let f=prettyNumber(c*9/5+32),cc=prettyNumber(c);return state.units==='F'?`${f}°F`:state.units==='B'?`${cc}°C / ${f}°F`:`${cc}°C`}
function tempMarker(v,x){let c=tempNumber(v);if(c===null)return '—';if(state.units==='B')return `<tspan x="${x}" dy="-3">${prettyNumber(c)}°C</tspan><tspan x="${x}" dy="10">${prettyNumber(c*9/5+32)}°F</tspan>`;return tempText(c)}
function timePart(v){return v?String(v).slice(11,16):'—'}
function renderWeather(){let w=state.weather||{},s=w.station||(state.demo?demoStation:state.stations.find(x=>x.id===state.selected))||{},m=w.metar||{},f=w.forecast||{},d=f.daily||{},h=f.hourly||{};$('#station-code').textContent=s.station_code||'—';$('#station-status').textContent=state.demo?'ДЕМО':s.enabled?'СТАНЦИЯ ВКЛЮЧЕНА':'СТАНЦИЯ ВЫКЛЮЧЕНА';$('#station-name').textContent=s.name||'Выберите станцию';$('#station-location').textContent=(w.info?.name||'Метеостанция')+' · '+(f.timezone||'часовой пояс недоступен');$('#station-toggle').classList.toggle('on',!!s.enabled);$('#weather-pill').innerHTML=state.demo?'ПРИМЕР ДАННЫХ':s.enabled?'<i></i> НАБЛЮДЕНИЕ':'ОПРОС ВЫКЛЮЧЕН';$('#temperature').textContent=tempText(m.temp);$('#temperature').classList.toggle('temperature-dual',state.units==='B');$('#condition').textContent=m.cloud?.description||w.message||'Нет данных';$('#humidity').textContent=fmt(m.humidity,'%');$('#wind').textContent=m.wind_speed!=null?`${m.wind_speed} узл. / ${m.wind_ms!=null?prettyNumber(m.wind_ms)+' м/с':'— м/с'}`:'—';$('#wind-direction').textContent=m.wind_dir==null?'Направление —':String(m.wind_dir).toUpperCase()==='VRB'?'Направление переменное':`Направление ${m.wind_dir}°`;$('#cloud').textContent=m.cloud?.description||'—';$('#cloud-code').textContent=m.cloud?.code?'METAR · '+m.cloud.code:'METAR';renderPrecipitation(m,f);$('#raw-metar').textContent=m.raw_metar||w.message||'Нет данных';$('#sunrise').textContent=timePart(d.sunrise?.[0]);$('#sunset').textContent=timePart(d.sunset?.[0]);let today=localDate(f.timezone), dates=d.time||d.sunrise?.map(x=>x.slice(0,10))||[], idx=dates.indexOf(today);if(!today)idx=0;state.dayIndex=idx;today=idx>=0?dates[idx]:today;$('#sunrise').textContent=timePart(d.sunrise?.[idx]);$('#sunset').textContent=timePart(d.sunset?.[idx]);let points=(h.time||[]).map((t,i)=>({t,v:h.temperature_2m?.[i]})).filter(x=>x.t.startsWith(today||'NO_DATE')&&typeof x.v==='number'&&Number.isFinite(x.v));let max=points.length?points.reduce((a,b)=>a.v>=b.v?a:b):null,min=points.length?points.reduce((a,b)=>a.v<=b.v?a:b):null;$('#forecast-max').textContent=tempText(max?.v??d.temperature_2m_max?.[idx]);$('#forecast-min').textContent=tempText(min?.v??d.temperature_2m_min?.[idx]);$('#forecast-max-time').textContent=max?'около '+timePart(max.t):'—';$('#forecast-min-time').textContent=min?'около '+timePart(min.t):'—';drawChart(w.history||[]);updateClock()}
function renderPrecipitation(m,f){
  $('#precip-observed').textContent=m.precipitation?.description||'Нет данных';
  let stamp=Number(m.obs_time),obsTime='Последняя сводка станции';
  if(m.obs_time!=null&&Number.isFinite(stamp)&&stamp>0){try{obsTime='METAR в '+new Intl.DateTimeFormat('ru-RU',{timeZone:f.timezone||'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(stamp*1000))+(f.timezone?' (местное время)':' UTC')}catch{obsTime='Время сводки недоступно'}}
  $('#precip-observed-time').textContent=obsTime;
  let p=precipitationForecast(f),el=$('#precip-probability');
  el.textContent=p?`${p.probability}%${p.probability>=60?' · высокая':''}`:'Нет данных';el.classList.toggle('risk-high',!!p&&p.probability>=60);
  $('#precip-period').textContent=p?`Интервал ${p.start}–${p.end} · местное время`:'Почасовой прогноз недоступен';
}
function drawChart(history){let now=Date.now()/1000,pts=history.filter(x=>x[1]!=null&&Number.isFinite(Number(x[1]))&&Number(x[0])>=now-24*3600&&Number(x[0])<=now);$('#chart-empty').classList.toggle('hidden',pts.length>=2);const svg=$('#history-chart');if(pts.length<2){svg.innerHTML='';$$('.chart-grid span').forEach(el=>el.textContent='—');return}let vals=pts.map(p=>+p[1]), min=Math.min(...vals)-2,max=Math.max(...vals)+2, end=Date.now()/1000, start=end-24*3600, span=end-start;let coords=pts.map(p=>`${((p[0]-start)/span*790+5).toFixed(1)},${(145-(p[1]-min)/(max-min)*130).toFixed(1)}`).join(' ');svg.innerHTML=`<defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#a6dfaa" stop-opacity=".18"/><stop offset="1" stop-color="#a6dfaa" stop-opacity="0"/></linearGradient></defs><polygon points="5,160 ${coords} 795,160" fill="url(#fill)"/><polyline points="${coords}" fill="none" stroke="#b6e6b7" stroke-width="2" vector-effect="non-scaling-stroke"/>`;let grid=$$('.chart-grid span');[max,max-(max-min)/3,max-2*(max-min)/3,min].forEach((v,i)=>grid[i].textContent=tempText(v))}
function localDate(tz){if(!tz)return null;try{let parts=new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());let get=k=>parts.find(p=>p.type===k)?.value;return `${get('year')}-${get('month')}-${get('day')}`}catch{return null}}
const {precipitationForecast, localClock, shiftDay, solarSeconds, curvePoint, dailyPath, observationsForDay, DAY_START_X, DAY_WIDTH} = WeatherView;
function displayedSolarDay(w,day){
  let saved=(w.solar_days||[]).find(item=>item.day===day);if(saved)return saved;
  let f=w.forecast||{},d=f.daily||{},dates=d.time||d.sunrise?.map(x=>x?.slice(0,10))||[],idx=dates.indexOf(day);
  return idx<0?null:{day,sunrise:d.sunrise?.[idx],sunset:d.sunset?.[idx],timezone:f.timezone};
}
function dateLabel(day){try{return new Intl.DateTimeFormat('ru-RU',{timeZone:'UTC',day:'numeric',month:'long',year:'numeric'}).format(new Date(day+'T12:00:00Z'))}catch{return day||'—'}}
function updateClock(){
  const w=state.weather||{},f=w.forecast||{},now=Date.now(),tz=f.timezone||(w.solar_days||[]).find(x=>x.timezone)?.timezone;
  const local=localClock(now,tz), day=local?shiftDay(local.day,state.arcOffset):null, solar=day?displayedSolarDay(w,day):null;
  $('#arc-prev').disabled=!local||state.arcOffset<=-6;$('#arc-next').disabled=!local||state.arcOffset>=0;
  $('#arc-day-counter').textContent=`${1-state.arcOffset} / 7`;
  $('#arc-date').textContent=day?(state.arcOffset===0?'Сегодня · ':'')+dateLabel(day):'Дата недоступна';
  $('#sunrise').textContent=timePart(solar?.sunrise);$('#sunset').textContent=timePart(solar?.sunset);
  $('#local-time-label').textContent=state.arcOffset===0?'МЕСТНОЕ ВРЕМЯ':'ВЫБРАННАЯ ДАТА';
  $('#local-time').textContent=state.arcOffset===0?(local?.time||'—'):(day?day.split('-').reverse().join('.'):'—');
  const sunrise=solarSeconds(solar?.sunrise), sunset=solarSeconds(solar?.sunset), path=dailyPath(sunrise,sunset);
  const marker=$('#sun-marker');$('#arc-observations').innerHTML='';
  $('#arc-full').setAttribute('d',path||'');$('#arc-progress').setAttribute('d',path||'');
  if(!path||!local){
    marker.classList.add('hidden');$('#arc-passed-rect').setAttribute('width',0);
    $('.sun-heading h4').textContent='Нет данных о восходе и закате';$('.sun-icon').textContent='◷';
    $('#arc-note').textContent='Для выбранной даты нет данных о восходе и закате. История может быть неполной.';return;
  }
  const current=state.arcOffset===0,seconds=current?local.seconds:86400;
  $('#arc-passed-rect').setAttribute('width',DAY_WIDTH*seconds/86400);
  const pt=current?curvePoint(seconds,sunrise,sunset):null;
  marker.classList.toggle('hidden',!pt);
  if(pt){
    for(const id of ['sun-dot','sun-glow']){$('#'+id).setAttribute('cx',pt.x);$('#'+id).setAttribute('cy',pt.y)}
    const text=$('#sun-temperature');text.setAttribute('x',pt.x);text.setAttribute('y',pt.y);text.innerHTML=tempMarker(w.metar?.temp,pt.x);
    const dayPhase=seconds>=sunrise&&seconds<sunset;$('#sun-dot').style.fill=dayPhase?'#e8d79c':'#c1d4e6';$('.sun-icon').textContent=dayPhase?'☼':'☾';
  }else $('.sun-icon').textContent='◷';
  $('.sun-heading h4').textContent=current?'Ночь · день · ночь':'История станции';
  // Points are recorded observations, placed by local wall-clock time, not predictions.
  const observations=observationsForDay(w.history||[],tz,day,now);let lastLabelX=-Infinity;
  $('#arc-observations').innerHTML=observations.map(item=>{
    const p=curvePoint(item.seconds,sunrise,sunset);if(!p)return '';
    let label='';if(p.x-lastLabelX>=80&&(!pt||Math.abs(p.x-pt.x)>30)){lastLabelX=p.x;label=`<text x="${p.x}" y="${p.y-11}" text-anchor="middle">${esc(tempText(item.temp))}</text>`}
    return `<g class="arc-observation"><title>${esc(item.time)} · ${esc(tempText(item.temp))}</title><circle cx="${p.x}" cy="${p.y}" r="2.4"/>${label}</g>`;
  }).join('');
  $('#arc-note').textContent=observations.length?`Сохранено точек: ${observations.length}. Температура и время — при наведении${current?'; на светиле — последний METAR':''}.`:'Нет сохранённых наблюдений за выбранный день.';
}
$('#arc-prev').onclick=()=>{if(state.arcOffset>-6){state.arcOffset--;updateClock()}};
$('#arc-next').onclick=()=>{if(state.arcOffset<0){state.arcOffset++;updateClock()}};
function renderMarketTabs(){let b=state.bindings;$('#market-count').textContent=String(b.length).padStart(2,'0');$('#market-tabs').innerHTML=b.map(x=>`<button class="market-tab ${x.market_slug===state.market?'active':''}" data-slug="${esc(x.market_slug)}">${esc(x.market_name)}</button>`).join('');$$('.market-tab').forEach(btn=>btn.onclick=()=>{state.market=btn.dataset.slug;renderMarketTabs();loadMarket()})}
async function loadMarket(){if(!state.market){renderMarket(null);return}if(state.demo){renderMarket({market:{title:state.market==='demo'?'Максимальная температура в Нью-Йорке':'Максимальная температура в Нью-Йорке · завтра',options:demoOptions,closed:false},positions:[]});return}const slug=state.market;try{let m=await api('/api/market/'+encodeURIComponent(slug));if(slug===state.market)renderMarket(m)}catch(e){renderMarket(null,e.message)}}
function renderMarket(data,error){let m=data?.market,binding=state.bindings.find(x=>x.market_slug===state.market);$('#market-title').textContent=binding?.market_name||m?.title||'Нет данных рынка';$('#market-description').textContent=error||(!m?'Найдите рынок или добавьте ссылку на событие.':state.demo?'Демонстрационные значения · не реальные котировки':(binding?.market_name!==m.title?'Оригинал: '+m.title:'Вероятности исходов по данным события Polymarket'));$('#market-state').textContent=state.demo?'ДЕМО':m?.closed?'ЗАКРЫТ':'СОБЫТИЕ';$('#market-link').href=state.demo||!state.market?'https://polymarket.com':'https://polymarket.com/event/'+encodeURIComponent(state.market);$('#market-options').innerHTML=m?.options?.length?m.options.map(o=>{let n=typeof o.prob==='number'?Math.max(0,Math.min(100,o.prob)):null;return `<div class="option-row"><span class="option-name">${esc(o.label)}</span><span class="option-prob"><span class="bar"><i style="width:${n??0}%"></i></span> ${n==null?'—':n+'%'}</span><span class="option-price">${n==null?'—':n+'¢'}</span><span class="trade-disabled" title="Торговля через веб-панель пока недоступна">Только просмотр</span></div>`}).join(''):'<div class="empty-state">Выберите или добавьте рынок</div>';let pos=data?.positions||[];$('#positions-count').textContent=`Позиций бота: ${pos.length}`;for(const id of ['rename-market','unlink-market','delete-market'])$('#'+id).classList.toggle('hidden',state.demo||!state.market);$('#positions').innerHTML=pos.length?'<b>ПОЗИЦИИ ИЗ БАЗЫ БОТА</b>'+pos.map(p=>`<div class="position-row">${esc(p.is_demo?'ДЕМО · ':'')}${esc(p.question||p.outcome||'Позиция')} · ${esc(p.side)} · ${esc(p.size)} shares · вход ${esc(p.entry_price)} · SL ${esc(p.sl??'—')} / TP ${esc(p.tp??'—')}</div>`).join(''):''}
$('#rename-market').onclick=async()=>{let binding=state.bindings.find(x=>x.market_slug===state.market);if(!binding)return;let name=prompt('Новое название рынка (изменится во всех станциях и в базе бота):',binding.market_name);if(name===null)return;try{await api('/api/markets/'+encodeURIComponent(binding.market_slug),{method:'PATCH',body:JSON.stringify({name})});await loadStation(true);toast('Рынок переименован')}catch(e){toast(e.message)}};
$('#unlink-market').onclick=async()=>{let binding=state.bindings.find(x=>x.market_slug===state.market);if(!binding||!confirm('Отвязать рынок от станции? Настройки стратегии этой связки будут удалены. Ордера на бирже не отменяются.'))return;try{await api('/api/bindings/'+binding.id,{method:'DELETE',body:'{}'});await loadStation();toast('Рынок отвязан')}catch(e){toast(e.message)}};
$('#delete-market').onclick=async()=>{if(!state.market||!confirm('Удалить рынок из общей базы бота? Будут удалены его история, привязки ко ВСЕМ станциям и настройки стратегий этих связок. Ордера на бирже не отменяются. Продолжить?'))return;try{await api('/api/markets/'+encodeURIComponent(state.market),{method:'DELETE',body:'{}'});await loadStation();toast('Рынок удалён')}catch(e){toast(e.message)}};
function renderAll(){if(state.arcStation!==state.selected){state.arcStation=state.selected;state.arcOffset=0}renderStationTabs();renderWeather();renderMarketTabs();loadMarket()}
function renderStations(){
  $('#station-list').innerHTML=state.stations.length?state.stations.map(s=>`<div class="manage-row"><div><b>${esc(s.name)}</b><small>${esc(s.station_code)} · ${esc(s.station_type)} · ${s.enabled?'включена':'выключена'}</small></div><div class="manage-actions"><button class="button secondary station-select" data-id="${s.id}">Открыть ↗</button><button class="button secondary station-rename" data-id="${s.id}">Переименовать</button><button class="button danger station-delete" data-id="${s.id}">Удалить</button></div></div>`).join(''):'<div class="empty-state">Станций пока нет. Добавьте первую станцию.</div>';
  $$('.station-select').forEach(b=>b.onclick=async()=>{state.selected=Number(b.dataset.id);page('weather');renderStationTabs();await loadStation()});
  $$('.station-rename').forEach(b=>b.onclick=async()=>{let s=state.stations.find(x=>x.id===Number(b.dataset.id));if(!s)return;let name=prompt('Новое название станции:',s.name);if(name===null)return;try{await api(`/api/stations/${s.id}`,{method:'PATCH',body:JSON.stringify({name})});await loadStations(s.id);renderStations();toast('Станция переименована')}catch(e){toast(e.message)}});
  $$('.station-delete').forEach(b=>b.onclick=async()=>{let s=state.stations.find(x=>x.id===Number(b.dataset.id));if(!s||!confirm(`Удалить станцию ${s.name} (${s.station_code})? Будут удалены история и все привязки станции с их настройками стратегий. Рынки останутся в общей базе. Ордера на бирже не отменяются. Продолжить?`))return;try{await api(`/api/stations/${s.id}`,{method:'DELETE',body:'{}'});await loadStations();renderStations();toast('Станция удалена')}catch(e){toast(e.message)}});
}
async function renderAllMarkets(){
  try{
    let [markets,all]=await Promise.all([api('/api/markets'),Promise.all(state.stations.map(async s=>({s,b:await api(`/api/stations/${s.id}/bindings`)})))]);
    if(!markets.length){$('#all-markets').innerHTML='<div class="empty-state">Рынков пока нет. Найдите и привяжите первый рынок.</div>';return}
    $('#all-markets').innerHTML=markets.map(m=>{let linked=all.filter(x=>x.b.some(b=>b.market_slug===m.slug));return `<div class="manage-row"><div><b>${esc(m.name)}</b><small>${linked.length?linked.map(x=>esc(x.s.name)).join(' · '):'Не привязан к станции'} · ${esc(m.slug)}</small></div><div class="manage-actions">${linked.length?`<button class="button secondary open-managed-market" data-id="${linked[0].s.id}" data-slug="${esc(m.slug)}">Открыть ↗</button>`:''}<button class="button secondary resolve-managed-market" data-slug="${esc(m.slug)}">Найти станцию</button><button class="button secondary rename-managed-market" data-slug="${esc(m.slug)}">Переименовать</button><button class="button danger delete-managed-market" data-slug="${esc(m.slug)}">Удалить</button></div></div>`}).join('');
    $$('.open-managed-market').forEach(b=>b.onclick=async()=>{state.selected=Number(b.dataset.id);page('weather');renderStationTabs();await loadStation();state.market=b.dataset.slug;renderMarketTabs();await loadMarket()});
    $$('.resolve-managed-market').forEach(b=>b.onclick=()=>openMarketFirst(b.dataset.slug, true));
    $$('.rename-managed-market').forEach(b=>b.onclick=async()=>{let m=markets.find(x=>x.slug===b.dataset.slug);if(!m)return;let name=prompt('Новое название рынка (изменится во всех станциях и в базе бота):',m.name);if(name===null)return;try{await api('/api/markets/'+encodeURIComponent(m.slug),{method:'PATCH',body:JSON.stringify({name})});if(state.selected)await loadStation(true);await renderAllMarkets();toast('Рынок переименован')}catch(e){toast(e.message)}});
    $$('.delete-managed-market').forEach(b=>b.onclick=async()=>{let slug=b.dataset.slug;if(!confirm('Удалить рынок из общей базы бота? Будут удалены его история, привязки ко ВСЕМ станциям и настройки стратегий этих связок. Ордера на бирже не отменяются. Продолжить?'))return;try{await api('/api/markets/'+encodeURIComponent(slug),{method:'DELETE',body:'{}'});if(!state.demo)await loadStation();await renderAllMarkets();toast('Рынок удалён')}catch(e){toast(e.message)}});
  }catch(e){toast(e.message)}
}
let searchSequence=0;
function showSearchResults(results, empty){let target=$('#search-results');target.innerHTML=results.length?results.map(r=>`<div class="search-result"><span>${esc(r.title)}</span><button type="button" data-slug="${esc(r.slug)}">Выбрать</button></div>`).join(''):`<div class="search-message">${esc(empty)}</div>`;target.querySelectorAll('button').forEach(b=>b.onclick=()=>{$('#bind-form input').value=b.dataset.slug})}
async function searchStationMarkets(){let selected=state.selected, seq=++searchSequence, target=$('#search-results');target.innerHTML='<div class="search-message">Автоматический поиск по станции…</div>';let st=state.stations.find(x=>x.id===selected);$('#market-search-hint').textContent=`Станция ${st?.name||''} (${st?.station_code||''}). Проверьте дату, правила и источник разрешения рынка.`;try{let data=await api(`/api/stations/${selected}/market-search`);if(seq!==searchSequence||selected!==state.selected)return;showSearchResults(data.events,data.message||'Подходящие активные рынки не найдены. Попробуйте ручной поиск или ссылку.')}catch(e){if(seq===searchSequence)target.innerHTML=`<div class="search-message">${esc(e.message)}. Можно использовать ручной поиск или ссылку.</div>`}}
$('#search-form').onsubmit=async e=>{e.preventDefault();let seq=++searchSequence, target=$('#search-results');target.innerHTML='<div class="search-message">Поиск…</div>';try{let q=new FormData(e.currentTarget).get('q');let results=await api('/api/search?q='+encodeURIComponent(q));if(seq!==searchSequence)return;showSearchResults(results,'Ничего не найдено. Попробуйте другое название.')}catch(err){if(seq===searchSequence)target.innerHTML=`<div class="search-message">${esc(err.message)}</div>`}};
$('#bind-form').onsubmit=async e=>{
  e.preventDefault();let f=e.currentTarget,slug=new FormData(f).get('slug'),sid=state.selected,station=state.stations.find(x=>x.id===sid);
  if(!station)return;
  f.querySelector('.dialog-error').textContent='Проверяем источник разрешения…';
  try{
    let resolution;
    try{resolution=await api('/api/market-station/resolve',{method:'POST',body:JSON.stringify({slug})})}
    catch(err){if(!confirm(`Не удалось проверить источник (${err.message}). Продолжить ручную привязку к ${station.station_code}?`)){f.querySelector('.dialog-error').textContent='';return}}
    if(sid!==state.selected){f.querySelector('.dialog-error').textContent='Станция изменилась. Повторите привязку.';return}
    if(resolution?.status==='found'){
      const code=resolution.candidate.code;
      if(code!==String(station.station_code).toUpperCase()){
        f.querySelector('.dialog-error').textContent='';
        if(confirm(`В правилах найден код ${code}, а выбрана станция ${station.station_code}. Открыть подбор найденной станции?`)){
          f.closest('dialog').close();openMarketFirst(slug, false);showResolution(resolution, slug);
        }
        return;
      }
      if(!confirm(`В правилах указан код ${code} (${resolution.candidate.kind}). Привязать к выбранной станции? Проверьте правила события.`)){f.querySelector('.dialog-error').textContent='';return}
    } else if(resolution && !confirm('В правилах нет однозначного кода станции. Привязать рынок к выбранной станции вручную? Обязательно проверьте источник разрешения.')){f.querySelector('.dialog-error').textContent='';return}
    let result=await api(`/api/stations/${sid}/bindings`,{method:'POST',body:JSON.stringify({slug})});f.reset();f.closest('dialog').close();await loadStation();state.market=result.market_slug;renderMarketTabs();await loadMarket();page('weather');toast('Рынок привязан');
  }catch(err){f.querySelector('.dialog-error').textContent=err.message}
};
let marketResolution=null, resolutionSequence=0;
function openMarketFirst(slug='', auto=false){
  if($('#market-dialog').open)$('#market-dialog').close();
  let dialog=$('#market-first-dialog');$('#market-resolve-form input').value=slug;$('#market-first-results').innerHTML='';$('#market-resolution').innerHTML='';marketResolution=null;++resolutionSequence;
  if(!dialog.open)dialog.showModal();
  if(auto&&slug)resolveMarketFirst();
}
$('#market-first-overview').onclick=$('#market-first-page').onclick=()=>openMarketFirst();
$('#market-first-from-bind').onclick=()=>openMarketFirst($('#bind-form input').value,!!$('#bind-form input').value);
$('#market-resolve-form input').addEventListener('input',()=>{++resolutionSequence;marketResolution=null;$('#market-resolution').innerHTML=''});
function evidenceHtml(c){let source=c.url?`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.url)} ↗</a>`:esc(c.kind);return `<div class="resolution-evidence">${esc(c.kind)} · ${esc(c.field)}<br>${source}</div>`}
function showResolution(result,input){
  marketResolution={...result,input};let box=$('#market-resolution');
  let content=`<div class="resolution-card"><h4>${esc(result.title)}</h4>`;
  if(result.status==='found'){
    let c=result.candidate, existing=result.existing_stations||[];
    content+=`<div class="resolution-code">${esc(c.code)}</div>${evidenceHtml(c)}<p>${existing.length?`Найдена существующая METAR-станция: <b>${esc(existing[0].name)}</b>.`:'Будет создана новая METAR-станция в выключенном состоянии.'}</p><p class="resolution-warning">Проверьте правила и источник рынка. METAR используется для наблюдения и может отличаться от итоговых данных разрешения.</p><button type="button" class="button primary full" id="confirm-market-station">Подтвердить и привязать</button>`;
  }else{
    let details=(result.candidates||[]).map(c=>`<li><b>${esc(c.code)}</b> ${evidenceHtml(c)}</li>`).join('');
    content+=`<p class="resolution-warning">${result.status==='conflict'?'В правилах найдены разные коды станции. Автоматическая привязка остановлена.':'Однозначный код станции в источнике разрешения не найден.'}</p>${details?`<ul>${details}</ul>`:''}<p>Проверьте правила на Polymarket. Можно сохранить рынок без привязки и добавить станцию вручную.</p>`;
  }
  content+=`<button type="button" class="button secondary full" id="save-market-only">Сохранить рынок без станции</button></div>`;
  box.innerHTML=content;
  let add=$('#confirm-market-station');if(add)add.onclick=async()=>{
    if(!marketResolution||$('#market-resolve-form input').value!==marketResolution.input)return;
    add.disabled=true;try{let response=await api('/api/market-station/add',{method:'POST',body:JSON.stringify({slug:result.slug,code:result.candidate.code,confirm:true})});$('#market-first-dialog').close();await loadStations(response.station.id);state.market=response.slug;renderMarketTabs();await loadMarket();page('weather');toast(response.already_bound?'Рынок уже привязан':response.created_station?'Станция создана выключенной; рынок привязан':'Рынок привязан к существующей станции')}catch(e){toast(e.message)}finally{add.disabled=false}
  };
  $('#save-market-only').onclick=async e=>{let button=e.currentTarget;if(!marketResolution||$('#market-resolve-form input').value!==marketResolution.input)return;if(!confirm('Сохранить рынок без привязки к станции?'))return;button.disabled=true;try{await api('/api/markets',{method:'POST',body:JSON.stringify({slug:result.slug})});$('#market-first-dialog').close();page('markets');toast('Рынок сохранён без станции')}catch(err){toast(err.message)}finally{button.disabled=false}};
}
async function resolveMarketFirst(){let input=$('#market-resolve-form input').value.trim();if(!input)return;$('#market-resolve-form input').value=input;let seq=++resolutionSequence;marketResolution=null;$('#market-resolution').innerHTML='<p class="search-message">Изучаем правила события…</p>';try{let data=await api('/api/market-station/resolve',{method:'POST',body:JSON.stringify({slug:input})});if(seq===resolutionSequence)showResolution(data,input)}catch(err){if(seq===resolutionSequence)$('#market-resolution').innerHTML=`<div class="market-resolution-error">${esc(err.message)}</div>`}}
$('#market-resolve-form').onsubmit=e=>{e.preventDefault();resolveMarketFirst()};
$('#market-first-search').onsubmit=async e=>{e.preventDefault();let target=$('#market-first-results');target.innerHTML='<div class="search-message">Поиск…</div>';try{let results=await api('/api/search?q='+encodeURIComponent(new FormData(e.currentTarget).get('q')));target.innerHTML=results.length?results.map(r=>`<div class="search-result"><span>${esc(r.title)}</span><button type="button" data-slug="${esc(r.slug)}">Выбрать</button></div>`).join(''):'<div class="search-message">Событий не найдено. Можно вставить ссылку ниже.</div>';target.querySelectorAll('button').forEach(b=>b.onclick=()=>{$('#market-resolve-form input').value=b.dataset.slug;resolveMarketFirst()})}catch(err){target.innerHTML=`<div class="search-message">${esc(err.message)}</div>`}};
async function loadSettings(){try{let s=await api('/api/settings');Object.entries(s).forEach(([k,v])=>{$(`#settings-form [name="${k}"]`).value=v});state.units=['C','F','B'].includes(s.units)?s.units:'C'}catch(e){toast(e.message)}}
$('#settings-form').onsubmit=async e=>{e.preventDefault();try{let result=await api('/api/settings',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))});state.units=result.units;renderWeather();toast('Настройки сохранены')}catch(err){toast(err.message)}};
$('#refresh').onclick=()=>loadStations();$('#today-label').textContent=new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric'}).format(new Date()).toUpperCase();setInterval(updateClock,30000);setInterval(()=>{if(!document.hidden&&!state.demo)loadStation(true)},60000);$('#logout').onclick=async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});location.assign('/login')}catch(e){toast(e.message)}};api('/api/session').then(data=>{csrfToken=data.csrf;return loadSettings()}).then(()=>loadStations()).catch(e=>toast(e.message));
