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
