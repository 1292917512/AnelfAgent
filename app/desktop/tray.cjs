// @ts-check
/** Tray actions reflect the shared renderer state; web access is verified again before opening. */
/** @typedef {import('../ui/lib/types').TrayState} TrayState */
/** @param {unknown} value @returns {value is TrayState} */
function isTrayState(value) {
  if (!value || typeof value !== 'object') return false;
  return 'ready' in value && typeof value.ready === 'boolean' &&
    'webReady' in value && typeof value.webReady === 'boolean' &&
    'settingsAvailable' in value && typeof value.settingsAvailable === 'boolean' &&
    'language' in value && typeof value.language==='string' && ['zh','en'].includes(value.language);
}
/** @param {TrayState} state
 * @param {{navigate:(view:'home'|'settings')=>void,web:()=>void,exit:()=>void}} actions
 * @returns {import('electron').MenuItemConstructorOptions[]} */
function menuFor(state, actions) {
  const zh=state.language==='zh';
  /** @type {import('electron').MenuItemConstructorOptions[]} */
  const items=[{label:state.ready ? zh?'打开控制面板':'Open control panel' : zh?'打开安装界面':'Open installation',click:()=>actions.navigate('home')}];
  if (state.ready) items.push({label:zh?'打开 Web 控制台':'Open Web console',enabled:state.webReady,click:actions.web});
  items.push({label:zh?'打开设置':'Open settings',enabled:state.settingsAvailable,click:()=>actions.navigate('settings')},
    {type:'separator'},{label:zh?'退出':'Quit',click:actions.exit});
  return items;
}
/** A cached enabled menu must never open a stopped, disconnected or different project's console.
 * @param {()=>Promise<unknown>} read
 * @param {(url:string)=>Promise<void>} open
 * @param {()=>void} fallback */
async function openWebConsole(read, open, fallback) {
  try {
    const state=await read();
    if (!state || typeof state!=='object' || !('ready' in state) || state.ready!==true ||
      !('running' in state) || state.running!==true || !('phase' in state) || state.phase!=='running' ||
      !('busy' in state) || state.busy!==false || 'web_ready' in state && state.web_ready===false || !('web_url' in state) || typeof state.web_url!=='string') throw new Error('Not ready');
    const url=new URL(state.web_url);
    if (!['http:','https:'].includes(url.protocol) || !['127.0.0.1','localhost','[::1]'].includes(url.hostname) || url.username || url.password) throw new Error('Invalid local console');
    await open(url.href);
    return true;
  } catch { fallback();return false; }
}
module.exports={isTrayState,menuFor,openWebConsole};
