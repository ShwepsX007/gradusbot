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
let toastTimer,weatherViewEpoch=0,weatherController=null,weatherLoadingSid=null,pageNavigationEpoch=0,quickOpenBusy=false;
function overviewActive(){return !document.hidden&&!$('#weather-page').classList.contains('hidden')&&!state.demo&&!!state.selected}
function stopViewRequests(){weatherViewEpoch++;if(weatherController)weatherController.abort();weatherController=null;weatherLoadingSid=null}
function resumeOverview(){return loadStation(state.weather?.station?.id===state.selected)}
function toast(message){$('#toast').textContent=message;$('#toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3500)}
async function api(url, options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken,...(options.headers||{})}});if(r.status===401){location.assign('/login');throw Error('Сессия завершена')}let data;try{data=await r.json()}catch{throw Error('Ошибка ответа сервера')}if(!r.ok){const e=Error(data.error||'Ошибка запроса');e.code=data.code;e.nonces=data.nonces;throw e}return data}
function page(name,refreshOnEnter=true,ackCopy=false){pageNavigationEpoch++;const wasWeather=!$('#weather-page').classList.contains('hidden');if(name!=='weather'&&wasWeather)stopViewRequests();$$('.page').forEach(x=>x.classList.add('hidden'));$(`#${name}-page`).classList.remove('hidden');$$('.nav').forEach(x=>x.classList.toggle('active',x.dataset.page===name));$('#crumb').textContent=({weather:'Обзор',stations:'Станции',markets:'Рынки',portfolio:'Портфель',copy:'Копирование',scalp:'Авто Скальп',settings:'Настройки'})[name];if(name==='stations')renderStations();if(name==='markets')renderAllMarkets();if(name==='portfolio')loadPortfolio();if(name==='copy')loadCopyObserver(ackCopy);if(name==='scalp')loadScalp();if(name==='settings'){loadSettings();loadSecurityInfo()}if(name==='weather'&&!wasWeather&&refreshOnEnter&&overviewActive())resumeOverview()}
$$('.nav').forEach(x=>x.onclick=()=>{if(x.dataset.page==='copy'){copyClickVersion++;ackCopyNotice();page(x.dataset.page,true,true)}else page(x.dataset.page)});
function showDialog(id){$(`#${id}`).showModal()}
$$('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
$('#add-station-top').onclick=$('#demo-add').onclick=$('.add-station').onclick=()=>showDialog('station-dialog');
$('#add-market').onclick=$('#markets-find').onclick=()=>{if(state.demo||!state.selected){toast('Сначала добавьте реальную станцию');showDialog('station-dialog');return}$('#search-results').innerHTML='';$('#market-dialog .dialog-error').textContent='';showDialog('market-dialog');searchStationMarkets()};
$('#station-form').onsubmit=async e=>{e.preventDefault();const f=e.currentTarget;try{const s=await api('/api/stations',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(f)))});f.reset();f.closest('dialog').close();await loadStations(s.id);page('weather');toast('Станция добавлена. Опрос обзора включается автоматически.')}catch(err){f.querySelector('.dialog-error').textContent=err.message}};
$('#station-toggle').onclick=async()=>{if(state.demo){toast('В демонстрации станция не изменяется');return}let s=state.stations.find(x=>x.id===state.selected);if(!s)return;if(!confirm('Изменить общую настройку станции для Telegram-бота? Если бот запущен, это может включить стратегии или остановить наблюдение. Автоопрос обзора от неё не зависит.'))return;try{await api(`/api/stations/${s.id}`,{method:'PATCH',body:JSON.stringify({enabled:!s.enabled})});await loadStations(s.id)}catch(e){toast(e.message)}};
async function loadStations(select){try{state.stations=await api('/api/stations');state.demo=state.stations.length===0;$('#demo-banner').classList.toggle('hidden',!state.demo);if(state.demo){stopViewRequests();state.selected=0;state.weather=demoWeather;state.bindings=demoBindings;state.market='demo';renderAiPanel();renderAll();return}state.selected=state.stations.some(x=>x.id===(select??state.selected))?(select??state.selected):state.stations[0].id;renderStationTabs();await loadStation()}catch(e){toast(e.message)}}
function renderStationPicker(){
  const list=state.demo?[demoStation]:state.stations,query=$('#station-picker-search').value.trim().toLocaleLowerCase();
  const matches=list.filter(s=>`${s.name||''} ${s.station_code||''}`.toLocaleLowerCase().includes(query));
  $('#station-picker-list').innerHTML=matches.length?matches.map(s=>`<button type="button" class="station-picker-item ${s.id===state.selected?'active':''}" data-id="${s.id}" ${s.id===state.selected?'aria-current="true"':''}><span>${esc(s.name)}</span><small>${esc(s.station_code)}${state.demo?' · ДЕМО':''}</small></button>`).join(''):'<div class="station-picker-empty">Станции не найдены</div>';
  $$('.station-picker-item').forEach(btn=>btn.onclick=()=>{const id=Number(btn.dataset.id);$('#station-picker').open=false;$('#station-picker-search').value='';if(state.demo||id===state.selected){renderStationPicker();return}state.selected=id;renderStationTabs();loadStation()});
}
function renderStationTabs(){const list=state.demo?[demoStation]:state.stations;$('#station-count').textContent=state.demo?'01':String(list.length).padStart(2,'0');$('#station-tabs').innerHTML=list.map(s=>`<button class="station-tab ${s.id===state.selected?'active':''}" data-id="${s.id}"><i class="tab-dot"></i>${esc(s.name)} <span style="opacity:.5;margin-left:7px">${esc(s.station_code)}</span></button>`).join('');$$('.station-tab').forEach(b=>b.onclick=async()=>{state.selected=Number(b.dataset.id);renderStationTabs();await loadStation()});renderStationPicker()}
$('#station-picker-search').addEventListener('input',renderStationPicker);
$('#station-picker').addEventListener('toggle',()=>{if($('#station-picker').open)$('#station-picker-search').focus()});
document.addEventListener('click',event=>{if(!$('#station-picker').contains(event.target))$('#station-picker').open=false});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('#station-picker').open){$('#station-picker').open=false;$('#station-picker summary').focus()}});

async function loadStation(background=false){
  if(!overviewActive())return;
  const sid=state.selected;
  if(background&&weatherController&&weatherLoadingSid===sid)return;
  stopViewRequests();const seq=weatherViewEpoch,controller=new AbortController();weatherController=controller;weatherLoadingSid=sid;
  if(state.arcStation!==sid){state.arcStation=sid;state.arcOffset=0}
  const previousMarket=state.market;
  if(!background){state.market=null;state.weather=null;state.bindings=[];renderWeather();renderAiPanel();if(aiTunnel&&!state.demo)loadAiLatest(sid);renderMarketTabs();renderMarket(null)}
  try{
    const [w,b]=await Promise.all([api(`/api/stations/${sid}/weather?view=1`,{signal:controller.signal}),api(`/api/stations/${sid}/bindings`,{signal:controller.signal})]);
    if(seq!==weatherViewEpoch||!overviewActive()||state.selected!==sid||w.station.id!==sid)return;
    const stationIndex=state.stations.findIndex(x=>x.id===sid);if(stationIndex>=0)state.stations[stationIndex]={...state.stations[stationIndex],...w.station};
    state.weather=w;state.bindings=b;const chosen=state.market||previousMarket;state.market=b.some(x=>x.market_slug===chosen)?chosen:(b[0]?.market_slug||null);renderWeather();renderMarketTabs();await loadMarket();
  }catch(e){if(seq===weatherViewEpoch&&e.name!=='AbortError')toast(e.message)}
  finally{if(seq===weatherViewEpoch){weatherController=null;weatherLoadingSid=null}}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden){stopViewRequests();renderWeather()}else if(overviewActive())resumeOverview()});

function fmt(v,unit=''){return v===null||v===undefined||v===''?'—':`${esc(v)}${unit}`}
function tempNumber(v){return Number.isFinite(Number(v))&&v!==null&&v!==undefined&&v!==''?Number(v):null}
function prettyNumber(n){return String(Math.round((n+Number.EPSILON)*10)/10)}
function tempText(v){let c=tempNumber(v);if(c===null)return '—';let f=prettyNumber(c*9/5+32),cc=prettyNumber(c);return state.units==='F'?`${f}°F`:state.units==='B'?`${cc}°C / ${f}°F`:`${cc}°C`}
function tempMarker(v,x){let c=tempNumber(v);if(c===null)return '—';if(state.units==='B')return `<tspan x="${x}" dy="-2">${prettyNumber(c)}°C</tspan><tspan x="${x}" dy="6">${prettyNumber(c*9/5+32)}°F</tspan>`;return tempText(c)}
function timePart(v){return v?String(v).slice(11,16):'—'}
function weatherExtrema(w,day){return w.forecast_ensemble?WeatherView.ensembleExtremaForDay(w.forecast_ensemble,day):w.forecast_status?null:WeatherView.hourlyExtremaForDay(w.forecast||{},day)}
function renderWeather(){let w=state.weather||{},s=w.station||(state.demo?demoStation:state.stations.find(x=>x.id===state.selected))||{},m=w.metar||{},f=w.forecast||{},d=f.daily||{},h=f.hourly||{};$('#station-code').textContent=s.station_code||'—';$('#station-status').textContent=state.demo?'ДЕМО':overviewActive()?'АВТООПРОС ОБЗОРА':'ОПРОС ОБЗОРА НА ПАУЗЕ';$('#station-name').textContent=s.name||'Выберите станцию';$('#station-location').textContent=(w.info?.name||w.info?.site||'Метеостанция')+' · '+(w.forecast_ensemble?.timezone||f.timezone||(w.solar_days||[]).find(x=>x.timezone)?.timezone||'часовой пояс недоступен');$('#station-toggle').classList.toggle('on',!!s.enabled);$('#station-toggle').setAttribute('aria-pressed',String(!!s.enabled));$('#bot-poll-label').textContent=state.demo?'БОТ: ДЕМО':s.enabled?'ОПРОС БОТА: РАЗРЕШЁН':'ОПРОС БОТА: ЗАПРЕЩЁН';$('#weather-pill').innerHTML=state.demo?'ПРИМЕР ДАННЫХ':overviewActive()?'<i></i> АВТООПРОС':'ОПРОС НА ПАУЗЕ';$('#temperature').textContent=tempText(m.temp);$('#temperature').classList.toggle('temperature-dual',state.units==='B');$('#condition').textContent=m.cloud?.description||w.message||'Нет данных';$('#humidity').textContent=fmt(m.humidity,'%');$('#wind').textContent=m.wind_speed!=null?`${m.wind_speed} узл. / ${m.wind_ms!=null?prettyNumber(m.wind_ms)+' м/с':'— м/с'}`:'—';$('#wind-direction').textContent=m.wind_dir==null?'Направление —':String(m.wind_dir).toUpperCase()==='VRB'?'Направление переменное':`Направление ${m.wind_dir}°`;$('#cloud').textContent=m.cloud?.description||'—';$('#cloud-code').textContent=m.cloud?.code?'METAR · '+m.cloud.code:'METAR';renderPrecipitation(m,f);$('#raw-metar').textContent=m.raw_metar||w.message||'Нет данных';$('#sunrise').textContent=timePart(d.sunrise?.[0]);$('#sunset').textContent=timePart(d.sunset?.[0]);let today=localDate(w.forecast_ensemble?.timezone||f.timezone||(w.solar_days||[]).find(x=>x.timezone)?.timezone), dates=d.time||d.sunrise?.map(x=>x.slice(0,10))||[], idx=dates.indexOf(today);if(!today)idx=0;state.dayIndex=idx;today=idx>=0?dates[idx]:today;$('#sunrise').textContent=timePart(d.sunrise?.[idx]);$('#sunset').textContent=timePart(d.sunset?.[idx]);let extrema=weatherExtrema(w,today),max=extrema?.max,min=extrema?.min;$('#forecast-max').textContent=tempText(max?.temp??(!w.forecast_status?d.temperature_2m_max?.[idx]:null));$('#forecast-min').textContent=tempText(min?.temp??(!w.forecast_status?d.temperature_2m_min?.[idx]:null));$('#forecast-max-time').textContent=max?'около '+max.time:'—';$('#forecast-min-time').textContent=min?'около '+min.time:'—';drawChart(state.demo?(w.history||[]):(w.metar_history||[]),w.forecast_ensemble?.timezone||f.timezone||(w.solar_days||[]).find(x=>x.timezone)?.timezone);renderForecastStatus(w,extrema);renderMetarArchive(w);renderForecastComparison(w);updateClock()}
function renderPrecipitation(m,f){
  $('#precip-observed').textContent=m.precipitation?.description||'Нет данных';
  let stamp=Number(m.obs_time),obsTime='Последняя сводка станции';
  if(m.obs_time!=null&&Number.isFinite(stamp)&&stamp>0){try{obsTime='METAR в '+new Intl.DateTimeFormat('ru-RU',{timeZone:f.timezone||'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(stamp*1000))+(f.timezone?' (местное время)':' UTC')}catch{obsTime='Время сводки недоступно'}}
  $('#precip-observed-time').textContent=obsTime;
  let p=precipitationForecast(f),el=$('#precip-probability');
  el.textContent=p?`${p.probability}%${p.probability>=60?' · высокая':''}`:'Нет данных';el.classList.toggle('risk-high',!!p&&p.probability>=60);
  $('#precip-period').textContent=p?`Интервал ${p.start}–${p.end} · местное время`:'Почасовой прогноз недоступен';
}
let chartState={points:[],selected:null,timezone:'UTC',isUTC:true,hours:168};
function chartNumber(n){return String(Math.round((n+Number.EPSILON)*100)/100)}
function chartTemp(c){if(!Number.isFinite(c))return '—';let a=chartNumber(c)+'°C',b=chartNumber(c*9/5+32)+'°F';return state.units==='F'?b:state.units==='B'?a+' / '+b:a}
function chartTime(ms,detail=false){
  if(!Number.isFinite(ms))return '—';
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
  const chart=WeatherView.metarChart(history,state.units,Date.now(),chartState.hours),pts=chart.points;chartState.points=pts;chartState.start=chart.start;chartState.end=chart.end;
  $('#chart-min-label').textContent=`МИНИМУМ ЗА ${chartState.hours===168?'7 ДНЕЙ':chartState.hours+' Ч'}`;$('#chart-max-label').textContent=`МАКСИМУМ ЗА ${chartState.hours===168?'7 ДНЕЙ':chartState.hours+' Ч'}`;
  $('.chart-inner').style.width=(chartState.hours*120)+'px';
  $$('.chart-range').forEach(b=>{const active=Number(b.dataset.hours)===chartState.hours;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))});
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
function renderForecastStatus(w,extrema){
  const s=w?.forecast_status,el=$('#forecast-source-status');
  if(state.demo){el.textContent='Демонстрационный прогноз';return}
  if(!s){el.textContent=state.weather?'Сведения об источниках недоступны':'Загрузка прогноза…';return}
  if(s.reason==='disabled'){el.textContent='Включите станцию для загрузки прогноза; архив METAR доступен отдельно.';return}
  if(s.reason==='no_coordinates'){el.textContent='Не удалось получить координаты станции; прогноз недоступен.';return}
  if(s.reason==='unsupported'||s.reason==='invalid_code'){el.textContent='Прогноз доступен для станции METAR с корректным ICAO-кодом.';return}
  const om=s.open_meteo==='ok'?'доступен':'недоступен',mn=s.met_norway==='ok'?'доступен':s.met_norway==='throttled'?'ограничен':s.met_norway==='loading'?'загружается':'недоступен';
  const sourceText=p=>Object.entries(p?.sources||{}).map(([name,value])=>name+' '+tempText(value)).join(' + ')||'нет данных';
  const details=extrema?` · минимум: ${sourceText(extrema.min)}; максимум: ${sourceText(extrema.max)}`:' · нет температур за текущую местную дату';
  el.textContent=`Open-Meteo: ${om}`+(s.open_meteo_requested_at?` (запрошен ${chartTime(s.open_meteo_requested_at*1000,true)})`:'')+` · MET Norway: ${mn}`+(s.met_norway_fetched_at?` (получен ${chartTime(s.met_norway_fetched_at*1000,true)}${s.met_norway_updated_at&&Number.isFinite(Date.parse(s.met_norway_updated_at))?' · модель '+chartTime(Date.parse(s.met_norway_updated_at),true):''})`:'')+(s.reason==='no_timezone'?' · часовой пояс станции неизвестен, минимум и максимум не рассчитаны':details)+'. По доступным часам; источники могут отсутствовать для части суток.';
}
function renderMetarArchive(w){
  const archive=w?.metar_archive,reports=Array.isArray(archive?.reports)?archive.reports:[];
  const messages={ok:'Архив получен',partial:'Архив загружен частично; повторная попытка позже',empty:'Источник не вернул сводки',error:'Архив временно недоступен; показаны сохранённые данные',throttled:'Архив временно отложен из-за ограничений источника',loading:'Архив загружается',not_loaded:'Архив ещё не загружен',unsupported:'Архив доступен только для METAR-станций с ICAO'};
  const updated=archive?.last_success?` · обновлён ${chartTime(Number(archive.last_success)*1000,true)}`:'';
  $('#metar-archive-status').textContent=(state.demo?'Демонстрационные данные':!state.weather?'Загрузка погоды и архива':messages[archive?.status]||'Архив ещё не загружен')+updated+'. Доступность и полнота архива не гарантируются; возможны пропуски.';
  $('#metar-archive-count').textContent=reports.length?`· ${reports.length}`:'';
  $('#metar-archive-reports').innerHTML=reports.length?reports.map(r=>{
    const speed=Number(r.wind_speed),hasSpeed=r.wind_speed!==null&&r.wind_speed!==undefined&&Number.isFinite(speed)&&speed>=0;
    const wind=hasSpeed?`${esc(shares(speed))} узл. / ${esc(shares(speed*0.514444))} м/с`:'—';
    const direction=r.wind_dir==null?'—':String(r.wind_dir).toUpperCase()==='VRB'?'переменное':`${esc(r.wind_dir)}°`;
    const gust=r.wind_gust==null?'':` · порывы ${esc(shares(r.wind_gust))} узл.`;
    return `<div class="metar-report"><b>${esc(chartTime(Number(r.obs_time)*1000,true))} · ${tempText(r.temp)}</b><span>Ветер: ${wind} · направление ${direction}${gust} · точка росы ${r.dew_point==null?'—':tempText(r.dew_point)}</span><code>${esc(r.raw||'Исходная строка недоступна')}</code></div>`;
  }).join(''):'Нет загруженных архивных сводок.';
}
$$('.chart-range').forEach(button=>button.onclick=()=>{
  const hours=Number(button.dataset.hours);if(![24,48,168].includes(hours)||hours===chartState.hours)return;
  chartState.hours=hours;chartState.drawn=false;renderWeather();
});
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
    return `<g class="arc-extreme ${kind}" tabindex="0" aria-label="${source} ${label}: ${esc(tempText(p.temp))}, ${esc(p.time)}"><title>${source} ${label}: ${esc(tempText(p.temp))}, ${esc(p.time)} · ${esc(Object.keys(p.sources||{}).join(' + ')||'Open-Meteo')}${p.interpolated?' (интерполяция)':''}, почасовая модель</title><line x1="${x}" y1="${y}" x2="${labelX}" y2="${kind==='min'?labelY-8:labelY+5}"/><circle cx="${x}" cy="${y}" r="${kind==='min'?5.5:3.7}"/><text x="${labelX}" y="${labelY}" text-anchor="middle">${label}</text></g>`;
  }).join('');
  return marks;
}
function renderHourlyStrip(w,now=Date.now()){
  const list=$('#hourly-strip-list'),scrollLeft=list.scrollLeft,points=Array.isArray(w?.forecast_ensemble?.points)?w.forecast_ensemble.points:[];
  $('#hourly-zone').textContent=(chartState.isUTC?'UTC (пояс станции неизвестен)':'Местное время станции')+' · ближайшие часы независимо от выбранной даты · прокрутите →';
  const future=points.filter(p=>Number.isFinite(p.time)&&p.time*1000>=now&&Number.isFinite(p.temp)).slice(0,48);
  list.replaceChildren();
  if(!future.length){list.textContent='Почасовой прогноз недоступен.';return}
  for(const p of future){
    const item=document.createElement('div'),time=document.createElement('span'),average=document.createElement('b'),sources=document.createElement('small');item.className='hourly-strip-item';
    time.textContent=chartTime(p.time*1000,true);average.textContent=tempText(p.temp);
    const src=p.sources||{};sources.textContent=['Open-Meteo','MET Norway'].map(name=>`${name==='Open-Meteo'?'OM':'MET'}: ${Number.isFinite(src[name])?tempText(src[name]):'—'}`).join(' · ');
    item.title=`Среднее: ${tempText(p.temp)}; ${sources.textContent}${p.interpolated?' · есть интерполяция':''}`;
    item.append(time,average,sources);list.append(item);
  }
  list.scrollLeft=scrollLeft;
}
function deltaText(value,units=state.units){
  if(!Number.isFinite(value))return '—';
  const format=n=>{const rounded=Number(prettyNumber(n));return `${rounded>0?'+':''}${rounded}`};
  return units==='F'?`${format(value*1.8)}°F`:units==='B'?`${format(value)}°C / ${format(value*1.8)}°F`:`${format(value)}°C`;
}
function renderForecastComparison(w){
  const box=$('#forecast-comparison'),c=w?.forecast_comparison;box.replaceChildren();
  const heading=document.createElement('strong');heading.textContent='ПОСЛЕДНЯЯ СВОДКА METAR − СОХРАНЁННЫЙ ПРОГНОЗ';box.append(heading);
  const note=document.createElement('div');note.className='comparison-note';
  note.textContent=c?`Сводка ${chartTime(c.obs_time*1000,true)} · факт ${tempText(c.actual_temp)}. Плюс означает теплее прогноза.`:'Нет свежей сводки METAR с достоверным временем наблюдения.';box.append(note);
  for(const [key,label] of [['latest','Последний подходящий до сводки'],['three_hours','Не позже чем за 3 часа']]){
    const row=document.createElement('div');row.className='comparison-row';const title=document.createElement('span'),detail=document.createElement('span');title.textContent=label;
    const value=c?.[key];detail.textContent=value?`${deltaText(value.difference)} · прогноз ${tempText(value.forecast_temp)} · снимок ${chartTime(value.captured_at*1000,true)} · ${Object.keys(value.sources||{}).join(' + ')}${value.interpolated?' · интерполяция':''}`:'Нет сохранённого подходящего прогноза';row.append(title,detail);box.append(row);
  }
  const hint=document.createElement('small');hint.textContent=(w?.forecast_history_status==='error'?'Хранилище прогнозов временно недоступно. ':'')+'Снимки сохраняются во время просмотра, не чаще раза в 5 минут; для сравнения нужны прогнозы, сохранённые до сводки. История до установки недоступна.';box.append(hint);
}
function updateClock(){
  const w=state.weather||{},f=w.forecast||{},now=Date.now(),tz=w.forecast_ensemble?.timezone||f.timezone||(w.solar_days||[]).find(x=>x.timezone)?.timezone;
  const local=localClock(now,tz), day=local?shiftDay(local.day,state.arcOffset):null, solar=day?displayedSolarDay(w,day):null,current=state.arcOffset===0;
  renderHourlyStrip(w,now);
  $('#arc-prev').disabled=!local||state.arcOffset<=-6;$('#arc-next').disabled=!local||state.arcOffset>=0;
  $('#arc-day-counter').textContent=`${1-state.arcOffset} / 7`;
  $('#arc-date').textContent=day?(current?'Сегодня · ':'')+dateLabel(day):'Дата недоступна';
  $('#sunrise').textContent=timePart(solar?.sunrise);$('#sunset').textContent=timePart(solar?.sunset);
  $('#local-time-label').textContent=current?'МЕСТНОЕ ВРЕМЯ':'ВЫБРАННАЯ ДАТА';
  $('#local-time').textContent=current?(local?.time||'—'):(day?day.split('-').reverse().join('.'):'—');
  const sunrise=solarSeconds(solar?.sunrise), sunset=solarSeconds(solar?.sunset), path=dailyPath(sunrise,sunset);
  const extrema=weatherExtrema(w,day),marks=renderArcExtrema(extrema,sunrise,sunset,current);
  const todaySolar=local?displayedSolarDay(w,local.day):null,todayRise=solarSeconds(todaySolar?.sunrise),todaySet=solarSeconds(todaySolar?.sunset);
  const isDay=local&&todayRise!==null&&todaySet!==null&&todayRise<todaySet?local.seconds>=todayRise&&local.seconds<todaySet:null;
  renderArcWeather(w,current,isDay,tz);
  const marker=$('#sun-marker');$('#arc-observations').innerHTML='';
  $('#arc-full').setAttribute('d',path||'');$('#arc-progress').setAttribute('d',path||'');
  if(!path||!local){
    marker.classList.add('hidden');$('#arc-passed-rect').setAttribute('width',0);$('#arc-observations').ownerSVGElement.setAttribute('viewBox','0 0 800 220');
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
  const observations=observationsForDay(w.history||[],tz,day,now);
  const points=observations.map(item=>{
    const p=curvePoint(item.seconds,sunrise,sunset);if(!p)return null;
    const direction=item.trend==='up'?'↑':item.trend==='down'?'↓':'';
    return {item,p,direction};
  }).filter(Boolean);
  // Keep labels above their arrows and out of the way of markers and each other.
  const obstacles=points.map(({p})=>({left:p.x-3,right:p.x+3,top:p.y-3,bottom:p.y+3}));
  if(pt)obstacles.push({left:pt.x-16,right:pt.x+16,top:pt.y-16,bottom:pt.y+16});
  for(const mark of marks){
    const {x,y}=mark.point,labelX=Math.max(70,Math.min(730,x)),labelY=mark.kind==='min'?Math.min(213,y+29):Math.max(14,y-29);
    obstacles.push({left:x-7,right:x+7,top:y-7,bottom:y+7},{left:labelX-23,right:labelX+23,top:labelY-13,bottom:labelY+4});
  }
  const arrows=WeatherView.layoutArcArrows(points.filter(p=>p.direction).map(({p})=>({x:p.x,pointY:p.y})),obstacles);
  let arrowIndex=0;for(const point of points)if(point.direction)point.arrowY=arrows[arrowIndex++].y;
  const labels=WeatherView.layoutArcTemperatures(points.filter(p=>p.direction).map(({item,p,arrowY})=>({x:p.x,arrowY,celsius:prettyNumber(item.temp)+'°C',fahrenheit:prettyNumber(item.temp*9/5+32)+'°F'})),[...obstacles,...arrows.map(a=>a.box)]);
  // Expand upward, rather than clipping densely packed labels at the top.
  const minY=labels.length?Math.min(0,Math.floor(Math.min(...labels.map(l=>l.box.top))-5)):0;
  $('#arc-observations').ownerSVGElement.setAttribute('viewBox',`0 ${minY} 800 ${220-minY}`);
  const lines=labels.map(l=>l.arrowY-l.box.bottom>12?`<line class="arc-temp-leader" x1="${l.x}" y1="${l.box.bottom+1}" x2="${l.x}" y2="${l.arrowY-11}"/>`:'').join('')+arrows.map(a=>a.pointY-a.y>7?`<line class="arc-temp-leader" x1="${a.x}" y1="${a.y+1}" x2="${a.x}" y2="${a.pointY-3}"/>`:'').join('');
  const symbols=points.map(({item,p,direction,arrowY})=>{
    const trendNote=item.trend==='up'?' · выше предыдущего наблюдения':item.trend==='down'?' · ниже предыдущего наблюдения':'';
    const arrow=direction?`<text class="arc-trend ${item.trend}" x="${p.x}" y="${arrowY}" text-anchor="middle" aria-hidden="true">${direction}</text>`:'';
    return `<g class="arc-observation"><title>${esc(item.time)} · ${esc(tempText(item.temp)+trendNote)}</title><circle cx="${p.x}" cy="${p.y}" r="2.4"/>${arrow}</g>`;
  }).join('');
  const temperatures=labels.map(l=>`<text class="arc-temp-label" x="${l.x}" y="${l.box.bottom-12}" text-anchor="middle"><tspan x="${l.x}">${esc(l.celsius)}</tspan><tspan x="${l.x}" dy="10">${esc(l.fahrenheit)}</tspan></text>`).join('');
  $('#arc-observations').innerHTML=lines+symbols+temperatures;
  const modelNote=extrema?`Цветные точки — ${current?'средний прогноз по доступным источникам':'данные модели за дату'} по доступным часам (${extrema.count})`:'Почасовые данные модели недоступны';
  $('#arc-note').textContent=`Сохранено наблюдений: ${observations.length}. Стрелки показывают изменение относительно предыдущего сохранённого наблюдения, в том числе за прошлый день. Над каждой стрелкой — °C и °F. На узком экране дугу можно прокрутить по горизонтали. ${modelNote}${current?'; на светиле — последний METAR':''}. Не гарантия результата рынка.`;
}

$('#arc-prev').onclick=()=>{if(state.arcOffset>-6){state.arcOffset--;updateClock()}};
$('#arc-next').onclick=()=>{if(state.arcOffset<0){state.arcOffset++;updateClock()}};
function renderMarketTabs(){
  const b=state.bindings, available=Array.isArray(accountSnapshot?.positions), stale=available&&Date.now()-accountSnapshotAt>65000;
  const slugs=new Set(available?accountSnapshot.positions.filter(p=>typeof p.event_slug==='string'&&p.event_slug&&p.size!=null&&Number.isFinite(Number(p.size))&&Number(p.size)>0).map(p=>p.event_slug):[]);
  $('#market-count').textContent=String(b.length).padStart(2,'0');
  $('#market-position-note').textContent=available?`● Позиция · первая страница Data API, ${new Date(accountSnapshotAt).toLocaleString('ru-RU')}${stale?' · УСТАРЕЛА':''}${accountSnapshot.positions_maybe_partial?' · НЕПОЛНАЯ':''}. Отсутствие отметки не доказывает отсутствие позиции.`:'Позиции не загружены; отсутствие отметки не означает отсутствие позиции. Для сверки нужен SSH-туннель.';
  $('#market-tabs').innerHTML=b.map(x=>{const held=slugs.has(x.market_slug),note=held?`Есть позиция · снимок ${new Date(accountSnapshotAt).toLocaleTimeString('ru-RU')}${stale?' · устарел':''}${accountSnapshot.positions_maybe_partial?' · неполный':''}`:available?'Позиция не найдена в снимке; данные могут запаздывать или быть неполными':'Позиции не загружены';return `<button class="market-tab ${x.market_slug===state.market?'active':''} ${held?'has-position':''} ${held&&stale?'position-stale':''}" title="${esc(note)}" data-slug="${esc(x.market_slug)}">${held?'<span aria-hidden="true">● </span>':''}${esc(x.market_name)}${held?'<span class="position-tab-label"> · позиция'+(stale?' (устарело)':'')+'</span>':''}</button>`}).join('');
  $$('.market-tab').forEach(btn=>btn.onclick=()=>{state.market=btn.dataset.slug;renderMarketTabs();loadMarket()});
}
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

function updateTradingControls(){const enabled=tradeEnabled&&!state.demo;$('#trade-submit').disabled=!enabled||tradeSending;$('#trade-submit').textContent=tradeSending?'Отправляем…':enabled?'Отправить реальный ордер':'Торговля выключена';const quick=$('.book-trade-submit');if(quick){quick.disabled=!enabled||tradeSending;quick.textContent=tradeSending?'Отправляем…':enabled?'Отправить реальный ордер':'Торговля выключена'}$('.trade-preview-badge').textContent=enabled?'РЕАЛЬНАЯ ТОРГОВЛЯ ВКЛЮЧЕНА':'РЕАЛЬНАЯ ТОРГОВЛЯ ВЫКЛЮЧЕНА';$('.readonly').innerHTML=enabled?'<i></i> РЕАЛЬНАЯ ТОРГОВЛЯ':'<i></i> ТОЛЬКО НАБЛЮДЕНИЕ';}
function renderTradeAccount(){
  const balance=$('#trade-live-balance'),updated=$('#trade-live-updated'),positions=$('#trade-live-positions');
  renderTradeOrders();
  if(!accountSnapshot){balance.textContent='Не загружен';updated.textContent='Нет свежего снимка аккаунта.';positions.textContent='Позиции недоступны. Это не означает, что их нет.';return}
  const data=accountSnapshot,age=Math.max(0,Date.now()-accountSnapshotAt),stale=age>65000;
  balance.textContent=data.collateral_balance==null?'Недоступен':`${data.collateral_balance} pUSD`;
  updated.textContent=`Снимок: ${new Date(accountSnapshotAt).toLocaleTimeString('ru-RU')} · ${accountSource||'сверка'}${stale?' · УСТАРЕЛ':''} · доступный остаток может быть меньше`;
  const opts=marketData?.market?.options||[],pairs=bookData?.pairs||[],tokens=new Map(),confirmed=confirmedMarketTokens();
  opts.forEach(o=>{const pair=pairs.find(p=>p.yes===o.token_yes&&p.no===o.token_no);if(pair){tokens.set(pair.yes,{label:o.label,side:'YES',book:bookFor(o,'yes')});tokens.set(pair.no,{label:o.label,side:'NO',book:bookFor(o,'no')})}});
  const ps=Array.isArray(data.positions)?data.positions.filter(p=>p.event_slug===state.market||tokens.has(p.asset)):null;
  if(!ps){positions.textContent='Позиции недоступны. Это не означает, что их нет.';return}
  if(!ps.length){positions.textContent=data.positions_maybe_partial?'В полученной странице позиция не найдена; снимок неполный.':'В полученном снимке нет позиций по этому событию.';return}
  positions.innerHTML=ps.map((p,i)=>{
    const t=tokens.get(p.asset),bid=t?.book?.bids?.[0],title=t?`${t.label} · ${t.side}`:`${p.title||'Позиция'} · ${p.outcome||'исход'}`;
    const action=confirmed.has(p.asset)&&Number.isFinite(Number(p.size))&&Number(p.size)>0?`<button type="button" class="button secondary trade-sell-shares" data-index="${i}">Продать доли</button>`:'';
    return `<div class="trade-live-position"><b>${esc(title)}</b><span>${esc(shares(p.size))} контрактов · вход ${p.avg_price==null?'—':esc(priceCents(p.avg_price))} · цена Data API ${p.current_price==null?'—':esc(priceCents(p.current_price))} · BID ${bid?esc(priceCents(bid.price)):'—'} · PnL ${p.cash_pnl==null?'—':esc(shares(p.cash_pnl))+' USDC'}</span>${action}</div>`;
  }).join('')+(data.positions_maybe_partial?'<small>Список позиций неполный.</small>':'');
  positions.querySelectorAll('.trade-sell-shares').forEach(button=>button.onclick=()=>preparePositionSale(ps[Number(button.dataset.index)]));
}
function renderAccountSnapshot(refreshBook=true){
  const status=$('#account-status'),positions=$('#account-positions'),orders=$('#account-orders');
  renderMarketTabs();
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
  $$('.account-close-draft').forEach(button=>button.onclick=()=>preparePositionSale(ps[Number(button.dataset.index)]));

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
    $('#trade-monitor-status').textContent=`Фоновое наблюдение: ${labels[result.status]||'недоступно'}${when}${interval}. Только чтение. Выставленные ордера исполняет биржа.`;
    if(!accountSnapshot&&result.snapshot&&['ok','partial'].includes(result.status)){
      accountSnapshot=result.snapshot;accountSnapshotAt=result.checked_at*1000;accountSource='фоновый монитор';renderAccountSnapshot();
    }
  }catch(e){if(generation===accountGeneration)$('#trade-monitor-status').textContent='Фоновое наблюдение недоступно. Выставленные ордера остаются на бирже.'}
}


async function loadTradeJournal(){
  try{
    const slug=state.market;if(!slug){$('#trade-journal').innerHTML='';return}
    const data=await api('/api/trading/intents?slug='+encodeURIComponent(slug));if(state.market!==slug)return;
    const auto=await api('/api/trading/auto-sell').catch(()=>null);if(state.market!==slug)return;
    const area=$('#auto-sell-status');if(area){
      const plans=(auto?.plans||[]).filter(p=>p.slug===slug),stateLabel={active:'Активно',paused:'Пауза',halt:'Проверить вручную',done:'Завершено'};
      area.innerHTML=`<div class="account-list-title">АВТОПРОДАЖА · ${auto?.running?'СЛУЖБА РАБОТАЕТ':'СЛУЖБА НЕДОСТУПНА'}</div>`+plans.map(p=>`<div class="account-item"><b>+${esc(priceCents(p.offset))} · ${esc(stateLabel[p.state]||p.state)}</b><span>${esc(p.reason||'Ожидаем сверку')}</span>${['paused','active'].includes(p.state)?`<button type="button" class="button secondary auto-sell-control" data-command="${p.state==='paused'?'resume':'pause'}" data-nonce="${esc(p.buy_nonce)}">${p.state==='paused'?'Возобновить':'Пауза'}</button>`:''}</div>`).join('');
      area.querySelectorAll('.auto-sell-control').forEach(b=>b.onclick=async()=>{const cmd=b.dataset.command;if(!confirm(cmd==='resume'?'Возобновить автоматическую продажу по этому плану?':'Приостановить автопродажу? Уже выставленный SELL останется на бирже.'))return;try{await api('/api/trading/auto-sell/'+cmd,{method:'POST',body:JSON.stringify({nonce:b.dataset.nonce,confirm:true})});await loadTradeJournal()}catch(e){toast(e.message)}})
    }
    const rows=(data.intents||[]).filter(x=>x.slug===slug);
    $('#trade-journal').innerHTML=rows.length?'<div class="account-list-title">ЖУРНАЛ ОТПРАВОК И СВЕРКА СДЕЛОК</div>'+rows.map(x=>{
      const state=x.state==='accepted'?'Подтверждён ордер CLOB':x.state==='not_sent'?'Не отправлен':'РЕЗУЛЬТАТ НЕИЗВЕСТЕН — не повторяйте';
      const r=x.reconciliation;
      const ex=x.exchange?.available?` · CLOB: ${x.exchange.status}, сопоставлено ${x.exchange.size_matched} из ${x.exchange.original_size} шт. (не равно окончательному исполнению)`:x.exchange?' · Статус CLOB не получен':'';
      const info=r?.size_matched!=null?` Сверка: ${r.order_status}, сопоставлено ${r.size_matched}; подтверждено ${r.confirmed_size}, ошибка ${r.failed_size} шт.`:'';
      const reason=r?.reason?` ${r.reason}`:'';
      const id=x.order_id?` ID ${x.order_id}`:r?.expected_id?` Расчётный ID ${r.expected_id} (не доказательство отправки)`:` Запись ${x.nonce}`;
      return `<div class="account-item"><b>${esc(x.side)} · ${esc(x.price)} pUSD · ${esc(x.size)} ${x.execution_mode==='market'&&x.side==='BUY'?'pUSD':'долей'} · ${esc(x.order_type)}</b><span>${esc(state+ex+info+reason+id)}</span></div>`;
    }).join(''):'';
  }catch(e){renderTradeOrders();$('#trade-journal').textContent='Журнал отправок недоступен.'}
}
function newTradeNonce(){const a=new Uint8Array(16);crypto.getRandomValues(a);return Array.from(a,x=>x.toString(16).padStart(2,'0')).join('')}
async function submitTrade(draft,quickKey=null){
  if(!tradeEnabled||tradeSending||state.demo)return;
  const d={...draft},slug=state.market,generation=accountGeneration,o=marketData?.market?.options?.[Number(d.index)];
  const report=message=>{if(generation!==accountGeneration)return;if(quickKey&&bookTradeDrafts.get(quickKey)===draft)draft.status=message;if(state.market===slug){$('#trade-send-status').textContent=message;const panel=$('.book-trade');if(panel&&panel.dataset.key===quickKey)panel.querySelector('.book-trade-status').textContent=message}};
  if(d.index===''||!o||!bookFor(o,d.token)||(quickKey&&(!confirmedMarketTokens().has(d.token==='yes'?o.token_yes:o.token_no)||quickKey!==bookTradeKey(o)))){toast('Выберите подтверждённый исход с доступным стаканом.');return}
  const price=Number(d.price),size=Number(d.size),asset=d.token==='yes'?o.token_yes:o.token_no;
  if(!Number.isFinite(price)||price<=0||price>=100||!Number.isFinite(size)||size<=0){toast('Укажите допустимую цену и количество.');return}
  const auto=quickKey&&d.action==='BUY'&&d.auto_sell_cents!==''?Number(d.auto_sell_cents):null;
  if(auto!==null&&(!Number.isFinite(auto)||auto<=0||auto>=100||Math.abs(auto*100-Math.round(auto*100))>1e-8)){toast('Укажите автопродажу от 0 до 100 центов с точностью 0,01.');return}
  const isMarket=d.type==='FAK',unit=isMarket&&d.action==='BUY'?'pUSD':'долей';
  const estimate=estimateForDraft(d);
  const message=`РЕАЛЬНЫЙ ОРДЕР: ${state.market} · ${o.label} · ${d.token.toUpperCase()} · ${d.action} · ${isMarket?'РЫНОЧНЫЙ FAK':'ЛИМИТНЫЙ GTC'} · ${isMarket?(d.action==='BUY'?'максимум':'минимум')+' ':''}${d.price}¢ · ${d.size} ${unit}. ${estimate} Комиссии могут изменить итоговую сумму. ${isMarket?'Неисполненный остаток отменяется.':'Ордер останется на бирже до исполнения или отмены.'} Отправить?`;
  if(!confirm(message+(auto!==null?` После подтверждения покупки разрешается АВТОМАТИЧЕСКАЯ ПРОДАЖА: средний вход + ${auto}¢. Служба может отменять и заменять SELL при изменении объёма; выключение торговли ставит процесс на паузу.`:'')))return;
  const body={slug:state.market,asset,selection:d.token,side:d.action,order_type:d.type,execution_mode:isMarket?'market':'limit',price:(price/100).toFixed(6),size:d.size,nonce:newTradeNonce(),confirm:true};
  if(auto!==null)body.auto_sell_cents=String(auto);
  tradeSending=true;updateTradingControls();report('Отправляем заявку. Не закрывайте страницу и не повторяйте запрос.');
  try{
    let result;
    while(true){
      try{result=await api('/api/trading/orders',{method:'POST',body:JSON.stringify(body)});break}
      catch(err){
        if(err.code!=='unknown_orders'||!Array.isArray(err.nonces)||!err.nonces.every(x=>/^[0-9a-f]{32}$/.test(x)))throw err;
        if(state.market!==slug||!overviewActive()||generation!==accountGeneration||!tradeEnabled||(quickKey&&(bookChoice!==Number(d.index)||quickKey!==bookTradeKey(marketData?.market?.options?.[Number(d.index)])))){report('Новая заявка не отправлена: рынок или стакан изменился. Проверьте предыдущие заявки.');return}
        if(!confirm(`ВНИМАНИЕ: ${slug} · ${o.label} · ${d.token.toUpperCase()} · ${d.action} · ${d.size} ${unit}. По этому исходу есть ${err.nonces.length} заявок с неизвестным результатом или незавершённой отправкой. Они могли уже исполниться. Новая заявка может привести к повторной покупке или продаже. Проверьте Polymarket и журнал. Всё равно отправить НОВУЮ заявку?`)){report('Новая заявка не отправлена. Проверьте предыдущие заявки.');return}
        body.acknowledged_unknown=err.nonces;
      }
    }
    report(result.state==='accepted'?`Ордер принят CLOB: ${result.order_id}. Это не подтверждение исполнения; проверьте заявки и сделки.`:'Результат неизвестен. Не повторяйте заявку — проверьте Polymarket и журнал.');
    await loadAccountSnapshot();await loadTradeJournal();
  }catch(err){report(err.message+' Если результат запроса неизвестен, не повторяйте: проверьте Polymarket и журнал.');await loadTradeJournal()}
  finally{tradeSending=false;updateTradingControls()}
}
$('#trade-submit').onclick=()=>submitTrade(getDraft());

setInterval(()=>{if(accountAutoRefresh&&!document.hidden&&!$('#weather-page').classList.contains('hidden'))loadAccountSnapshot()},30000);
setInterval(()=>{if(accountSnapshot&&(Date.now()-accountSnapshotAt>65000)!==renderedSnapshotStale)renderAccountSnapshot()},15000);
let draftMarket=null,draftOptionsKey='',tradeDrafts=new Map(),draftMarketOptions=new Map();
function getDraft(){if(!tradeDrafts.has(state.market))tradeDrafts.set(state.market,{index:'',token:'yes',action:'BUY',type:'GTC',price:'',size:''});return tradeDrafts.get(state.market)}
function preparePositionSale(position){
  if(!position||!accountSnapshot||Date.now()-accountSnapshotAt>65000){toast('Обновите снимок аккаунта перед продажей.');return}
  const match=confirmedMarketTokens().get(position.asset),qty=Number(position.size);
  if(!match||!Number.isFinite(qty)||qty<=0){toast('Не удалось подтвердить исход и количество позиции.');return}
  const amount=Math.floor(qty*100)/100;if(amount<=0){toast('Количество позиции меньше доступной точности заявки.');return}
  const opts=marketData?.market?.options||[],index=opts.indexOf(match.option);if(index<0)return;
  const d=getDraft();d.index=String(index);d.token=match.token;d.action='SELL';d.type='GTC';d.size=String(amount);
  const bid=bookFor(match.option,match.token)?.bids?.[0];d.price=bid?String(Math.round(Number(bid.price)*1000000)/10000):'';
  draftOptionsKey='';renderTradeDraft(marketData.market);$('#trade-send-status').textContent='Черновик продажи: выберите лимитный или рыночный тип, проверьте цену и количество. Ордер ещё не отправлен.';
  $('#trade-preview').scrollIntoView?.({behavior:'smooth',block:'nearest'});$('#trade-price').focus();
}
function estimateForDraft(d){
  const price=Number(d.price),amount=Number(d.size),market=d.type==='FAK';
  if(d.price===''||d.size===''||!Number.isFinite(price)||price<=0||price>=100||!Number.isFinite(amount)||amount<=0)return 'Укажите цену и сумму или количество для оценки.';
  if(!market)return `Оценка: ${(price*amount/100).toLocaleString('ru-RU',{maximumFractionDigits:4})} pUSD без комиссий · не ордер`;
  const option=marketData?.market?.options?.[Number(d.index)],book=option&&bookFor(option,d.token),cap=price/100;
  const levels=(d.action==='BUY'?book?.asks:book?.bids)||[];
  const valid=levels.map(x=>({price:Number(x.price),size:Number(x.size)})).filter(x=>Number.isFinite(x.price)&&x.price>0&&x.price<1&&Number.isFinite(x.size)&&x.size>0&&(d.action==='BUY'?x.price<=cap:x.price>=cap)).sort((a,b)=>d.action==='BUY'?a.price-b.price:b.price-a.price);
  let left=amount,cash=0,qty=0;for(const x of valid){const q=d.action==='BUY'?Math.min(x.size,left/x.price):Math.min(x.size,left);qty+=q;cash+=q*x.price;left-=d.action==='BUY'?q*x.price:q;if(left<=1e-9)break}
  const n=x=>x.toLocaleString('ru-RU',{maximumFractionDigits:4});
  return `Оценка по показанным уровням: ${n(qty)} долей / ${n(cash)} pUSD без комиссий${left>1e-8?' · объём показанных уровней недостаточен':''}. Стакан может измениться; остаток FAK отменяется, защитная цена может быть строже из-за округления.`;
}
function draftEstimate(){
  const d=getDraft(),market=d.type==='FAK';
  $('#trade-price-label').textContent=market?(d.action==='BUY'?'Защитная цена: максимум, ¢':'Защитная цена: минимум, ¢'):'Цена, ¢';
  $('#trade-size-label').textContent=market&&d.action==='BUY'?'Сумма покупки, pUSD':'Количество долей';
  $('#trade-estimate').textContent=estimateForDraft(d);
}
function renderTradeDraft(m){
  let opts=m?.options||[],key=JSON.stringify(opts.map(o=>[o.label,o.token_yes,o.token_no]));
  if(draftMarket===state.market&&draftOptionsKey===key){draftEstimate();return}
  draftMarket=state.market;draftOptionsKey=key;
  let d=getDraft(),previousKey=draftMarketOptions.get(state.market);
  if(m&&previousKey&&previousKey!==key){d.index='';d.price='';}
  if(m)draftMarketOptions.set(state.market,key);
  $('#trade-option').innerHTML='<option value="">Выберите исход</option>'+opts.map((o,i)=>`<option value="${i}">${esc(o.label)}</option>`).join('');
  if(m&&(!opts[Number(d.index)]||d.index===''))d.index='';
  for(const [id,key] of [['option','index'],['token','token'],['action','action'],['type','type'],['price','price'],['size','size']])$('#trade-'+id).value=d[key];
  draftEstimate();
}
for(const [id,key] of [['option','index'],['token','token'],['action','action'],['type','type'],['price','price'],['size','size']]){
  $('#trade-'+id).addEventListener('input',e=>{const d=getDraft(),wasUsd=d.type==='FAK'&&d.action==='BUY';d[key]=e.currentTarget.value;const isUsd=d.type==='FAK'&&d.action==='BUY';if(wasUsd!==isUsd){d.size='';$('#trade-size').value='';$('#trade-send-status').textContent='Единица объёма изменилась. Введите сумму или количество заново.'}draftEstimate()});
}
function pickBookPrice(index,token,side,price,withinBook=false){
  if(state.demo||!marketData?.market?.options?.[index]||!bookFor(marketData.market.options[index],token))return;
  const n=Number(price);if(!Number.isFinite(n)||n<=0||n>=1)return;
  const d=getDraft(),wasUsd=d.type==='FAK'&&d.action==='BUY';d.index=String(index);d.token=token;d.action=side==='ask'?'BUY':'SELL';if(wasUsd!==(d.type==='FAK'&&d.action==='BUY')){d.size='';$('#trade-send-status').textContent='Единица объёма изменилась. Введите сумму или количество заново.'}d.price=String(Math.round(n*1000000)/10000);
  draftOptionsKey='';renderTradeDraft(marketData.market);
  if(withinBook&&bookChoice===index){
    const option=marketData.market.options[index],panel=$('.book-trade'),q=bookTradeDraft(option,index),newAction=side==='ask'?'BUY':'SELL',newPrice=String(Math.round(n*1000000)/10000);
    if(q.token!==token||q.action!==newAction)q.size='';q.token=token;q.action=newAction;q.price=newPrice;q.status='Цена выбрана из стакана. Проверьте объём; ордер ещё не отправлен.';
    if(panel&&panel.dataset.key===bookTradeKey(option,index)){for(const field of ['token','action','price','size'])panel.querySelector(`[data-field="${field}"]`).value=q[field];updateBookTrade(panel,option,index)}
  }else $('#trade-preview').scrollIntoView?.({behavior:'smooth',block:'nearest'});
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
// History is indicative CLOB data, not archived executable bid/ask levels.
let historySide='yes',historyPeriod='1d',historyView=null,historySeq=0,historyShowBid=true,historyShowAsk=true;
function historyKey(){
  const option=marketData?.market?.options?.[bookChoice],token=option?.['token_'+historySide];
  if(state.demo||bookChoice===null||bookSlug!==state.market||!state.market||typeof token!=='string'||!/^\d{1,80}$/.test(token))return null;
  return {slug:state.market,token,side:historySide,period:historyPeriod,id:JSON.stringify([state.market,token,historySide,historyPeriod])};
}
function historyVisible(){return !document.hidden&&!$('#weather-page').classList.contains('hidden')}
async function syncBookHistory(force=false){
  const key=historyKey();if(!key||!historyVisible())return;
  if(historyView?.id===key.id&&(historyView.status==='loading'||(!force&&Date.now()-historyView.loadedAt<60000)))return;
  const seq=++historySeq;historyView={id:key.id,status:'loading'};
  // Update only the chart, without replacing order-book or trading controls.
  updateBookHistory();
  try{
    const data=await api(`/api/market/${encodeURIComponent(key.slug)}/price-history?token=${encodeURIComponent(key.token)}&side=${encodeURIComponent(key.side)}&period=${encodeURIComponent(key.period)}`);
    if(seq!==historySeq||historyKey()?.id!==key.id||!historyVisible()){if(seq===historySeq)historyView=null;return}
    if(!Array.isArray(data.history))throw Error('Неверный ответ истории');
    historyView={id:key.id,status:'ready',data,loadedAt:Date.now()};
  }catch(e){
    if(seq!==historySeq||historyKey()?.id!==key.id||!historyVisible()){if(seq===historySeq)historyView=null;return}
    historyView={id:key.id,status:'error',message:e.message,loadedAt:Date.now()};
  }
  updateBookHistory();
}
function historyDate(t){return new Date(t*1000).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}
function historyGraph(data,option){
  const now=Date.now()/1000,durations={'1h':3600,'6h':21600,'1d':86400,'1w':604800};
  const validPrice=p=>typeof p==='number'&&Number.isFinite(p)&&p>=0&&p<=1;
  const prices=(Array.isArray(data.history)?data.history:[]).filter(p=>typeof p.t==='number'&&Number.isFinite(p.t)&&validPrice(p.p)).sort((a,b)=>a.t-b.t);
  const quotes=(Array.isArray(data.quotes)?data.quotes:[]).filter(p=>typeof p.t==='number'&&Number.isFinite(p.t)).map(p=>({t:p.t,bid:validPrice(p.bid)?p.bid:null,ask:validPrice(p.ask)?p.ask:null}));
  // The current book has its own real observation time; never synthesize old bid/ask values.
  const book=bookFor(option,historySide);
  if(book&&Number.isFinite(Number(book.fetched_at))){
    const read=levels=>{if(!levels?.length)return null;const n=Number(levels[0].price);return validPrice(n)?n:null};
    const t=Number(book.fetched_at),snapshot={t,bid:read(book.bids),ask:read(book.asks)};
    if(!quotes.some(q=>Math.abs(q.t-t)<.001))quotes.push(snapshot);
  }
  quotes.sort((a,b)=>a.t-b.t);
  const allTimes=[...prices.map(p=>p.t),...quotes.map(p=>p.t)].filter(t=>t>0&&t<=now+60);
  const end=now,start=durations[historyPeriod]?end-durations[historyPeriod]:(allTimes.length?Math.min(...allTimes):end-86400);
  const inRange=p=>p.t>=start&&p.t<=end+60;
  const pricePoints=prices.filter(inRange),quotePoints=quotes.filter(inRange),values=pricePoints.map(p=>p.p);
  for(const q of quotePoints){if(historyShowBid&&q.bid!==null)values.push(q.bid);if(historyShowAsk&&q.ask!==null)values.push(q.ask)}
  if(!values.length)return '<div class="book-chart-empty">'+(data.history_error?esc(data.history_error):'За выбранный период история отсутствует.')+'</div>';
  const low=Math.min(...values),high=Math.max(...values),pad=Math.max((high-low)*.15,.02),min=Math.max(0,low-pad),max=Math.min(1,high+pad),span=max-min||1;
  const x=t=>Math.max(0,Math.min(728,(t-start)/Math.max(1,end-start)*728)),y=p=>5+(max-p)/span*90;
  const line=pricePoints.map((p,i)=>`${i?'L':'M'}${x(p.t).toFixed(2)},${y(p.p).toFixed(2)}`).join(' ');
  const series=(side,show)=>{
    if(!show)return '';
    const segments=[];let segment=[];
    const flush=()=>{if(segment.length)segments.push(segment);segment=[]};
    for(const p of quotePoints){
      if(p[side]===null){flush();continue}
      if(segment.length&&p.t-segment[segment.length-1].t>90)flush();
      segment.push(p);
    }
    flush();
    return segments.map(part=>part.length===1?`<circle class="history-${side}-dot" cx="${x(part[0].t).toFixed(2)}" cy="${y(part[0][side]).toFixed(2)}" r="2"/>`:`<path class="history-${side}-line" d="${part.map((p,i)=>`${i?'L':'M'}${x(p.t).toFixed(2)},${y(p[side]).toFixed(2)}`).join(' ')}"/>`).join('');
  };
  const horizontal=Array.from({length:5},(_,i)=>{const yy=5+i*22.5;return `<line x1="0" y1="${yy}" x2="728" y2="${yy}" class="history-grid"/>`}).join('');
  const vertical=Array.from({length:7},(_,i)=>`<line x1="${i*728/6}" y1="5" x2="${i*728/6}" y2="95" class="history-grid time-grid"/>`).join('');
  const ylabels=Array.from({length:5},(_,i)=>`<span>${esc(priceCents(max-i*span/4))}</span>`).join('');
  const xlabels=Array.from({length:7},(_,i)=>`<span style="left:${i*100/6}%">${esc(historyDate(start+(end-start)*i/6))}</span>`).join('');
  const last=pricePoints[pricePoints.length-1];
  return `<div class="book-history-plot"><div class="history-axis-y">${ylabels}</div><div class="history-plot-main"><svg class="book-history-svg" viewBox="0 0 728 100" preserveAspectRatio="none" role="img" aria-label="История цены и снимков стакана ${historySide.toUpperCase()}"><title>Цена CLOB и сохранённые снимки BID/ASK. Время устройства.</title>${horizontal}${vertical}<line class="history-cursor" x1="0" x2="0" y1="5" y2="95" visibility="hidden"/><path class="history-line" d="${line}"/>${last?`<circle class="history-last" cx="${x(last.t).toFixed(2)}" cy="${y(last.p).toFixed(2)}" r="3"/>`:''}${series('bid',historyShowBid)}${series('ask',historyShowAsk)}</svg><div class="history-axis-x">${xlabels}</div></div></div><div class="book-history-value" aria-live="polite">${last?`Цена CLOB: ${esc(priceCents(last.p))} · ${esc(historyDate(last.t))}`:'Цена CLOB отсутствует.'}</div>`;
}

function bookHistory(option){
  const key=historyKey(),v=key&&historyView?.id===key.id?historyView:null;
  const sides=['yes','no'].map(x=>`<button type="button" class="history-side ${historySide===x?'active':''}" data-side="${x}" aria-pressed="${historySide===x}">${x.toUpperCase()}</button>`).join('');
  const periods=[['1h','1ч'],['6h','6ч'],['1d','1д'],['1w','1н'],['max','Всё']].map(([id,label])=>`<button type="button" class="history-period ${historyPeriod===id?'active':''}" data-period="${id}" aria-pressed="${historyPeriod===id}">${label}</button>`).join('');
  let graph=!key?'<div class="book-chart-empty">Нет подтверждённого токена для истории.</div>':!v||v.status==='loading'?'<div class="book-chart-empty">Загрузка истории…</div>':v.status==='error'?`<div class="book-chart-empty">${esc(v.message)} <button type="button" class="history-retry">Повторить</button></div>`:historyGraph(v.data,option);
  const book=bookFor(option,historySide),bid=book?.bids?.[0],ask=book?.asks?.[0];
  const overlays=`<div class="history-controls history-overlays" aria-label="Линии стакана"><button type="button" class="history-overlay bid ${historyShowBid?'active':''}" data-line="bid" aria-pressed="${historyShowBid}">● BID</button><button type="button" class="history-overlay ask ${historyShowAsk?'active':''}" data-line="ask" aria-pressed="${historyShowAsk}">● ASK</button></div>`;
  return `<section class="book-history"><div class="book-history-head"><div><strong>История цены · ${esc(option.label)}</strong><small>Polymarket CLOB · время устройства</small></div><div class="history-controls" aria-label="Исход">${sides}</div><div class="history-controls" aria-label="Период">${periods}</div>${overlays}</div>${graph}<div class="book-history-note">Цена CLOB — отдельная серия; BID/ASK — реальные снимки терминала с момента наблюдения (не чаще 30 сек, хранение до 7 дней). Пробелы не соединяются; доступность данных и цены исполнения не гарантируются. Сейчас в снимке ${historySide.toUpperCase()}: BID ${bid?esc(priceCents(bid.price)):'—'} / ASK ${ask?esc(priceCents(ask.price)):'—'}${book?' · '+esc(new Date(book.fetched_at*1000).toLocaleTimeString('ru-RU')):''}. ${v?.status==='ready'?`Шаг цены ${esc(v.data.fidelity_minutes)} мин; ${esc(v.data.history_error||v.data.quote_error||'прошлые BID/ASK до наблюдения недоступны')}.`:''}</div></section>`;
}
function updateBookHistory(){
  const current=$('.book-history'),option=marketData?.market?.options?.[bookChoice];
  if(!current||!option)return;
  current.outerHTML=bookHistory(option);bindHistoryControls();
}
function bindHistoryControls(){
  $$('.history-side').forEach(btn=>btn.onclick=()=>{historySide=btn.dataset.side;updateBookHistory();syncBookHistory()});
  $$('.history-period').forEach(btn=>btn.onclick=()=>{historyPeriod=btn.dataset.period;updateBookHistory();syncBookHistory()});
  const retry=$('.history-retry');if(retry)retry.onclick=()=>syncBookHistory(true);
  $$('.history-overlay').forEach(btn=>btn.onclick=()=>{if(btn.dataset.line==='bid')historyShowBid=!historyShowBid;else historyShowAsk=!historyShowAsk;updateBookHistory()});
  const svg=$('.book-history-svg'),value=$('.book-history-value');
  if(svg&&value){
    const data=historyView.data,option=marketData?.market?.options?.[bookChoice],now=Date.now()/1000,durations={'1h':3600,'6h':21600,'1d':86400,'1w':604800};
    const prices=(Array.isArray(data.history)?data.history:[]).filter(p=>typeof p.t==='number'&&Number.isFinite(p.t)&&typeof p.p==='number'&&Number.isFinite(p.p)&&p.p>=0&&p.p<=1);
    const quotes=(Array.isArray(data.quotes)?data.quotes:[]).filter(q=>typeof q.t==='number'&&Number.isFinite(q.t));
    const book=bookFor(option,historySide),read=levels=>{if(!levels?.length)return null;const n=Number(levels[0].price);return Number.isFinite(n)&&n>=0&&n<=1?n:null};
    if(book&&Number.isFinite(Number(book.fetched_at)))quotes.push({t:Number(book.fetched_at),bid:read(book.bids),ask:read(book.asks)});
    const times=[...prices.map(p=>p.t),...quotes.map(p=>p.t)].filter(t=>t>0&&t<=now+60),start=durations[historyPeriod]?now-durations[historyPeriod]:(times.length?Math.min(...times):now-86400);
    const candidates=[...prices.map(p=>({t:p.t,text:`Цена CLOB ${priceCents(p.p)}`})),...quotes.filter(q=>(historyShowBid&&typeof q.bid==='number')||(historyShowAsk&&typeof q.ask==='number')).map(q=>({t:q.t,text:[historyShowBid?'BID '+(q.bid==null?'—':priceCents(q.bid)):null,historyShowAsk?'ASK '+(q.ask==null?'—':priceCents(q.ask)):null].filter(Boolean).join(' / ')}))].filter(p=>p.t>=start&&p.t<=now+60);
    const original=value.textContent,cursor=svg.querySelector('.history-cursor');
    svg.addEventListener('pointermove',e=>{const box=svg.getBoundingClientRect();if(!box.width||!candidates.length)return;const ratio=Math.max(0,Math.min(1,(e.clientX-box.left)/box.width)),target=start+ratio*(now-start);let nearest=candidates[0];for(const p of candidates)if(Math.abs(p.t-target)<Math.abs(nearest.t-target))nearest=p;const x=Math.max(0,Math.min(728,(nearest.t-start)/Math.max(1,now-start)*728));cursor.setAttribute('x1',x);cursor.setAttribute('x2',x);cursor.setAttribute('visibility','visible');value.textContent=`${nearest.text} · ${historyDate(nearest.t)}`});
    svg.addEventListener('pointerleave',()=>{cursor.setAttribute('visibility','hidden');value.textContent=original});
  }
}

// Each expanded range has a separate draft; token IDs, not row labels, anchor it.
const bookTradeDrafts=new Map();
function resetBookTradeDrafts(){bookTradeDrafts.clear();$('.book-trade')?.remove()}
function bookTradeKey(option,index=bookChoice){return JSON.stringify([state.market,option?.token_yes||null,option?.token_no||null,option?.token_yes&&option?.token_no?null:index])}
function bookTradeDraft(option,index){
  const key=bookTradeKey(option,index);
  if(!bookTradeDrafts.has(key)){
    if(bookTradeDrafts.size>=100)bookTradeDrafts.delete(bookTradeDrafts.keys().next().value);
    bookTradeDrafts.set(key,{index:String(index),token:'yes',action:'BUY',type:'GTC',price:'',size:'',auto_sell_cents:'',status:'Черновик отдельно от общего торгового блока. Ордер ещё не отправлен.'});
  }
  const draft=bookTradeDrafts.get(key);draft.index=String(index);return draft;
}
function bookTradePanel(option,index){
  const d=bookTradeDraft(option,index),key=bookTradeKey(option,index);
  const selected=(value,current)=>value===current?' selected':'';
  return `<section class="book-trade" data-key="${esc(key)}" aria-label="Торговля по выбранному диапазону"><div class="book-trade-heading"><div><b>Торговля · ${esc(option.label)}</b><small>Отдельный черновик для этого диапазона</small></div><button type="button" class="book-trade-refresh button secondary">Обновить позиции</button></div><div class="book-trade-positions"></div><div class="book-trade-fields"><label>Исход<select data-field="token"><option value="yes"${selected('yes',d.token)}>YES · ДА</option><option value="no"${selected('no',d.token)}>NO · НЕТ</option></select></label><label>Действие<select data-field="action"><option value="BUY"${selected('BUY',d.action)}>Купить</option><option value="SELL"${selected('SELL',d.action)}>Продать</option></select></label><label>Тип<select data-field="type"><option value="GTC"${selected('GTC',d.type)}>Лимитный GTC</option><option value="FAK"${selected('FAK',d.type)}>Рыночный FAK</option></select></label><label><span class="book-trade-price-label">Цена, ¢</span><input data-field="price" type="number" min="0.01" max="99.99" step="any" value="${esc(d.price)}" placeholder="Цена"></label><label><span class="book-trade-size-label">Количество долей</span><input data-field="size" type="number" min="0" step="any" value="${esc(d.size)}" placeholder="Количество"></label></div><label class="book-auto-sell">Автопродажа выше среднего входа, ¢ <input data-field="auto_sell_cents" type="number" min="0.01" max="99.99" step="0.01" value="${esc(d.auto_sell_cents||'')}" placeholder="Пусто — выключена"><small>Только при покупке: фоновая служба выставит SELL после подтверждения долей. Не гарантирует исполнение.</small></label><div class="book-trade-estimate"></div><div class="book-trade-footer"><div class="book-trade-status" role="status">${esc(d.status)}</div><button type="button" class="book-trade-submit button primary" disabled>Торговля выключена</button></div></section>`;
}
function updateBookTrade(panel,option,index){
  if(!panel||panel.dataset.key!==bookTradeKey(option,index))return;
  const d=bookTradeDraft(option,index),stale=!accountSnapshot||Date.now()-accountSnapshotAt>65000;
  panel.querySelector('.book-trade-price-label').textContent=d.type==='FAK'?(d.action==='BUY'?'Защитная цена: максимум, ¢':'Защитная цена: минимум, ¢'):'Цена, ¢';
  panel.querySelector('.book-trade-size-label').textContent=d.type==='FAK'&&d.action==='BUY'?'Сумма покупки, pUSD':'Количество долей';
  panel.querySelector('.book-trade-estimate').textContent=estimateForDraft(d);
  panel.querySelector('.book-trade-status').textContent=d.status;
  const confirmed=confirmedMarketTokens(),positions=Array.isArray(accountSnapshot?.positions)?accountSnapshot.positions:null;
  const matched=positions?positions.filter(p=>[option.token_yes,option.token_no].includes(p.asset)&&confirmed.has(p.asset)&&Number.isFinite(Number(p.size))&&Number(p.size)>0):[];
  const box=panel.querySelector('.book-trade-positions');
  if(!positions)box.textContent='Позиции не загружены. Обновите снимок аккаунта.';
  else if(!matched.length)box.textContent=accountSnapshot.positions_maybe_partial?'Позиция не найдена в неполном снимке.':'В снимке нет позиций по этому диапазону.';
  else box.innerHTML=matched.map(p=>{const t=confirmed.get(p.asset),duplicates=matched.filter(x=>x.asset===p.asset).length>1;return `<div class="book-trade-position"><span>${t.token.toUpperCase()} · ${esc(shares(p.size))} долей · средний вход ${p.avg_price==null?'—':esc(priceCents(p.avg_price))}${stale?' · СНИМОК УСТАРЕЛ':''}${duplicates?' · неоднозначный снимок':''}</span><button type="button" class="button secondary book-prepare-sale" data-asset="${esc(p.asset)}" ${stale||duplicates?'disabled':''}>Продать доли ${t.token.toUpperCase()}</button></div>`}).join('')+(accountSnapshot.positions_maybe_partial?'<small>Снимок позиций может быть неполным.</small>':'');
  panel.querySelector('.book-trade-refresh').onclick=()=>loadAccountSnapshot();
  panel.querySelectorAll('.book-prepare-sale').forEach(btn=>btn.onclick=()=>{
    // Re-read the current snapshot and token binding at click time, never guess a position.
    const match=confirmedMarketTokens().get(btn.dataset.asset),rows=Array.isArray(accountSnapshot?.positions)?accountSnapshot.positions.filter(p=>p.asset===btn.dataset.asset&&Number.isFinite(Number(p.size))&&Number(p.size)>0):[];
    if(!accountSnapshot||Date.now()-accountSnapshotAt>65000||!match||match.index!==index||rows.length!==1){toast('Обновите позиции: исход или количество не подтверждены.');return}
    const amount=Math.floor(Number(rows[0].size)*100)/100;if(amount<=0){toast('Количество позиции меньше доступной точности заявки.');return}
    d.token=match.token;d.action='SELL';d.type='GTC';d.size=String(amount);
    const bid=bookFor(option,d.token)?.bids?.[0],n=Number(bid?.price);d.price=Number.isFinite(n)&&n>0&&n<1?String(Math.round(n*1000000)/10000):'';
    d.status='Черновик продажи: проверьте исход, цену и количество. Ордер ещё не отправлен.';
    for(const field of ['token','action','type','price','size'])panel.querySelector(`[data-field="${field}"]`).value=d[field];
    updateBookTrade(panel,option,index);panel.querySelector('[data-field="price"]').focus();
  });
  panel.querySelectorAll('[data-field]').forEach(input=>input.oninput=()=>{
    const field=input.dataset.field,old=d[field],wasUsd=d.type==='FAK'&&d.action==='BUY';d[field]=input.value;
    if((field==='token'||field==='action')&&old!==d[field]){d.price='';d.size='';panel.querySelector('[data-field="price"]').value='';panel.querySelector('[data-field="size"]').value='';d.status='Исход или действие изменено. Проверьте цену и количество.'}
    else if(wasUsd!==(d.type==='FAK'&&d.action==='BUY')){d.size='';panel.querySelector('[data-field="size"]').value='';d.status='Единица объёма изменилась. Введите сумму или количество заново.'}
    panel.querySelector('.book-trade-price-label').textContent=d.type==='FAK'?(d.action==='BUY'?'Защитная цена: максимум, ¢':'Защитная цена: минимум, ¢'):'Цена, ¢';
    panel.querySelector('.book-trade-size-label').textContent=d.type==='FAK'&&d.action==='BUY'?'Сумма покупки, pUSD':'Количество долей';
    panel.querySelector('.book-trade-estimate').textContent=estimateForDraft(d);panel.querySelector('.book-trade-status').textContent=d.status;
  });
  panel.querySelector('.book-trade-submit').onclick=()=>{
    if(panel.dataset.key!==bookTradeKey(marketData?.market?.options?.[index],index)||bookChoice!==index){toast('Стакан изменился. Проверьте исход заново.');return}
    submitTrade(d,panel.dataset.key);
  };
  updateTradingControls();
}

function renderMarketOptions(m=marketData?.market){
  if(!state.market)m=null;
  const groups=groupMarketOrders(),oldPanel=$('.book-trade'),focused=oldPanel?.contains(document.activeElement)?document.activeElement:null;
  $('#market-options').innerHTML=m?.options?.length?m.options.map((o,i)=>{
    let n=typeof o.prob==='number'?Math.max(0,Math.min(100,o.prob)):null,yes=bookFor(o,'yes'),no=bookFor(o,'no');
    const mine=[...ordersForBook(i,'yes',groups),...ordersForBook(i,'no',groups)];
    const badge=mine.length?`<small class="book-own-badge">Мои открытые: ${mine.length} · откройте стакан</small>`:'';
    return `<div class="option-row book-option"><span class="option-name">${esc(o.label)}${badge}</span><span class="option-prob"><span class="bar"><i style="width:${n??0}%"></i></span> ${n==null?'—':n+'%'}</span>${quote(yes,i,'yes')}${quote(no,i,'no')}<button class="book-toggle" data-index="${i}" aria-expanded="${bookChoice===i}">${bookChoice===i?'Скрыть':'Стакан'} ↕</button></div>${bookChoice===i?`<div class="book-details">${bookHistory(o)}${bookSide('YES · ДА',yes,i,'yes',groups)}${bookSide('NO · НЕТ',no,i,'no',groups)}<p class="book-disclaimer">До 5 лучших цен на каждой стороне. Мои ордера — отдельный снимок первой страницы CLOB${Array.isArray(accountSnapshot?.orders)?` на ${esc(new Date(accountSnapshotAt).toLocaleTimeString('ru-RU'))}${Date.now()-accountSnapshotAt>65000?' · УСТАРЕЛ':''}`:' · недоступен'}; совпадение цены не подтверждает наличие ордера в публичном стакане; объёмы не складываются. Другие ключи могут быть не видны. YES и NO могут отражать общую ликвидность; не суммируйте их.</p>${bookTradePanel(o,i)}</div>`:''}`
  }).join(''):'<div class="empty-state">Выберите или добавьте рынок</div>';
  let panel=$('.book-trade');
  if(panel&&oldPanel&&panel.dataset.key===oldPanel.dataset.key){panel.replaceWith(oldPanel);panel=oldPanel;if(focused?.isConnected)focused.focus()}
  if(panel&&m?.options?.[bookChoice])updateBookTrade(panel,m.options[bookChoice],bookChoice);
  $$('.book-toggle').forEach(btn=>btn.onclick=()=>{const next=Number(btn.dataset.index);if(bookChoice!==next)historySide='yes';bookChoice=bookChoice===next?null:next;renderMarketOptions()});
  bindHistoryControls();syncBookHistory();
  $$('.quote-pick').forEach(btn=>btn.onclick=()=>{const index=Number(btn.dataset.index),within=!!btn.closest('.book-details');pickBookPrice(index,btn.dataset.token,btn.dataset.side,btn.dataset.price,within);});
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
  $('#station-list').innerHTML=state.stations.length?state.stations.map(s=>`<div class="manage-row"><div><b>${esc(s.name)}</b><small>${esc(s.station_code)} · ${esc(s.station_type)} · ${s.enabled?'опрос бота разрешён':'опрос бота запрещён'}</small></div><div class="manage-actions"><button class="button secondary station-select" data-id="${s.id}">Открыть ↗</button><button class="button secondary station-rename" data-id="${s.id}">Переименовать</button><button class="button danger station-delete" data-id="${s.id}">Удалить</button></div></div>`).join(''):'<div class="empty-state">Станций пока нет. Добавьте первую станцию.</div>';
  $$('.station-select').forEach(b=>b.onclick=async()=>{state.selected=Number(b.dataset.id);page('weather',false);renderStationTabs();await loadStation()});
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
async function addScannedMarket(slug,button,override=null){
  const item=override||scanData?.events?.find(e=>e.slug===slug);if(!item||scanAdding.has(slug))return;
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
      toast(response.already_bound?'Рынок уже привязан':response.created_station?'Рынок добавлен; опрос бота для новой станции выключен':'Рынок добавлен и привязан к станции');
    }else{
      await api('/api/markets',{method:'POST',body:JSON.stringify({slug})});
      toast(result.status==='conflict'?'Рынок сохранён без станции: коды конфликтуют. Выберите станцию вручную.':'Рынок сохранён без станции. При необходимости выберите станцию вручную.');
    }
    await renderAllMarkets();
  }catch(e){toast(e.message)}finally{scanAdding.delete(slug);button.disabled=false;renderTemperatureScan()}
}
let opportunityRows=[],opportunitySession=null,opportunityNext=null,opportunityBusy=false,opportunityChecked=0,opportunityUnavailable=0,opportunityStale=0,opportunityMeta=null,opportunityGeneration=0;
function opportunityStationTime(stamp,tz){
  if(!Number.isFinite(Number(stamp)))return 'время неизвестно';
  try{return new Intl.DateTimeFormat('ru-RU',{timeZone:tz||'UTC',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'shortOffset',hourCycle:'h23'}).format(new Date(Number(stamp)*1000))}
  catch{return new Date(Number(stamp)*1000).toISOString().replace('T',' ').slice(0,16)+' UTC'}
}
function renderOpportunities(){
  const now=Date.now()/1000;
  $('#opportunity-results').innerHTML=opportunityRows.length?opportunityRows.map((r,i)=>{
    const event=r.event||{},reference=r.reference||{},age=now-Number(r.book_at),stale=!Number.isFinite(age)||age>=120;
    const quote=(name,level)=>`${name}: ${level?esc(priceCents(level.price))+' · '+esc(shares(level.size))+' долей':'нет'}`;
    const ref=reference.kind==='observed'?`METAR ${reference.extreme==='min'?'минимум':'максимум'} ${esc(tempText(reference.temp))} · ${esc(reference.count)} сводок · последняя ${esc(opportunityStationTime(reference.last_observation,event.timezone))} · архив ${esc(reference.archive_status)} (может быть неполным)`: `лидер ${esc(r.leader?.label)} · YES ${esc(priceCents(r.leader?.yes_price))} (Gamma)`;
    const gammaTime=Number(event.quotes_at)>0?new Date(event.quotes_at*1000).toLocaleString('ru-RU'):'неизвестно';
    return `<article class="opportunity-card"><h4>${esc(event.title)}</h4><div>${esc(event.station_code||'станция не определена')} · дата ${esc(event.date||'неизвестна')} · ${esc(event.timezone||'пояс неизвестен')}</div><div><strong>${esc(r.label)} · ${esc(String(r.side).toUpperCase())}</strong> · разрыв ${esc(shares(r.gap))}°${esc(r.gap_units)} · ${ref}${reference.kind==='observed'?` · лидер ${esc(r.leader?.label)} · YES ${esc(priceCents(r.leader?.yes_price))} (Gamma)`:''}</div><div><strong>${quote('BID',r.bid)} &nbsp; | &nbsp; ${quote('ASK',r.ask)}</strong>${r.ask_entry?'':' <span class="opp-risk">· подходящего ASK для покупки нет</span>'}</div><small class="${stale?'opp-stale':''}">CLOB: ${esc(new Date(r.book_at*1000).toLocaleString('ru-RU'))}${stale?' · снимок устарел, запустите новый поиск':''} · Gamma: ${esc(gammaTime)}${now-Number(event.quotes_at)>=120?' · снимок лидера устарел':''} (время устройства). Цены и доступный объём могут измениться.</small><div class="opp-actions"><button type="button" class="button secondary opportunity-add" data-index="${i}">Добавить рынок</button><a href="https://polymarket.com/event/${encodeURIComponent(event.slug||'')}" target="_blank" rel="noopener noreferrer">Проверить рынок и правила ↗</a></div></article>`;
  }).join(''):'<div class="empty-state">В проверенной порции совпадений нет.</div>';
  $$('.opportunity-add').forEach(b=>b.onclick=()=>{const row=opportunityRows[Number(b.dataset.index)];if(row)addScannedMarket(row.event.slug,b,row.event)});
}
async function searchOpportunities(next=false){
  if(opportunityBusy)return;opportunityBusy=true;const generation=opportunityGeneration,form=$('#opportunity-form'),submit=form.querySelector('button[type=submit]'),more=$('#opportunity-more');submit.disabled=true;more.disabled=true;
  if(!next){opportunityRows=[];opportunitySession=null;opportunityNext=null;opportunityChecked=0;opportunityUnavailable=0;opportunityStale=0;$('#opportunity-results').innerHTML='';}
  $('#opportunity-status').textContent='Проверяем каталог и стаканы CLOB. Это может занять некоторое время…';
  try{
    const params=next?new URLSearchParams({session:opportunitySession,offset:String(opportunityNext)}):new URLSearchParams(new FormData(form));
    const data=await api('/api/temperature-opportunities?'+params.toString());if(generation!==opportunityGeneration)return;opportunityMeta=data;opportunitySession=data.session;opportunityNext=data.next_offset;
    opportunityChecked+=data.checked;opportunityUnavailable+=data.unavailable;opportunityStale+=data.stale_leader||0;opportunityRows.push(...data.rows);
    renderOpportunities();
    const skipped=data.skipped||{};
    $('#opportunity-status').textContent=`Проверено позиций: ${opportunityChecked} из ${data.total_candidates} · совпадений: ${opportunityRows.length} · стакан недоступен: ${opportunityUnavailable}.`+(opportunityStale?` Исключено из-за устаревшей цены лидера: ${opportunityStale}; запустите новый поиск.`:'')+(data.catalog_incomplete?' Каталог неполный.':'')+(data.candidate_limit?' Достигнут предел кандидатов; уточните поиск.':'')+(skipped.no_observation?` Без подходящих сохранённых METAR: ${skipped.no_observation} событий.`:'')+(skipped.no_leader?` Без однозначного подходящего лидера: ${skipped.no_leader} событий.`:'')+(skipped.unparsed?` Нераспознанных диапазонов: ${skipped.unparsed}.`:'')+(skipped.no_tokens?` Без подтверждённых токенов YES/NO: ${skipped.no_tokens}.`:'')+(skipped.truncated_events?` Часть событий исключена: неполные данные исходов (${skipped.truncated_events}).`:'')+(data.next_offset!==null?' Проверьте следующую порцию.':'');
    more.classList.toggle('hidden',data.next_offset===null);
  }catch(e){if(generation===opportunityGeneration){$('#opportunity-status').textContent='Поиск не завершён: '+e.message;more.classList.add('hidden')}}
  finally{opportunityBusy=false;submit.disabled=false;more.disabled=false}
}
$('#opportunity-form').addEventListener('submit',e=>{e.preventDefault();searchOpportunities()});
$('#opportunity-form').addEventListener('input',()=>{opportunityGeneration++;opportunitySession=null;opportunityNext=null;opportunityRows=[];$('#opportunity-results').innerHTML='';$('#opportunity-more').classList.add('hidden');$('#opportunity-status').textContent='Фильтр изменён: нажмите «Найти зазоры» заново.'});
$('#opportunity-more').onclick=()=>{if(opportunitySession&&opportunityNext!==null)searchOpportunities(true)};
setInterval(()=>{if(opportunityRows.length&&!document.hidden&&!$('#markets-page').classList.contains('hidden'))renderOpportunities()},30000);
$('#scan-temperature').onclick=scanTemperatureMarkets;
$('#scan-filter').addEventListener('input',renderTemperatureScan);
setInterval(()=>{if(scanData&&!document.hidden&& !$('#markets-page').classList.contains('hidden'))renderTemperatureScan()},60000);
function filterSavedMarkets(){
  const q=$('#saved-markets-search').value.trim().toLocaleLowerCase(),rows=$$('#all-markets .saved-market-row');let visible=0;
  for(const row of rows){row.hidden=!!q&&!row.dataset.search.toLocaleLowerCase().includes(q);if(!row.hidden)visible++}
  const empty=$('#saved-markets-filter-empty');if(empty)empty.hidden=!q||visible>0;
}
$('#saved-markets-search').addEventListener('input',filterSavedMarkets);
async function renderAllMarkets(){
  try{
    let [markets,all]=await Promise.all([api('/api/markets'),Promise.all(state.stations.map(async s=>({s,b:await api(`/api/stations/${s.id}/bindings`)})))]);
    scanSaved=new Map(markets.map(m=>[m.slug,{codes:all.filter(x=>x.b.some(b=>b.market_slug===m.slug)).map(x=>x.s.station_code)}]));
    renderTemperatureScan();
    if(!markets.length){$('#all-markets').innerHTML='<div class="empty-state">Рынков пока нет. Найдите и привяжите первый рынок.</div>';return}
    $('#all-markets').innerHTML=markets.map(m=>{let linked=all.filter(x=>x.b.some(b=>b.market_slug===m.slug));let linkedNames=linked.length?linked.map(x=>x.s.name).join(' · '):'Не привязан к станции';return `<div class="manage-row saved-market-row" data-search="${esc([m.name,m.slug,...linked.map(x=>x.s.name),...linked.map(x=>x.s.station_code)].join(' '))}"><div class="saved-market-main"><span class="portfolio-market-icon" aria-hidden="true">◈</span><div><b>${esc(m.name)}</b><small>${esc(linkedNames)} · ${esc(m.slug)}</small></div></div><div class="manage-actions">${linked.length?`<button class="button secondary open-managed-market" data-id="${linked[0].s.id}" data-slug="${esc(m.slug)}">Открыть ↗</button>`:''}<button class="button secondary resolve-managed-market" data-slug="${esc(m.slug)}">Найти станцию</button><button class="button secondary rename-managed-market" data-slug="${esc(m.slug)}">Переименовать</button><button class="button danger delete-managed-market" data-slug="${esc(m.slug)}">Удалить</button></div></div>`}).join('')+'<div id="saved-markets-filter-empty" class="portfolio-filter-empty" hidden>По вашему запросу ничего не найдено.</div>';
    filterSavedMarkets();
    $$('.open-managed-market').forEach(b=>b.onclick=async()=>{state.selected=Number(b.dataset.id);page('weather',false);renderStationTabs();await loadStation();state.market=b.dataset.slug;renderMarketTabs();await loadMarket()});
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
    add.disabled=true;try{let response=await api('/api/market-station/add',{method:'POST',body:JSON.stringify({slug:result.slug,code:result.candidate.code,confirm:true})});$('#market-first-dialog').close();await loadStations(response.station.id);state.market=response.slug;renderMarketTabs();await loadMarket();page('weather');toast(response.already_bound?'Рынок уже привязан':response.created_station?'Станция создана; опрос бота выключен; рынок привязан':'Рынок привязан к существующей станции')}catch(e){toast(e.message)}finally{add.disabled=false}
  };
  $('#save-market-only').onclick=async e=>{let button=e.currentTarget;if(!marketResolution||$('#market-resolve-form input').value!==marketResolution.input)return;if(!confirm('Сохранить рынок без привязки к станции?'))return;button.disabled=true;try{await api('/api/markets',{method:'POST',body:JSON.stringify({slug:result.slug})});$('#market-first-dialog').close();page('markets');toast('Рынок сохранён без станции')}catch(err){toast(err.message)}finally{button.disabled=false}};
}
async function resolveMarketFirst(){let input=$('#market-resolve-form input').value.trim();if(!input)return;$('#market-resolve-form input').value=input;let seq=++resolutionSequence;marketResolution=null;$('#market-resolution').innerHTML='<p class="search-message">Изучаем правила события…</p>';try{let data=await api('/api/market-station/resolve',{method:'POST',body:JSON.stringify({slug:input})});if(seq===resolutionSequence)showResolution(data,input)}catch(err){if(seq===resolutionSequence)$('#market-resolution').innerHTML=`<div class="market-resolution-error">${esc(err.message)}</div>`}}
$('#market-resolve-form').onsubmit=e=>{e.preventDefault();resolveMarketFirst()};
$('#market-first-search').onsubmit=async e=>{e.preventDefault();let target=$('#market-first-results');target.innerHTML='<div class="search-message">Поиск…</div>';try{let results=await api('/api/search?q='+encodeURIComponent(new FormData(e.currentTarget).get('q')));target.innerHTML=results.length?results.map(r=>`<div class="search-result"><span>${esc(r.title)}</span><button type="button" data-slug="${esc(r.slug)}">Выбрать</button></div>`).join(''):'<div class="search-message">Событий не найдено. Можно вставить ссылку ниже.</div>';target.querySelectorAll('button').forEach(b=>b.onclick=()=>{$('#market-resolve-form input').value=b.dataset.slug;resolveMarketFirst()})}catch(err){target.innerHTML=`<div class="search-message">${esc(err.message)}</div>`}};
// Clicking this button adds/reuses a verified station and opens the manual ticket; it never trades.
function quickOpenButton(slug,token,condition){
  slug=typeof slug==='string'&&/^[a-z0-9-]{1,200}$/.test(slug)?slug:'';
  token=typeof token==='string'&&/^[0-9]{1,80}$/.test(token)?token:'';
  condition=typeof condition==='string'&&/^0x[0-9a-f]{64}$/i.test(condition)?condition.toLowerCase():'';
  return `<button type="button" class="button secondary quick-market-open" data-slug="${esc(slug)}" data-token="${esc(token)}" data-condition="${esc(condition)}" ${!slug&&!token?'disabled title="Нет адреса события или идентификатора исхода"':'title="Добавить рынок и открыть ручную торговлю; ордер не отправляется"'}>Открыть в обзоре ↗</button>`;
}
async function quickOpenMarket(button){
  if(quickOpenBusy||button.disabled)return;
  const startedPage=pageNavigationEpoch,startedStation=state.selected;
  const request={slug:button.dataset.slug||'',token:button.dataset.token||'',condition_id:button.dataset.condition||'',confirm:true};
  quickOpenBusy=true;button.disabled=true;const oldText=button.textContent;button.textContent='Открываем…';
  try{
    const result=await api('/api/market-station/quick-open',{method:'POST',body:JSON.stringify(request)});
    if(startedPage!==pageNavigationEpoch||startedStation!==state.selected){toast(result.needs_review?'Требуется проверить источник станции. Повторите открытие.':'Рынок добавлен; вы остались на выбранной странице.');return}
    if(result.needs_review){openMarketFirst(result.slug,false);showResolution(result.resolution,result.slug);toast('Не удалось однозначно определить станцию. Проверьте источник.');return}
    if(!result.station||!Number.isSafeInteger(result.station.id)||result.slug!== (request.slug||result.slug))throw Error('Некорректный ответ сервера.');
    page('weather',false);const overviewEpoch=pageNavigationEpoch,sid=result.station.id;
    // Load the market independently of potentially slow METAR/archive/forecast requests.
    const [stations,bindings]=await Promise.all([api('/api/stations'),api(`/api/stations/${sid}/bindings`)]);
    if(pageNavigationEpoch!==overviewEpoch||state.selected!==startedStation||$('#weather-page').classList.contains('hidden'))return;
    if(!Array.isArray(stations)||!stations.some(s=>s.id===sid)||!Array.isArray(bindings)||!bindings.some(b=>b.market_slug===result.slug))throw Error('Рынок добавлен, но станция или привязка не загрузилась. Обновите обзор.');
    state.stations=stations;state.selected=sid;state.demo=false;$('#demo-banner').classList.add('hidden');
    state.market=result.slug;renderStationTabs();loadStation(); // weather continues in the background
    state.bindings=bindings;state.market=result.slug;renderMarketTabs();await loadMarket();
    const current=()=>pageNavigationEpoch===overviewEpoch&&state.selected===sid&&!$('#weather-page').classList.contains('hidden');
    if(!current()||state.market!==result.slug)return;
    let selected=false;
    if(result.token&&['yes','no'].includes(result.selection)&&marketData?.market){
      const options=marketData.market.options||[],matches=[];
      options.forEach((o,i)=>{for(const side of ['yes','no'])if(o['token_'+side]===result.token)matches.push({i,side})});
      if(matches.length===1&&matches[0].side===result.selection){
        const d=getDraft();d.index=String(matches[0].i);d.token=matches[0].side;d.price='';d.size='';draftOptionsKey='';renderTradeDraft(marketData.market);selected=true;
        $('#trade-send-status').textContent='Исход выбран по токену. Проверьте действие, цену и количество; ордер не отправлен.';
      }
    }
    if(!marketData?.market)toast('Рынок добавлен, но его данные не загрузились. Обновите обзор.');
    else toast(selected?'Рынок и исход открыты. Проверьте правила и черновик.':'Рынок открыт. Выберите исход вручную и проверьте правила.');
    $('#trade-preview').scrollIntoView?.({behavior:'smooth',block:'nearest'});
  }catch(e){toast(e.message)}finally{quickOpenBusy=false;button.disabled=false;button.textContent=oldText}
}
document.addEventListener('click',e=>{const button=e.target.closest?.('.quick-market-open');if(button)quickOpenMarket(button)});
let aiTunnel=false,aiBusy=false,aiConfig=null,aiRevision=0;
const aiForecasts=new Map();
function setAiAccess(allowed){
  aiTunnel=!!allowed;
  $$('#ai-settings-form button,#ai-key-form button').forEach(b=>b.disabled=!aiTunnel);
  if(!aiTunnel){aiConfig=null;aiForecasts.clear();$('#ai-config-status').textContent='Настройки и ИИ-прогноз доступны только через SSH-туннель.';$('#ai-key-list').textContent='Ключи недоступны.'}
  renderAiPanel();
}
function renderAiPanel(){
  const sid=state.selected,station=state.stations.find(s=>s.id===sid),result=aiForecasts.get(sid);
  $('#ai-station-label').textContent=state.demo?'Демонстрация':station?`${station.name} · ${station.station_code}`:'Выберите станцию';
  $('#ai-generate').disabled=!aiTunnel||state.demo||!sid||aiBusy;
  $('#ai-generate').textContent=aiBusy?'Запрос выполняется…':'Получить прогноз';
  $('#ai-forecast-output').textContent=result?.text||'Прогноз ещё не запрошен.';
  let status=!aiTunnel?'Для настройки и запроса нужен SSH-туннель.':state.demo?'Добавьте реальную станцию.':aiBusy?'Запрос выполняется. Не закрывайте страницу и не повторяйте запрос.':result?`Получен ${new Date(Number(result.created_at)*1000).toISOString().replace('T',' ').slice(0,16)} UTC · ${result.model} · проект ${result.project}${result.total_count!=null?` · токенов всего ${result.total_count}`:''}${result.truncated?' · ответ может быть обрезан':''}${result.input_summary?` · METAR: ${result.input_summary.metar_points} точек (${result.input_summary.archive_status||'нет статуса'}${result.input_summary.archive_truncated?', неполные данные':''}); Open-Meteo: ${result.input_summary.sources?.['Open-Meteo']||0}, MET Norway: ${result.input_summary.sources?.['MET Norway']||0} часов; часовой пояс: ${result.input_summary.timezone||'неизвестен'}`:''}`:'Ручной запрос; прошлый ответ сохраняется до 30 дней.';
  $('#ai-forecast-status').textContent=status;
}
async function loadAiLatest(sid){
  if(!sid||!aiTunnel)return;
  const rev=aiRevision;try{const data=await api(`/api/stations/${sid}/ai-forecast`);if(rev!==aiRevision)return;if(data.forecast)aiForecasts.set(sid,data.forecast);else aiForecasts.delete(sid);if(sid===state.selected)renderAiPanel()}
  catch(err){if(sid===state.selected)$('#ai-forecast-status').textContent='Не удалось загрузить сохранённый прогноз: '+err.message}
}
$('#ai-generate').onclick=async()=>{
  if(!aiTunnel||state.demo||!state.selected||aiBusy)return;
  const sid=state.selected,station=state.stations.find(s=>s.id===sid);
  if(!confirm(`Отправить промпт и погодные данные станции ${station?.station_code||''} в Google Gemini? Запрос может быть платным.`))return;
  aiBusy=true;aiRevision++;renderAiPanel();let failure='';
  try{const data=await api(`/api/stations/${sid}/ai-forecast`,{method:'POST',body:JSON.stringify({confirm:true})});aiForecasts.set(sid,data.forecast);if(sid===state.selected)renderAiPanel()}
  catch(err){failure=err.message+' Автоповтора нет. При обрыве связи запрос мог быть платным: проверьте сохранённый результат и расходы перед повтором.';toast(failure)}
  finally{aiBusy=false;renderAiPanel();if(failure&&sid===state.selected)$('#ai-forecast-status').textContent=failure}
};
function renderAiConfig(data){
  aiConfig=data;const form=$('#ai-settings-form');form.elements.model.value=data.model||'';form.elements.prompt.value=data.prompt||'';form.elements.switch_authorized.checked=!!data.switch_authorized;
  $('#ai-config-status').textContent=`SSH-туннель активен · ключей: ${(data.keys||[]).length}. Ключи после сохранения не показываются.`;
  $('#ai-key-list').innerHTML=(data.keys||[]).length?(data.keys||[]).map((k,i)=>`<div class="ai-key-row"><span>Ключ ${i+1} · проект ${esc(k.project)}</span><button type="button" class="button danger ai-key-delete" data-id="${esc(k.id)}">Удалить</button></div>`).join(''):'Ключи не добавлены.';
  $$('.ai-key-delete').forEach(b=>{b.disabled=!aiTunnel;b.onclick=async()=>{const password=prompt('Введите текущий пароль терминала для удаления ключа:');if(password===null)return;if(!password){toast('Введите пароль');return}if(!confirm('Удалить этот ключ Gemini с сервера?'))return;b.disabled=true;try{renderAiConfig(await api('/api/ai/config',{method:'POST',body:JSON.stringify({action:'delete_key',id:b.dataset.id,current_password:password})}));toast('Ключ удалён')}catch(err){toast(err.message);b.disabled=false}}});
}
async function loadAiConfig(){if(!aiTunnel)return;try{renderAiConfig(await api('/api/ai/config'))}catch(err){$('#ai-config-status').textContent='Настройки Gemini недоступны: '+err.message}}
$('#ai-settings-form').onsubmit=async e=>{e.preventDefault();if(!aiTunnel)return;const f=e.currentTarget,b=f.querySelector('button'),authorized=f.elements.switch_authorized.checked;
  if(authorized&&!confirm('Подтверждаете разрешение Google на использование квот указанных проектов для общей нагрузки?'))return;
  b.disabled=true;try{const data={action:'save',model:f.elements.model.value.trim(),prompt:f.elements.prompt.value,switch_authorized:authorized,current_password:f.elements.current_password.value};renderAiConfig(await api('/api/ai/config',{method:'POST',body:JSON.stringify(data)}));f.elements.current_password.value='';toast('Настройки Gemini сохранены')}catch(err){toast(err.message)}finally{b.disabled=!aiTunnel}
};
$('#ai-key-form').onsubmit=async e=>{e.preventDefault();if(!aiTunnel)return;const f=e.currentTarget,b=f.querySelector('button');
  if(!confirm('Сохранить ключ Gemini в незашифрованном защищённом файле на сервере?'))return;
  b.disabled=true;try{const data={action:'add_key',project:f.elements.project.value.trim(),key:f.elements.key.value,current_password:f.elements.current_password.value};renderAiConfig(await api('/api/ai/config',{method:'POST',body:JSON.stringify(data)}));f.reset();toast('Ключ добавлен')}catch(err){toast(err.message)}finally{b.disabled=!aiTunnel}
};
async function loadSettings(){try{let s=await api('/api/settings');Object.entries(s).forEach(([k,v])=>{$(`#settings-form [name="${k}"]`).value=v});state.units=['C','F','B'].includes(s.units)?s.units:'C'}catch(e){toast(e.message)}}
async function loadSecurityInfo(){
  const form=$('#password-form'),button=form.querySelector('button'),accountButton=$('#account-form button[type=submit]'),deleteButton=$('#account-delete'),controlButton=$('#trade-control-form button');button.disabled=true;accountButton.disabled=true;deleteButton.disabled=true;controlButton.disabled=true;
  try{let s;try{s=await api('/api/security-info')}catch(e){setAiAccess(false);setCopyNoticeAccess(false);throw e}setAiAccess(s.tunnel);setCopyNoticeAccess(s.tunnel);if(s.tunnel)loadAiConfig();button.disabled=!s.tunnel;accountButton.disabled=!s.tunnel;deleteButton.disabled=!s.tunnel;controlButton.disabled=!s.tunnel;$('#monitor-interval-form button').disabled=!s.tunnel;tradeEnabled=!!s.trading_enabled;updateTradingControls();$('#trade-control-status').textContent=tradeEnabled?'Реальная торговля включена.':'Торговля выключена.';$('#password-status').textContent=s.tunnel?'SSH-туннель: защищённое подключение к серверу.':'Смена пароля недоступна: требуется настроенный SSH-туннель.';
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
  try{const result=await api('/api/trading/account-config',{method:'POST',body:JSON.stringify(data)});f.reset();updateAccountApiMode();tradeEnabled=false;updateTradingControls();accountGeneration++;resetBookTradeDrafts();accountSnapshot=null;accountSource='';accountAutoRefresh=false;$('#trade-cancel-status').textContent='';$('#trade-monitor-status').textContent='Аккаунт изменён; состояние фонового наблюдения не проверено.';renderAccountSnapshot();$('#account-status').textContent='Аккаунт изменён; повторите сверку с биржей.';$('#account-config-status').textContent=`Сохранён кошелёк ${result.wallet}. Работоспособность ещё не проверена.`;toast('Настройки сохранены; проверьте соединение с биржей')}
  catch(err){toast(err.message)}finally{button.disabled=false}
};
$('#account-delete').onclick=async e=>{
  const f=$('#account-form'),button=e.currentTarget,password=f.elements.current_password.value;
  if(!password){toast('Введите текущий пароль терминала');return}
  if(!confirm('Удалить сохранённые ключи с сервера? Открытые ордера и позиции на бирже не изменятся.'))return;
  button.disabled=true;try{await api('/api/trading/account-config',{method:'DELETE',body:JSON.stringify({current_password:password})});f.reset();updateAccountApiMode();tradeEnabled=false;updateTradingControls();accountGeneration++;resetBookTradeDrafts();accountSnapshot=null;accountSource='';accountAutoRefresh=false;$('#trade-cancel-status').textContent='';$('#trade-monitor-status').textContent='Аккаунт изменён; состояние фонового наблюдения не проверено.';renderAccountSnapshot();$('#account-status').textContent='Аккаунт отключён; ордера на бирже не отменялись.';$('#account-config-status').textContent='Аккаунт не настроен.';toast('Сохранённые ключи удалены')}
  catch(err){toast(err.message)}finally{button.disabled=false}
};
$('#monitor-interval-form').onsubmit=async e=>{
  e.preventDefault();const form=e.currentTarget,button=form.querySelector('button'),n=Number(form.elements.interval_seconds.value);
  if(!Number.isInteger(n)||n<15||n>300){$('#monitor-interval-status').textContent='Укажите целое число секунд от 15 до 300.';return}
  button.disabled=true;
  try{const result=await api('/api/trading/monitor-interval',{method:'POST',body:JSON.stringify({interval_seconds:n,confirm:true})});form.elements.interval_seconds.value=result.interval_seconds;$('#monitor-interval-status').textContent='Сохранено. Служба применит изменение при следующей проверке;';await loadMonitorStatus()}
  catch(err){$('#monitor-interval-status').textContent=err.message}
  finally{button.disabled=false}
};
$('#trade-control-form').onsubmit=async e=>{
  e.preventDefault();const f=e.currentTarget,button=f.querySelector('button'),on=f.elements.enabled.value==='true';
  if(!confirm(on?'Включить отправку РЕАЛЬНЫХ ордеров? Автоматической защиты позиции нет.':'Выключить торговлю? Уже отправленные заявки на бирже не отменятся.'))return;
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
let copyBusy=false,copyGeneration=0,copyClickVersion=0;
let copyNoticeAccess=false,copyNoticeLatest=null,copyNoticeRequest=0,copyNoticeApplied=0,copyNoticeFailed=false;
const copyNoticeKey='gradus.copy.notification.v1',copyDetails=new Map(),copyMetadata=new Map();
let copyNoticeMemory=null;
function copyTime(stamp){if(stamp===null||stamp===undefined||!Number.isFinite(Number(stamp))||Number(stamp)<=0)return '—';const d=new Date(Number(stamp)*1000);return Number.isFinite(d.getTime())?d.toLocaleString('ru-RU'):'—'}
function copySavedNotice(){try{const x=JSON.parse(localStorage.getItem(copyNoticeKey));if(x&&typeof x.epoch==='string'&&Number.isSafeInteger(x.seen)&&x.seen>=0)return x}catch{}return copyNoticeMemory}
function copyStoreNotice(x){copyNoticeMemory=x;try{localStorage.setItem(copyNoticeKey,JSON.stringify(x))}catch{}}
function renderCopyNotice(){
  const button=$('[data-page="copy"]'),saved=copySavedNotice(),latest=copyNoticeLatest;
  const unseen=!!(latest&&latest.seq>0&&(!saved||saved.epoch!==latest.epoch||saved.seen<latest.seq));
  button.classList.toggle('copy-unread',unseen);button.classList.toggle('copy-notice-error',copyNoticeFailed);
  button.setAttribute('aria-label',`Копирование${unseen?' — есть новые сигналы':''}${copyNoticeFailed?' — проверка сигналов недоступна':''}`);
  button.title=[unseen?'Есть новые сигналы. Нажмите, чтобы просмотреть.':'',copyNoticeFailed?'Не удалось проверить сигналы; повторим автоматически.':''].filter(Boolean).join(' ');
}
function acceptCopyNotice(n,request){
  if(!n||typeof n.epoch!=='string'||!Number.isSafeInteger(n.seq)||n.seq<0)return;
  if(request<copyNoticeApplied)return;copyNoticeApplied=request;copyNoticeFailed=false;
  if(copyNoticeLatest&&copyNoticeLatest.epoch===n.epoch&&n.seq<copyNoticeLatest.seq){renderCopyNotice();return;}
  copyNoticeLatest=n;renderCopyNotice();
}
function ackCopyNotice(n=copyNoticeLatest){
  if(!n||typeof n.epoch!=='string'||!Number.isSafeInteger(n.seq)||n.seq<0)return;const saved=copySavedNotice();
  copyStoreNotice({epoch:n.epoch,seen:Math.max(n.seq,saved&&saved.epoch===n.epoch?saved.seen:0)});renderCopyNotice();
}
async function pollCopyNotice(){
  if(!copyNoticeAccess||document.hidden)return;
  const request=++copyNoticeRequest;
  try{const n=await api('/api/copy-observer/notification');acceptCopyNotice(n,request)}catch{if(request>=copyNoticeApplied){copyNoticeApplied=request;copyNoticeFailed=true;renderCopyNotice()}}
}
function setCopyNoticeAccess(allowed){copyNoticeAccess=!!allowed;if(allowed)pollCopyNotice()}
window.addEventListener('storage',e=>{if(e.key===copyNoticeKey)renderCopyNotice()});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollCopyNotice()});
function copySafeSlug(slug){return typeof slug==='string'&&/^[a-zA-Z0-9_-]{1,200}$/.test(slug)?slug:''}
function copyEventLink(slug){slug=copySafeSlug(slug);return slug?`<a href="https://polymarket.com/event/${esc(slug)}" target="_blank" rel="noopener noreferrer">Открыть событие ↗</a>`:'<span class="copy-link-missing">Ссылка на событие недоступна</span>'}
function copyTx(hash){return /^0x[0-9a-f]{64}$/i.test(hash||'')?`<a href="https://polygonscan.com/tx/${esc(hash)}" target="_blank" rel="noopener noreferrer">Транзакция ↗</a>`:'Транзакция недоступна'}
function copyProfile(wallet){return /^0x[0-9a-f]{40}$/i.test(wallet||'')?`<a href="https://polymarket.com/profile/${esc(wallet)}" target="_blank" rel="noopener noreferrer">Профиль трейдера ↗</a>`:'Профиль недоступен'}
function copyNum(value,digits=6){if(value===null||value===undefined||value===''||!Number.isFinite(Number(value)))return '—';return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:digits}).format(Number(value))}
function copyPercent(value){const n=copyNum(value);return n==='—'?n:n+'%'}
function copyBookLevels(x){if(!x||!Array.isArray(x.bids)||!Array.isArray(x.asks))return 'Уровни стакана не сохранены.';const side=rows=>rows.length?rows.slice(0,5).map(l=>`${esc(l?.price??'—')} × ${esc(l?.size??'—')}`).join(' · '):'пусто';return `BID: ${side(x.bids)}<br>ASK: ${side(x.asks)} <span>(цена × доли)</span>`}
function copyValue(t){if(t.size==null||t.price==null||t.size===''||t.price==='')return '—';const a=Number(t.size),b=Number(t.price);return Number.isFinite(a)&&Number.isFinite(b)?copyNum(a*b):'—'}
function copyHistorySummary(trades){return ['BUY','SELL'].map(side=>{const rows=trades.filter(t=>t.side===side&&t.size!=null&&t.price!=null&&Number.isFinite(Number(t.size))&&Number(t.size)>0&&Number.isFinite(Number(t.price)));const shares=rows.reduce((n,t)=>n+Number(t.size),0),value=rows.reduce((n,t)=>n+Number(t.size)*Number(t.price),0);return `${side==='BUY'?'Покупки':'Продажи'}: ${rows.length} исполнений · ${esc(copyNum(shares))} долей · ≈ ${esc(copyNum(value))} USD · средняя цена ≈ ${shares?esc(copyNum(value/shares)):'—'}`}).join('<br>')}
function copyDetailKey(s){return `${s.leader_id}:${s.event_key}`}
function copyPositionHTML(section,label){
  if(!section||section.error)return `<div>${esc(label)}: ${esc(section?.error||'нет данных')}</div>`;
  const rows=Array.isArray(section.rows)?section.rows:[];
  if(!rows.length)return `<div>${esc(label)}: API не вернул позицию${section.partial?' (прочитана только первая страница)':''}.</div>`;
  return rows.map(p=>`<div class="copy-position"><b>${esc(label)} · ${esc(p.status||'статус неизвестен')}</b><small>Остаток: ${esc(copyNum(p.current_size))} долей · средняя цена: ${esc(copyNum(p.avg_price))} pUSD · текущая цена: ${esc(copyNum(p.current_price))} pUSD</small><small>Стоимость входа: ${esc(copyNum(p.entry_cost_usdc))} USD · комиссия входа по API: ${esc(copyNum(p.entry_fees_usdc))} USD · текущая стоимость: ${esc(copyNum(p.current_value))} USD</small><small>Реализованный PnL: ${esc(copyNum(p.realized_pnl))} USD · нереализованный: ${esc(copyNum(p.unrealized_pnl))} USD · общий: ${esc(copyNum(p.total_pnl))} USD</small><small>Общий объём по API: ${esc(copyNum(p.total_size))} долей · PnL: ${esc(copyPercent(p.percent_pnl))} · реализованный PnL: ${esc(copyPercent(p.percent_realized_pnl))}</small><small>Первый вход по API: ${esc(copyTime(p.first_entry_at))} · последнее событие: ${esc(copyTime(p.last_event_at))} · получено: ${esc(copyTime(section.fetched_at))}${section.partial?' · первая страница':''}</small></div>`).join('');
}
function copyDetailHTML(d){
  if(!d||!d.open)return '';
  if(d.loading)return '<div class="copy-detail-content" role="status">Загружаем публичную историю и снимок позиции…</div>';
  if(d.error)return `<div class="copy-detail-content" role="status">${esc(d.error)} <button type="button" class="button secondary copy-detail-retry">Повторить</button></div>`;
  const data=d.data||{},h=data.history||{},p=data.positions||{},trades=Array.isArray(h.trades)?h.trades:[];
  const history=h.error?`<p>${esc(h.error)}</p>`:trades.length?`<div class="copy-history">${trades.map(t=>`<div class="copy-history-row"><strong>${t.side==='BUY'?'BUY · покупка':'SELL · продажа'}</strong><span>${esc(copyTime(t.timestamp))}</span><span>${esc(copyNum(t.size))} долей × ${esc(copyNum(t.price))} pUSD ≈ ${esc(copyValue(t))} USD</span><span>${copyTx(t.transaction_hash)}</span></div>`).join('')}</div>`:'<p>API не вернул сделок для этого исхода в прочитанной части.</p>';
  return `<div class="copy-detail-content"><button type="button" class="button secondary copy-detail-refresh">Обновить историю и позицию</button><h4>Покупки и продажи этого исхода</h4><p>Публичная история Polymarket: от новых к старым, до 300 последних сделок всего рынка, затем отбор по этому исходу. ${h.partial?'История обрезана или содержит пропуски.':'Источник может запаздывать и не содержать все операции.'} Получено: ${esc(copyTime(h.fetched_at))}.</p>${trades.length?`<div class="copy-history-summary">${copyHistorySummary(trades)}<small>Только показанная выборка; сумма = цена × доли, без комиссий. Это не остаток и не прибыль.</small></div>`:''}${history}<h4>Позиция трейдера по данным Polymarket</h4><p>Это снимок на момент запроса, а не состояние в момент сигнала. Нет строки не означает, что позиция закрыта. PnL и средняя цена — значения API для позиции, не прибыль этой сделки.</p>${copyPositionHTML(p.OPEN,'Открытая / доступная к погашению')}${copyPositionHTML(p.CLOSED,'Закрытая')}<p>Покупки и продажи не связываются в пары: возможны частичные исполнения, прежние сделки, переводы, погашения, split/merge и пропуски. Порядок сделок в одну секунду может быть неопределён.</p></div>`;
}
function updateCopyDetail(key){
  const card=[...$('#copy-signals').querySelectorAll('.copy-signal')].find(x=>x.dataset.detailKey===key);if(!card)return;
  const d=copyDetails.get(key),area=card.querySelector('.copy-detail-area'),button=card.querySelector('.copy-detail-toggle');
  if(!area||!button)return;area.innerHTML=copyDetailHTML(d);button.textContent=d?.open?'Скрыть историю':'История и позиция';button.setAttribute('aria-expanded',d?.open?'true':'false');
  const retry=area.querySelector('.copy-detail-retry'),refresh=area.querySelector('.copy-detail-refresh');if(retry)retry.onclick=()=>fetchCopyDetail(key,card.dataset.leaderId,card.dataset.eventKey);if(refresh)refresh.onclick=()=>fetchCopyDetail(key,card.dataset.leaderId,card.dataset.eventKey);
}
async function fetchCopyDetail(key,leaderId,eventKey){
  const d=copyDetails.get(key);if(!d||d.loading)return;d.loading=true;d.error=null;updateCopyDetail(key);
  try{d.data=await api(`/api/copy-observer/${encodeURIComponent(leaderId)}/signal/${encodeURIComponent(eventKey)}`);if(d.data.metadata)copyMetadata.set(d.data.metadata.token_id,{data:d.data.metadata,at:Date.now()})}
  catch(e){d.error=e.message}finally{d.loading=false;updateCopyDetail(key);updateCopyLinks()}
}
function updateCopyLinks(){
  $('#copy-signals').querySelectorAll('.copy-event-link').forEach(el=>{
    const m=copyMetadata.get(el.dataset.token)?.data;
    const slug=el.dataset.slug||(m&&m.condition_id===el.dataset.condition?m.event_slug:'');el.innerHTML=copyEventLink(slug);
  });
}
async function lookupCopyLinks(signals){
  const now=Date.now(),tokens=[...new Set(signals.filter(s=>!copySafeSlug(s.event_slug)&&/^[0-9]{1,80}$/.test(s.token_id||'')&&(!copyMetadata.has(s.token_id)||now-copyMetadata.get(s.token_id).at>300000)).map(s=>s.token_id))];
  if(!tokens.length)return;tokens.forEach(t=>copyMetadata.set(t,{data:null,at:now}));
  for(let i=0;i<tokens.length;i+=50){const batch=tokens.slice(i,i+50);try{const result=await api('/api/copy-observer/metadata?tokens='+encodeURIComponent(batch.join(',')));for(const t of batch)copyMetadata.set(t,{data:result[t]||null,at:now});updateCopyLinks()}catch{/* Retry after the cache interval; a missing link is never guessed. */}}
}
function copyAmountMode(){
  const mode=$('#copy-mode').value,label=$('#copy-amount-label');label.style.display=mode==='same'?'none':'';
  label.childNodes[0].textContent=mode==='percent'?'Процент от долей трейдера':'Сумма на каждую сделку, pUSD';
}
$('#copy-mode').addEventListener('change',copyAmountMode);copyAmountMode();
async function loadCopyObserver(ack=false){
  const generation=++copyGeneration,clickVersion=copyClickVersion,noticeRequest=++copyNoticeRequest;
  try{const data=await api('/api/copy-observer');if(data.notification){acceptCopyNotice(data.notification,noticeRequest);if(ack&&(!copyNoticeLatest||copyNoticeLatest.epoch===data.notification.epoch)&&clickVersion===copyClickVersion&&!$('#copy-page').classList.contains('hidden'))ackCopyNotice(data.notification)}if(generation!==copyGeneration)return;
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
    const visibleKeys=new Set(signals.map(copyDetailKey));for(const key of copyDetails.keys())if(!visibleKeys.has(key))copyDetails.delete(key);
    while(copyMetadata.size>1000)copyMetadata.delete(copyMetadata.keys().next().value);
    $('#copy-signals').innerHTML=signals.length?signals.map(s=>{
      const x=s.estimate||{},buy=s.side==='BUY',best=x.best_price==null?'нет данных':`${esc(x.best_price)} pUSD`,depth=x.available_shares==null?'стакан недоступен':`${esc(x.available_shares)} долей на пяти уровнях`;
      const target=buy?`Расчёт: ${esc(x.desired_shares??'—')} долей ≈ ${esc(x.desired_value??'—')} pUSD${x.capped?' · ограничено потолком':''}`:'Размер продажи не рассчитан: виртуальная позиция и доля выхода пока не моделируются.';
      const key=copyDetailKey(s),detail=copyDetails.get(key),validKey=Number.isSafeInteger(Number(s.leader_id))&&/^[0-9a-f]{64}:[1-9][0-9]{0,5}$/.test(s.event_key||'');
      const delay=Number(s.observed_at)-Number(s.timestamp),latency=Number.isFinite(delay)&&delay>=0?`${copyNum(delay,1)} с`:'неизвестна';
      return `<article class="copy-signal" data-detail-key="${esc(key)}" data-leader-id="${esc(s.leader_id)}" data-event-key="${esc(s.event_key)}"><div class="copy-signal-head"><strong>${buy?'ПОКУПКА':'ПРОДАЖА'} · ${esc(s.outcome||'исход не указан')}</strong><span>Сделка: ${esc(copyTime(s.timestamp))} · обнаружена: ${esc(copyTime(s.observed_at))} · задержка: ${esc(latency)}</span></div><b>${esc(s.title||'Без названия')}</b><div class="copy-links"><span class="copy-event-link" data-token="${esc(s.token_id)}" data-condition="${esc(s.condition_id)}" data-slug="${esc(copySafeSlug(s.event_slug))}">${copyEventLink(s.event_slug)}</span> ${copyProfile(s.wallet)} ${copyTx(s.transaction_hash)} ${quickOpenButton(s.event_slug,s.token_id,s.condition_id)}</div><small>${esc(s.label)}${s.trader_name?' · '+esc(s.trader_name):''}${s.pseudonym?' · '+esc(s.pseudonym):''} · ${esc(s.size)} долей по ${esc(s.price)} pUSD ≈ ${esc(copyValue(s))} USD (цена × доли, без комиссий)</small><small>${target}</small><small>Лучший ${buy?'ASK':'BID'}: ${best} · по цене трейдера или лучше: ${depth}</small><small>Снимок стакана: ${esc(copyTime(x.book_at))} · это не исполнение и не гарантия доступности.</small><small>${copyBookLevels(x.book_levels)}</small><code>Кошелёк: ${esc(s.wallet||'—')} · Исход / token: ${esc(s.token_id||'—')}${s.outcome_index==null?'':` (индекс ${esc(s.outcome_index)})`} · Рынок / condition: ${esc(s.condition_id||'—')} · Транзакция: ${esc(s.transaction_hash||'—')}</code>${validKey?`<button type="button" class="button secondary copy-detail-toggle" aria-expanded="${detail?.open?'true':'false'}">${detail?.open?'Скрыть историю':'История и позиция'}</button><div class="copy-detail-area">${copyDetailHTML(detail)}</div>`:''}</article>`;
    }).join(''):'Новых сделок пока нет.';
    $('#copy-signals').querySelectorAll('.copy-signal').forEach(card=>{
      const button=card.querySelector('.copy-detail-toggle');if(!button)return;
      const key=card.dataset.detailKey;button.onclick=()=>{let d=copyDetails.get(key);if(!d){d={open:false};copyDetails.set(key,d)}d.open=!d.open;updateCopyDetail(key);if(d.open&&!d.data&&!d.error)fetchCopyDetail(key,card.dataset.leaderId,card.dataset.eventKey)};
      updateCopyDetail(key);
    });
    updateCopyLinks();lookupCopyLinks(signals);

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
setInterval(()=>{if(!document.hidden){pollCopyNotice();if(!$('#copy-page').classList.contains('hidden'))loadCopyObserver()}},15000);
$('#refresh').onclick=()=>loadStations();$('#today-label').textContent=new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric'}).format(new Date()).toUpperCase();setInterval(updateClock,30000);setInterval(()=>{if(overviewActive())loadOrderBooks()},30000);setInterval(()=>{if(overviewActive())loadStation(true)},60000);$('#logout').onclick=async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});location.assign('/login')}catch(e){toast(e.message)}};api('/api/session').then(data=>{csrfToken=data.csrf;api('/api/security-info').then(async s=>{setAiAccess(s.tunnel);setCopyNoticeAccess(s.tunnel);if(s.tunnel&&state.selected&&!state.demo)loadAiLatest(state.selected);tradeEnabled=!!s.trading_enabled;updateTradingControls();if(s.tunnel){const c=await api('/api/trading/account-config');if(c.configured)await loadAccountSnapshot()}}).catch(()=>{});return loadSettings()}).then(()=>loadStations()).catch(e=>toast(e.message));

// Portfolio is read-only. Never infer a station from a title or market name.
let portfolioGeneration=0;
const portfolioColors=['#4a70ff','#8c72f7','#48b9cd','#d39f65','#7290b8','#bb80aa','#90ba9c','#c9b777'];
function portfolioNum(n,digits=2){return n===null||n===undefined||!Number.isFinite(Number(n))?'—':Number(n).toLocaleString('ru-RU',{maximumFractionDigits:digits})}
function portfolioLine(points){
  if(!Array.isArray(points))return '<p>Данные PnL недоступны.</p>';
  const valid=points.filter(p=>p.value!==null&&p.timestamp!==null&&Number.isFinite(Number(p.value))&&Number.isFinite(Number(p.timestamp)));
  if(!valid.length)return '<p>Нет доступных данных PnL.</p>';
  const latest=valid.at(-1),latestValue=Number(latest.value),stamp=new Date(Number(latest.timestamp)*1000);
  const summary=`<div class="portfolio-pnl-value ${latestValue>0?'positive':latestValue<0?'negative':''}">${esc(portfolioNum(latestValue))} <span>pUSD</span></div><div class="portfolio-pnl-caption">Последнее доступное значение · ${esc(Number.isFinite(stamp.getTime())?stamp.toLocaleString('ru-RU'):'время неизвестно')}</div>`;
  if(valid.length<2)return summary+'<p>Для графика недостаточно данных.</p>';
  let min=Math.min(...valid.map(p=>Number(p.value))),max=Math.max(...valid.map(p=>Number(p.value))),start=Number(valid[0].timestamp),end=Number(valid.at(-1).timestamp),dx=end-start||1,dy=max-min||1;
  let segments=[],current=[];for(const p of points){if(p.value===null||p.timestamp===null||!Number.isFinite(Number(p.value))||!Number.isFinite(Number(p.timestamp))){if(current.length)segments.push(current);current=[];continue}current.push(`${20+360*(Number(p.timestamp)-start)/dx},${90-75*(Number(p.value)-min)/dy}`)}if(current.length)segments.push(current);
  return `${summary}<svg viewBox="0 0 400 100" preserveAspectRatio="none" role="img" aria-label="График PnL позиций за месяц"><defs><linearGradient id="portfolio-pnl-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#5268f8" stop-opacity=".35"/><stop offset="100%" stop-color="#5268f8" stop-opacity="0"/></linearGradient></defs><line x1="20" y1="90" x2="380" y2="90" stroke="#353d52"/>${segments.map(a=>`${a.length>1?`<polygon points="${a.join(' ')} ${a.at(-1).split(',')[0]},90 ${a[0].split(',')[0]},90" fill="url(#portfolio-pnl-fill)"/>`:''}<polyline points="${a.join(' ')}" fill="none" stroke="#6f7aff" stroke-width="2.5"/>`).join('')}</svg><small>Диапазон ${esc(portfolioNum(min))}…${esc(portfolioNum(max))} pUSD · ${esc(new Date(start*1000).toLocaleDateString('ru-RU'))} — ${esc(new Date(end*1000).toLocaleDateString('ru-RU'))}</small>`;
}
function portfolioDistribution(positions,partial){
  if(!Array.isArray(positions))return '<p>Данные недоступны.</p>';
  let valid=positions.filter(p=>p.value!==null&&Number.isFinite(Number(p.value))&&Number(p.value)>0),sum=valid.reduce((a,p)=>a+Number(p.value),0),missing=positions.some(p=>p.value===null);
  if(!sum)return '<p>Нет доступных положительных оценок позиций.</p>';
  let offset=0;const circles=valid.map((p,i)=>{let fraction=Number(p.value)/sum,entry=`<circle cx="100" cy="100" r="70" fill="none" stroke="${portfolioColors[i%portfolioColors.length]}" stroke-width="28" pathLength="100" stroke-dasharray="${fraction*100} ${100-fraction*100}" stroke-dashoffset="${-offset}" transform="rotate(-90 100 100)"/>`;offset+=fraction*100;return entry}).join('');
  return `<svg viewBox="0 0 200 200" role="img" aria-label="Распределение стоимости имеющихся позиций">${circles}</svg>${valid.map((p,i)=>`<div class="portfolio-key"><span class="portfolio-swatch" style="background:${portfolioColors[i%portfolioColors.length]}"></span>${esc(p.title||p.event_slug||p.asset)} · ${esc(p.outcome)} · ${esc(portfolioNum(p.value))} pUSD</div>`).join('')}${missing||partial?'<p>Некоторые позиции или их оценки отсутствуют; доли относятся только к показанным данным.</p>':''}`;
}
async function portfolioOpen(slug){
  if(!slug||!/^[-a-z0-9]{1,200}$/.test(slug)){toast('Не удалось определить событие. Откройте его на Polymarket или привяжите рынок вручную.');return}
  try{
    const all=await Promise.all(state.stations.map(async station=>({station,bindings:await api(`/api/stations/${station.id}/bindings`)})));
    const matches=all.filter(x=>x.bindings.some(b=>b.market_slug===slug));
    if(!matches.length){if(confirm('Событие не привязано к станции. Перейти к поиску рынка и предложению привязки? Для непогодного рынка станцию выбирать нельзя.')){page('markets');openMarketFirst(slug,true)}return}
    let match=matches[0];if(matches.length>1){const choices=matches.map((x,i)=>`${i+1}. ${x.station.name} (${x.station.station_code})`).join('\n');const selected=prompt(`Рынок привязан к нескольким станциям. Выберите номер:\n${choices}`);if(selected===null)return;const index=Number(selected)-1;if(!Number.isInteger(index)||!matches[index])return;match=matches[index]}
    state.selected=match.station.id;page('weather',false);renderStationTabs();await loadStation();if(!state.bindings.some(b=>b.market_slug===slug)){toast('Привязка изменилась. Обновите портфель.');return}state.market=slug;renderMarketTabs();await loadMarket();
  }catch(e){toast('Не удалось проверить привязки: '+e.message)}
}
// Rows display only fields supplied by the snapshot; opening a market remains a separate action.
function portfolioMarket(title,subtitle,badge,subtitleTitle){
  const side=String(badge||'').toLowerCase(),style=side==='yes'?'yes':side==='no'?'no':'neutral';
  return `<div class="portfolio-market"><span class="portfolio-market-icon" aria-hidden="true">◈</span><div class="portfolio-market-copy"><b title="${esc(title||'Рынок не определён')}">${esc(title||'Рынок не определён')}</b><small title="${esc(subtitleTitle||subtitle||'')}">${badge?`<span class="portfolio-pill ${style}">${esc(badge)}</span> `:''}${esc(subtitle||'')}</small></div></div>`;
}
function portfolioCell(label,value,extra=''){return `<div class="portfolio-cell" data-label="${esc(label)}"><strong class="${extra}">${esc(value)}</strong></div>`}
function portfolioAction(slug){return `<button type="button" class="button secondary portfolio-open" data-slug="${esc(slug||'')}">Открыть ↗</button>`}
function portfolioRow(search,market,cells,slug){return `<div class="portfolio-row" data-search="${esc(search)}">${market}${cells}${portfolioAction(slug)}</div>`}
function portfolioRows(data,headers,make,empty,unavailable){
  if(!Array.isArray(data))return `<div class="portfolio-empty">${esc(unavailable)}</div>`;
  if(!data.length)return `<div class="portfolio-empty">${esc(empty)}</div>`;
  return `<div class="portfolio-table-head"><span>РЫНОК</span>${headers.map(h=>`<span>${esc(h)}</span>`).join('')}<span></span></div><div class="portfolio-row-list">${data.map(make).join('')}</div><div class="portfolio-filter-empty" hidden>По вашему запросу ничего не найдено.</div>`;
}
function filterPortfolioRows(){
  const q=$('#portfolio-search').value.trim().toLocaleLowerCase();
  for(const id of ['portfolio-positions','portfolio-orders','portfolio-intents','portfolio-activity']){
    const root=$('#'+id),rows=[...root.querySelectorAll('.portfolio-row')];let visible=0;
    for(const row of rows){row.hidden=!!q&&!row.dataset.search.toLocaleLowerCase().includes(q);if(!row.hidden)visible++}
    const empty=root.querySelector('.portfolio-filter-empty');if(empty)empty.hidden=!q||visible>0;
  }
}
$('#portfolio-search').addEventListener('input',filterPortfolioRows);
async function loadPortfolio(){
  const seq=++portfolioGeneration;$('#portfolio-refresh').disabled=true;$('#portfolio-status').textContent='Загружаем снимок…';for(const id of ['portfolio-balances','portfolio-pnl','portfolio-allocation','portfolio-positions','portfolio-orders','portfolio-intents','portfolio-activity'])$('#'+id).textContent='';
  try{
    const data=await api('/api/trading/portfolio');if(seq!==portfolioGeneration)return;
    $('#portfolio-status').textContent=[`Получено терминалом: ${new Date().toLocaleString('ru-RU')}. Кошелёк ${data.wallet}.`,...(data.errors||[]),data.orders_partial?'Список ордеров CLOB усечён.':'',data.positions_partial?'Показаны первые 500 позиций.':'',data.activity_partial?'Показаны первые 500 действий.':'',data.quotes_partial?'Стаканы запрошены только для первых 100 токенов.':''].filter(Boolean).join(' ');
    const total=data.balance!==null&&data.balance!==undefined&&data.value!==null&&data.value!==undefined&&Number.isFinite(Number(data.balance))&&Number.isFinite(Number(data.value))?Number(data.balance)+Number(data.value):null;
    $('#portfolio-balances').innerHTML=`<strong>${esc(portfolioNum(total))} <span>pUSD</span></strong><small class="portfolio-total-note">Сумма независимых снимков</small><div class="portfolio-balance-breakdown"><div><small>Баланс CLOB</small><b>${esc(portfolioNum(data.balance))} pUSD</b></div><div><small>Оценка позиций</small><b>${esc(portfolioNum(data.value))} pUSD</b></div></div>`;
    $('#portfolio-pnl').innerHTML=portfolioLine(data.pnl);$('#portfolio-allocation').innerHTML=portfolioDistribution(data.positions,data.positions_partial);
    $('#portfolio-positions').innerHTML=portfolioRows(data.positions,['ДОЛИ','СР. ВХОД / BID','ОЦЕНКА','PNL'],p=>portfolioRow([p.title,p.event_slug,p.outcome].join(' '),portfolioMarket(p.title||p.event_slug,p.redeemable?'Доступно погашение':'Открытая позиция',p.outcome),portfolioCell('Доли',portfolioNum(p.size))+portfolioCell('Средний вход / BID',`${p.avg_price==null?'—':priceCents(p.avg_price)} / ${p.bid==null?(p.bid_at==null?'недоступен':'нет BID'):priceCents(p.bid)}${p.bid_at!=null?' · '+new Date(p.bid_at*1000).toLocaleString('ru-RU'):''}${p.bid_size!=null?' · '+portfolioNum(p.bid_size)+' долей':''}`)+portfolioCell('Оценка',portfolioNum(p.value)+' pUSD')+portfolioCell('PnL',portfolioNum(p.pnl)+' pUSD',p.pnl==null?'':Number(p.pnl)>0?'positive':Number(p.pnl)<0?'negative':''),p.event_slug),'Позиций нет.','Позиции недоступны.');
    const byAsset=new Map();for(const p of [...(data.positions||[]),...(data.activity||[]),...(data.intents||[]).map(i=>({asset:i.asset,event_slug:i.slug}))])if(p.asset&&p.event_slug){let info=byAsset.get(p.asset)||{slugs:new Set(),titles:new Set()};info.slugs.add(p.event_slug);if(p.title)info.titles.add(p.title);byAsset.set(p.asset,info)}
    $('#portfolio-orders').innerHTML=portfolioRows(data.orders,['ЦЕНА','СОПОСТАВЛЕНО','СТАТУС'],o=>{let info=byAsset.get(o.asset_id),slug=info?.slugs.size===1?[...info.slugs][0]:'',title=slug&&info.titles.size===1?[...info.titles][0]:slug||`Ордер ${o.id}`,id=String(o.id||''),shortId=id.length>20?id.slice(0,10)+'…'+id.slice(-6):id;let price=o.price==null?'—':portfolioNum(Number(o.price)*100,4)+'¢';return portfolioRow([title,slug,o.id,o.side,o.status].join(' '),portfolioMarket(title,shortId?'ID '+shortId:'',o.side,id),portfolioCell('Цена',price)+portfolioCell('Сопоставлено',`${portfolioNum(o.size_matched)} / ${portfolioNum(o.original_size)}`)+portfolioCell('Статус',o.status||'—'),slug)},'Открытых ордеров в полученном снимке нет.','Ордера недоступны.');
    $('#portfolio-intents').innerHTML=portfolioRows(data.intents,['ТИП','ОБЪЁМ','СОСТОЯНИЕ'],x=>{let r=x.reconciliation,detail=r?.size_matched!=null?`Сопоставлено ${r.size_matched}, подтверждено ${r.confirmed_size}, ошибка ${r.failed_size}`:'Локальная запись терминала',status=x.state==='accepted'?'Принят CLOB, исполнение не подтверждено':x.state==='not_sent'?'Не отправлен':'Результат неизвестен';return portfolioRow([x.slug,x.side,x.state,detail].join(' '),portfolioMarket(x.slug,detail,x.side),portfolioCell('Тип',x.execution_mode==='market'?'FAK':'GTC')+portfolioCell('Объём',`${portfolioNum(x.size)} ${x.execution_mode==='market'&&x.side==='BUY'?'pUSD':'долей'}`)+portfolioCell('Состояние',status),x.slug)},'Записей терминала нет.','Журнал терминала недоступен.');
    $('#portfolio-activity').innerHTML=portfolioRows(data.activity,['ДОЛИ','ВХОД / ВЫХОД','СУММА','ВРЕМЯ'],p=>{let date=new Date(Number(p.timestamp)*1000),when=p.timestamp!=null&&Number.isFinite(date.getTime())?date.toLocaleString('ru-RU'):'Время недоступно',type=String(p.type||'').toUpperCase(),side=String(p.side||'').toUpperCase(),action=type==='TRADE'&&side==='BUY'?'Покупка':type==='TRADE'&&side==='SELL'?'Продажа':[p.type,p.side].filter(Boolean).join(' ')||'Действие';return portfolioRow([p.title,p.event_slug,p.outcome,action].join(' '),portfolioMarket(p.title||p.event_slug,action,p.outcome),portfolioCell('Доли',portfolioNum(p.size))+portfolioCell('Вход / выход',type==='TRADE'&&side==='BUY'?`Вход ${p.price==null?'—':priceCents(p.price)}`:type==='TRADE'&&side==='SELL'?`Вход — · выход ${p.price==null?'—':priceCents(p.price)}`:'—')+portfolioCell('Сумма',portfolioNum(p.value)+' pUSD')+portfolioCell('Время',when),p.event_slug)},'Активность отсутствует.','История недоступна.');
    filterPortfolioRows();
  }catch(e){if(seq===portfolioGeneration){$('#portfolio-status').textContent=e.message;for(const id of ['portfolio-balances','portfolio-pnl','portfolio-allocation','portfolio-positions','portfolio-orders','portfolio-intents','portfolio-activity'])$('#'+id).textContent=''}}
  finally{if(seq===portfolioGeneration)$('#portfolio-refresh').disabled=false}
}
// Switching sections never reloads the account or changes the selected market.
const portfolioTabs=$$('.portfolio-tab');
function selectPortfolioTab(tab,focus=false){
  if(!portfolioTabs.includes(tab))return;
  for(const item of portfolioTabs){
    const selected=item===tab;
    item.setAttribute('aria-selected',String(selected));item.tabIndex=selected?0:-1;
    const panel=document.getElementById(item.getAttribute('aria-controls'));if(panel)panel.hidden=!selected;
  }
  if(focus)tab.focus();
}
for(const tab of portfolioTabs){
  tab.addEventListener('click',()=>selectPortfolioTab(tab));
  tab.addEventListener('keydown',event=>{
    const index=portfolioTabs.indexOf(tab);let next;
    if(event.key==='ArrowRight')next=(index+1)%portfolioTabs.length;
    else if(event.key==='ArrowLeft')next=(index-1+portfolioTabs.length)%portfolioTabs.length;
    else if(event.key==='Home')next=0;
    else if(event.key==='End')next=portfolioTabs.length-1;
    else return;
    event.preventDefault();selectPortfolioTab(portfolioTabs[next],true);
  });
}
$('#portfolio-refresh').onclick=loadPortfolio;
$('#portfolio-page').addEventListener('click',e=>{const b=e.target.closest('.portfolio-open');if(b)portfolioOpen(b.dataset.slug)});

// Auto Scalping is currently a demo-only public-data simulator.
let scalpGeneration=0,scalpDirty=false,scalpBusy=false;
function scalpNumber(x){return x===null||x===undefined||!Number.isFinite(Number(x))?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:4}).format(Number(x))}
function scalpChart(journal){
  const sells=journal.filter(x=>x.kind==='SELL'&&x.pnl!==null&&Number.isFinite(Number(x.pnl))).slice().reverse();
  if(!sells.length)return '<p>Для графика нужна хотя бы одна виртуальная продажа.</p>';
  let total=0;const points=[0,...sells.map(x=>total+=Number(x.pnl))],min=Math.min(...points),max=Math.max(...points),span=max-min||1;
  const coords=points.map((p,i)=>`${10+380*i/(points.length-1)},${85-70*(p-min)/span}`);
  return `<div class="scalp-graph-title">Реализованный результат в последних 200 записях: ${esc(scalpNumber(total))} pUSD</div><svg viewBox="0 0 400 100" preserveAspectRatio="none" role="img" aria-label="График виртуального реализованного результата"><line x1="10" y1="85" x2="390" y2="85" stroke="#34463b"/><polyline points="${coords.join(' ')}" fill="none" stroke="#89d9ab" stroke-width="2"/></svg><small>Только показанный журнал, без открытых позиций и результата разрешения.</small>`;
}
async function loadScalp(){
  const generation=++scalpGeneration;
  try{const data=await api('/api/auto-scalp');if(generation!==scalpGeneration)return;
    const cfg=data.config||{},settings=cfg.settings||{},positions=Array.isArray(data.positions)?data.positions:[],journal=Array.isArray(data.journal)?data.journal:[];
    const form=$('#scalp-form');if(!scalpDirty)for(const name of Object.keys(settings))if(form.elements[name])form.elements[name].value=settings[name];
    form.querySelector('button[type=submit]').disabled=!!cfg.enabled||scalpBusy;
    $('#scalp-start').disabled=!!cfg.enabled||scalpBusy;$('#scalp-stop').disabled=!cfg.enabled||scalpBusy;
    const service={running:'работает',stale:'давно не отвечала',not_running:'не запущена'};
    $('#scalp-status').textContent=`Служба: ${service[cfg.service]||'неизвестно'} · новые входы: ${cfg.enabled?'включены':'остановлены'} · ${cfg.message||'ожидание'}${cfg.scan_at?' · проверка '+copyTime(cfg.scan_at):''}`;
    const opened=positions.filter(p=>!p.closed),exposure=opened.reduce((n,p)=>n+Number(p.remaining)*Number(p.entry_price),0);
    $('#scalp-stats').innerHTML=`<div><small>ДЕМО · СВОБОДНО</small><strong>${esc(scalpNumber(cfg.balance))} pUSD</strong></div><div><small>ОТКРЫТО ПОЗИЦИЙ</small><strong>${opened.length}</strong></div><div><small>СТОИМОСТЬ ВХОДА ОСТАТКА</small><strong>≈ ${esc(scalpNumber(exposure))} pUSD</strong></div>`;
    $('#scalp-positions').innerHTML=positions.length?positions.map(p=>`<article class="scalp-row"><div><b>${esc(p.title||'Рынок')} · ${esc(p.label)} · NO</b><small>${p.closed?'Закрыта в демо':'Открыта в демо'} · вход ${esc(copyTime(p.opened))}</small><small>Вход ${esc(scalpNumber(p.entry_price))} pUSD · цель ${esc(scalpNumber(p.target))} pUSD · остаток ${esc(scalpNumber(p.remaining))} из ${esc(scalpNumber(p.original))} долей</small><small>Реализованный результат: ${esc(scalpNumber(p.realized))} pUSD · нереализованный не рассчитывается.</small>${copyEventLink(p.event_slug)} ${quickOpenButton(p.event_slug,p.token,p.condition_id)}</div></article>`).join(''):'Демо-позиций пока нет.';
    const byId=new Map(positions.map(p=>[p.id,p]));
    $('#scalp-journal').innerHTML=journal.length?journal.map(x=>`<article class="scalp-row"><div><b>${x.kind==='BUY'?'ВИРТУАЛЬНАЯ ПОКУПКА':'ВИРТУАЛЬНАЯ ПРОДАЖА'} · ${esc(copyTime(x.timestamp))}</b><small>${esc(byId.get(x.position_id)?.title||x.title||'Позиция #'+x.position_id)} · ${esc(byId.get(x.position_id)?.label||x.label||'')}</small><small>${esc(scalpNumber(x.size))} долей × ${esc(scalpNumber(x.price))} pUSD · оценка комиссии ${esc(scalpNumber(x.fee))} pUSD${x.pnl!==null?' · результат '+esc(scalpNumber(x.pnl))+' pUSD':''}</small><small>${esc(x.note)}</small>${quickOpenButton((byId.get(x.position_id)||x).event_slug,(byId.get(x.position_id)||x).token,(byId.get(x.position_id)||x).condition_id)}</div></article>`).join(''):'Виртуальных сделок пока нет.';
    $('#scalp-chart').innerHTML=scalpChart(journal);
  }catch(e){if(generation===scalpGeneration)$('#scalp-status').textContent='Демо-скальпер недоступен: '+e.message}
}
$('#scalp-form').addEventListener('input',()=>{scalpDirty=true});
$('#scalp-refresh').onclick=()=>loadScalp();
async function scalpAction(action,settings){
  if(scalpBusy)return;
  if(!confirm(action==='start'?'Запустить только демо? Реальные заявки не отправляются.':action==='stop'?'Остановить новые демо-входы? Виртуальные выходы продолжат наблюдаться.':'Сохранить настройки демо?'))return;
  scalpBusy=true;$('#scalp-form-status').textContent='Выполняется…';
  try{await api('/api/auto-scalp',{method:'POST',body:JSON.stringify({mode:'demo',action,settings,confirm:true})});if(action==='settings')scalpDirty=false;$('#scalp-form-status').textContent='Готово.';await loadScalp()}
  catch(e){$('#scalp-form-status').textContent=e.message}finally{scalpBusy=false;await loadScalp()}
}
$('#scalp-form').onsubmit=e=>{e.preventDefault();const form=e.currentTarget,settings=Object.fromEntries(new FormData(form));settings.max_positions=Number(settings.max_positions);settings.interval_seconds=Number(settings.interval_seconds);scalpAction('settings',settings)};
$('#scalp-start').onclick=()=>scalpAction('start');$('#scalp-stop').onclick=()=>scalpAction('stop');
setInterval(()=>{if(!document.hidden&&!$('#scalp-page').classList.contains('hidden'))loadScalp()},15000);

setInterval(()=>{if(historyVisible()&&historyKey())syncBookHistory(true)},60000);
