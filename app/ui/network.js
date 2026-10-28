import {el} from './dom.js';
import {t} from './i18n.js';

/** @param {import('./lib/types').NetworkCheck} result */
export function renderNetwork(result) {
  const ok = result.results.filter(item => item.ok).length;
  el('network-summary').textContent = t('networkTitle') + ' · ' + t(result.source) + ' · ' + ok + '/' + result.results.length;
  if (ok < result.results.length) el('network-details').setAttribute('open','');
  const rows = result.results.map(item => {
    const row = document.createElement('div'); row.className = 'network-row';
    const heading = document.createElement('div');
    const name = document.createElement('strong'); name.textContent = item.name === 'pypi' ? 'Python / PyPI' : item.name;
    const status = document.createElement('span'); status.dataset.ok = String(item.ok);
    status.textContent = item.ok ? t('networkOk') + ' · ' + item.elapsed_ms + ' ms' : t('networkFailed');
    heading.append(name,status);
    const address = document.createElement('code'); address.textContent = item.url;
    row.append(heading,address);
    if (!item.ok) { const detail = document.createElement('small'); detail.textContent = item.detail; row.append(detail); }
    return row;
  });
  el('network-results').replaceChildren(...rows);
}
