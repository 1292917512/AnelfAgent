import {t, language, setLanguage, translate} from './i18n.js';
import {el, button, input, message, settingsForm, environmentRows, storageText} from './dom.js';
import {controls, installing} from './model.js';
import {renderProgress} from './progress.js';
import {renderNetwork} from './network.js';
import {WebAccessPanel} from './access.js';
const token = location.hash.slice(1) || sessionStorage.getItem('anelf-manager-token') || '';
if (token) sessionStorage.setItem('anelf-manager-token', token);
let autoInstall = new URLSearchParams(location.search).has('setup');
const initialStep = autoInstall ? 2 : 1;
history.replaceState(null, '', location.pathname);
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
/** @type {import('./lib/types').State | null} */ let state = null;
/** @type {import('./lib/types').Schema[]} */ let schema = [];
/** @type {Record<string,string>} */ let repositories = {};
/** @type {Record<string,import('./lib/types').PypiSource>} */ let pypiSources = {};
/** @type {Record<string,string>} */ let pypiDefaults = {};
/** @type {string[]} */ let chinaTimezones = [];
let connected = false, refreshing = false, pending = false, dirty = false, configured = false;
let failures = 0;
let networkPending = false;
let view = 'home', step = initialStep, wizard = initialStep === 2, sourceDirty = false;
let windowMode = '';
let trayKey = '';
let stoppingForExit = false, exitError = '';
let requestedHeight = 0;
/** @param {unknown} error */ const errorText = error => error instanceof Error ? error.message : String(error);
/** @param {string} path @param {object} [body] */
async function api(path, body) {
  const response = await fetch('/api/' + path, {method:body ? 'POST' : 'GET',headers:{Authorization:'Bearer ' + token,'Content-Type':'application/json'},body:body ? JSON.stringify(body) : undefined,signal:AbortSignal.timeout(path === 'environment' ? 50000 : path === 'web-access' ? 25000 : path === 'network' ? 15000 : 7000)});
  const result = await response.json(); if (!response.ok) throw new Error(result.message || response.statusText); return result;
}
const access = new WebAccessPanel(api);
function pypiOptions() {
  const control=input('install-pypi-source'),current=control.value || 'auto';
  control.replaceChildren(new Option(t('followSource'),'auto'),...Object.entries(pypiSources).map(([key,value])=>new Option(value.label[language] || key,key)));
  control.value=current;
}
function sources() {
  const choice = input('install-source').value;
  const resolved = choice === 'auto' ? state?.resolved_source || 'github' : choice;
  el('source-reason').textContent = (choice === 'auto' ? t('automatic') + ' · ' + timezone : t('manual')) + ' → ' + (resolved === 'gitee' ? 'Gitee' : 'GitHub');
  el('source-url').textContent = repositories[resolved] || '';
  const pypi=input('install-pypi-source').value,downloads=input('install-download-source').value;
  el('pypi-url').textContent=pypi === 'auto' ? pypiDefaults[downloads === 'auto' ? chinaTimezones.includes(timezone) ? 'china' : 'global' : downloads] || '' : pypiSources[pypi]?.url || '';
}
function render() {
  const flags = controls(state, connected && !pending && !stoppingForExit);
  /** @type {import('./lib/types').TrayState} */
  const trayState={ready:!!state?.ready,webReady:!!state?.ready && flags.workspace,settingsAvailable:configured && !stoppingForExit,language:language==='zh'?'zh':'en'};
  const nextTrayKey=JSON.stringify(trayState);
  if(window.anelfDesktop?.trayState && nextTrayKey!==trayKey){trayKey=nextTrayKey;void window.anelfDesktop.trayState(trayState).catch(()=>{trayKey='';});}
  const updating = !!state?.busy && state.operation.action === 'update';
  el('connection-notice').hidden = connected || failures < 3;
  el('connection-notice-text').textContent = t(!state ? 'unavailable' : state.ready ? 'disconnected' : 'installDisconnected');
  el('connection-loading').hidden = !!state || failures >= 3;
  el('connection-dot').className = 'dot ' + (connected ? 'online' : '');
  el('connection-label').textContent = t(connected ? 'connected' : failures < 3 ? 'connecting' : 'offline');
  el('nav-home').hidden = view !== 'settings'; el('nav-settings').classList.toggle('active',view === 'settings');
  for (const [id,key] of [['nav-home','home'],['nav-settings','settings']]) { el(id).title = t(key); el(id).setAttribute('aria-label',t(key)); }
  el('configuration').hidden = view !== 'settings';
  el('wizard').hidden = !state || view !== 'home' || !wizard;
  el('dashboard').hidden = view !== 'home' || wizard || !state?.ready && !state?.running && !updating;
  el('phase').hidden = true;
  el('page-heading').hidden = view !== 'settings';
  el('project-bar').hidden = !wizard && view !== 'settings';
  el('log-panel').hidden = !state;
  el('page-title').textContent = t(view === 'settings' ? 'settings' : wizard ? 'welcome' : 'manage');
  el('language').textContent = language === 'zh' ? 'EN' : '中文';
  el('footer-hint').textContent = t(window.anelfDesktop ? 'trayFooter' : 'footer');
  const mode = view === 'settings' ? 'settings' : wizard || !state ? 'setup' : 'manage';
  const logs = el('log-panel').hasAttribute('open');
  const sizeKey = mode + ':' + (wizard ? step : '') + ':' + !!state?.busy;
  if (sizeKey !== windowMode || !requestedHeight) { windowMode = sizeKey; requestedHeight=0;requestAnimationFrame(fitWindow); }
  for (let n=1;n<=3;n++) el('step-' + n).hidden = step !== n;
  document.querySelectorAll('[data-step]').forEach(node => { if (node instanceof HTMLElement) { node.classList.toggle('current',Number(node.dataset.step) === step); node.classList.toggle('done',Number(node.dataset.step) < step); } });
  if (!state) { el('dashboard').hidden = true; return; }
  const phase = !connected ? 'unknown' : installing(state) ? state.operation.action === 'update' ? 'updating' : 'installing' : ({start:'starting',stop:'stopping',restart:'restarting',setup:'installing'})[state.phase] || state.phase;
  el('phase').textContent = t(phase); el('runtime-state').textContent = t(phase);
  el('root').textContent = state.root; el('root').title = state.root;
  el('pid').textContent = state.pid ? String(state.pid) : '—'; el('web-address').textContent = state.web_url;
  const active = state.running || ['starting','restarting','stopping'].includes(state.phase);
  const transitioning = state.busy || ['starting','restarting','stopping'].includes(state.phase);
  el('runtime-description').textContent = t(transitioning ? 'workingHint' : state.external_pid ? 'externalHint' : state.running ? 'runningHint' : 'stoppedHint');
  el('runtime-mark').classList.toggle('running',state.phase === 'running' && connected);
  el('runtime-mark').classList.toggle('working',transitioning);
  button('start').disabled = active ? !flags.stop : !flags.start;
  button('start').disabled ||= transitioning;
  button('start').textContent = t(transitioning ? phase : active ? 'stop' : 'start');
  el('workspace').hidden = !flags.workspace;
  el('log-preview').hidden = !transitioning || logs || updating;
  const link = el('workspace'); link.setAttribute('aria-disabled',String(!flags.workspace)); if (flags.workspace) link.setAttribute('href',state.web_url); else link.removeAttribute('href');
  button('save').disabled = !flags.configure; button('choose-project').disabled = !connected || state.busy || pending;
  button('network-check').disabled = !connected || networkPending;
  button('finish').disabled = !connected || !state.ready || state.busy;
  for (const id of ['next','back','install','repair','update','inspect']) button(id).disabled = !connected || state.busy || pending;
  const space = state.storage;
  for (const id of ['next','install','update']) button(id).disabled ||= !space?.sufficient;
  button('update').disabled ||= !!state.external_pid;
  button('update').disabled ||= transitioning;
  button('update').hidden = !state.running && !updating;
  button('update').title = space && !space.sufficient ? storageText(space) : t('updateHint');
  el('storage').hidden = !wizard || state.busy;
  el('storage').textContent = space ? storageText(space) : t('diskUnknown');
  el('storage').title = t('diskHint') + (space?.error ? '\n' + space.error : '');
  el('storage').classList.toggle('danger',!space?.sufficient);
  input('install-source').disabled = state.busy || pending; input('install-download-source').disabled = state.busy || pending;
  input('install-pypi-source').disabled = state.busy || pending;
  const resumeSource=!!state.resumable && !state.busy && step === 2;
  const sourceTarget=el(resumeSource ? 'resume-source' : 'initial-pypi-source');
  if(el('pypi-source-field').parentElement!==sourceTarget)sourceTarget.append(el('pypi-source-field'));
  el('resume-source').hidden=!resumeSource;
  button('install').hidden = state.busy;
  button('back').hidden = state.busy;
  button('install').textContent = t(state.resumable ? 'continueInstall' : state.last && !state.last.ok ? 'retry' : 'install');
  el('resume-hint').hidden = !state.resumable || state.busy;
  el('environment-heading').hidden = installing(state);
  el('environment-details').hidden = installing(state);
  button('cancel').hidden = !installing(state); button('cancel').disabled = !flags.cancel; button('cancel').textContent = t(state.operation.cancelling ? 'cancelling' : 'cancel');
  el('cleanup').hidden = !window.anelfDesktop; button('cleanup').disabled = state.busy || pending;
  el('choose-project').hidden = !window.anelfDesktop;
  const progressTarget = el(updating ? 'runtime-progress' : 'installation-progress');
  if (el('progress').parentElement !== progressTarget) progressTarget.append(el('progress'));
  const cancelTarget = el(updating ? 'runtime-progress' : 'install-actions');
  if (el('cancel').parentElement !== cancelTarget) cancelTarget.append(el('cancel'));
  renderProgress(state.operation, state.busy);
  el('install-index').textContent = state.downloads?.pypi || '';
  if (configured && !dirty) for (const field of schema) { const control = input('setting-' + field.name); if (control instanceof HTMLInputElement && field.type === 'checkbox') control.checked = Boolean(state.settings[field.name]); else control.value = String(state.settings[field.name]); }
  sources();
  access.update(state, flags.configure && !stoppingForExit);
  if (stoppingForExit || exitError) {
    message(stoppingForExit ? t('exitStopping') : t('exitFailed') + ' ' + exitError);
    if (stoppingForExit) document.querySelectorAll('button').forEach(node => {if(node.id !== 'copy-log')node.disabled=true;});
  }
}
function fitWindow() {
  if (!window.anelfDesktop || stoppingForExit || !state || !configured) return;
  const main=document.querySelector('main');
  if (!main) return;
  const height=Math.ceil(main.getBoundingClientRect().height);
  if (Math.abs(height-requestedHeight)<2) return;
  requestedHeight=height;
  const mode=view === 'settings' ? 'settings' : wizard || !state ? 'setup' : 'manage';
  void window.anelfDesktop.resize({view:mode,logs:el('log-panel').hasAttribute('open'),height}).catch(()=>{});
}
async function inspect() {
  el('environment').textContent = t('detecting'); button('inspect').disabled = true;
  try { const result = await api('environment'); environmentRows(result); }
  catch (error) { message(errorText(error)); }
  finally { button('inspect').disabled = !!state?.busy; }
}
async function checkNetwork() {
  if (networkPending) return;
  networkPending = true; button('network-check').disabled = true;
  el('network-results').textContent = t('networkChecking');
  const selected = input('install-download-source').value,pypi=input('install-pypi-source').value;
  try {
    const result = await api('network', {download_source:selected,pypi_source:pypi,timezone});
    if (selected === input('install-download-source').value && pypi === input('install-pypi-source').value) renderNetwork(result);
  } catch (error) { el('network-results').textContent = errorText(error); }
  finally { networkPending = false; render(); if (selected !== input('install-download-source').value || pypi !== input('install-pypi-source').value) void checkNetwork(); }
}
async function refresh() {
  if (refreshing) return; refreshing = true;
  let beginInstallation=false;
  try {
    const previous = state;
    try { state = await api('status'); connected = true; failures = 0; }
    catch { connected = false; failures++; render(); return; }
    if (!state) return;
    if (state.ready && (!previous?.ready || previous.phase !== 'running' && state.phase === 'running')) void access.refresh();
    if (!configured) {
      const definitions = await api('schema'); schema = definitions; const catalog=await api('sources');repositories=catalog.repositories;pypiSources=catalog.pypi_sources;pypiDefaults=catalog.pypi_defaults;chinaTimezones=catalog.china_timezones;
      pypiOptions();
      settingsForm(schema,state); configured = true; wizard = wizard || !state.ready && !state.running;
      if(autoInstall){
        autoInstall=false;
        if(state.ready || state.running)wizard=false;
        else if(!state.busy && !state.external_pid && state.storage?.sufficient){beginInstallation=true;wizard=true;step=2;}
      }
      if (wizard && state.resumable) step = 2;
      if (wizard && step === 2 && !beginInstallation && !state.busy) void inspect();
    }
    if (!sourceDirty) { input('install-source').value = state.settings.source; input('install-download-source').value = state.settings.download_source; input('install-pypi-source').value = state.settings.pypi_source; }
    if (!previous) void checkNetwork();
    const updating = state.busy && state.operation.action === 'update';
    if (installing(state)) { wizard = !updating; step = 2; if (!previous?.busy) el('environment-details').removeAttribute('open'); }
    if (!state.ready && !state.running && !updating) { wizard = true; if (step === 3) step = 2; }
    if (previous?.busy && !state.busy && state.ready && state.last?.ok && ['install','setup','update'].includes(state.operation.action || '')) { step = 3; wizard = false; view = 'home'; message(''); }
    if (state.last && !state.last.ok) message(state.last.cancelled ? t('cancelled') : state.last.message || t('error'));
    else if (state.external_pid) message(t('external'));
    else if (previous?.external_pid) message('');
    render();
    if (el('log-panel').hasAttribute('open') || state.busy || ['starting','restarting','stopping'].includes(state.phase)) {
      const kind = wizard ? 'commands' : 'application'; el('log-path').textContent = state.root + '/logs/manager/' + kind + '.log';
      const log = el('logs'), atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 35;
      try { log.textContent = (await api('logs/' + kind)).text || t('noLogs'); el('log-preview').textContent = log.textContent.trim().split('\n').slice(-3).join('\n'); if (atBottom) log.scrollTop = log.scrollHeight; }
      catch { log.textContent = t('logUnavailable'); }
    }
  } catch (error) { message(errorText(error)); render(); }
  finally { refreshing = false;if(beginInstallation)void act('install'); }
}
/** @param {string} action */
async function act(action) {
  if (pending || stoppingForExit) return; pending = true; exitError='';message(''); render();
  try { if (['install','setup'].includes(action)) await api('settings',{source:input('install-source').value,download_source:input('install-download-source').value,pypi_source:input('install-pypi-source').value,timezone}); await api('action',{action}); }
  catch (error) { message(errorText(error)); }
  finally { pending = false; await refresh(); render(); }
}
for (const node of document.querySelectorAll('[data-action]')) if (node instanceof HTMLButtonElement) node.onclick = () => { void act(node.dataset.action || ''); };
button('start').onclick = () => { void act(state?.running || ['starting','restarting','stopping'].includes(state?.phase || '') ? 'stop' : 'start'); };
button('cancel').onclick = async () => { try { await api('cancel',{}); await refresh(); } catch (error) { message(errorText(error)); } };
for (const id of ['nav-home','settings-back']) button(id).onclick = () => { view = 'home'; render(); };
button('nav-settings').onclick = () => { view = view === 'settings' ? 'home' : 'settings'; render(); if(view === 'settings')void access.refresh(); };
button('next').onclick = async () => { step = 2; await act('install'); };
button('network-check').onclick = checkNetwork;
button('back').onclick = () => { step = 1; render(); };
button('finish').onclick = () => { if (connected && state?.ready && !state.busy) { wizard = false; message(''); render(); } };
button('repair').onclick = () => { wizard = true; step = 1; view = 'home'; message(''); render(); };
button('inspect').onclick = inspect;
input('install-source').onchange = () => { sourceDirty = true; sources(); };
input('install-download-source').onchange = () => { sourceDirty = true; el('network-summary').textContent = t('networkTitle'); void checkNetwork(); };
input('install-pypi-source').onchange = () => { sourceDirty = true; sources(); el('network-summary').textContent=t('networkTitle'); void checkNetwork(); };
button('language').onclick = () => { setLanguage(language === 'zh' ? 'en' : 'zh'); translate(); pypiOptions(); settingsForm(schema,state); render(); };
el('settings').oninput = () => { dirty = true; }; el('settings').onsubmit = event => event.preventDefault();
button('save').onclick = async () => {
  if (pending || stoppingForExit) return;
  pending=true;render();
  try {
    /** @type {Record<string,string|number|boolean>} */
    const body = {}; for (const field of schema) { const control = input('setting-' + field.name); body[field.name] = control instanceof HTMLInputElement && field.type === 'checkbox' ? control.checked : field.type === 'number' ? Number(control.value) : control.value; } await api('settings',body); dirty = false; sourceDirty = false;
    message(await access.savePending() ? t('saved') : t('settingsPasswordFailed')); }
  catch (error) { message(errorText(error)); }
  finally { pending=false;await refresh();render(); }
};
button('ports').onclick = async () => { try { const result = await api('ports'); message(result.host + ':' + result.port + ' · ' + t(result.available ? 'available' : 'occupied')); } catch (error) { message(errorText(error)); } };
button('choose-project').onclick = async () => { try { if (window.anelfDesktop) await window.anelfDesktop.chooseProject(); else message(t('browserProject')); } catch (error) { message(errorText(error)); } };
button('reconnect').onclick = async () => { try { if (window.anelfDesktop) await window.anelfDesktop.reconnect(); else await refresh(); } catch (error) { message(errorText(error)); } };
button('cleanup').onclick = async () => { try { await window.anelfDesktop?.cleanup(); } catch (error) { message(errorText(error)); } };
button('copy-log').onclick = async () => { try { await navigator.clipboard.writeText(el('logs').textContent || ''); message(t('copied')); } catch (error) { message(errorText(error)); } };
el('log-panel').ontoggle = () => { render(); void refresh(); };
translate(); render();
window.anelfDesktop?.onExitState?.(value => {
  stoppingForExit=value.stopping;exitError=value.error;
  if (!stoppingForExit) document.querySelectorAll('button').forEach(node=>{node.disabled=false;});
  render();
});
window.anelfDesktop?.onNavigate?.(target=>{
  view=target;render();window.scrollTo(0,0);if(target==='settings')void access.refresh();
});
const main=document.querySelector('main');
const expandedDetails=()=>[...document.querySelectorAll('details[open]')].some(node=>node.getClientRects().length>0);
document.addEventListener('toggle',event=>{
  if(event.target instanceof HTMLDetailsElement){requestedHeight=0;requestAnimationFrame(fitWindow);}
},true);
if (main) new ResizeObserver(()=>{if(expandedDetails())requestAnimationFrame(fitWindow);}).observe(main);
if (token) { void refresh(); setInterval(() => { void refresh(); },1500); window.anelfDesktop?.version().then(version => { el('app-version').textContent = version; }); } else message(t('token'));
