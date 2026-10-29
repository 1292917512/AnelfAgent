// @ts-check
/** Drain managed services before an explicit shutdown, upgrade or uninstall. */
const fs = require('node:fs');
const path = require('node:path');
const {setTimeout:delay}=require('node:timers/promises');
const {connectionAt} = require('./backend.cjs');
const {projects, removeOwned, verifyOwnership} = require('./managed.cjs');
const {preserveProjects}=require('./preservation.cjs');
/** @param {import('./backend.cjs').Connection} connection @param {string} endpoint @param {object} [body] @param {AbortSignal} [signal] */
async function request(connection, endpoint, body, signal) {
  const timeout=AbortSignal.timeout(8000);
  const response = await fetch(connection.url + '/api/' + endpoint,{method:body ? 'POST' : 'GET',headers:{Authorization:'Bearer ' + connection.token,'Content-Type':'application/json'},body:body ? JSON.stringify(body) : undefined,signal:signal ? AbortSignal.any([signal,timeout]) : timeout});
  const result = await response.json(); if (!response.ok) throw new Error(result.message || 'Manager request failed'); return result;
}
/** @param {number} pid */
function alive(pid) { try { process.kill(pid,0); return true; } catch (error) { return !!error && typeof error === 'object' && 'code' in error && error.code === 'EPERM'; } }
/** Only application services count; an idle management API is not a running service.
 * @param {string[]} roots */
async function hasRunningServices(roots) {
  const states=await Promise.all([...new Set(roots)].map(async root=>{
    try {
      const connection=await connectionAt(root);
      if (connection) {
        const status=await request(connection,'status');
        return !!status.running || ['start','starting','restart','restarting','stop','stopping'].includes(status.phase);
      }
    } catch { /* An unavailable manager may still have a live application record. */ }
    for (const filename of ['logs/anelf.pid','logs/anelf_guardian.pid','logs/manager/runtime.json']) {
      try {
        const content=fs.readFileSync(path.join(root,filename),'utf8');
        const pid=filename.endsWith('.json') ? JSON.parse(content).pid : Number(content.trim());
        if (Number.isInteger(pid) && pid>0 && alive(pid))return true;
      } catch { /* Missing or stale records do not prove that a service is running. */ }
    }
    return false;
  }));
  return states.some(Boolean);
}
/** @typedef {{signal?:AbortSignal,progress?:(stage:string)=>void}} DrainOptions */
/** @param {string} root @param {boolean} stopServices @param {()=>Promise<unknown>} [stopRuntime] @param {DrainOptions} [options] */
async function drain(root, stopServices, stopRuntime, options={}) {
  const {signal}=options;
  const progress=(/** @type {string} */ stage)=>{signal?.throwIfAborted();options.progress?.(stage);};
  progress('checking');
  const connection = await connectionAt(root);
  signal?.throwIfAborted();
  if (!connection) {
    if (stopServices && stopRuntime && await hasRunningServices([root])) await stopRuntime();
    // A live runtime without its manager needs its original entry point for safe shutdown.
    for (const filename of ['runtime.json','connection.json']) {
      try { const value = JSON.parse(fs.readFileSync(path.join(root,'logs/manager',filename),'utf8')); if (Number.isInteger(value.pid) && value.pid > 0 && alive(value.pid)) throw new Error('AnelfAgent is still running without its manager. Stop it through its original entry point.'); }
      catch (error) { if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') continue; throw error; }
    }
    for (const name of ['anelf.pid','anelf_guardian.pid']) try {
      const pid=Number(fs.readFileSync(path.join(root,'logs',name),'utf8').trim());
      if (Number.isInteger(pid) && pid > 0 && alive(pid)) throw new Error('AnelfAgent is still running without its manager. Stop it through its original entry point.');
    } catch (error) {if (!error || typeof error !== 'object' || !('code' in error) || error.code !== 'ENOENT') throw error;}
    return;
  }
  const call=(/** @type {string} */ endpoint,/** @type {object | undefined} */ body=undefined)=>request(connection,endpoint,body,signal);
  const checkControl=(/** @type {any} */ status)=>{
    if(!stopServices)return;
    let legacyMissing=false;
    if(status.controllable===undefined && status.pid===null)try {
      const pid=Number(fs.readFileSync(path.join(root,'logs/anelf.pid'),'utf8').trim());
      legacyMissing=Number.isInteger(pid) && pid>0 && alive(pid);
    } catch { /* No live project record. */ }
    if(legacyMissing || status.controllable===false && status.running)
      throw new Error('服务控制记录不可用，无法确认安全关停。请从原启动入口停止服务；也可返回或仅退出应用。 / Service control is unavailable. Stop it from its original entry point, or return / quit only the app.');
  };
  let status = await call('status');
  checkControl(status);
  if (status.busy && status.operation?.cancellable) {
    progress(['install','setup'].includes(status.operation.action)?'cancel-install':status.operation.action==='update'?'cancel-update':'wait-operation');
    await call('cancel',{});
  } else if(status.busy)progress(['stop','stopping'].includes(status.phase)?'wait-service':'wait-operation');
  const deadline = Date.now() + 660000;
  while (status.busy && Date.now() < deadline) { await delay(300,undefined,{signal}); status = await call('status'); checkControl(status); }
  if (status.busy) throw new Error('The current operation has not stopped.');
  if (stopServices) {
    progress('stop-service');
    await call('action',{action:'stop'});
    progress('wait-service');
    do { await delay(300,undefined,{signal}); status = await call('status'); checkControl(status); } while (status.busy && Date.now() < deadline);
    if (status.busy || status.running || !status.last?.ok) throw new Error(status.last?.message || 'The service has not stopped.');
  }
  progress('close-manager');
  await call(stopServices ? 'shutdown' : 'detach',{});
  const exitDeadline = Date.now() + 20000;
  while (alive(connection.pid) && Date.now() < exitDeadline) await delay(200,undefined,{signal});
  if (alive(connection.pid)) throw new Error('The manager has not exited. Files were preserved.');
}
/** Retaining data is the default; deletion always requires an explicit purge.
 * @param {string} data @param {'upgrade'|'uninstall'|'purge'} [mode] */
async function prepare(data, mode='uninstall') {
  if(!['upgrade','uninstall','purge'].includes(mode))throw new Error('Invalid maintenance mode');
  const removeData=mode==='purge';
  const entries = projects(data);
  if(removeData && fs.existsSync(path.join(data,'migration.json')))throw new Error('Finish or recover the project migration before deleting app data.');
  if (removeData) for (const entry of entries) if (entry.owned) verifyOwnership(data,entry);
  const retained=preserveProjects(data,removeData);
  if(removeData)for(const entry of entries)if(entry.owned && fs.existsSync(entry.root)) {
    for(const item of retained) {
      const relative=path.relative(fs.realpathSync(entry.root),fs.realpathSync(item.root));
      if(!relative || relative!=='..' && !relative.startsWith('..'+path.sep) && !path.isAbsolute(relative))throw new Error('An imported or selected project is inside this directory; preserve it before cleanup.');
    }
  }
  for (const entry of entries) await drain(entry.root,mode!=='upgrade' && entry.owned);
  if (removeData) for (const entry of entries) if (entry.owned) await removeOwned(data,entry);
}
module.exports = {prepare,drain,request,hasRunningServices};
