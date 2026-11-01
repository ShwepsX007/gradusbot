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
function page(name){$$('.page').forEach(x=>x.classList.add('hidden'));$(`#${name}-page`).classList.remove('hidden');$$('.nav').forEach(x=>x.classList.toggle('active',x.dataset.page===name));$('#crumb').textContent=({weather:'Обзор',stations:'Станции',markets:'Рынки',copy:'Копирование',settings:'Настройки'})[name];if(name==='stations')renderStations();if(name==='markets')renderAllMarkets();if(name==='copy')loadCopyObserver();if(name==='settings'){loadSettings();loadSecurityInfo()}}
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
function tempMarker(v,x){let c=tempNumber(v);if(c===null)return '—';if(state.units==='B')return `<tspan x="${x}" dy="-2">${prettyNumber(c)}°C</tspan><tspan x="${x}" dy="6">${prettyNumber(c*9/5+32)}°F</tspan>`;return tempText(c)}
function timePart(v){return v?String(v).slice(11,16):'—'}
function renderWeather(){let w=state.weather||{},s=w.station||(state.demo?demoStation:state.stations.find(x=>x.id===state.selected))||{},m=w.metar||{},f=w.forecast||{},d=f.daily||{},h=f.hourly||{};$('#station-code').textContent=s.station_code||'—';$('#station-status').textContent=state.demo?'ДЕМО':s.enabled?'СТАНЦИЯ ВКЛЮЧЕНА':'СТАНЦИЯ ВЫКЛЮЧЕНА';$('#station-name').textContent=s.name||'Выберите станцию';$('#station-location').textContent=(w.info?.name||'Метеостанция')+' · '+(f.timezone||'часовой пояс недоступен');$('#station-toggle').classList.toggle('on',!!s.enabled);$('#weather-pill').innerHTML=state.demo?'ПРИМЕР ДАННЫХ':s.enabled?'<i></i> НАБЛЮДЕНИЕ':'ОПРОС ВЫКЛЮЧЕН';$('#temperature').textContent=tempText(m.temp);$('#temperature').classList.toggle('temperature-dual',state.units==='B');$('#condition').textContent=m.cloud?.description||w.message||'Нет данных';$('#humidity').textContent=fmt(m.humidity,'%');$('#wind').textContent=m.wind_speed!=null?`${m.wind_speed} узл. / ${m.wind_ms!=null?prettyNumber(m.wind_ms)+' м/с':'— м/с'}`:'—';$('#wind-direction').textContent=m.wind_dir==null?'Направление —':String(m.wind_dir).toUpperCase()==='VRB'?'Направление переменное':`Направление ${m.wind_dir}°`;$('#cloud').textContent=m.cloud?.description||'—';$('#cloud-code').textContent=m.cloud?.code?'METAR · '+m.cloud.code:'METAR';renderPrecipitation(m,f);$('#raw-metar').textContent=m.raw_metar||w.message||'Нет данных';$('#sunrise').textContent=timePart(d.sunrise?.[0]);$('#sunset').textContent=timePart(d.sunset?.[0]);let today=localDate(f.timezone), dates=d.time||d.sunrise?.map(x=>x.slice(0,10))||[], idx=dates.indexOf(today);if(!today)idx=0;state.dayIndex=idx;today=idx>=0?dates[idx]:today;$('#sunrise').textContent=timePart(d.sunrise?.[idx]);$('#sunset').textContent=timePart(d.sunset?.[idx]);let extrema=WeatherView.hourlyExtremaForDay(f,today),max=extrema?.max,min=extrema?.min;$('#forecast-max').textContent=tempText(max?.temp??d.temperature_2m_max?.[idx]);$('#forecast-min').textContent=tempText(min?.temp??d.temperature_2m_min?.[idx]);$('#forecast-max-time').textContent=max?'около '+max.time:'—';$('#forecast-min-time').textContent=min?'около '+min.time:'—';drawChart(state.demo?(w.history||[]):(w.metar_history||[]),f.timezone||(w.solar_days||[]).find(x=>x.timezone)?.timezone);updateClock()}
function renderPrecipitation(m,f){
  $('#precip-observed').textContent=m.precipitation?.description||'Нет данных';
  let stamp=Number(m.obs_time),obsTime='Последняя сводка станции';
  if(m.obs_time!=null&&Number.isFinite(stamp)&&stamp>0){try{obsTime='METAR в '+new Intl.DateTimeFormat('ru-RU',{timeZone:f.timezone||'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(stamp*1000))+(f.timezone?' (местное время)':' UTC')}catch{obsTime='Время сводки недоступно'}}
  $('#precip-observed-time').textContent=obsTime;
  let p=precipitationForecast(f),el=$('#precip-probability');
  el.textContent=p?`${p.probability}%${p.probability>=60?' · высокая':''}`:'Нет данных';el.classList.toggle('risk-high',!!p&&p.probability>=60);
  $('#precip-period').textContent=p?`Интервал ${p.start}–${p.end} · местное время`:'Почасовой прогноз недоступен';
}
let chartState={points:[],selected:null,timezone:'UTC',isUTC:true};
function chartNumber(n){return String(Math.round((n+Number.EPSILON)*100)/100)}
function chartTemp(c){if(!Number.isFinite(c))return '—';let a=chartNumber(c)+'°C',b=chartNumber(c*9/5+32)+'°F';return state.units==='F'?b:state.units==='B'?a+' / '+b:a}
function chartTime(ms,detail=false){
  try{return new Intl.DateTimeFormat('ru-RU',{timeZone:chartState.timezone, ...(detail?{day:'2-digit',month:'2-digit',year:'numeric',timeZoneName:'shortOffset'}:{}),hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(ms))}
  catch{return new Date(ms).toISOString().replace('T',' ').slice(0,16)+' UTC'}
}
function hideChartSelection(){chartState.selected=null;$('#chart-crosshair').classList.add('hidden');$('#chart-tooltip').classList.add('hidden')}
function selectChartPoint(index,autoScroll=false){
  const p=chartState.points[index];if(!p){hideChartSelection();return}
  chartState.selected=p.ms;if(autoScroll){const sc=$('#chart-scroll'),x=p.x/100*$('#chart-plot').clientWidth;if(x<sc.scrollLeft+35||x>sc.scrollLeft+sc.clientWidth-35)sc.scrollLeft=Math.max(0,x-sc.clientWidth/2)}const cross=$('#chart-crosshair');cross.classList.remove('hidden');
  cross.querySelector('.cross-x').style.left=p.x+'%';cross.querySelector('.cross-y').style.top=p.y+'%';
  cross.querySelector('.cross-dot').style.left=p.x+'%';cross.querySelector('.cross-dot').style.top=p.y+'%';
  const tip=$('#chart-tooltip');tip.classList.remove('hidden');$('#chart-tooltip-temp').textContent=chartTemp(p.c);$('#chart-tooltip-time').textContent=chartTime(p.ms,true);
  const plot=$('#chart-plot'),scroll=$('#chart-scroll'),width=plot.clientWidth,height=plot.clientHeight,tw=tip.offsetWidth,th=tip.offsetHeight;
  let left=p.x/100*width+14,top=p.y/100*height-th-12;
  const viewLeft=scroll.scrollLeft,viewRight=Math.min(width,viewLeft+scroll.clientWidth);
  if(left+tw>viewRight-4)left=p.x/100*width-tw-14;
  tip.style.left=Math.max(viewLeft+4,Math.min(viewRight-tw-4,left))+'px';tip.style.top=Math.max(4,Math.min(height-th-4,top))+'px';
}
function drawChart(history,tz){
  let previous=chartState.selected;
  try{new Intl.DateTimeFormat('ru-RU',{timeZone:tz||'UTC'});chartState.timezone=tz||'UTC';chartState.isUTC=!tz}catch{chartState.timezone='UTC';chartState.isUTC=true}
  const chart=WeatherView.metarChart(history,state.units),pts=chart.points;chartState.points=pts;chartState.start=chart.start;chartState.end=chart.end;
  $('#chart-zone').textContent=chartState.isUTC?'Время: UTC (часовой пояс станции недоступен)':'Время станции: '+chartState.timezone;
  // Align ticks to the station's actual wall-clock half-hours, including DST changes.
  const timeTicks=WeatherView.halfHourTicks(chart.start,chart.end,chartState.timezone);
  $('#chart-axis').innerHTML=timeTicks.map(t=>`<span style="left:${t.x}%" title="${esc(chartTime(t.ms,true))}">${esc(t.time)}</span>`).join('');
  $('#chart-time-grid').innerHTML=timeTicks.map(t=>`<i style="left:${t.x}%"></i>`).join('');
  $('#chart-empty').classList.toggle('hidden',!!pts.length);
  $('#history-chart').innerHTML=chart.segments.filter(segment=>segment.length>1).map(segment=>`<polyline points="${segment.map(p=>(p.x*10).toFixed(2)+','+(p.y*10).toFixed(2)).join(' ')}" fill="none" stroke="#b5e8b8" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join('');
  $('#chart-grid').innerHTML=chart.ticks.map(t=>`<div class="chart-tick" style="top:${t.y}%"><span>${esc(chartNumber(t.value))}°C <small>${esc(chartNumber(t.value*9/5+32))}°F</small></span></div>`).join('');
  const area=$('.chart-area'),plot=$('#chart-plot'),scroll=$('#chart-scroll'),follow=!chartState.drawn||chartState.station!==state.selected||scroll.scrollLeft+scroll.clientWidth>=scroll.scrollWidth-20;chartState.station=state.selected;
  const height=Math.max(210,(chart.ticks.length-1)*26);area.style.height=(height+36)+'px';plot.style.height=height+'px';if(follow)scroll.scrollLeft=scroll.scrollWidth;chartState.drawn=true;
  $('#chart-plot').style.background=chart.ticks.map(t=>`linear-gradient(to bottom,transparent calc(${t.y}% - .5px),#2e3a3c calc(${t.y}% - .5px),#2e3a3c calc(${t.y}% + .5px),transparent calc(${t.y}% + .5px))`).join(',');
  let lastLabel=-Infinity,labels='';
  pts.forEach((p,i)=>{labels+=`<i class="chart-point ${i===pts.length-1?'latest':''}" style="left:${p.x}%;top:${p.y}%"></i>`;if(p.x-lastLabel>=15){labels+=`<span class="chart-point-label" style="left:${Math.max(5,Math.min(95,p.x))}%;top:${p.y}%">${esc(chartTemp(p.c))}</span>`;lastLabel=p.x}});
  $('#chart-labels').innerHTML=labels;
  if(!pts.length){for(const id of ['last','min','max']){$('#chart-'+id).textContent='—';$('#chart-'+id+'-time').textContent=id==='last'?'Нет данных':'—'}hideChartSelection();return}
  const last=pts[pts.length-1],min=pts.reduce((a,b)=>b.c<a.c?b:a),max=pts.reduce((a,b)=>b.c>a.c?b:a);
  for(const [id,p] of [['last',last],['min',min],['max',max]]){$('#chart-'+id).textContent=chartTemp(p.c);$('#chart-'+id+'-time').textContent=chartTime(p.ms,true)}
  const selected=pts.findIndex(p=>p.ms===previous);if(selected>=0)selectChartPoint(selected);else hideChartSelection();
}
const chartPlot=$('#chart-plot');
function chartPointer(e){const rect=chartPlot.getBoundingClientRect();if(!rect.width||!chartState.points.length)return;const fraction=Math.max(0,Math.min(1,(e.clientX-rect.left)/rect.width));selectChartPoint(WeatherView.nearestChartPoint(chartState.points,chartState.start+fraction*(chartState.end-chartState.start)))}
chartPlot.addEventListener('pointermove',chartPointer);
chartPlot.addEventListener('pointerdown',chartPointer);
chartPlot.addEventListener('pointerleave',e=>{if(e.pointerType!=='touch')hideChartSelection()});
chartPlot.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End','Escape'].includes(e.key))return;e.preventDefault();if(e.key==='Escape'){hideChartSelection();return}const pts=chartState.points;let i=pts.findIndex(p=>p.ms===chartState.selected);if(e.key==='Home')i=0;else if(e.key==='End')i=pts.length-1;else if(i<0)i=e.key==='ArrowLeft'?pts.length-1:0;else i=Math.max(0,Math.min(pts.length-1,i+(e.key==='ArrowLeft'?-1:1)));selectChartPoint(i,true)});

function localDate(tz){if(!tz)return null;try{let parts=new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());let get=k=>parts.find(p=>p.type===k)?.value;return `${get('year')}-${get('month')}-${get('day')}`}catch{return null}}
const {precipitationForecast, localClock, shiftDay, solarSeconds, curvePoint, dailyPath, observationsForDay, DAY_START_X, DAY_WIDTH} = WeatherView;
function displayedSolarDay(w,day){
  let saved=(w.solar_days||[]).find(item=>item.day===day);if(saved)return saved;
  let f=w.forecast||{},d=f.daily||{},dates=d.time||d.sunrise?.map(x=>x?.slice(0,10))||[],idx=dates.indexOf(day);
  return idx<0?null:{day,sunrise:d.sunrise?.[idx],sunset:d.sunset?.[idx],timezone:f.timezone};
}
function dateLabel(day){try{return new Intl.DateTimeFormat('ru-RU',{timeZone:'UTC',day:'numeric',month:'long',year:'numeric'}).format(new Date(day+'T12:00:00Z'))}catch{return day||'—'}}
// Explicit station-local wall-clock scale (the endpoints are 00:00 and 23:59).
$('#arc-time-axis').innerHTML=Array.from({length:25},(_,hour)=>{
  const major=hour%3===0, label=hour===24?'23:59':String(hour).padStart(2,'0')+':00';
  return `<span class="arc-time-tick ${major?'major':''}" style="left:${hour===24?86340/86400*100:hour/24*100}%">${major?`<small class="${hour%6&&hour!==24?'minor':''}">${label}</small>`:''}</span>`;
}).join('');
const weatherIcons={
  clear:'<circle cx="24" cy="24" r="8" class="wx-sun"/><path d="M24 4v6M24 38v6M4 24h6M38 24h6M10 10l4 4M34 34l4 4M38 10l-4 4M14 34l-4 4" class="wx-rays"/>',
  'clear-neutral':'<circle cx="24" cy="24" r="13" class="wx-unknown"/><path d="M17 25h14" class="wx-unknown"/>',
  'clear-night':'<path d="M30 8a15 15 0 1 0 10 22A14 14 0 0 1 30 8z" class="wx-moon"/>',
  partial:'<circle cx="18" cy="17" r="7" class="wx-sun"/><path d="M18 4v4M6 17h4M8 7l3 3M28 7l-3 3" class="wx-rays"/><path d="M14 36h23a7 7 0 0 0 0-14 10 10 0 0 0-19-1 7.5 7.5 0 0 0-4 15z" class="wx-cloud"/>',
  'partial-night':'<path d="M19 6a10 10 0 1 0 8 16A10 10 0 0 1 19 6z" class="wx-moon"/><path d="M14 36h23a7 7 0 0 0 0-14 10 10 0 0 0-19-1 7.5 7.5 0 0 0-4 15z" class="wx-cloud"/>',
  cloudy:'<path d="M11 36h26a8 8 0 0 0 0-16 11 11 0 0 0-21-1 8.5 8.5 0 0 0-5 17z" class="wx-cloud"/>',
  rain:'<path d="M11 29h26a8 8 0 0 0 0-16 11 11 0 0 0-21-1 8.5 8.5 0 0 0-5 17z" class="wx-cloud"/><path d="M16 34l-3 6M25 34l-3 6M34 34l-3 6" class="wx-rain"/>',
  snow:'<path d="M11 29h26a8 8 0 0 0 0-16 11 11 0 0 0-21-1 8.5 8.5 0 0 0-5 17z" class="wx-cloud"/><path d="M16 34v7m-3.5-3.5h7M30 34v7m-3.5-3.5h7" class="wx-rain"/>',
  storm:'<path d="M11 29h26a8 8 0 0 0 0-16 11 11 0 0 0-21-1 8.5 8.5 0 0 0-5 17z" class="wx-cloud"/><path d="M25 29l-5 8h5l-2 7 8-10h-5l3-5" class="wx-lightning"/>',
  unknown:'<circle cx="24" cy="24" r="16" class="wx-unknown"/><path d="M19 19a5 5 0 0 1 10 1c0 3-5 4-5 7M24 33h.1" class="wx-unknown"/>'
};
function renderArcWeather(w,current,isDay,tz){
  const actual=WeatherView.metarIcon(w.metar,isDay);
  const icon=current?actual:{kind:'unknown',label:'Историческое состояние не сохранено'};
  $('#weather-symbol').innerHTML=`<svg viewBox="0 0 48 48" focusable="false">${weatherIcons[actual.kind]||weatherIcons.unknown}</svg>`;$('#weather-symbol').setAttribute('aria-label',actual.label+' · последний METAR');
  $('#arc-weather-icon').innerHTML=`<svg viewBox="0 0 48 48" focusable="false">${weatherIcons[icon.kind]||weatherIcons.unknown}</svg>`;
  $('#arc-weather-label').textContent=icon.label;
  $('#arc-weather-source').textContent=current?'ПОСЛЕДНИЙ METAR':'ВЫБРАННАЯ ПРОШЛАЯ ДАТА';
  let time='';const stamp=Number(w.metar?.obs_time);
  if(current&&w.metar?.obs_time!=null&&Number.isFinite(stamp)&&stamp>0){try{time='Сводка: '+new Intl.DateTimeFormat('ru-RU',{timeZone:tz||'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(stamp*1000))+(tz?' · местное время':' · UTC')}catch{time='Время сводки недоступно'}}
  if(current && isDay===null && w.metar)time+=(time?' · ':'')+'фаза дня неизвестна';
  $('#arc-weather-time').textContent=time;
}
function renderArcExtrema(extrema,sunrise,sunset,current){
  const group=$('#arc-extremes'),summary=$('#arc-forecast-summary');group.innerHTML='';
  if(!extrema){summary.innerHTML='<span class="arc-model-empty">Нет почасовых данных модели на эту дату</span>';return []}
  const source=current?'ПРОГНОЗ':'МОДЕЛЬ ЗА ДАТУ';
  summary.innerHTML=[['min','МИНИМУМ',extrema.min],['max','МАКСИМУМ',extrema.max]].map(([kind,label,p])=>`<span class="arc-model-item ${kind}"><i></i><span><small>${source} · ${label}</small><b>${esc(tempText(p.temp))} <em>в ${esc(p.time)}</em></b></span></span>`).join('');
  const marks=[['min','МИН',extrema.min],['max','МАКС',extrema.max]].map(([kind,label,p])=>({kind,label,forecast:p,point:curvePoint(p.seconds,sunrise,sunset)})).filter(x=>x.point);
  group.innerHTML=marks.map(({kind,label,forecast:p,point})=>{
    const x=point.x,y=point.y,labelX=Math.max(70,Math.min(730,x)),labelY=kind==='min'?Math.min(213,y+29):Math.max(14,y-29);
    return `<g class="arc-extreme ${kind}" tabindex="0" aria-label="${source} ${label}: ${esc(tempText(p.temp))}, ${esc(p.time)}"><title>${source} ${label}: ${esc(tempText(p.temp))}, ${esc(p.time)} · Open-Meteo, почасовая модель</title><line x1="${x}" y1="${y}" x2="${labelX}" y2="${kind==='min'?labelY-8:labelY+5}"/><circle cx="${x}" cy="${y}" r="${kind==='min'?5.5:3.7}"/><text x="${labelX}" y="${labelY}" text-anchor="middle">${label}</text></g>`;
  }).join('');
  return marks;
}
function updateClock(){
  const w=state.weather||{},f=w.forecast||{},now=Date.now(),tz=f.timezone||(w.solar_days||[]).find(x=>x.timezone)?.timezone;
  const local=localClock(now,tz), day=local?shiftDay(local.day,state.arcOffset):null, solar=day?displayedSolarDay(w,day):null,current=state.arcOffset===0;
  $('#arc-prev').disabled=!local||state.arcOffset<=-6;$('#arc-next').disabled=!local||state.arcOffset>=0;
  $('#arc-day-counter').textContent=`${1-state.arcOffset} / 7`;
  $('#arc-date').textContent=day?(current?'Сегодня · ':'')+dateLabel(day):'Дата недоступна';
  $('#sunrise').textContent=timePart(solar?.sunrise);$('#sunset').textContent=timePart(solar?.sunset);
  $('#local-time-label').textContent=current?'МЕСТНОЕ ВРЕМЯ':'ВЫБРАННАЯ ДАТА';
  $('#local-time').textContent=current?(local?.time||'—'):(day?day.split('-').reverse().join('.'):'—');
  const sunrise=solarSeconds(solar?.sunrise), sunset=solarSeconds(solar?.sunset), path=dailyPath(sunrise,sunset);
  const extrema=WeatherView.hourlyExtremaForDay(f,day),marks=renderArcExtrema(extrema,sunrise,sunset,current);
  const todaySolar=local?displayedSolarDay(w,local.day):null,todayRise=solarSeconds(todaySolar?.sunrise),todaySet=solarSeconds(todaySolar?.sunset);
  const isDay=local&&todayRise!==null&&todaySet!==null&&todayRise<todaySet?local.seconds>=todayRise&&local.seconds<todaySet:null;
  renderArcWeather(w,current,isDay,tz);
  const marker=$('#sun-marker');$('#arc-observations').innerHTML='';
  $('#arc-full').setAttribute('d',path||'');$('#arc-progress').setAttribute('d',path||'');
  if(!path||!local){
    marker.classList.add('hidden');$('#arc-passed-rect').setAttribute('width',0);
    $('.sun-heading h4').textContent='Нет данных о восходе и закате';
    $('#arc-note').textContent='Нет данных о восходе и закате для выбранной даты: точки на дуге недоступны.';return;
  }
  const seconds=current?local.seconds:86400;
  $('#arc-passed-rect').setAttribute('width',DAY_WIDTH*seconds/86400);
  const pt=current?curvePoint(seconds,sunrise,sunset):null;
  marker.classList.toggle('hidden',!pt);
  if(pt){
    for(const id of ['sun-dot','sun-glow']){$('#'+id).setAttribute('cx',pt.x);$('#'+id).setAttribute('cy',pt.y)}
    const text=$('#sun-temperature');text.setAttribute('x',pt.x);text.setAttribute('y',pt.y);text.innerHTML=tempMarker(w.metar?.temp,pt.x);
    const dayPhase=seconds>=sunrise&&seconds<sunset;$('#sun-dot').style.fill=dayPhase?'#e8d79c':'#c1d4e6';
  }
  $('.sun-heading h4').textContent=current?'Ночь · день · ночь':'История станции';
  const observations=observationsForDay(w.history||[],tz,day,now);let lastLabelX=-Infinity;
  $('#arc-observations').innerHTML=observations.map(item=>{
    const p=curvePoint(item.seconds,sunrise,sunset);if(!p)return '';
    const direction=item.trend==='up'?'↑':item.trend==='down'?'↓':'',trendNote=item.trend==='up'?' · выше предыдущего наблюдения':item.trend==='down'?' · ниже предыдущего наблюдения':'';
    let label='';if(p.x-lastLabelX>=80&&(!pt||Math.abs(p.x-pt.x)>20)&&marks.every(m=>Math.abs(p.x-m.point.x)>45)){lastLabelX=p.x;label=`<text class="arc-temp-label" x="${p.x}" y="${p.y-(direction?19:11)}" text-anchor="middle">${esc(tempText(item.temp))}</text>`}
    const arrowY=pt&&Math.abs(p.x-pt.x)<15?Math.min(p.y-5,pt.y-16):p.y-5;
    const arrow=direction?`<text class="arc-trend ${item.trend}" x="${p.x}" y="${arrowY}" text-anchor="middle" aria-hidden="true">${direction}</text>`:'';
    return `<g class="arc-observation"><title>${esc(item.time)} · ${esc(tempText(item.temp)+trendNote)}</title><circle cx="${p.x}" cy="${p.y}" r="2.4"/>${arrow}${label}</g>`;
  }).join('');
  const modelNote=extrema?`Цветные точки — ${current?'прогноз':'данные модели за дату'} Open-Meteo по доступным часам (${extrema.count})`:'Почасовые данные модели недоступны';
  $('#arc-note').textContent=`Сохранено наблюдений: ${observations.length}. Стрелки показывают изменение относительно предыдущего сохранённого наблюдения, в том числе за прошлый день. ${modelNote}${current?'; на светиле — последний METAR':''}. Не гарантия результата рынка.`;
}

$('#arc-prev').onclick=()=>{if(state.arcOffset>-6){state.arcOffset--;updateClock()}};
$('#arc-next').onclick=()=>{if(state.arcOffset<0){state.arcOffset++;updateClock()}};
function renderMarketTabs(){let b=state.bindings;$('#market-count').textContent=String(b.length).padStart(2,'0');$('#market-tabs').innerHTML=b.map(x=>`<button class="market-tab ${x.market_slug===state.market?'active':''}" data-slug="${esc(x.market_slug)}">${esc(x.market_name)}</button>`).join('');$$('.market-tab').forEach(btn=>btn.onclick=()=>{state.market=btn.dataset.slug;renderMarketTabs();loadMarket()})}
let bookSlug=null,bookData=null,bookMessage='',bookChoice=null,marketData=null;
const bookInFlight=new Set();
async function loadMarket(){
  if(!state.market){bookSlug=null;bookData=null;marketData=null;renderMarket(null);return}
  if(bookSlug!==state.market){bookSlug=state.market;bookData=null;bookChoice=null;marketData=null;bookMessage='Загрузка стакана…';renderMarket(null,'Загрузка события…')}
  if(state.demo){bookMessage='Демо: реальные заявки стакана не отображаются.';marketData={market:{title:state.market==='demo'?'Максимальная температура в Нью-Йорке':'Максимальная температура в Нью-Йорке · завтра',options:demoOptions,closed:false},positions:[]};renderMarket(marketData);return}
  const slug=state.market;
  try{let m=await api('/api/market/'+encodeURIComponent(slug));if(slug!==state.market)return;marketData=m;renderMarket(m);loadOrderBooks()}
  catch(e){if(slug===state.market){marketData=null;renderMarket(null,e.message)}}
}
async function loadOrderBooks(){
  const slug=state.market;if(!slug||state.demo||document.hidden||bookInFlight.has(slug))return;
  bookInFlight.add(slug);
  try{
    const data=await api('/api/market/'+encodeURIComponent(slug)+'/books');if(slug!==state.market)return;
    bookData=data;const total=Object.keys(data.books||{}).length,received=Object.values(data.books||{}).filter(Boolean).length;
    const time=new Date(data.requested_at*1000).toLocaleTimeString('ru-RU');
    bookMessage=!total?'Нет подтверждённых токенов YES/NO для стакана.':!received?'Не удалось получить стаканы CLOB; попробуем снова.':`Снимки CLOB: ${received} из ${total} · проверка ${time} · обновление примерно раз в 30 сек.`;
    if(data.limited)bookMessage+=' Слишком много исходов: часть стаканов не запрошена.';
  }catch(e){if(slug!==state.market)return;bookData=null;bookMessage='Стакан временно недоступен: '+e.message}
  finally{bookInFlight.delete(slug);if(slug===state.market&&marketData)renderMarket(marketData)}
}
function bookFor(option,side){
  if(!bookData||!option.token_yes||!option.token_no)return null;
  const pair=(bookData.pairs||[]).find(p=>p.yes===option.token_yes&&p.no===option.token_no);
  if(!pair)return null;
  return bookData.books?.[pair[side]]||null;
}
function priceCents(value){const n=Number(value);return Number.isFinite(n)?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:4}).format(n*100)+'¢':'—'}
function shares(value){if(value===null||value===undefined||value==='')return '—';const n=Number(value);return Number.isFinite(n)?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:4}).format(n):'—'}
let accountSnapshot=null,accountSnapshotAt=0,accountInFlight=null,accountAutoRefresh=false,tradeEnabled=false,tradeSending=false,accountGeneration=0,accountSource='';
const cancelingOrders=new Set();
let renderedSnapshotStale=false;
function confirmedMarketTokens(){
  const opts=marketData?.market?.options||[],pairs=bookData?.pairs||[],candidates=[];
  if(state.demo||!state.market||bookSlug!==state.market)return new Map();
  const pairKeys=new Set(pairs.map(p=>JSON.stringify([p.yes,p.no])));
  opts.forEach((o,index)=>{
    if(typeof o.token_yes!=='string'||typeof o.token_no!=='string'||o.token_yes===o.token_no)return;
    if(!pairKeys.has(JSON.stringify([o.token_yes,o.token_no])))return;
    candidates.push({asset:o.token_yes,option:o,index,token:'yes'}, {asset:o.token_no,option:o,index,token:'no'});
  });
  // Never guess which outcome a duplicated token belongs to.
  const counts=new Map();candidates.forEach(c=>counts.set(c.asset,(counts.get(c.asset)||0)+1));
  return new Map(candidates.filter(c=>counts.get(c.asset)===1).map(c=>[c.asset,c]));
}
function currentMarketOrders(tokens=confirmedMarketTokens()){
  if(!accountSnapshot||!Array.isArray(accountSnapshot.orders))return null;
  return accountSnapshot.orders.filter(o=>o&&tokens.has(o.asset_id));
}
function orderSide(order){return order.side==='BUY'?'Покупка':order.side==='SELL'?'Продажа':'Сторона неизвестна'}
function orderRemaining(order){
  const value=v=>v===null||v===undefined||v===''?null:Number(v),total=value(order.original_size),matched=value(order.size_matched);
  if(total===null||matched===null||!Number.isFinite(total)||!Number.isFinite(matched)||total<0||matched<0||matched>total)return null;
  return total-matched;
}
function cancelButton(order){
  const id=typeof order.id==='string'?order.id:'',valid=/^0x[0-9a-fA-F]{64}$/.test(id),busy=cancelingOrders.has(id.toLowerCase());
  return `<button type="button" class="button danger account-cancel" data-id="${esc(id)}" ${!valid||busy?'disabled':''}>${busy?'Отменяем…':'Отменить'}</button>`;
}
function orderDescription(order){
  const remaining=orderRemaining(order),price=order.price==null?'—':priceCents(order.price);
  return `${orderSide(order)} · ${price} · всего ${shares(order.original_size)} / сопоставлено ${shares(order.size_matched)} / не сопоставлено ${remaining===null?'—':shares(remaining)} шт. · ${order.status||'статус неизвестен'}`;
}
function renderTradeOrders(){
  const target=$('#trade-live-orders'),tokens=confirmedMarketTokens(),orders=currentMarketOrders(tokens);
  if(!accountSnapshot||orders===null){target.textContent='Ордера недоступны. Это не означает, что их нет.';return}
  const stale=Date.now()-accountSnapshotAt>65000;renderedSnapshotStale=stale;
  const note=`<small>Первая страница CLOB · ${esc(new Date(accountSnapshotAt).toLocaleTimeString('ru-RU'))}${stale?' · УСТАРЕЛО':''}. Сопоставление не означает окончательный расчёт; другие ключи могут быть не видны.</small>`;
  if(!orders.length){target.innerHTML=(tokens.size?'В полученной странице открытых ордеров этого события нет.':'Ожидаем подтверждённое сопоставление исходов; отсутствие данных не означает отсутствие ордеров.')+note;return}
  target.innerHTML=orders.map(o=>{const t=tokens.get(o.asset_id);return `<div class="trade-live-order"><div><b>${esc(t.option.label)} · ${t.token.toUpperCase()}</b><span>${esc(orderDescription(o))}<br>ID ${esc(o.id||'не указан')}</span></div>${cancelButton(o)}</div>`}).join('')+note;
}

function updateTradingControls(){const enabled=tradeEnabled&&!state.demo;$('#trade-submit').disabled=!enabled||tradeSending;$('#trade-submit').textContent=tradeSending?'Отправляем…':enabled?'Отправить реальный ордер':'Торговля выключена';$('.trade-preview-badge').textContent=enabled?'РЕАЛЬНАЯ ТОРГОВЛЯ ВКЛЮЧЕНА':'РЕАЛЬНАЯ ТОРГОВЛЯ ВЫКЛЮЧЕНА';$('.readonly').innerHTML=enabled?'<i></i> РЕАЛЬНАЯ ТОРГОВЛЯ':'<i></i> ТОЛЬКО НАБЛЮДЕНИЕ';}
function renderTradeAccount(){
  const balance=$('#trade-live-balance'),updated=$('#trade-live-updated'),positions=$('#trade-live-positions');
  renderTradeOrders();
  if(!accountSnapshot){balance.textContent='Не загружен';updated.textContent='Нет свежего снимка аккаунта.';positions.textContent='Позиции недоступны. Это не означает, что их нет.';return}
  const data=accountSnapshot,age=Math.max(0,Date.now()-accountSnapshotAt),stale=age>65000;
  balance.textContent=data.collateral_balance==null?'Недоступен':`${data.collateral_balance} pUSD`;
  updated.textContent=`Снимок: ${new Date(accountSnapshotAt).toLocaleTimeString('ru-RU')} · ${accountSource||'сверка'}${stale?' · УСТАРЕЛ':''} · доступный остаток может быть меньше`;
  const opts=marketData?.market?.options||[],pairs=bookData?.pairs||[],tokens=new Map();
  opts.forEach(o=>{const pair=pairs.find(p=>p.yes===o.token_yes&&p.no===o.token_no);if(pair){tokens.set(pair.yes,{label:o.label,side:'YES',book:bookFor(o,'yes')});tokens.set(pair.no,{label:o.label,side:'NO',book:bookFor(o,'no')})}});
  const ps=Array.isArray(data.positions)?data.positions.filter(p=>p.event_slug===state.market||tokens.has(p.asset)):null;
  if(!ps){positions.textContent='Позиции недоступны. Это не означает, что их нет.';return}
  if(!ps.length){positions.textContent=data.positions_maybe_partial?'В полученной странице позиция не найдена; снимок неполный.':'В полученном снимке нет позиций по этому событию.';return}
  positions.innerHTML=ps.map(p=>{
    const t=tokens.get(p.asset),bid=t?.book?.bids?.[0],title=t?`${t.label} · ${t.side}`:`${p.title||'Позиция'} · ${p.outcome||'исход'}`;
    return `<div class="trade-live-position"><b>${esc(title)}</b><span>${esc(shares(p.size))} контрактов · вход ${p.avg_price==null?'—':esc(priceCents(p.avg_price))} · цена Data API ${p.current_price==null?'—':esc(priceCents(p.current_price))} · BID ${bid?esc(priceCents(bid.price)):'—'} · PnL ${p.cash_pnl==null?'—':esc(shares(p.cash_pnl))+' USDC'}</span></div>`;
  }).join('')+(data.positions_maybe_partial?'<small>Список позиций неполный.</small>':'');
}
function renderAccountSnapshot(refreshBook=true){
  const status=$('#account-status'),positions=$('#account-positions'),orders=$('#account-orders');
  if(!accountSnapshot){positions.innerHTML='';orders.innerHTML='';$('#account-balance').textContent='';renderTradeAccount();if(refreshBook)renderMarketOptions();return}
  const data=accountSnapshot,opts=marketData?.market?.options||[],pairs=bookData?.pairs||[];
  const tokens=new Map([...confirmedMarketTokens()].map(([asset,t])=>[asset,`${t.option.label} · ${t.token.toUpperCase()}`]));
  const ps=Array.isArray(data.positions)?data.positions.filter(p=>p.event_slug===state.market||tokens.has(p.asset)):null;
  const os=Array.isArray(data.orders)?data.orders.filter(o=>tokens.has(o.asset_id)):null;
  renderTradeAccount();
  status.textContent=`Снимок: ${new Date(accountSnapshotAt).toLocaleTimeString('ru-RU')} · кошелёк ${data.wallet}. Ордера — только первая страница CLOB.`+(data.positions_maybe_partial?' Позиции могут быть неполными.':'')+(data.errors?.length?' '+data.errors.join(' '):'');
  $('#account-balance').textContent=data.collateral_balance!=null?`Баланс залога CLOB: ${data.collateral_balance} pUSD · снимок; доступная сумма может быть меньше из-за открытых ордеров и комиссий.`:'Баланс CLOB недоступен; это не означает нулевой баланс.';
  positions.innerHTML=ps?'<div class="account-list-title">ПОЗИЦИИ DATA API · ВЫБРАННОЕ СОБЫТИЕ</div>'+(ps.length?ps.map((p,i)=>`<div class="account-item"><b>${esc(p.title||p.outcome||'Позиция')} · ${esc(p.outcome)}</b><span>${esc(shares(p.size))} контрактов · вход ${p.avg_price!=null?esc(priceCents(p.avg_price)):'—'} · текущая ${p.current_price!=null?esc(priceCents(p.current_price)):'—'} · PnL ${p.cash_pnl==null?'—':esc(p.cash_pnl)+' USDC'}${p.redeemable?' · доступно погашение':''}</span>${tokens.has(p.asset)&&p.size>0?`<button type="button" class="button secondary account-close-draft" data-index="${i}">Подготовить продажу</button>`:''}</div>`).join(''):'<div class="account-empty">Нет позиций по выбранному событию в полученном снимке.</div>'):'<div class="account-empty">Позиции недоступны; это не означает, что их нет.</div>';
  orders.innerHTML=os?'<div class="account-list-title">ОТКРЫТЫЕ ОРДЕРА CLOB · ВЫБРАННОЕ СОБЫТИЕ</div>'+(os.length?os.map(o=>`<div class="account-item"><b>${esc(tokens.get(o.asset_id))} · ${esc(o.side)}</b><span>${o.price!=null?esc(priceCents(o.price)):'—'} · всего ${esc(shares(o.original_size))} / сопоставлено ${esc(shares(o.size_matched))} · ${esc(o.status||'статус неизвестен')}<br>ID ${esc(o.id)}</span>${cancelButton(o)}</div>`).join(''):`<div class="account-empty">${pairs.length?'Нет открытых ордеров по выбранному событию на первой странице.':'Ордера появятся после получения идентификаторов стакана; отсутствие данных не означает отсутствие ордеров.'}</div>`):'<div class="account-empty">Ордера недоступны; это не означает, что их нет.</div>';
  if(refreshBook)renderMarketOptions();
  $$('.account-close-draft').forEach(button=>button.onclick=()=>{
    const p=ps[Number(button.dataset.index)];if(!p)return;
    const index=opts.findIndex(o=>{let pair=pairs.find(x=>x.yes===o.token_yes&&x.no===o.token_no);return pair&&(pair.yes===p.asset||pair.no===p.asset)});
    if(index<0)return;const option=opts[index],token=option.token_yes===p.asset?'yes':'no',book=bookFor(option,token),bid=book?.bids?.[0];
    const d=getDraft();d.index=String(index);d.token=token;d.action='SELL';d.size=String(Math.floor(Number(p.size)*100)/100);d.price=bid?String(Math.round(Number(bid.price)*1000000)/10000):'';
    draftOptionsKey='';renderTradeDraft(marketData.market);$('#trade-send-status').textContent='Черновик продажи: проверьте количество, цену и свежесть позиции. До подтверждения ордер не отправляется.';$('#trade-preview').scrollIntoView?.({behavior:'smooth',block:'nearest'});
  });

}
function loadAccountSnapshot(forceAfterCurrent=false){
  if(accountInFlight)return forceAfterCurrent?accountInFlight.then(()=>loadAccountSnapshot()):accountInFlight;
  accountAutoRefresh=true;const generation=accountGeneration,b=$('#account-refresh');b.disabled=true;$('#trade-live-refresh').disabled=true;$('#account-status').textContent='Получаем позиции и заявки Polymarket…';
  const work=(async()=>{
    try{const snapshot=await api('/api/trading/account');if(generation!==accountGeneration)return;accountSnapshot=snapshot;accountSnapshotAt=Date.now();accountSource='сверка страницы';renderAccountSnapshot();await loadTradeJournal()}
    catch(e){if(generation!==accountGeneration)return;accountSnapshot=null;renderAccountSnapshot();$('#account-balance').textContent='Баланс недоступен.';$('#account-status').textContent='Сверка не выполнена: '+e.message}
    finally{b.disabled=false;$('#trade-live-refresh').disabled=false}
    await loadMonitorStatus();
  })();
  accountInFlight=work.finally(()=>{accountInFlight=null});
  return accountInFlight;
}
$('#account-refresh').onclick=()=>loadAccountSnapshot();
$('#trade-live-refresh').onclick=()=>loadAccountSnapshot();
document.addEventListener('click',async event=>{
  const button=event.target.closest?.('.account-cancel');if(!button||button.disabled)return;
  const id=button.dataset.id;if(!/^0x[0-9a-fA-F]{64}$/.test(id))return;
  const key=id.toLowerCase(),generation=accountGeneration;if(cancelingOrders.has(key)||!confirm(`Отменить ордер ${id}? Уже сопоставленная или исполненная часть не отменяется.`))return;
  cancelingOrders.add(key);$('#trade-cancel-status').textContent='Запрос на отмену ордера '+id+' отправляется. Дождитесь ответа.';renderAccountSnapshot();
  try{
    const result=await api('/api/trading/cancel',{method:'POST',body:JSON.stringify({order_id:id,confirm:true})});
    const message=result.state==='canceled'?'Отмена подтверждена CLOB. Проверьте частичные исполнения.':'Результат отмены неизвестен. Проверьте ордер на Polymarket; не считайте его отменённым.';
    if(generation===accountGeneration)$('#trade-cancel-status').textContent=id+' · '+message;alert(message);
  }catch(err){const message=err.message+' Проверьте ордер на Polymarket, прежде чем повторять отмену.';if(generation===accountGeneration)$('#trade-cancel-status').textContent=id+' · '+message;alert(message)}
  finally{try{if(generation===accountGeneration)await loadAccountSnapshot(true)}finally{cancelingOrders.delete(key);renderAccountSnapshot()}}
});

async function loadMonitorStatus(){
  const generation=accountGeneration;
  try{
    const result=await api('/api/trading/monitor');if(generation!==accountGeneration)return;
    const labels={ok:'снимок получен',partial:'снимок получен с ошибками',stale:'нет свежего снимка',error:'ошибка',not_running:'не запущено',not_configured:'аккаунт не настроен',account_changed:'аккаунт изменён; нужна новая сверка'};
    const when=result.checked_at?` · ${new Date(result.checked_at*1000).toLocaleTimeString('ru-RU')}`:'';
    const interval=Number.isInteger(result.interval_seconds)?` · интервал ${result.interval_seconds} с`:'';
    $('#trade-monitor-status').textContent=`Фоновое наблюдение: ${labels[result.status]||'недоступно'}${when}${interval}. Только чтение, SL/TP не выполняются.`;
    if(!accountSnapshot&&result.snapshot&&['ok','partial'].includes(result.status)){
      accountSnapshot=result.snapshot;accountSnapshotAt=result.checked_at*1000;accountSource='фоновый монитор';renderAccountSnapshot();
    }
  }catch(e){if(generation===accountGeneration)$('#trade-monitor-status').textContent='Фоновое наблюдение недоступно. SL/TP не выполняются.'}
}


async function loadTradeJournal(){
  try{
    const slug=state.market;if(!slug){$('#trade-journal').innerHTML='';return}
    const data=await api('/api/trading/intents?slug='+encodeURIComponent(slug));if(state.market!==slug)return;
    const rows=(data.intents||[]).filter(x=>x.slug===slug);
    $('#trade-journal').innerHTML=rows.length?'<div class="account-list-title">ЖУРНАЛ ОТПРАВОК И СВЕРКА СДЕЛОК</div>'+rows.map(x=>{
      const state=x.state==='accepted'?'Подтверждён ордер CLOB':x.state==='not_sent'?'Не отправлен':'РЕЗУЛЬТАТ НЕИЗВЕСТЕН — не повторяйте';
      const ex=x.exchange?.available?` · CLOB: ${x.exchange.status}, сопоставлено ${x.exchange.size_matched} из ${x.exchange.original_size} шт. (не равно окончательному исполнению)`:x.exchange?' · Статус CLOB не получен':'';
      const r=x.reconciliation, info=r?.size_matched!=null?` Сверка: ${r.order_status}, сопоставлено ${r.size_matched}; подтверждено ${r.confirmed_size}, ошибка ${r.failed_size} шт.`:'';
      const reason=r?.reason?` ${r.reason}`:'';
      const id=x.order_id?` ID ${x.order_id}`:r?.expected_id?` Расчётный ID ${r.expected_id} (не доказательство отправки)`:` Запись ${x.nonce}`;
      return `<div class="account-item"><b>${esc(x.side)} · ${esc(x.price)} pUSD · ${esc(x.size)} шт. · ${esc(x.order_type)}</b><span>${esc(state+ex+info+reason+id)}</span></div>`;
    }).join(''):'';
  }catch(e){$('#trade-journal').textContent='Журнал отправок недоступен.'}
}
function newTradeNonce(){const a=new Uint8Array(16);crypto.getRandomValues(a);return Array.from(a,x=>x.toString(16).padStart(2,'0')).join('')}
$('#trade-submit').onclick=async()=>{
  if(!tradeEnabled||tradeSending||state.demo)return;const d=getDraft(),o=marketData?.market?.options?.[Number(d.index)];
  if(d.index===''||!o||!bookFor(o,d.token)){toast('Выберите подтверждённый исход с доступным стаканом.');return}
  const price=Number(d.price),size=Number(d.size),asset=d.token==='yes'?o.token_yes:o.token_no;
  if(!Number.isFinite(price)||price<=0||price>=100||!Number.isFinite(size)||size<=0){toast('Укажите допустимую цену и количество.');return}
  if(!confirm(`РЕАЛЬНЫЙ ОРДЕР: ${state.market} · ${o.label} · ${d.token.toUpperCase()} · ${d.action} · ${d.type} · ${d.price}¢ · ${d.size} контрактов. SL/TP не активны. Отправить?`))return;
  const body={slug:state.market,asset,selection:d.token,side:d.action,order_type:d.type,price:(price/100).toFixed(6),size:d.size,nonce:newTradeNonce(),confirm:true};
  tradeSending=true;updateTradingControls();$('#trade-send-status').textContent='Отправляем заявку. Не закрывайте страницу и не повторяйте запрос.';
  try{const result=await api('/api/trading/orders',{method:'POST',body:JSON.stringify(body)});
    $('#trade-send-status').textContent=result.state==='accepted'?`Ордер принят CLOB: ${result.order_id}. Это не подтверждение исполнения; проверьте заявки и сделки.`:'Результат неизвестен. Не повторяйте заявку — проверьте Polymarket и журнал.';
    await loadAccountSnapshot();await loadTradeJournal();
  }catch(err){$('#trade-send-status').textContent=err.message+' Если результат запроса неизвестен, не повторяйте: проверьте Polymarket и журнал.';await loadTradeJournal()}
  finally{tradeSending=false;updateTradingControls()}
};

setInterval(()=>{if(accountAutoRefresh&&!document.hidden&&!$('#weather-page').classList.contains('hidden'))loadAccountSnapshot()},30000);
setInterval(()=>{if(accountSnapshot&&(Date.now()-accountSnapshotAt>65000)!==renderedSnapshotStale)renderAccountSnapshot()},15000);
let draftMarket=null,draftOptionsKey='',tradeDrafts=new Map(),draftMarketOptions=new Map();
function getDraft(){if(!tradeDrafts.has(state.market))tradeDrafts.set(state.market,{index:'',token:'yes',action:'BUY',type:'GTC',price:'',size:'',sl:'',tp:''});return tradeDrafts.get(state.market)}
function draftEstimate(){
  const d=getDraft(),price=Number(d.price),size=Number(d.size);
  $('#trade-estimate').textContent=d.price!==''&&d.size!==''&&price>0&&price<100&&size>0?`Оценка: ${(price*size/100).toLocaleString('ru-RU',{maximumFractionDigits:4})} USDC без комиссий · не ордер`:'Укажите цену и количество для оценки суммы.';
}
function renderTradeDraft(m){
  let opts=m?.options||[],key=JSON.stringify(opts.map(o=>[o.label,o.token_yes,o.token_no]));
  if(draftMarket===state.market&&draftOptionsKey===key)return;
  draftMarket=state.market;draftOptionsKey=key;
  let d=getDraft(),previousKey=draftMarketOptions.get(state.market);
  if(m&&previousKey&&previousKey!==key){d.index='';d.price='';}
  if(m)draftMarketOptions.set(state.market,key);
  $('#trade-option').innerHTML='<option value="">Выберите исход</option>'+opts.map((o,i)=>`<option value="${i}">${esc(o.label)}</option>`).join('');
  if(m&&(!opts[Number(d.index)]||d.index===''))d.index='';
  for(const [id,key] of [['option','index'],['token','token'],['action','action'],['type','type'],['price','price'],['size','size'],['sl','sl'],['tp','tp']])$('#trade-'+id).value=d[key];
  draftEstimate();
}
for(const [id,key] of [['option','index'],['token','token'],['action','action'],['type','type'],['price','price'],['size','size'],['sl','sl'],['tp','tp']]){
  $('#trade-'+id).addEventListener('input',e=>{getDraft()[key]=e.currentTarget.value;draftEstimate()});
}
function pickBookPrice(index,token,side,price){
  if(state.demo||!marketData?.market?.options?.[index]||!bookFor(marketData.market.options[index],token))return;
  const n=Number(price);if(!Number.isFinite(n)||n<=0||n>=1)return;
  const d=getDraft();d.index=String(index);d.token=token;d.action=side==='ask'?'BUY':'SELL';d.price=String(Math.round(n*1000000)/10000);
  draftOptionsKey='';renderTradeDraft(marketData.market);
  $('#trade-preview').scrollIntoView?.({behavior:'smooth',block:'nearest'});
}
function pickButton(price,index,token,side,css){return `<button type="button" class="quote-pick ${css}" data-index="${index}" data-token="${token}" data-side="${side}" data-price="${esc(price)}" title="Заполнить черновик; ордер не отправляется">${esc(priceCents(price))}</button>`}
function quote(book,index,token){if(!book)return '<span class="quote-missing">нет данных</span>';const ask=book.asks?.[0],bid=book.bids?.[0];return `<div class="quote">${ask?pickButton(ask.price,index,token,'ask','ask'):'—'}<small>купить · ${ask?esc(shares(ask.size))+' шт.':'нет заявок'}</small><small>продать · ${bid?pickButton(bid.price,index,token,'bid','bid'):'нет заявок'}</small></div>`}
function groupMarketOrders(){
  const tokens=confirmedMarketTokens(),groups=new Map();
  for(const order of currentMarketOrders(tokens)||[]){
    const t=tokens.get(order.asset_id),key=`${t.index}:${t.token}`;
    if(!groups.has(key))groups.set(key,[]);groups.get(key).push(order);
  }
  return groups;
}
function ordersForBook(index,token,groups){return groups.get(`${index}:${token}`)||[]}

function samePrice(a,b){return a!=null&&b!=null&&a!==''&&b!==''&&Number.isFinite(Number(a))&&Number.isFinite(Number(b))&&Number(a)>0&&Number(a)<1&&Number(a)===Number(b)}
function bookOwnOrder(order,where){
  const remaining=orderRemaining(order),id=typeof order.id==='string'?order.id:'',shortId=id.length>18?`${id.slice(0,10)}…${id.slice(-6)}`:id||'не указан';
  const summary=`${orderSide(order)} · ${order.price==null?'—':priceCents(order.price)} · не сопоставлено ${remaining===null?'—':shares(remaining)} шт.`;
  return `<div class="book-own-order"><div><strong>МОЙ ОРДЕР${where?' · '+esc(where):''}</strong><span>${esc(summary)}<br>сопоставлено ${esc(shares(order.size_matched))} / всего ${esc(shares(order.original_size))}<br><span title="${esc(id)}">ID ${esc(shortId)}</span></span></div>${cancelButton(order)}</div>`;
}
function bookSide(title,book,index,token,groups){
  const mine=ordersForBook(index,token,groups);
  const levels=(items,kind)=>{
    const side=kind==='bid'?'BUY':'SELL',own=mine.filter(o=>o.side===side);
    const list=Array.isArray(items)?items:[];
    const shown=list.length?list.map(x=>`<div class="book-entry"><div class="book-level"><span>${pickButton(x.price,index,token,kind,kind)}</span><span>${esc(shares(x.size))}</span></div>${own.filter(o=>samePrice(o.price,x.price)).map(o=>bookOwnOrder(o,'на этой цене')).join('')}</div>`).join(''):'<div class="book-empty">'+(book?'Нет заявок':'Стакан недоступен')+'</div>';
    const elsewhere=own.filter(o=>!list.some(x=>samePrice(o.price,x.price)));
    return shown+elsewhere.map(o=>bookOwnOrder(o,book?'вне показанных уровней':'стакан недоступен')).join('');
  };
  const unknown=mine.filter(o=>o.side!=='BUY'&&o.side!=='SELL').map(o=>bookOwnOrder(o,'сторона не распознана')).join('');
  const updated=book?`Получен: ${esc(new Date(book.fetched_at*1000).toLocaleTimeString('ru-RU'))}`:'Снимок стакана недоступен.';
  return `<section class="book-side"><h4>${title}</h4><div class="book-updated">${updated}</div><div class="book-columns"><div><div class="book-column-title">Покупатели · BID</div><div class="book-level book-label"><span>Цена</span><span>Количество</span></div>${levels(book?.bids,'bid')}</div><div><div class="book-column-title">Продавцы · ASK</div><div class="book-level book-label"><span>Цена</span><span>Количество</span></div>${levels(book?.asks,'ask')}</div></div>${unknown}</section>`;
}
function renderMarketOptions(m=marketData?.market){
  if(!state.market)m=null;
  const groups=groupMarketOrders();
  $('#market-options').innerHTML=m?.options?.length?m.options.map((o,i)=>{
    let n=typeof o.prob==='number'?Math.max(0,Math.min(100,o.prob)):null,yes=bookFor(o,'yes'),no=bookFor(o,'no');
    const mine=[...ordersForBook(i,'yes',groups),...ordersForBook(i,'no',groups)];
    const badge=mine.length?`<small class="book-own-badge">Мои открытые: ${mine.length} · откройте стакан</small>`:'';
    return `<div class="option-row book-option"><span class="option-name">${esc(o.label)}${badge}</span><span class="option-prob"><span class="bar"><i style="width:${n??0}%"></i></span> ${n==null?'—':n+'%'}</span>${quote(yes,i,'yes')}${quote(no,i,'no')}<button class="book-toggle" data-index="${i}" aria-expanded="${bookChoice===i}">${bookChoice===i?'Скрыть':'Стакан'} ↕</button></div>${bookChoice===i?`<div class="book-details">${bookSide('YES · ДА',yes,i,'yes',groups)}${bookSide('NO · НЕТ',no,i,'no',groups)}<p class="book-disclaimer">До 5 лучших цен на каждой стороне. Мои ордера — отдельный снимок первой страницы CLOB${Array.isArray(accountSnapshot?.orders)?` на ${esc(new Date(accountSnapshotAt).toLocaleTimeString('ru-RU'))}${Date.now()-accountSnapshotAt>65000?' · УСТАРЕЛ':''}`:' · недоступен'}; совпадение цены не подтверждает наличие ордера в публичном стакане; объёмы не складываются. Другие ключи могут быть не видны. YES и NO могут отражать общую ликвидность; не суммируйте их.</p></div>`:''}`
  }).join(''):'<div class="empty-state">Выберите или добавьте рынок</div>';
  $$('.book-toggle').forEach(btn=>btn.onclick=()=>{bookChoice=bookChoice===Number(btn.dataset.index)?null:Number(btn.dataset.index);renderMarketOptions()});
  $$('.quote-pick').forEach(btn=>btn.onclick=()=>pickBookPrice(Number(btn.dataset.index),btn.dataset.token,btn.dataset.side,btn.dataset.price));
}

function renderMarket(data,error){
  let m=data?.market,binding=state.bindings.find(x=>x.market_slug===state.market);
  $('#market-title').textContent=binding?.market_name||m?.title||'Нет данных рынка';
  $('#market-description').textContent=error||(!m?'Найдите рынок или добавьте ссылку на событие.':state.demo?'Демонстрационные значения · не реальные котировки':(binding?.market_name!==m.title?'Оригинал: '+m.title:'Цены и заявки по исходам события Polymarket'));
  $('#market-state').textContent=state.demo?'ДЕМО':m?.closed?'ЗАКРЫТ':'СОБЫТИЕ';
  $('#market-link').href=state.demo||!state.market?'https://polymarket.com':'https://polymarket.com/event/'+encodeURIComponent(state.market);
  renderTradeDraft(m);
  renderMarketOptions(m??null);
  $('#book-status').textContent=state.demo?'Демо: реальные заявки стакана не отображаются.':bookMessage||'Стакан CLOB загружается отдельно';
  let pos=data?.positions||[];$('#positions-count').textContent=`Позиций бота: ${pos.length}`;for(const id of ['rename-market','unlink-market','delete-market'])$('#'+id).classList.toggle('hidden',state.demo||!state.market);
  $('#positions').innerHTML=pos.length?'<b>ПОЗИЦИИ ИЗ БАЗЫ БОТА</b>'+pos.map(p=>`<div class="position-row">${esc(p.is_demo?'ДЕМО · ':'')}${esc(p.question||p.outcome||'Позиция')} · ${esc(p.side)} · ${esc(p.size)} shares · вход ${esc(p.entry_price)} · SL ${esc(p.sl??'—')} / TP ${esc(p.tp??'—')}</div>`).join(''):'';
  renderAccountSnapshot(false);
  if(accountSnapshot)loadTradeJournal();else $('#trade-journal').innerHTML='';
  updateTradingControls();
}

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
let scanData=null,scanSaved=new Map(),scanBusy=false;
const scanAdding=new Set();
const deviceTz=Intl.DateTimeFormat().resolvedOptions().timeZone||null;
function offsetLabel(minutes){if(minutes===null)return 'сравнение недоступно';if(minutes===0)return 'одинаковое время';const sign=minutes>0?'+':'−',m=Math.abs(minutes);return `${sign}${Math.floor(m/60)} ч${m%60?' '+m%60+' мин':''} относительно вас`}
function scanPhaseClass(rank){return ['current','future','past','unknown','unknown'][rank]}
function scanPricePercent(price){
  if(price>0&&price<0.00005)return '<0,01%';
  if(price<1&&price>0.99995)return '>99,99%';
  return (price*100).toLocaleString('ru-RU',{maximumFractionDigits:2})+'%';
}
function scanMultiplier(price){
  const multiple=1/price;
  return price>0&&Number.isFinite(multiple)?'×'+multiple.toLocaleString('ru-RU',{maximumFractionDigits:2}):'—';
}
function scanQuotesHtml(e,now){
  const quotes=Array.isArray(e.top_yes_prices)?e.top_yes_prices:[];
  if(!quotes.length)return '<div class="scan-quotes-empty">Цены YES активных исходов недоступны</div>';
  const age=Number(e.quotes_at)>0?now-Number(e.quotes_at)*1000:null;
  const stamp=age!==null?new Date(Number(e.quotes_at)*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):'время неизвестно';
  const stale=age!==null&&age>=120000?' · снимок устарел, обновите сканирование':'';
  const chips=quotes.map(q=>`<span class="scan-quote"><span>${esc(q.label)}${q.accepting_orders?'':' · приём не подтверждён'}</span><b>${esc(scanPricePercent(q.price))}</b><small>${esc(scanMultiplier(q.price))}</small></span>`).join('');
  const max=e.max_odds_yes;
  const maximum=max?`<div class="scan-max-odds">Макс. расчётный коэффициент YES: <b>${esc(scanMultiplier(max.price))}</b> · ${esc(max.label)} (${esc(scanPricePercent(max.price))})${max.accepting_orders?'':' · приём не подтверждён'}</div>`:'';
  return `<div class="scan-quotes"><div class="scan-quotes-label">ТОП-3 ПО ЦЕНЕ YES · GAMMA ${esc(stamp)}${esc(stale)}</div><div class="scan-quotes-list">${chips}</div>${maximum}</div>`;
}
function renderTemperatureScan(){
  if(!scanData)return;
  const now=Date.now(),own=WeatherView.localClock(now,deviceTz),filter=$('#scan-filter').value.trim().toLocaleLowerCase();
  $('#scan-device').textContent=own?`Ваше время: ${own.time} · ${own.day.split('-').reverse().join('.')} · ${deviceTz}`:'Часовой пояс устройства не определён';
  const rows=WeatherView.sortTemperatureMarkets(scanData.events,deviceTz,now).filter(({event})=>!filter||[event.title,event.slug,event.station_code].some(v=>String(v||'').toLocaleLowerCase().includes(filter)));
  $('#scan-status').textContent=`Найдено событий: ${scanData.events.length}${filter?' · показано: '+rows.length:''}${scanData.scanned_at?' · сканирование в '+new Date(scanData.scanned_at*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):''}.`+(scanData.incomplete?' Каталог получен не полностью — повторите позже.':'')+(scanData.unknown_timezones?' Для части событий часовой пояс неизвестен.':'');
  $('#scan-results').innerHTML=rows.length?rows.map(({event:e,time:t})=>{
    const c=e.station?.candidate,existing=scanSaved.get(e.slug),linked=!!(c&&existing?.codes?.includes(c.code)),saved=!!existing;
    const buttonText=scanAdding.has(e.slug)?'Проверка…':linked?'Добавлен ✓':saved?(c?'Привязать станцию':'Сохранён ✓'):'Добавить';
    const trusted=c&&['Aviation Weather Center','Weather Underground'].includes(c.kind)&&c.url;
    const source=c?`${esc(c.code)} · ${esc(c.kind)} · ${esc(c.field)}${trusted?'':' · требуется проверка'}`:e.station?.status==='conflict'?'Найдены разные станции — требуется проверка':'Источник станции не определён';
    const evidence=c?.url?`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">Источник ↗</a>`:'';
    const displayTime=t.local?t.local.time:'—',date=e.date?e.date.split('-').reverse().join('.'):'не определена';
    return `<article class="scan-card"><div class="scan-clock"><span>МЕСТНОЕ ВРЕМЯ</span><b>${esc(displayTime)}</b><small>${esc(t.local?t.local.day.split('-').reverse().join('.')+' · '+offsetLabel(t.difference):'Не определено')}</small></div><div class="scan-content"><div class="scan-meta"><span class="scan-phase ${scanPhaseClass(t.rank)}">${esc(t.phase)}</span><span>ДАТА РЫНКА: ${esc(date)}</span>${e.orders_suspended?'<span>ЗАЯВКИ ПРИОСТАНОВЛЕНЫ</span>':''}</div><h4>${esc(e.title)}</h4><div class="scan-source">${source} ${evidence}${e.timezone?`<span> · ${esc(e.timezone)}${e.timezone_basis?' · '+esc(e.timezone_basis):''}</span>`:''}</div>${scanQuotesHtml(e,now)}</div><div class="scan-actions"><button type="button" class="button ${linked||saved&&!c?'secondary':'primary'} scan-add" data-slug="${esc(e.slug)}" ${linked||saved&&!c||scanAdding.has(e.slug)?'disabled':''}>${buttonText}</button><a href="https://polymarket.com/event/${encodeURIComponent(e.slug)}" target="_blank" rel="noopener noreferrer">Правила ↗</a></div></article>`;
  }).join(''):'<div class="empty-state">Подходящих событий не найдено.</div>';
  $$('.scan-add').forEach(button=>button.onclick=()=>addScannedMarket(button.dataset.slug,button));
}
async function scanTemperatureMarkets(){
  if(scanBusy)return;scanBusy=true;const button=$('#scan-temperature');button.disabled=true;$('#scan-status').textContent='Сканируем Polymarket и определяем часовые пояса станций. Это может занять некоторое время…';
  try{scanData=await api('/api/temperature-scan');$('#scan-filters').classList.remove('hidden');await renderAllMarkets();renderTemperatureScan()}
  catch(e){$('#scan-status').textContent='Не удалось получить список: '+e.message}
  finally{scanBusy=false;button.disabled=false}
}
async function addScannedMarket(slug,button){
  const item=scanData?.events?.find(e=>e.slug===slug);if(!item||scanAdding.has(slug))return;
  scanAdding.add(slug);button.disabled=true;button.textContent='Проверка…';
  try{
    // Re-read rules at click time; server validates source again before creating a binding.
    const result=await api('/api/market-station/resolve',{method:'POST',body:JSON.stringify({slug})});
    if(result.status==='found'){
      const c=result.candidate,prior=item.station?.candidate;
      const trusted=['Aviation Weather Center','Weather Underground'].includes(c.kind)&&!!c.url;
      const changed=prior?.code!==c.code||prior?.kind!==c.kind||prior?.url!==c.url;
      if((changed||!trusted)&&!confirm(`Найдена станция ${c.code} (${c.kind}). Проверьте правила и источник рынка. Добавить и привязать эту станцию?`))return;
      const response=await api('/api/market-station/add',{method:'POST',body:JSON.stringify({slug,code:result.candidate.code,confirm:true})});
      await loadStations(state.demo?response.station.id:state.selected);
      toast(response.already_bound?'Рынок уже привязан':response.created_station?'Рынок добавлен; новая станция выключена':'Рынок добавлен и привязан к станции');
    }else{
      await api('/api/markets',{method:'POST',body:JSON.stringify({slug})});
      toast(result.status==='conflict'?'Рынок сохранён без станции: коды конфликтуют. Выберите станцию вручную.':'Рынок сохранён без станции. При необходимости выберите станцию вручную.');
    }
    await renderAllMarkets();
  }catch(e){toast(e.message)}finally{scanAdding.delete(slug);button.disabled=false;renderTemperatureScan()}
}
$('#scan-temperature').onclick=scanTemperatureMarkets;
$('#scan-filter').addEventListener('input',renderTemperatureScan);
setInterval(()=>{if(scanData&&!document.hidden&& !$('#markets-page').classList.contains('hidden'))renderTemperatureScan()},60000);
async function renderAllMarkets(){
  try{
    let [markets,all]=await Promise.all([api('/api/markets'),Promise.all(state.stations.map(async s=>({s,b:await api(`/api/stations/${s.id}/bindings`)})))]);
    scanSaved=new Map(markets.map(m=>[m.slug,{codes:all.filter(x=>x.b.some(b=>b.market_slug===m.slug)).map(x=>x.s.station_code)}]));
    renderTemperatureScan();
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
async function loadSecurityInfo(){
  const form=$('#password-form'),button=form.querySelector('button'),accountButton=$('#account-form button[type=submit]'),deleteButton=$('#account-delete'),controlButton=$('#trade-control-form button');button.disabled=true;accountButton.disabled=true;deleteButton.disabled=true;controlButton.disabled=true;
  try{const s=await api('/api/security-info');button.disabled=!s.tunnel;accountButton.disabled=!s.tunnel;deleteButton.disabled=!s.tunnel;controlButton.disabled=!s.tunnel;$('#monitor-interval-form button').disabled=!s.tunnel;tradeEnabled=!!s.trading_enabled;updateTradingControls();$('#trade-control-status').textContent=tradeEnabled?'Реальная торговля включена.':'Торговля выключена.';$('#password-status').textContent=s.tunnel?'SSH-туннель: защищённое подключение к серверу.':'Смена пароля недоступна: требуется настроенный SSH-туннель.';
    if(s.tunnel){try{const interval=await api('/api/trading/monitor-interval');$('#monitor-interval-form [name=interval_seconds]').value=interval.interval_seconds;$('#monitor-interval-status').textContent='Служба применит изменение при следующей проверке.'}catch(e){$('#monitor-interval-status').textContent='Интервал недоступен: '+e.message}const c=await api('/api/trading/account-config');$('#account-config-status').textContent=c.configured?`Настроен кошелёк ${c.wallet} · тип подписи ${c.signature_type}. Доступ к бирже ещё нужно проверить.`:'Аккаунт ещё не настроен.'}
    else {$('#account-config-status').textContent='Настройка доступна только через SSH-туннель.';$('#monitor-interval-status').textContent='Требуется SSH-туннель.';}
  }catch(e){$('#password-status').textContent='Не удалось проверить подключение: '+e.message;$('#account-config-status').textContent='Не удалось получить состояние аккаунта.'}
}
function updateAccountApiMode(){
  const manual=$('#account-api-mode').value==='manual';
  for(const name of ['api_key','api_secret','api_passphrase']){
    const field=$(`#account-form [name="${name}"]`);field.disabled=!manual;field.required=manual;
  }
}
$('#account-api-mode').addEventListener('change',updateAccountApiMode);
updateAccountApiMode();
$('#account-form').onsubmit=async e=>{
  e.preventDefault();const f=e.currentTarget,button=f.querySelector('button[type=submit]'),data=Object.fromEntries(new FormData(f));
  data.auto_api=$('#account-api-mode').value==='auto';
  if(!confirm('Сохранить приватный ключ на сервере в незашифрованном файле с ограниченными правами?'))return;
  button.disabled=true;
  try{const result=await api('/api/trading/account-config',{method:'POST',body:JSON.stringify(data)});f.reset();updateAccountApiMode();tradeEnabled=false;updateTradingControls();accountGeneration++;accountSnapshot=null;accountSource='';accountAutoRefresh=false;$('#trade-cancel-status').textContent='';$('#trade-monitor-status').textContent='Аккаунт изменён; состояние фонового наблюдения не проверено. SL/TP не работают.';renderAccountSnapshot();$('#account-status').textContent='Аккаунт изменён; повторите сверку с биржей.';$('#account-config-status').textContent=`Сохранён кошелёк ${result.wallet}. Работоспособность ещё не проверена.`;toast('Настройки сохранены; проверьте соединение с биржей')}
  catch(err){toast(err.message)}finally{button.disabled=false}
};
$('#account-delete').onclick=async e=>{
  const f=$('#account-form'),button=e.currentTarget,password=f.elements.current_password.value;
  if(!password){toast('Введите текущий пароль терминала');return}
  if(!confirm('Удалить сохранённые ключи с сервера? Открытые ордера и позиции на бирже не изменятся.'))return;
  button.disabled=true;try{await api('/api/trading/account-config',{method:'DELETE',body:JSON.stringify({current_password:password})});f.reset();updateAccountApiMode();tradeEnabled=false;updateTradingControls();accountGeneration++;accountSnapshot=null;accountSource='';accountAutoRefresh=false;$('#trade-cancel-status').textContent='';$('#trade-monitor-status').textContent='Аккаунт изменён; состояние фонового наблюдения не проверено. SL/TP не работают.';renderAccountSnapshot();$('#account-status').textContent='Аккаунт отключён; ордера на бирже не отменялись.';$('#account-config-status').textContent='Аккаунт не настроен.';toast('Сохранённые ключи удалены')}
  catch(err){toast(err.message)}finally{button.disabled=false}
};
$('#monitor-interval-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget,button=form.querySelector('button'),n=Number(form.elements.interval_seconds.value);
  if(!Number.isInteger(n)||n<15||n>300){$('#monitor-interval-status').textContent='Укажите целое число секунд от 15 до 300.';return}
  button.disabled=true;
  try{const result=await api('/api/trading/monitor-interval',{method:'POST',body:JSON.stringify({interval_seconds:n,confirm:true})});form.elements.interval_seconds.value=result.interval_seconds;$('#monitor-interval-status').textContent='Сохранено. Служба применит изменение при следующей проверке; SL/TP не работают.';await loadMonitorStatus()}
  catch(err){$('#monitor-interval-status').textContent=err.message}
  finally{button.disabled=false}
};
$('#trade-control-form').onsubmit=async e=>{
  e.preventDefault();const f=e.currentTarget,button=f.querySelector('button'),on=f.elements.enabled.value==='true';
  if(!confirm(on?'Включить отправку РЕАЛЬНЫХ ордеров? Модуль не проверен на бирже, SL/TP и автономный мониторинг не работают.':'Выключить торговлю? Уже отправленные заявки на бирже не отменятся.'))return;
  button.disabled=true;try{const result=await api('/api/trading/control',{method:'POST',body:JSON.stringify({enabled:on,confirm:true,current_password:f.elements.current_password.value})});f.elements.current_password.value='';tradeEnabled=result.enabled;updateTradingControls();$('#trade-control-status').textContent=tradeEnabled?'Реальная торговля включена.':'Торговля выключена.';renderAccountSnapshot()}
  catch(err){toast(err.message)}finally{button.disabled=false}
};
$('#password-form').onsubmit=async e=>{
  e.preventDefault();const f=e.currentTarget,button=f.querySelector('button'),data=Object.fromEntries(new FormData(f));
  if(data.new_password!==data.confirmation){toast('Новые пароли не совпадают');return}
  button.disabled=true;
  try{await api('/api/change-password',{method:'POST',body:JSON.stringify({current_password:data.current_password,new_password:data.new_password})});f.reset();alert('Пароль изменён. Войдите заново.');location.assign('/login')}
  catch(err){toast(err.message);button.disabled=false}
};
$('#settings-form').onsubmit=async e=>{e.preventDefault();try{let result=await api('/api/settings',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)))});state.units=result.units;renderWeather();toast('Настройки сохранены')}catch(err){toast(err.message)}};
// Read-only public trade observation. Never calls the trading or cancellation APIs.
let copyBusy=false,copyGeneration=0;
function copyTime(stamp){return Number.isFinite(Number(stamp))&&Number(stamp)>0?new Date(Number(stamp)*1000).toLocaleString('ru-RU'):'—'}
function copyAmountMode(){
  const mode=$('#copy-mode').value,label=$('#copy-amount-label');label.style.display=mode==='same'?'none':'';
  label.childNodes[0].textContent=mode==='percent'?'Процент от долей трейдера':'Сумма на каждую сделку, pUSD';
}
$('#copy-mode').addEventListener('change',copyAmountMode);copyAmountMode();
async function loadCopyObserver(){
  const generation=++copyGeneration;
  try{const data=await api('/api/copy-observer');if(generation!==copyGeneration)return;
    const status={running:'свежий сигнал',stale:'давно не отвечала',not_running:'не запущена'};
    $('#copy-service-status').textContent=`Служба наблюдения: ${status[data.service]||'неизвестно'}${data.service_checked_at?' · '+copyTime(data.service_checked_at):''}. Только публичное чтение.`;
    const leaders=Array.isArray(data.leaders)?data.leaders:[];
    $('#copy-leaders').innerHTML=leaders.length?leaders.map(l=>{
      const labels={fixed:`${esc(l.amount)} pUSD на исполнение`,same:'равное число долей',percent:`${esc(l.amount)}% от долей`};
      const stale=l.last_success&&(Date.now()/1000-Number(l.last_success)>Math.max(90,3*Number(l.interval_seconds)));
      const state=!l.enabled?'пауза':l.status==='error'?'ошибка чтения':stale?'данные устарели':l.status==='ok'?'наблюдение':'ожидает проверки';
      return `<article class="copy-leader"><div><strong>${esc(l.label)}</strong><code>${esc(l.wallet)}</code><small>${esc(state)} · проверка ${esc(l.interval_seconds)} с · последняя успешная: ${esc(copyTime(l.last_success))}</small>${l.error_reason?`<small>Причина: ${esc(l.error_reason)}</small>`:''}<small>${labels[l.mode]||'—'} · потолок ${esc(l.cap)} pUSD на расчёт</small></div><div class="copy-actions"><button type="button" class="button secondary" data-copy-edit="${Number(l.id)}">Настроить</button><button type="button" class="button secondary" data-copy-toggle="${Number(l.id)}" data-enabled="${l.enabled?0:1}">${l.enabled?'Пауза':'Продолжить'}</button><button type="button" class="button danger" data-copy-delete="${Number(l.id)}">Удалить</button></div></article>`;
    }).join(''):'Нет наблюдаемых трейдеров.';
    $('#copy-leaders').querySelectorAll('[data-copy-edit]').forEach(b=>b.onclick=()=>{
      const l=leaders.find(x=>Number(x.id)===Number(b.dataset.copyEdit));if(!l)return;
      const form=$('#copy-form');form.dataset.editId=String(l.id);for(const field of ['mode','amount','cap','interval_seconds'])form.elements[field].value=l[field];
      form.elements.profile.required=false;$('#copy-profile-label').classList.add('hidden');$('#copy-form-heading').textContent='Настройки наблюдения: '+l.label;
      form.querySelector('button[type=submit]').textContent='Сохранить настройки';$('#copy-edit-cancel').classList.remove('hidden');$('#copy-form-status').textContent='Изменения применяются только к новым оценкам.';copyAmountMode();
    });
    $('#copy-leaders').querySelectorAll('[data-copy-toggle]').forEach(b=>b.onclick=async()=>{const on=b.dataset.enabled==='1';if(on&&!confirm('Возобновить с текущего времени? Сделки во время паузы не обрабатываются.'))return;b.disabled=true;try{await api('/api/copy-observer/'+b.dataset.copyToggle,{method:'PATCH',body:JSON.stringify({enabled:on,confirm:true})});await loadCopyObserver()}catch(e){toast(e.message);b.disabled=false}});
    $('#copy-leaders').querySelectorAll('[data-copy-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Удалить трейдера и сохранённые наблюдения?'))return;b.disabled=true;try{await api('/api/copy-observer/'+b.dataset.copyDelete,{method:'DELETE',body:JSON.stringify({confirm:true})});await loadCopyObserver()}catch(e){toast(e.message);b.disabled=false}});
    const signals=Array.isArray(data.signals)?data.signals:[];
    $('#copy-signals').innerHTML=signals.length?signals.map(s=>{
      const x=s.estimate||{},buy=s.side==='BUY',best=x.best_price==null?'нет данных':`${esc(x.best_price)} pUSD`,depth=x.available_shares==null?'стакан недоступен':`${esc(x.available_shares)} долей на пяти уровнях`;
      const target=buy?`Расчёт: ${esc(x.desired_shares)} долей ≈ ${esc(x.desired_value)} pUSD${x.capped?' · ограничено потолком':''}`:'Размер продажи не рассчитан: виртуальная позиция и доля выхода пока не моделируются.';
      const eventLink=/^[a-z0-9-]{1,160}$/.test(s.event_slug||'')?` <a href="https://polymarket.com/event/${esc(s.event_slug)}" target="_blank" rel="noopener noreferrer">Рынок ↗</a>`:'';
      return `<article class="copy-signal"><div class="copy-signal-head"><strong>${buy?'ПОКУПКА':'ПРОДАЖА'} · ${esc(s.outcome||'исход не указан')}</strong><span>${esc(copyTime(s.timestamp))} · обнаружена ${esc(copyTime(s.observed_at))}</span></div><b>${esc(s.title||'Без названия')}${eventLink}</b><small>${esc(s.label)} · ${esc(s.size)} долей по ${esc(s.price)} pUSD</small><small>${target}</small><small>Лучший ${buy?'ASK':'BID'}: ${best} · по цене трейдера или лучше: ${depth}</small><small>Снимок стакана: ${esc(copyTime(x.book_at))} · это не исполнение и не гарантия доступности.</small><code>${esc(s.transaction_hash)}</code></article>`;
    }).join(''):'Новых сделок пока нет.';
  }catch(e){if(generation===copyGeneration){$('#copy-service-status').textContent='Наблюдение недоступно: '+e.message;$('#copy-leaders').textContent='Не удалось загрузить трейдеров.';$('#copy-signals').textContent='Не удалось загрузить сделки.'}}
}
$('#copy-refresh').onclick=()=>loadCopyObserver();
function resetCopyForm(){const form=$('#copy-form');delete form.dataset.editId;form.elements.profile.required=true;$('#copy-profile-label').classList.remove('hidden');$('#copy-form-heading').textContent='Добавить наблюдение';form.querySelector('button[type=submit]').textContent='Начать наблюдение';$('#copy-edit-cancel').classList.add('hidden');copyAmountMode()}
$('#copy-edit-cancel').onclick=()=>{resetCopyForm();$('#copy-form-status').textContent=''};
$('#copy-form').onsubmit=async e=>{e.preventDefault();if(copyBusy)return;const form=e.currentTarget,button=form.querySelector('button[type=submit]');const data=Object.fromEntries(new FormData(form));data.interval_seconds=Number(data.interval_seconds);data.confirm=true;const editing=form.dataset.editId;
  if(!confirm(editing?'Сохранить настройки наблюдения? Реальные сделки не отправляются.':'Начать только наблюдение? Реальные сделки не отправляются.'))return;
  copyBusy=true;button.disabled=true;$('#copy-form-status').textContent=editing?'Сохраняем настройки…':'Проверяем профиль…';
  try{
    if(editing){await api('/api/copy-observer/'+editing,{method:'PATCH',body:JSON.stringify({settings:data,confirm:true})});resetCopyForm();$('#copy-form-status').textContent='Настройки сохранены для новых оценок.'}
    else{const result=await api('/api/copy-observer',{method:'POST',body:JSON.stringify(data)});$('#copy-form-status').textContent=`Добавлен торговый кошелёк ${result.wallet}. Отслеживаются только новые сделки.`;form.elements.profile.value=''}
    await loadCopyObserver()
  }catch(err){$('#copy-form-status').textContent=err.message}finally{copyBusy=false;button.disabled=false}
};
setInterval(()=>{if(!document.hidden&&!$('#copy-page').classList.contains('hidden'))loadCopyObserver()},15000);
$('#refresh').onclick=()=>loadStations();$('#today-label').textContent=new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric'}).format(new Date()).toUpperCase();setInterval(updateClock,30000);setInterval(()=>{if(!document.hidden&&!state.demo)loadOrderBooks()},30000);setInterval(()=>{if(!document.hidden&&!state.demo)loadStation(true)},60000);$('#logout').onclick=async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});location.assign('/login')}catch(e){toast(e.message)}};api('/api/session').then(data=>{csrfToken=data.csrf;api('/api/security-info').then(async s=>{tradeEnabled=!!s.trading_enabled;updateTradingControls();if(s.tunnel){const c=await api('/api/trading/account-config');if(c.configured)await loadAccountSnapshot()}}).catch(()=>{});return loadSettings()}).then(()=>loadStations()).catch(e=>toast(e.message));
