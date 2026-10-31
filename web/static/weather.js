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
  const result = {zonedEpoch, precipitationForecast};
  if (typeof module !== 'undefined' && module.exports) module.exports = result;
  else root.WeatherView = result;
})(typeof globalThis !== 'undefined' ? globalThis : this);
