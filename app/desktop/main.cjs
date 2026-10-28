// @ts-check
/** Native shell, offline first-run setup and safe package lifecycle. */
const {app,BrowserWindow,Menu,Tray,nativeImage,nativeTheme,dialog,ipcMain,screen,session,shell} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawn} = require('node:child_process');
const {ensureBackend,findUv,connectionAt,resolveProfile,runTask} = require('./backend.cjs');
const {inspectProject,transferProject} = require('./project-import.cjs');
const {register,projects} = require('./managed.cjs');
const {prepare,request,drain,hasRunningServices} = require('./maintenance.cjs');
const {inspectStorage,requireStorage} = require('./storage.cjs');
const {createExitHandler} = require('./exit.cjs');
const {showWindow,closeWindow} = require('./window.cjs');
const {isTrayState,menuFor,openWebConsole} = require('./tray.cjs');
const packageRoot = path.resolve(__dirname,'../..');
const data = app.isPackaged
  ? process.platform === 'darwin' ? path.join(app.getPath('appData'),'AnelfAgent')
    : process.platform === 'linux' && !process.env.APPIMAGE ? path.join(app.getPath('appData'),'AnelfAgent')
      : path.join(path.dirname(process.env.APPIMAGE || app.getPath('exe')),'data')
  : path.join(packageRoot,'app/data');
fs.mkdirSync(data,{recursive:true});
fs.writeFileSync(path.join(data,'app-owner.json'),JSON.stringify({app:'cn.anelf.launcher',path:path.resolve(data)}));
app.setPath('userData',path.join(data,'profile'));
app.setPath('sessionData',path.join(data,'profile'));
app.setPath('logs',path.join(data,'logs'));
app.setPath('crashDumps',path.join(data,'crashes'));
/** @type {BrowserWindow | null} */ let window = null;
/** @type {import('electron').Tray | null} */ let tray = null;
/** @type {import('./backend.cjs').Connection | null} */ let connection = null;
/** @type {AbortController | null} */ let bootstrap = null;
let quitting = false;
let stoppingForExit = false;
let bootstrapDone = Promise.resolve();
/** @type {Set<string>} */ const connectedRoots = new Set();
const chinese = app.getPreferredSystemLanguages().some(value => value.startsWith('zh'));
/** @type {import('./tray.cjs').TrayState} */
let trayState={ready:false,webReady:false,settingsAvailable:false,language:chinese?'zh':'en'};
const bootstrapFile = path.join(__dirname,'bootstrap.html'), bootstrapUrl = pathToFileURL(bootstrapFile).href;
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const backendSource = app.isPackaged ? path.join(process.resourcesPath,'backend') : packageRoot;
const downloads = JSON.parse(fs.readFileSync(path.join(backendSource,'app/downloads.json'),'utf8'));
const requirements = JSON.parse(fs.readFileSync(path.join(backendSource,'app/install-requirements.json'),'utf8'));
/** @type {{root:string,source:string,downloadSource:string,pypiSource:string,configured:boolean,projectChoice?:'new'|'existing'}} */
let preferences = {root:app.isPackaged ? path.join(data,'project') : packageRoot,source:'auto',downloadSource:'auto',pypiSource:'auto',configured:false};
try { preferences = {...preferences,...JSON.parse(fs.readFileSync(path.join(data,'bootstrap.json'),'utf8'))}; } catch { /* A fresh installation has no selection. */ }
/** @param {import('electron').IpcMainInvokeEvent} event */
function trusted(event) {
  return !stoppingForExit && !!window && event.sender === window.webContents && event.senderFrame === event.sender.mainFrame &&
    (event.senderFrame.url === bootstrapUrl || !!connection && new URL(event.senderFrame.url).origin === connection.url);
}
function save() { fs.writeFileSync(path.join(data,'bootstrap.json'),JSON.stringify(preferences)); }
function show() { showWindow(window); }
/** @param {'home'|'settings'} view */
function navigate(view) {
  show();
  const contents=window?.webContents;
  if (!contents || contents.isDestroyed()) return;
  const send=()=>{if(!contents.isDestroyed())contents.send('anelf:navigate',view);};
  if(contents.isLoadingMainFrame())contents.once('did-finish-load',send);else send();
}
function refreshTray() {
  if (!tray || tray.isDestroyed()) return;
  tray.setContextMenu(Menu.buildFromTemplate(menuFor(trayState,{navigate,
    web:()=>{void openWebConsole(async()=>{
      const expected=connection;
      if(!expected || stoppingForExit || quitting)return null;
      const status=await request(expected,'status');
      return connection===expected && typeof status.root==='string' && path.resolve(status.root)===path.resolve(preferences.root) ? status : null;
    },url=>shell.openExternal(url),()=>{trayState={...trayState,webReady:false};refreshTray();navigate('home');});},
    exit:()=>{void requestExit();},
  })));
}
const requestExit = createExitHandler({
  active:()=>hasRunningServices([...connectedRoots,preferences.root]),
  confirm: () => {
    show();
    const options = {type:/** @type {const} */ ('question'),title:'AnelfAgent',
      message:chinese?'退出 AnelfAgent？':'Quit AnelfAgent?',
      detail:chinese?'服务仍在运行。默认保留后台，勾选可一起停止。':'Keep services running, or check below to stop them.',
      buttons:chinese?['退出','取消']:['Quit','Cancel'],defaultId:0,cancelId:1,
      checkboxLabel:chinese?'同时安全停止服务':'Also stop services safely',checkboxChecked:false,noLink:true};
    return window ? dialog.showMessageBox(window,options) : dialog.showMessageBox(options);
  },
  stop: async () => {
    stoppingForExit=true;
    trayState={...trayState,webReady:false,settingsAvailable:false};refreshTray();
    window?.setTitle(chinese?'AnelfAgent · 正在安全退出':'AnelfAgent · Stopping safely');
    window?.webContents.send('anelf:exit-state',{stopping:true,error:''});
    bootstrap?.abort();
    await bootstrapDone;
    for (const root of new Set([...connectedRoots,preferences.root])) await drain(root,true);
  },
  quit: () => {quitting=true;app.quit();},
  failed: error => {
    stoppingForExit=false;
    const detail=error instanceof Error ? error.message : String(error);
    window?.setTitle('AnelfAgent');
    if (window) {show();window.webContents.send('anelf:exit-state',{stopping:false,error:detail});}
    else dialog.showErrorBox(chinese?'未能安全退出':'Could not stop safely',detail);
  },
});
function storage() {
  return [inspectStorage(preferences.root,requirements.project_free_bytes),
    inspectStorage(path.join(data,'tools'),requirements.management_free_bytes)];
}
/** @param {string} url */
function safeExternal(url) { try { const value = new URL(url); return ['http:','https:'].includes(value.protocol) && !value.username && !value.password; } catch { return false; } }
function runtime() {
  let backend = backendSource;
  let bundled = app.isPackaged ? path.join(process.resourcesPath,'tools',process.platform === 'win32' ? 'uv.exe' : 'uv') : path.join(packageRoot,'app/build/tools',process.platform === 'win32' ? 'uv.exe' : 'uv');
  if (app.isPackaged) {
    const manifest = JSON.parse(fs.readFileSync(path.join(backend,'manifest.json'),'utf8'));
    if (!/^[a-f0-9]{64}$/.test(manifest.sha256)) throw new Error('Invalid backend manifest');
    const target = path.join(data,'runtime',manifest.sha256.slice(0,16));
    const cached = path.join(target,'backend'), cachedUv = path.join(target,path.basename(bundled));
    if (!fs.existsSync(path.join(target,'ready'))) {
      fs.mkdirSync(target,{recursive:true}); fs.cpSync(backend,cached,{recursive:true}); fs.copyFileSync(bundled,cachedUv); fs.chmodSync(cachedUv,0o755); fs.writeFileSync(path.join(target,'ready'),'');
    }
    backend = cached; bundled = cachedUv;
  }
  return {backend,uv:findUv(bundled)};
}
/** @param {boolean} [setup] */
async function connect(setup=false) {
  if (bootstrap) return;
  bootstrap = new AbortController();
  let completed = () => {};
  bootstrapDone = new Promise(resolve => {completed=resolve;});
  connectedRoots.add(preferences.root);
  try {
    if (setup) requireStorage(storage());
    register(data,preferences.root);
    const state = path.join(preferences.root,'logs/manager'); fs.mkdirSync(state,{recursive:true});
    const settingsFile = path.join(state,'settings.json');
    const existing = fs.existsSync(settingsFile) ? JSON.parse(fs.readFileSync(settingsFile,'utf8')) : {};
    if (setup) {
      fs.writeFileSync(settingsFile,JSON.stringify({...existing,source:preferences.source,download_source:preferences.downloadSource,pypi_source:preferences.pypiSource,timezone}));
    }
    const selected = runtime();
    const profile=resolveProfile(downloads,setup ? preferences.downloadSource : existing.download_source || preferences.downloadSource,
      setup ? preferences.pypiSource : existing.pypi_source || preferences.pypiSource,timezone);
    connection = await ensureBackend({root:preferences.root,backend:selected.backend,uv:selected.uv.path,timezone,
      profile,indexEnvironment:downloads.uv_index_environment,tools:path.join(data,'tools'),signal:bootstrap.signal,
      progress:value => window?.webContents.send('anelf:bootstrap-progress',value)});
    preferences.configured = true; save();
    if (window && !quitting && !stoppingForExit) await window.loadURL(connection.url + '/' + (setup ? '?setup=1' : '') + '#' + connection.token);
  } finally { bootstrap = null; completed(); }
}
async function start() {
  app.setAppUserModelId('cn.anelf.launcher');
  nativeTheme.themeSource = 'dark';
  session.defaultSession.setPermissionRequestHandler((_contents,_permission,callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  Menu.setApplicationMenu(null);
  const area = screen.getPrimaryDisplay().workAreaSize;
  const width = Math.min(640,area.width - 24), height = Math.min(580,area.height - 24);
  window = new BrowserWindow({width,height,minWidth:Math.min(480,width),minHeight:Math.min(300,height),title:'AnelfAgent',backgroundColor:'#0d131e',icon:path.join(__dirname,'icon.png'),
    webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true,webSecurity:true,backgroundThrottling:false}});
  const trayImage = nativeImage.createFromPath(path.join(__dirname,process.platform === 'darwin' ? 'trayTemplate.png' : 'icon.png'));
  if (process.platform === 'darwin') trayImage.setTemplateImage(true);
  tray = new Tray(process.platform === 'darwin' ? trayImage : trayImage.resize({width:20,height:20}));
  tray.setToolTip('AnelfAgent');
  refreshTray();
  tray.on('click',show);
  tray.on('double-click',show);
  window.webContents.setWindowOpenHandler(({url}) => { if (safeExternal(url)) void shell.openExternal(url); return {action:'deny'}; });
  window.webContents.on('will-navigate',(event,url) => { if (url !== bootstrapUrl && (!connection || new URL(url).origin !== connection.url)) event.preventDefault(); });
  window.on('close',event => closeWindow(event,{window,quitting,trayAvailable:!!tray && !tray.isDestroyed(),exit:requestExit}));
  window.on('closed',() => {window=null;});
  ipcMain.handle('anelf:version',event => {if (!trusted(event)) throw new Error('Untrusted sender');return app.getVersion();});
  ipcMain.handle('anelf:tray-state',(event,value) => {
    if (!trusted(event) || !isTrayState(value)) throw new Error('Invalid tray state');
    if(JSON.stringify(value)!==JSON.stringify(trayState)){trayState=value;refreshTray();}
  });
  window.webContents.on('did-start-navigation',(_event,_url,inPlace,isMainFrame)=>{
    if(isMainFrame && !inPlace){trayState={...trayState,ready:false,webReady:false,settingsAvailable:false};refreshTray();}
  });
  ipcMain.handle('anelf:bootstrap-storage',event => {if (!trusted(event)) throw new Error('Untrusted sender');return storage();});
  ipcMain.handle('anelf:resize',(event,value) => {
    if (!trusted(event) || !window || !value || !['setup','manage','settings'].includes(value.view) || typeof value.logs !== 'boolean') throw new Error('Invalid window layout');
    if (window.isMaximized() || window.isFullScreen()) return;
    const bounds = window.getBounds(), work = screen.getDisplayMatching(bounds).workArea;
    const width = Math.min(value.view === 'manage' ? 570 : 640,work.width - 24);
    const chrome = bounds.height - window.getContentBounds().height;
    const content = typeof value.height === 'number' && Number.isFinite(value.height) ? value.height : value.view === 'manage' ? 370 : 540;
    const height = Math.min(Math.max(300,Math.ceil(content)+chrome),760,work.height - 24);
    if (bounds.width!==width || bounds.height!==height) window.setBounds({x:Math.max(work.x,Math.min(bounds.x,work.x + work.width - width)),y:Math.max(work.y,Math.min(bounds.y,work.y + work.height - height)),width,height},true);
  });
  ipcMain.handle('anelf:bootstrap-info',event => {
    if (!trusted(event)) throw new Error('Untrusted sender');
    const selected = runtime();
    return {...preferences,defaultTarget:path.join(data,'project'),pypiSources:downloads.pypi_sources,pypiDefaults:{china:downloads.china.pypi,global:downloads.global.pypi},timezone,china:downloads.china_timezones.includes(timezone),uv:selected.uv,storage:storage(),language:chinese?'zh':'en'};
  });
  ipcMain.handle('anelf:bootstrap-start',async(event,value) => {
    if (!trusted(event) || event.senderFrame?.url !== bootstrapUrl) throw new Error('Untrusted sender');
    if (bootstrap) throw new Error('Please wait for the current operation');
    if (!value || !['auto','github','gitee'].includes(value.source) || !['auto','china','global'].includes(value.downloadSource)) throw new Error('Invalid download source');
    resolveProfile(downloads,value.downloadSource,value.pypiSource,timezone);
    if (value.existing) {
      if(typeof value.existing.root!=='string' || typeof value.existing.target!=='string' || typeof value.existing.move!=='boolean')throw new Error('Invalid project selection');
      const root=inspectProject(value.existing.root),target=path.resolve(value.existing.target);
      bootstrap=new AbortController();
      let complete=()=>{};bootstrapDone=new Promise(resolve=>{complete=resolve;});
      try {
        const selected=runtime();
        const result=await transferProject({root,target,move:value.existing.move},{root,backend:selected.backend,uv:selected.uv.path,timezone,
          profile:resolveProfile(downloads,value.downloadSource,value.pypiSource,timezone),indexEnvironment:downloads.uv_index_environment,
          tools:path.join(data,'tools'),signal:bootstrap.signal,progress:text=>window?.webContents.send('anelf:bootstrap-progress',text)},data,async()=>{
          if(!window)return false;
          const choice=await dialog.showMessageBox(window,{type:'question',title:'AnelfAgent',
            message:chinese?'旧项目服务正在运行，关闭后继续迁移？':'The existing service is running. Stop it and continue?',
            detail:root,buttons:chinese?['关闭服务并迁移','否，返回上一步']:['Stop and move','No, go back'],defaultId:1,cancelId:1,noLink:true});
          return choice.response===0;
        });
        if(!result.accepted)return {accepted:false};
        preferences.root=result.root;preferences.configured=false;preferences.projectChoice='existing';
      } catch(error) {
        // A durable move may have committed even if subsequent cleanup was interrupted.
        if(fs.existsSync(path.join(data,'bootstrap.json')))preferences={...preferences,...JSON.parse(fs.readFileSync(path.join(data,'bootstrap.json'),'utf8'))};
        throw error;
      } finally {bootstrap=null;complete();}
    } else preferences.projectChoice='new';
    preferences.source=value.source;preferences.downloadSource=value.downloadSource;preferences.pypiSource=value.pypiSource;save();
    await connect(!value.existing || value.existing.move);
    return {accepted:true};
  });
  ipcMain.handle('anelf:bootstrap-cancel',event => { if (!trusted(event)) throw new Error('Untrusted sender'); bootstrap?.abort(); });
  ipcMain.handle('anelf:choose-project',async (event,purpose) => {
    if (!trusted(event) || !window || bootstrap) throw new Error('Please wait for the current operation');
    if (connection && (await request(connection,'status')).busy) throw new Error('Cancel the current installation before changing projects');
    const result = await dialog.showOpenDialog(window,{title:chinese?'选择已有项目或空安装目录':'Choose a checkout or empty folder',properties:['openDirectory','createDirectory']});
    if (!result.canceled && result.filePaths[0]) {
      if(purpose==='existing')return inspectProject(result.filePaths[0]);
      if(purpose==='destination')return path.resolve(result.filePaths[0]);
      preferences.root=path.resolve(result.filePaths[0]);save();
      if (event.senderFrame?.url === bootstrapUrl) return preferences.root;
      await window.loadFile(bootstrapFile);
    }
    return purpose ? undefined : preferences.root;
  });
  ipcMain.handle('anelf:reconnect',async event => { if (!trusted(event) || !window) throw new Error('Untrusted sender'); await window.loadFile(bootstrapFile); });
  ipcMain.handle('anelf:cleanup',async event => {
    if (!trusted(event) || !window || !app.isPackaged) throw new Error('Cleanup is only available in the installed app');
    const owned = projects(data).filter(entry => entry.owned).map(entry => entry.root).join('\n');
    const result = await dialog.showMessageBox(window,{type:'warning',title:'AnelfAgent',message:chinese?'停止服务并删除 APP 创建的项目与环境？':'Stop services and delete app-created projects and environments?',detail:owned,buttons:chinese?['取消','删除并退出']:['Cancel','Delete and quit'],defaultId:0,cancelId:0});
    if(result.response===1){
      await prepare(data,true);
      const child=spawn(process.execPath,[path.join(__dirname,'cleanup.cjs'),data,String(process.pid)],{env:{...process.env,ELECTRON_RUN_AS_NODE:'1'},detached:process.platform !== 'win32',windowsHide:true,stdio:'ignore'});
      child.unref();quitting=true;app.quit();
    }
  });
  await window.loadFile(bootstrapFile);
  if(fs.existsSync(path.join(data,'migration.json'))) {
    bootstrap=new AbortController();
    try {
      const selected=runtime();
      await runTask({root:preferences.root,backend:selected.backend,uv:selected.uv.path,timezone,
        profile:resolveProfile(downloads,preferences.downloadSource,preferences.pypiSource,timezone),tools:path.join(data,'tools'),
        progress:text=>window?.webContents.send('anelf:bootstrap-progress',text)},['recover-migration','--data',data]);
      if(fs.existsSync(path.join(data,'bootstrap.json')))preferences={...preferences,...JSON.parse(fs.readFileSync(path.join(data,'bootstrap.json'),'utf8'))};
    } finally {bootstrap=null;}
    await window.loadFile(bootstrapFile);
  }
  if (preferences.configured || await connectionAt(preferences.root)) { try { await connect(); } catch(error) { window?.webContents.send('anelf:bootstrap-progress',String(error)); } }
}
const maintenance = process.argv.includes('--prepare-uninstall') || process.argv.includes('--prepare-upgrade');
if (maintenance) {
  app.whenReady().then(() => prepare(data,process.argv.includes('--prepare-uninstall'))).then(() => app.exit(0)).catch(error => {fs.writeFileSync(path.join(data,'uninstall-error.log'),String(error));app.exit(1);});
} else if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance',show);app.whenReady().then(start).catch(error => {dialog.showErrorBox('AnelfAgent',String(error));app.quit();});
  app.on('before-quit',event => {if(!quitting){event.preventDefault();void requestExit();}});
  app.on('window-all-closed',() => {if(!quitting && !tray)void requestExit();});app.on('activate',show);
  app.on('will-quit',() => {tray?.destroy();tray=null;});
}
