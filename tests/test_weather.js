const assert = require('node:assert/strict');
const {zonedEpoch, precipitationForecast} = require('../web/static/weather.js');
const ny = {timezone:'America/New_York', hourly:{time:['2026-06-01T12:00','2026-06-01T13:00','2026-06-01T14:00'], precipitation_probability:[10,70,30]}};
assert.equal(zonedEpoch('2026-06-01T13:00', 'America/New_York'), Date.parse('2026-06-01T17:00:00Z'));
assert.deepEqual(precipitationForecast(ny, Date.parse('2026-06-01T16:15:00Z')), {probability:70,start:'12:00',end:'13:00'});
assert.deepEqual(precipitationForecast(ny, Date.parse('2026-06-01T17:00:00Z')), {probability:30,start:'13:00',end:'14:00'});
assert.equal(precipitationForecast(ny, Date.parse('2026-06-01T19:00:00Z')), null);
assert.equal(precipitationForecast({timezone:'UTC',hourly:{time:['2026-06-01T13:00'],precipitation_probability:[null]}}, Date.parse('2026-06-01T12:30:00Z')), null);
assert.deepEqual(precipitationForecast({timezone:'UTC',hourly:{time:['2026-06-01T13:00'],precipitation_probability:[0]}}, Date.parse('2026-06-01T12:30:00Z')), {probability:0,start:'12:00',end:'13:00'});
assert.deepEqual(precipitationForecast({timezone:'America/New_York',hourly:{time:['2026-06-02T00:00'],precipitation_probability:[80]}}, Date.parse('2026-06-02T03:30:00Z')), {probability:80,start:'23:00',end:'00:00'});
assert.equal(precipitationForecast({timezone:'invalid-zone',hourly:{time:['2026-06-01T13:00'],precipitation_probability:[80]}}, Date.parse('2026-06-01T12:30:00Z')), null);
console.log('Weather forecast UI helpers: OK');

const {localClock,shiftDay,solarSeconds,curvePoint,dailyPath,observationsForDay,HORIZON} = require('../web/static/weather.js');
assert.equal(shiftDay('2026-01-01',-1),'2025-12-31');
assert.equal(shiftDay('2024-03-01',-1),'2024-02-29');
assert.equal(localClock(Date.parse('2026-06-01T03:30:00Z'),'America/New_York').day,'2026-05-31');
assert.equal(solarSeconds('2026-06-01T05:30'),19800);
const rise=6*3600, set=18*3600;
assert.ok(curvePoint(0,rise,set).y>HORIZON);
assert.equal(curvePoint(rise,rise,set).y,HORIZON);
assert.ok(curvePoint(12*3600,rise,set).y<HORIZON);
assert.equal(curvePoint(set,rise,set).y,HORIZON);
assert.ok(curvePoint(86400,rise,set).y>HORIZON);
assert.equal(curvePoint(0, null, set),null);
assert.equal(dailyPath(set,rise),null);
assert.ok(dailyPath(rise,set).includes('L'));
const rows=[[Date.parse('2026-06-01T03:30:00Z')/1000,12],[Date.parse('2026-06-01T04:30:00Z')/1000,13]];
assert.deepEqual(observationsForDay(rows,'America/New_York','2026-05-31', Date.parse('2026-06-02T00:00:00Z')).map(x=>x.temp),[12]);
assert.deepEqual(observationsForDay(rows,'America/New_York','2026-06-01', Date.parse('2026-06-02T00:00:00Z')).map(x=>x.temp),[13]);
assert.deepEqual(observationsForDay(rows,'America/New_York','2026-06-01', Date.parse('2026-06-01T04:00:00Z')),[]);
console.log('Daily solar curve helpers: OK');
// Repeated local hour at the end of daylight saving time: both observations
// belong to the same station-local date and are retained.
const dst = [[Date.parse('2026-11-01T05:30:00Z')/1000,10],[Date.parse('2026-11-01T06:30:00Z')/1000,11]];
assert.deepEqual(observationsForDay(dst,'America/New_York','2026-11-01',Date.parse('2026-11-02T00:00:00Z')).map(x=>x.time),['01:30','01:30']);

const {metarChart,nearestChartPoint} = require('../web/static/weather.js');
const now=Date.parse('2026-06-01T12:00:00Z');
assert.deepEqual(metarChart([], 'C', now).points, []);
const source=[[now/1000-86400, 12.5],[now/1000-3600, 14.3],[now/1000-3600, 14.4],[now/1000, 13],[now/1000+1, 40],[null,15],[now/1000-90000,5],['',3]];
const chart=metarChart(source,'C',now);
assert.deepEqual(chart.points.map(p=>p.c),[12.5,14.4,13]);
assert.equal(chart.points[0].x,0);assert.equal(chart.points.at(-1).x,100);
assert.ok(chart.min<12.5 && chart.max>14.4);
assert.ok(chart.ticks.every((t,i,a)=>!i || t.value-a[i-1].value===1));
assert.ok(chart.ticks.every(t=>Number.isInteger(t.value)));
assert.equal(chart.segments.length,2); // long gap is not connected
assert.equal(nearestChartPoint(chart.points,now-3500000),1);
assert.equal(nearestChartPoint(chart.points,now+100000),2);
assert.equal(nearestChartPoint([],now),-1);
const single=metarChart([[now/1000,0]],'F',now);
assert.equal(single.points[0].value,0);
assert.ok(single.min<0 && single.max>0 && single.points[0].y>0 && single.points[0].y<100);
assert.equal(metarChart([[now/1000,10],[now/1000-10,11]],'C',now).segments.length,1);
console.log('METAR chart scaling and selection: OK');

for(const crossing of [rise,set]){
  const before=(curvePoint(crossing,rise,set).y-curvePoint(crossing-1,rise,set).y);
  const after=(curvePoint(crossing+1,rise,set).y-curvePoint(crossing,rise,set).y);
  assert.ok(Math.abs(before-after)<1e-6,`continuous slope at ${crossing}: ${before} vs ${after}`);
}
console.log('Smooth day/night transition: OK');

const {halfHourTicks}=require('../web/static/weather.js');
const dstTicks=halfHourTicks(Date.parse('2026-11-01T04:00:00Z'),Date.parse('2026-11-01T08:00:00Z'),'America/New_York');
assert.equal(dstTicks.filter(t=>t.time==='01:30').length,2);
const nepalTicks=halfHourTicks(Date.parse('2026-06-01T00:00:00Z'),Date.parse('2026-06-01T02:00:00Z'),'Asia/Kathmandu');
assert.equal(new Date(nepalTicks[0].ms).toISOString(),'2026-06-01T00:15:00.000Z');
assert.ok(nepalTicks.every(t=>t.time.endsWith(':00')||t.time.endsWith(':30')));
assert.deepEqual(metarChart([[now/1000,1e200]],'C',now).points,[]);
console.log('Half-hour station-local grid and invalid temperatures: OK');

const {hourlyExtremaForDay, metarIcon}=require('../web/static/weather.js');
const f={hourly:{time:['2026-06-01T12:00','2026-06-01T02:00','2026-06-01T09:00','2026-06-01T16:00','2026-06-02T00:00','2026-06-01T25:00','bad'],temperature_2m:[14,3,14,0,99,-99,1]}};
assert.deepEqual(hourlyExtremaForDay(f,'2026-06-01'),{min:{time:'16:00',seconds:57600,temp:0},max:{time:'09:00',seconds:32400,temp:14},count:4});
assert.deepEqual(hourlyExtremaForDay(f,'2026-06-02').min,{time:'00:00',seconds:0,temp:99});
assert.equal(hourlyExtremaForDay(f,'2026-06-03'),null);
assert.equal(hourlyExtremaForDay({hourly:{time:['2026-06-01T12:00'],temperature_2m:[null]}},'2026-06-01'),null);
assert.equal(hourlyExtremaForDay({hourly:{time:['2026-06-01T12:00'],temperature_2m:[NaN]}},'2026-06-01'),null);
assert.equal(hourlyExtremaForDay({},'2026-06-01'),null);
assert.equal(metarIcon(null).kind,'unknown');
assert.equal(metarIcon({cloud:{code:'SKC',description:'Ясно'},precipitation:{codes:[]}},true).kind,'clear');
assert.equal(metarIcon({cloud:{code:'SKC'},precipitation:{codes:[]}},false).kind,'clear-night');
assert.equal(metarIcon({cloud:{code:'SKC'}},null).kind,'clear-neutral');
assert.equal(metarIcon({cloud:{code:'CLR',description:'Низкая облачность не обнаружена'}},true).kind,'clear-neutral');
assert.equal(metarIcon({cloud:{code:'SCT'}},true).kind,'partial');
assert.equal(metarIcon({cloud:{code:'SCT'}},false).kind,'partial-night');
assert.equal(metarIcon({cloud:{code:'BKN'}}).kind,'cloudy');
assert.deepEqual(metarIcon({cloud:{code:'SKC'},precipitation:{codes:['-RA'],description:'Слабые осадки: дождь'}}),{kind:'rain',label:'Слабые осадки: дождь'});
assert.equal(metarIcon({precipitation:{codes:['SN']}}).kind,'snow');
assert.equal(metarIcon({precipitation:{codes:['VCTS'],description:'Гроза поблизости'}}).kind,'storm');
assert.equal(metarIcon({cloud:{code:null}}).kind,'unknown');
console.log('Daily model extrema and observed METAR icons: OK');
