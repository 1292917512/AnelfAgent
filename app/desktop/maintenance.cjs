// @ts-check
/** Drain managed services before an explicit shutdown, upgrade or uninstall. */
const fs = require('node:fs');
const path = require('node:path');
const {connectionAt} = require('./backend.cjs');
const {projects, removeOwned, verifyOwnership} = require('./managed.cjs');
/** @param {import('./backend.cjs').Connection} connection @param {string} endpoint @param {object} [body] */
async function request(connection, endpoint, body) {
  const response = await fetch(connection.url + '/api/' + endpoint,{method:body ? 'POST' : 'GET',headers:{Authorization:'Bearer ' + connection.token,'Content-Type':'application/json'},body:body ? JSON.stringify(body) : undefined,signal:AbortSignal.timeout(8000)});
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
/** @param {string} root @param {boolean} stopServices */
async function drain(root, stopServices) {
  const connection = await connectionAt(root);
  if (!connection) {
    // A live runtime without its manager needs its original entry point for safe shutdown.
    for (const filename of ['runtime.json','connection.json']) {
      try { const value = JSON.parse(fs.readFileSync(path.join(root,'logs/manager',filename),'utf8')); if (Number.isInteger(value.pid) && value.pid > 0 && alive(value.pid)) throw new Error('AnelfAgent is still running without its manager. Stop it through its original entry point.'); }
      catch (error) { if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') continue; throw error; }
    }
    try {
      const pid=Number(fs.readFileSync(path.join(root,'logs/anelf.pid'),'utf8').trim());
      if (Number.isInteger(pid) && pid > 0 && alive(pid)) throw new Error('AnelfAgent is still running without its manager. Stop it through its original entry point.');
    } catch (error) {if (!error || typeof error !== 'object' || !('code' in error) || error.code !== 'ENOENT') throw error;}
    return;
  }
  let status = await request(connection,'status');
  if (status.busy && status.operation?.cancellable) await request(connection,'cancel',{});
  const deadline = Date.now() + 660000;
  while (status.busy && Date.now() < deadline) { await new Promise(resolve => setTimeout(resolve,300)); status = await request(connection,'status'); }
  if (status.busy) throw new Error('The current operation has not stopped.');
  if (stopServices) {
    await request(connection,'action',{action:'stop'});
    do { await new Promise(resolve => setTimeout(resolve,300)); status = await request(connection,'status'); } while (status.busy && Date.now() < deadline);
    if (status.busy || status.running || !status.last?.ok) throw new Error(status.last?.message || 'The service has not stopped.');
  }
  await request(connection,stopServices ? 'shutdown' : 'detach',{});
  const exitDeadline = Date.now() + 20000;
  while (alive(connection.pid) && Date.now() < exitDeadline) await new Promise(resolve => setTimeout(resolve,200));
  if (alive(connection.pid)) throw new Error('The manager has not exited. Files were preserved.');
}
/** @param {string} data @param {boolean} uninstall */
async function prepare(data, uninstall) {
  const entries = projects(data);
  if (uninstall) for (const entry of entries) if (entry.owned) verifyOwnership(data,entry);
  for (const entry of entries) await drain(entry.root,uninstall && entry.owned);
  if (uninstall) for (const entry of entries) if (entry.owned) await removeOwned(data,entry);
}
module.exports = {prepare,drain,request,hasRunningServices};
