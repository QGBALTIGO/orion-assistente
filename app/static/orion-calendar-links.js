/* Orion customer calendar shortcut.
 * This is a manual save link, not a Google API write or access grant. No
 * customer email, phone, bearer token or appointment UUID is included.
 */
(() => {
  'use strict';
  const bounded = (value, max) => String(value == null ? '' : value)
    .replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/g, ' ')
    .replace(/\s+/g, ' ').trim().slice(0, max);
  const stamp = value => {
    const moment = new Date(value);
    if (!Number.isFinite(moment.getTime())) return null;
    return moment.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  };
  function googleEventURL(details) {
    if (!details || details.status !== 'confirmed') return null;
    const begin=new Date(details.start_at), finish=new Date(details.end_at);
    if (!Number.isFinite(begin.getTime()) || !Number.isFinite(finish.getTime()) ||
        finish <= begin || finish.getTime()-begin.getTime()>24*60*60*1000) return null;
    const company=bounded(details.company,110);
    if (!company) return null;
    const services=Array.isArray(details.services)
      ? details.services.map(s=>bounded(s,70)).filter(Boolean).slice(0,6):[];
    const professional=bounded(details.professional,80);
    const address=bounded(details.address,250);
    const title=[company,services.join(' + ')].filter(Boolean).join(' · ').slice(0,170);
    const detailsText=[
      'Agendamento realizado pelo Orion Agenda.',
      professional?'Profissional: '+professional+'.':'',
      'Esta é uma cópia na sua agenda pessoal. Se remarcar ou cancelar no Orion, atualize o evento manualmente.'
    ].filter(Boolean).join('\n');
    const url=new URL('https://calendar.google.com/calendar/r/eventedit');
    url.searchParams.set('action','TEMPLATE');
    url.searchParams.set('text',title);
    url.searchParams.set('dates',stamp(begin)+'/'+stamp(finish));
    url.searchParams.set('details',detailsText);
    if (address) url.searchParams.set('location',address);
    const timezone=details.timezone;
    if (typeof timezone==='string' && timezone.length<=90) {
      try { new Intl.DateTimeFormat('en-US',{timeZone:timezone});url.searchParams.set('stz',timezone);url.searchParams.set('etz',timezone); }
      catch (_) { /* UTC instants are still unambiguous. */ }
    }
    return url.href;
  }
  Object.defineProperty(window,'OrionCalendarLinks',{
    value:Object.freeze({googleEventURL}),configurable:false,enumerable:false
  });
})();
