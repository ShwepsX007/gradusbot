/* Forecast helpers shared by the browser and Node tests. */
(function (root) {
  'use strict';
  function zonedEpoch(iso, tz) {
    if (!iso || !tz || !/^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(iso)) return null;
    const [date, clock] = iso.split('T');
    const [y, m, d] = date.split('-').map(Number), [h, min] = clock.split(':').map(Number);
    const target = Date.UTC(y, m - 1, d, h, min);
    let guess = target;
    try {
      const fmt = new Intl.DateTimeFormat('en-GB', {timeZone:tz, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
      for (let i = 0; i < 3; i++) {
        const parts = Object.fromEntries(fmt.formatToParts(new Date(guess)).map(p => [p.type, p.value]));
        const local = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute);
        const delta = target - local;
        if (!delta) break;
        guess += delta;
      }
      return guess;
    } catch { return null; }
  }
  function precipitationForecast(forecast, now = Date.now()) {
    if (!forecast?.timezone || !Array.isArray(forecast.hourly?.time)) return null;
    let best = null;
    forecast.hourly.time.forEach((time, index) => {
      const end = zonedEpoch(time, forecast.timezone);
      // An hourly accumulation/probability refers to the preceding hour.
      // Never use another, more distant hour as a substitute for missing data.
      if (end !== null && end > now && end <= now + 3600000 && (!best || end < best.end)) best = {end, index};
    });
    if (!best) return null;
    const probability = forecast.hourly.precipitation_probability?.[best.index];
    if (typeof probability !== 'number' || !Number.isFinite(probability) || probability < 0 || probability > 100) return null;
    try {
      const fmt = new Intl.DateTimeFormat('ru-RU', {timeZone:forecast.timezone, hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
      return {probability, start:fmt.format(new Date(best.end - 3600000)), end:fmt.format(new Date(best.end))};
    } catch { return null; }
  }
  function localClock(epochMs, tz, formatter) {
    if (!Number.isFinite(epochMs) || !tz) return null;
    try {
      const f = formatter || new Intl.DateTimeFormat('en-GB', {timeZone:tz, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'});
      const p = Object.fromEntries(f.formatToParts(new Date(epochMs)).map(x => [x.type, x.value]));
      return {day:`${p.year}-${p.month}-${p.day}`, seconds:+p.hour*3600 + +p.minute*60 + +p.second, time:`${p.hour}:${p.minute}`};
    } catch { return null; }
  }
  function shiftDay(day, offset) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day || '')) return null;
    const d = new Date(day + 'T00:00:00Z');
    if (!Number.isFinite(d.getTime())) return null;
    d.setUTCDate(d.getUTCDate() + offset);
    return d.toISOString().slice(0,10);
  }
  function solarSeconds(iso) {
    if (typeof iso !== 'string') return null;
    const m = iso.match(/T(\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m || +m[1] > 23 || +m[2] > 59 || +(m[3] || 0) > 59) return null;
    return +m[1]*3600 + +m[2]*60 + +(m[3] || 0);
  }
  const DAY_START_X = 45, DAY_WIDTH = 710, HORIZON = 128;
  function curvePoint(seconds, sunrise, sunset) {
    if (![seconds,sunrise,sunset].every(Number.isFinite) || sunrise < 0 || sunset > 86400 || sunrise >= sunset || seconds < 0 || seconds > 86400) return null;
    // Monotone cubic Hermite segments: matching slopes at sunrise and sunset
    // make the transition from night to day smooth rather than kinked.
    const mid=(sunrise+sunset)/2, half=mid-sunrise;
    const harmonic=(a,b)=>a && b ? 2*a*b/(a+b) : (a||b);
    const riseSlope=-harmonic(sunrise ? 45/sunrise : 0, 70/half);
    const setSlope=harmonic(sunset<86400 ? 45/(86400-sunset) : 0, 70/half);
    function hermite(t,x0,x1,y0,y1,m0,m1){
      const h=x1-x0,u=(t-x0)/h;
      return (2*u**3-3*u**2+1)*y0+(u**3-2*u**2+u)*h*m0+(-2*u**3+3*u**2)*y1+(u**3-u**2)*h*m1;
    }
    let y;
    if(seconds<sunrise)y=hermite(seconds,0,sunrise,HORIZON+45,HORIZON,0,riseSlope);
    else if(seconds<=mid)y=hermite(seconds,sunrise,mid,HORIZON,HORIZON-70,riseSlope,0);
    else if(seconds<=sunset)y=hermite(seconds,mid,sunset,HORIZON-70,HORIZON,0,setSlope);
    else y=hermite(seconds,sunset,86400,HORIZON,HORIZON+45,setSlope,0);
    return {x:DAY_START_X + DAY_WIDTH*seconds/86400, y};
  }
  function dailyPath(sunrise, sunset) {
    if (!curvePoint(0, sunrise, sunset)) return null;
    const samples = new Set([0, 86400, sunrise, sunset]);
    for(let t=120;t<86400;t+=120) samples.add(t);
    return [...samples].sort((a,b)=>a-b).map((t,i)=>{const p=curvePoint(t,sunrise,sunset);return `${i?'L':'M'}${p.x.toFixed(2)},${p.y.toFixed(2)}`}).join(' ');
  }
  function hourlyExtremaForDay(forecast, day) {
    const hourly=forecast?.hourly;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day || '') || !Array.isArray(hourly?.time) || !Array.isArray(hourly?.temperature_2m)) return null;
    const points=[];
    hourly.time.forEach((t,i)=>{
      if(typeof t!=='string' || !t.startsWith(day+'T'))return;
      const match=t.match(/^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})(?::(\d{2}))?$/);
      const temp=hourly.temperature_2m[i];
      if(!match||+match[1]>23||+match[2]>59||+(match[3]||0)>59||typeof temp!=='number'||!Number.isFinite(temp))return;
      points.push({time:`${match[1]}:${match[2]}`,seconds:+match[1]*3600 + +match[2]*60 + +(match[3]||0),temp});
    });
    if(!points.length)return null;
    points.sort((a,b)=>a.seconds-b.seconds);
    return {min:points.reduce((a,b)=>b.temp<a.temp?b:a),max:points.reduce((a,b)=>b.temp>a.temp?b:a),count:points.length};
  }
  function metarIcon(metar, isDay=true) {
    if(!metar || typeof metar!=='object')return {kind:'unknown',label:'Нет данных METAR'};
    const precip=Array.isArray(metar.precipitation?.codes)?metar.precipitation.codes:[];
    const desc=metar.precipitation?.description;
    if(precip.length){
      const codes=precip.join(' ');
      const kind=/TS/.test(codes)?'storm':/(?:SN|SG|IC|PL|GR|GS)/.test(codes)?'snow':'rain';
      return {kind,label:desc||'Осадки по METAR'};
    }
    const code=metar.cloud?.code;
    const label=metar.cloud?.description;
    if(['CLR','NSC','CAVOK'].includes(code))return {kind:'clear-neutral',label:label||'Значимой облачности не обнаружено'};
    if(['SKC','NCD'].includes(code))return {kind:isDay===null?'clear-neutral':isDay?'clear':'clear-night',label:label||'Облака не обнаружены'};
    if(['FEW','SCT'].includes(code))return {kind:isDay===null?'cloudy':isDay?'partial':'partial-night',label:label||'Переменная облачность'};
    if(['BKN','OVC','VV'].includes(code))return {kind:'cloudy',label:label||'Облачно'};
    return {kind:'unknown',label:'Состояние погоды не указано в METAR'};
  }
  function observationsForDay(history, tz, day, now = Date.now()) {
    if (!Array.isArray(history) || !tz || !day) return [];
    let formatter;
    try { formatter = new Intl.DateTimeFormat('en-GB', {timeZone:tz, year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'}); }
    catch { return []; }
    const points = [];
    for (const item of history) {
      if (!Array.isArray(item) || item[1] == null || item[0] == null) continue;
      const ms=Number(item[0])*1000, temp=Number(item[1]);
      if (!Number.isFinite(ms) || !Number.isFinite(temp) || ms > now) continue;
      const local=localClock(ms,tz,formatter);
      if (local?.day === day) points.push({ms,temp,seconds:local.seconds,time:local.time});
    }
    return points.sort((a,b)=>a.seconds-b.seconds || a.ms-b.ms);
  }
  // Last 24 elapsed hours, with no extrapolation beyond actual reports.
  function metarChart(history, unit='C', now=Date.now()) {
    const start=now-86400000, byTime=new Map();
    if (Array.isArray(history)) for (const row of history) {
      if (!Array.isArray(row) || row[0] == null || row[1] == null || row[0] === '' || row[1] === '') continue;
      const ms=Number(row[0])*1000, c=Number(row[1]);
      if (Number.isFinite(ms) && Number.isFinite(c) && c>=-150 && c<=150 && ms>=start && ms<=now) byTime.set(ms,c);
    }
    // The grid is anchored in whole degrees Celsius, with Fahrenheit equivalents.
    const points=[...byTime].sort((a,b)=>a[0]-b[0]).map(([ms,c])=>({ms,c,value:c}));
    if (!points.length) return {points:[],ticks:[],start,end:now,segments:[]};
    const values=points.map(p=>p.c), lo=Math.min(...values), hi=Math.max(...values);
    const min=Math.floor(lo)-1, max=Math.ceil(hi)+1, step=1;
    const ticks=[];
    for(let value=min;value<=max;value++)ticks.push({value,y:100-(value-min)/(max-min)*100});
    for(const p of points){p.x=(p.ms-start)/86400000*100;p.y=100-(p.c-min)/(max-min)*100}
    const segments=[];
    for(const p of points){if(!segments.length || p.ms-segments[segments.length-1].at(-1).ms>2*3600000)segments.push([]);segments.at(-1).push(p)}
    return {points,ticks,segments,start,end:now,min,max,step};
  }
  function halfHourTicks(start, end, tz) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || end<=start || end-start>86400000) return [];
    let formatter;
    try { formatter=new Intl.DateTimeFormat('en-GB',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}); }
    catch { return []; }
    const ticks=[];
    for(let t=Math.ceil(start/60000)*60000;t<=end;t+=60000){
      const local=localClock(t,tz,formatter);
      if(local && local.seconds%1800===0)ticks.push({x:(t-start)/(end-start)*100,time:local.time,ms:t});
    }
    return ticks;
  }
  function nearestChartPoint(points, ms) {
    if (!points.length || !Number.isFinite(ms)) return -1;
    let lo=0,hi=points.length-1;
    while(lo<hi){const mid=Math.floor((lo+hi)/2);if(points[mid].ms<ms)lo=mid+1;else hi=mid}
    return lo>0 && Math.abs(points[lo-1].ms-ms)<=Math.abs(points[lo].ms-ms)?lo-1:lo;
  }
  function zoneOffsetMinutes(tz, now) {
    const clock=localClock(now,tz);if(!clock)return null;
    const midnight=Date.parse(clock.day+'T00:00:00Z');
    return Math.round((midnight+clock.seconds*1000-Math.floor(now/1000)*1000)/60000);
  }
  function scanTimeInfo(item, deviceTz, now=Date.now()) {
    const local=localClock(now,item?.timezone);
    if(!local)return {rank:4,seconds:Infinity,phase:'Местное время неизвестно',local:null,difference:null};
    const target=item.date;
    const rank=!target?3:target===local.day?0:target>local.day?1:2;
    const phase=['Идут местные сутки','Местные сутки ещё не начались','Местные сутки прошли','Дата наблюдения не определена'][rank];
    const stationOffset=zoneOffsetMinutes(item.timezone,now),deviceOffset=zoneOffsetMinutes(deviceTz,now);
    return {rank,phase,seconds:local.seconds,local,difference:stationOffset!==null&&deviceOffset!==null?stationOffset-deviceOffset:null};
  }
  function sortTemperatureMarkets(events, deviceTz, now=Date.now()) {
    return (Array.isArray(events)?events:[]).map(event=>({event,time:scanTimeInfo(event,deviceTz,now)}))
      .sort((a,b)=>a.time.rank-b.time.rank||a.time.seconds-b.time.seconds||(a.event.date||'').localeCompare(b.event.date||'')||String(a.event.title).localeCompare(String(b.event.title)));
  }
  const result = {zonedEpoch, precipitationForecast, localClock, shiftDay, solarSeconds, curvePoint, dailyPath, hourlyExtremaForDay, metarIcon, observationsForDay, metarChart, halfHourTicks, nearestChartPoint, zoneOffsetMinutes, scanTimeInfo, sortTemperatureMarkets, DAY_START_X, DAY_WIDTH, HORIZON};
  if (typeof module !== 'undefined' && module.exports) module.exports = result;
  else root.WeatherView = result;
})(typeof globalThis !== 'undefined' ? globalThis : this);
