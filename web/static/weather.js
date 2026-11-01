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
    const smooth = t => t*t*(3-2*t);
    let y;
    if (seconds < sunrise) y = HORIZON + 45*(1-smooth(seconds/sunrise));
    else if (seconds <= sunset) { const t=(seconds-sunrise)/(sunset-sunrise); y=HORIZON-70*4*t*(1-t); }
    else y=HORIZON+45*smooth((seconds-sunset)/(86400-sunset));
    return {x:DAY_START_X + DAY_WIDTH*seconds/86400, y};
  }
  function dailyPath(sunrise, sunset) {
    if (!curvePoint(0, sunrise, sunset)) return null;
    const samples = new Set([0, 86400, sunrise, sunset]);
    for(let t=600;t<86400;t+=600) samples.add(t);
    return [...samples].sort((a,b)=>a-b).map((t,i)=>{const p=curvePoint(t,sunrise,sunset);return `${i?'L':'M'}${p.x.toFixed(2)},${p.y.toFixed(2)}`}).join(' ');
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
      if (Number.isFinite(ms) && Number.isFinite(c) && ms>=start && ms<=now) byTime.set(ms,c);
    }
    const points=[...byTime].sort((a,b)=>a[0]-b[0]).map(([ms,c])=>({ms,c,value:unit==='F'?c*9/5+32:c}));
    if (!points.length) return {points:[],ticks:[],start,end:now,segments:[]};
    const values=points.map(p=>p.value), lo=Math.min(...values), hi=Math.max(...values);
    const pad=Math.max((hi-lo)*0.12,unit==='F'?0.9:0.5);
    const desired=(hi-lo+pad*2)/4, power=Math.pow(10,Math.floor(Math.log10(desired)));
    const normalized=desired/power, step=(normalized<=1?1:normalized<=2?2:normalized<=2.5?2.5:normalized<=5?5:10)*power;
    const min=Math.floor((lo-pad)/step)*step, max=Math.ceil((hi+pad)/step)*step;
    const ticks=[];
    for(let i=0;i<=Math.round((max-min)/step);i++){const value=+(min+i*step).toPrecision(12);ticks.push({value,y:100-(value-min)/(max-min)*100})}
    for(const p of points){p.x=(p.ms-start)/86400000*100;p.y=100-(p.value-min)/(max-min)*100}
    const segments=[];
    for(const p of points){if(!segments.length || p.ms-segments[segments.length-1].at(-1).ms>2*3600000)segments.push([]);segments.at(-1).push(p)}
    return {points,ticks,segments,start,end:now,min,max,step};
  }
  function nearestChartPoint(points, ms) {
    if (!points.length || !Number.isFinite(ms)) return -1;
    let lo=0,hi=points.length-1;
    while(lo<hi){const mid=Math.floor((lo+hi)/2);if(points[mid].ms<ms)lo=mid+1;else hi=mid}
    return lo>0 && Math.abs(points[lo-1].ms-ms)<=Math.abs(points[lo].ms-ms)?lo-1:lo;
  }
  const result = {zonedEpoch, precipitationForecast, localClock, shiftDay, solarSeconds, curvePoint, dailyPath, observationsForDay, metarChart, nearestChartPoint, DAY_START_X, DAY_WIDTH, HORIZON};
  if (typeof module !== 'undefined' && module.exports) module.exports = result;
  else root.WeatherView = result;
})(typeof globalThis !== 'undefined' ? globalThis : this);
