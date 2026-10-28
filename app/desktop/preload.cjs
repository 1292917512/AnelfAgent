// @ts-check
/** Expose only fixed native actions; the renderer has no Node or shell access. */
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('anelfDesktop', Object.freeze({
  chooseProject: (/** @type {'existing'|'destination'} */ purpose) => ipcRenderer.invoke('anelf:choose-project',purpose),
  version: () => ipcRenderer.invoke('anelf:version'),
  reconnect: () => ipcRenderer.invoke('anelf:reconnect'),
  cleanup: () => ipcRenderer.invoke('anelf:cleanup'),
  bootstrapInfo: () => ipcRenderer.invoke('anelf:bootstrap-info'),
  projectRepository: (/** @type {string} */ root) => ipcRenderer.invoke('anelf:project-repository',root),
  bootstrapStorage: () => ipcRenderer.invoke('anelf:bootstrap-storage'),
  resize: (/** @type {{view:string,logs:boolean,height?:number}} */ value) => ipcRenderer.invoke('anelf:resize',value),
  trayState: (/** @type {import('./tray.cjs').TrayState} */ value) => ipcRenderer.invoke('anelf:tray-state',value),
  onNavigate: (/** @type {(view:'home'|'settings')=>void} */ callback) => {
    const listener=(/** @type {Electron.IpcRendererEvent} */ _event, /** @type {'home'|'settings'} */ view)=>callback(view);
    ipcRenderer.on('anelf:navigate',listener);
    return ()=>ipcRenderer.removeListener('anelf:navigate',listener);
  },
  bootstrapStart: (/** @type {import('../ui/lib/types').BootstrapSelection} */ value) => ipcRenderer.invoke('anelf:bootstrap-start',value),
  bootstrapCancel: () => ipcRenderer.invoke('anelf:bootstrap-cancel'),
  onExitState: (/** @type {(value:{stopping:boolean,error:string})=>void} */ callback) => {
    const listener = (/** @type {Electron.IpcRendererEvent} */ _event, /** @type {{stopping:boolean,error:string}} */ value) => callback(value);
    ipcRenderer.on('anelf:exit-state',listener);
    return () => ipcRenderer.removeListener('anelf:exit-state',listener);
  },
  onBootstrapProgress: (/** @type {(value:string)=>void} */ callback) => {
    const listener = (/** @type {Electron.IpcRendererEvent} */ _event, /** @type {string} */ value) => callback(value);
    ipcRenderer.on('anelf:bootstrap-progress',listener);
    return () => ipcRenderer.removeListener('anelf:bootstrap-progress',listener);
  },
}));
