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
    let label='';if(p.x-lastLabelX>=80&&(!pt||Math.abs(p.x-pt.x)>30)&&marks.every(m=>Math.abs(p.x-m.point.x)>45)){lastLabelX=p.x;label=`<text x="${p.x}" y="${p.y-11}" text-anchor="middle">${esc(tempText(item.temp))}</text>`}
    return `<g class="arc-observation"><title>${esc(item.time)} · ${esc(tempText(item.temp))}</title><circle cx="${p.x}" cy="${p.y}" r="2.4"/>${label}</g>`;
  }).join('');
  const modelNote=extrema?`Цветные точки — ${current?'прогноз':'данные модели за дату'} Open-Meteo по доступным часам (${extrema.count})`:'Почасовые данные модели недоступны';
  $('#arc-note').textContent=`Сохранено наблюдений: ${observations.length}. ${modelNote}${current?'; на светиле — последний METAR':''}. Не гарантия результата рынка.`;
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
function shares(value){const n=Number(value);return Number.isFinite(n)?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:4}).format(n):'—'}
function quote(book){if(!book)return '<span class="quote-missing">нет данных</span>';const ask=book.asks?.[0],bid=book.bids?.[0];return `<div class="quote"><b>${ask?esc(priceCents(ask.price)):'—'}</b><small>купить · ${ask?esc(shares(ask.size))+' шт.':'нет заявок'}</small><small>продать · ${bid?esc(priceCents(bid.price)):'нет заявок'}</small></div>`}
function bookSide(title,book){
  if(!book)return `<section class="book-side"><h4>${title}</h4><p>Стакан недоступен или отсутствует.</p></section>`;
  const levels=(items,kind)=>items?.length?items.map(x=>`<div class="book-level"><span class="${kind}">${esc(priceCents(x.price))}</span><span>${esc(shares(x.size))}</span></div>`).join(''):'<div class="book-empty">Нет заявок</div>';
  return `<section class="book-side"><h4>${title}</h4><div class="book-updated">Получен: ${esc(new Date(book.fetched_at*1000).toLocaleTimeString('ru-RU'))}</div><div class="book-columns"><div><div class="book-column-title">Покупатели · BID</div><div class="book-level book-label"><span>Цена</span><span>Количество</span></div>${levels(book.bids,'bid')}</div><div><div class="book-column-title">Продавцы · ASK</div><div class="book-level book-label"><span>Цена</span><span>Количество</span></div>${levels(book.asks,'ask')}</div></div></section>`;
}
function renderMarket(data,error){
  let m=data?.market,binding=state.bindings.find(x=>x.market_slug===state.market);
  $('#market-title').textContent=binding?.market_name||m?.title||'Нет данных рынка';
  $('#market-description').textContent=error||(!m?'Найдите рынок или добавьте ссылку на событие.':state.demo?'Демонстрационные значения · не реальные котировки':(binding?.market_name!==m.title?'Оригинал: '+m.title:'Цены и заявки по исходам события Polymarket'));
  $('#market-state').textContent=state.demo?'ДЕМО':m?.closed?'ЗАКРЫТ':'СОБЫТИЕ';
  $('#market-link').href=state.demo||!state.market?'https://polymarket.com':'https://polymarket.com/event/'+encodeURIComponent(state.market);
  $('#market-options').innerHTML=m?.options?.length?m.options.map((o,i)=>{
    let n=typeof o.prob==='number'?Math.max(0,Math.min(100,o.prob)):null,yes=bookFor(o,'yes'),no=bookFor(o,'no');
    return `<div class="option-row book-option"><span class="option-name">${esc(o.label)}</span><span class="option-prob"><span class="bar"><i style="width:${n??0}%"></i></span> ${n==null?'—':n+'%'}</span>${quote(yes)}${quote(no)}<button class="book-toggle" data-index="${i}" aria-expanded="${bookChoice===i}">${bookChoice===i?'Скрыть':'Стакан'} ↕</button></div>${bookChoice===i?`<div class="book-details">${bookSide('YES · ДА',yes)}${bookSide('NO · НЕТ',no)}<p class="book-disclaimer">До 5 лучших ценовых уровней на каждой стороне. Количество — число контрактов, не число людей. YES и NO могут отражать общую ликвидность; не суммируйте их.</p></div>`:''}`
  }).join(''):'<div class="empty-state">Выберите или добавьте рынок</div>';
  $$('.book-toggle').forEach(btn=>btn.onclick=()=>{bookChoice=bookChoice===Number(btn.dataset.index)?null:Number(btn.dataset.index);renderMarket(marketData)});
  $('#book-status').textContent=state.demo?'Демо: реальные заявки стакана не отображаются.':bookMessage||'Стакан CLOB загружается отдельно';
  let pos=data?.positions||[];$('#positions-count').textContent=`Позиций бота: ${pos.length}`;for(const id of ['rename-market','unlink-market','delete-market'])$('#'+id).classList.toggle('hidden',state.demo||!state.market);
  $('#positions').innerHTML=pos.length?'<b>ПОЗИЦИИ ИЗ БАЗЫ БОТА</b>'+pos.map(p=>`<div class="position-row">${esc(p.is_demo?'ДЕМО · ':'')}${esc(p.question||p.outcome||'Позиция')} · ${esc(p.side)} · ${esc(p.size)} shares · вход ${esc(p.entry_price)} · SL ${esc(p.sl??'—')} / TP ${esc(p.tp??'—')}</div>`).join(''):'';
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
$('#refresh').onclick=()=>loadStations();$('#today-label').textContent=new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric'}).format(new Date()).toUpperCase();setInterval(updateClock,30000);setInterval(()=>{if(!document.hidden&&!state.demo)loadOrderBooks()},30000);setInterval(()=>{if(!document.hidden&&!state.demo)loadStation(true)},60000);$('#logout').onclick=async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});location.assign('/login')}catch(e){toast(e.message)}};api('/api/session').then(data=>{csrfToken=data.csrf;return loadSettings()}).then(()=>loadStations()).catch(e=>toast(e.message));
