import {el} from './dom.js';
import {t} from './i18n.js';

/** @param {import('./lib/types').Operation} operation @param {boolean} busy */
export function renderProgress(operation, busy) {
  el('progress').hidden = !operation.stage;
  el('progress-toggle').title = t('toggleProgress');
  if (!operation.stage) return;
  el('stage').textContent = t(operation.cancelling ? 'cancelling' : operation.status !== 'running'
    ? operation.status || 'unknown' : operation.stage === 'stop' ? 'stopStage' : operation.stage);
  const now = operation.finished_at || Date.now()/1000;
  const seconds = Math.max(0, Math.floor(now - (operation.stage_started_at || operation.started_at || now)));
  el('elapsed').textContent = t('stageElapsed') + ' ' + Math.floor(seconds/60) + t('minutes') + ' ' + seconds%60 + t('seconds');
  const progress = el('download');
  if (progress instanceof HTMLProgressElement) {
    if (operation.total) { progress.max = operation.total; progress.value = operation.received || 0; }
    else if (operation.status !== 'running') { progress.max = 1; progress.value = operation.status === 'done' ? 1 : 0; }
    else progress.removeAttribute('value');
  }
  const uv = operation.progress;
  const summary=[];
  if (operation.status !== 'running') summary.push(t(operation.status === 'cancelled' ? 'progressPaused' : operation.status || 'unknown'));
  else if(uv){
    summary.push(t('uv_'+uv.phase));
    for(const key of /** @type {const} */ (['resolved','prepared','installed','audited']))if(uv[key]!==undefined)summary.push(t('uv_'+key)+' '+uv[key]);
  } else if(operation.received) summary.push((operation.received/1048576).toFixed(1)+' MiB'+(operation.total?' / '+(operation.total/1048576).toFixed(1)+' MiB':''));
  else summary.push(t('workingHint'));
  el('progress-summary').textContent=summary.join(' · ');
  el('existing-packages').hidden=operation.stage!=='dependencies' || !operation.existing_packages;
  el('existing-packages').textContent=t('existingPackages').replace('{count}',String(operation.existing_packages || 0));
  el('uv-progress').hidden = !uv;
  if (uv) {
    el('uv-phase').textContent = t('uv_' + uv.phase);
    const stats = [];
    for (const key of /** @type {const} */ (['resolved','prepared','installed','audited'])) {
      if (uv[key] !== undefined) stats.push(t('uv_' + key) + ' ' + uv[key]);
    }
    if (uv.downloads) stats.push(t('uv_downloads') + ' ' + uv.downloads);
    el('uv-counts').textContent = stats.join(' · ');
    el('uv-package').textContent = uv.active.length ? t('uv_active') + ' ' + uv.active.join(', ') : uv.package;
    el('uv-package').hidden = !!uv.packages?.length;
    const packages = (uv.packages || []).map(item => {
      const row = document.createElement('div'); row.className = 'package-row';
      const label = document.createElement('div');
      const name = document.createElement('strong'); name.textContent = item.name;
      const bytes = document.createElement('span');
      bytes.textContent = (item.received/1048576).toFixed(1) + ' / ' + (item.total/1048576).toFixed(1) + ' MiB';
      label.append(name,bytes);
      const bar = document.createElement('progress'); bar.setAttribute('aria-label',item.name);
      if (item.total > 0) { bar.max = item.total; bar.value = item.received; }
      row.append(label,bar); return row;
    });
    el('package-progress').replaceChildren(...packages.slice(0,3));
    el('package-extra').replaceChildren(...packages.slice(3));
    el('package-details').hidden = packages.length <= 3;
    el('package-more').textContent = t('moreDownloads').replace('{count}',String(Math.max(0,packages.length-3)));
  }
  el('progress-detail').hidden = !!uv;
  el('progress-detail').textContent = (operation.received ? (operation.received/1048576).toFixed(1) + ' MB'
    + (operation.total ? ' / ' + (operation.total/1048576).toFixed(1) + ' MB' : '') + '\n' : '') + (operation.detail || '');
  el('waiting').hidden = !busy || Date.now()/1000 - (operation.updated_at || 0) < 25;
  el('stage-list').replaceChildren(...operation.steps.map(item => {
    const li = document.createElement('li'); li.dataset.status = item.status;
    li.textContent = (item.status === 'done' ? '✓ ' : '· ') + t(item.key === 'stop' ? 'stopStage' : item.key);
    return li;
  }));
}
