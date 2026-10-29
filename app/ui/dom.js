import {t, language} from './i18n.js';
import {describeError} from '../desktop/errors.mjs';
/** @param {string} id */ export function el(id) { const node = document.getElementById(id); if (!node) throw new Error('Missing element: ' + id); return node; }
/** @param {string} id */ export function button(id) { const node = el(id); if (!(node instanceof HTMLButtonElement)) throw new Error(id); return node; }
/** @param {string} id */ export function input(id) { const node = el(id); if (!(node instanceof HTMLInputElement || node instanceof HTMLSelectElement)) throw new Error(id); return node; }
/** Keep notices brief; detailed diagnostics are opt-in and retain their expanded state. @param {string} value */
export function message(value) {
  const notice=el('message');notice.hidden=!value;
  delete notice.dataset.error;
  if(notice.dataset.message===value)return;
  notice.dataset.message=value;
  const preview=document.createElement('span');preview.className='notice-preview';
  if(value.includes('\n') || value.length>90){
    const existing=notice.querySelector('details');
    const details=existing || document.createElement('details');
    const summary=details.querySelector('summary') || document.createElement('summary');
    const full=details.querySelector('pre') || document.createElement('pre');
    summary.textContent=value.split('\n')[0].slice(0,70)+' · '+t('details');
    full.textContent=value;
    if(!existing){details.append(summary,full);notice.replaceChildren(details);}
  }else{preview.textContent=value;notice.replaceChildren(preview);}
}
/** @param {unknown} error */
export function showError(error) {
  const value=describeError(error,language);
  message(value.message+'\n'+value.detail);
  el('message').dataset.error=value.detail;
}
export function translateError() {const raw=el('message').dataset.error;if(raw)showError(raw);}
/** @param {import('./lib/types').Schema[]} schema @param {import('./lib/types').State | null} state */
export function settingsForm(schema, state) {
  const wasOpen=el('settings').querySelector('details')?.open || false;
  const password=el('password-control'),maintenance=el('maintenance-details');
  password.remove();maintenance.remove();
  el('settings').replaceChildren();
  const advanced=document.createElement('details'),summary=document.createElement('summary'),fields=document.createElement('div');
  advanced.id='advanced-settings';advanced.open=wasOpen;fields.className='advanced-fields';summary.textContent=t('advancedSettings');advanced.append(summary,fields);
  for (const field of schema) {
    const label = document.createElement('label'); label.className = 'field'; label.dataset.name = field.name;
    const title = document.createElement('span'); title.textContent = field.label[language];
    const control = field.type === 'select' ? document.createElement('select') : document.createElement('input');
    control.id = 'setting-' + field.name; control.name = field.name;
    if (control instanceof HTMLSelectElement) {
      const options=[...(field.options || [])], current=state ? String(state.settings[field.name]) : '';
      if (current && !options.includes(current)) options.push(current);
      for (const value of options) { const option = document.createElement('option'); option.value = value; option.textContent = field.option_labels?.[value]?.[language] || (value === 'auto' ? t('auto') : value === 'github' ? 'GitHub' : value === 'gitee' ? 'Gitee' : t(value)); control.append(option); }
    }
    else { control.type = field.type; if (field.min) control.min = String(field.min); if (field.max) control.max = String(field.max); }
    if (state) { if (control instanceof HTMLInputElement && field.type === 'checkbox') control.checked = Boolean(state.settings[field.name]); else control.value = String(state.settings[field.name]); }
    label.append(title, control); (field.advanced ? fields : el('settings')).append(label);
  }
  advanced.append(maintenance);
  el('settings').append(password,advanced);
}
/** @param {import('./lib/types').Environment} result */
export function environmentRows(result) {
  el('environment').replaceChildren();
  const items = Object.entries(result.tools).map(([name,value]) => ({name, detail:value.path ? value.version : t('missing'), path:value.path || '', good:!!value.path}));
  items.push({name:'Python ' + (result.python_version || ''),detail:t(result.python_reusable ? 'reusePython' : 'missing'),path:result.python_reusable || '',good:!!result.python_reusable},
    {name:t('projectEnvironment'),detail:t(result.python_installed ? 'installed' : 'createVenv'),path:result.python,good:result.python_installed},
    {name:t('frontend'),detail:t(result.frontend_built ? 'installed' : 'pending'),path:'web/frontend/dist',good:result.frontend_built});
  for (const item of items) { const row = document.createElement('div'); row.className = 'env-row'; const name = document.createElement('span'); name.textContent = item.name;
    const info = document.createElement('div'), detail = document.createElement('span'), path = document.createElement('code'); detail.textContent = item.detail; detail.className = item.good ? '' : 'missing'; path.textContent = item.path; info.append(detail,path); row.append(name,info); el('environment').append(row); }
}

/** @param {import('./lib/types').Storage} storage */
export function storageText(storage) {
  const free = storage.free === null ? t('unknown') : (storage.free / 1024 ** 3).toFixed(1) + ' GiB';
  return t('diskAvailable') + ' ' + free + ' · ' + t('diskRequired') + ' ' + storage.required / 1024 ** 3 + ' GiB' +
    (storage.sufficient ? '' : ' · ' + t(storage.free === null ? 'diskUnknown' : 'diskInsufficient'));
}
