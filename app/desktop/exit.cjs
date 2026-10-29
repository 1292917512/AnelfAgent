// @ts-check
/** Explicit application exits share one service-aware confirmation and shutdown path. */
/** @typedef {{active?:()=>Promise<boolean>,confirm:()=>Promise<{response:number,checkboxChecked:boolean}>,stop:(signal:AbortSignal)=>Promise<void>,quit:()=>void,failed:(error:unknown)=>void,cancelled?:()=>void}} Options */
/** Stop waiting without claiming to undo an already accepted service request.
 * @template T @param {Promise<T>} promise @param {AbortSignal} signal @returns {Promise<T>} */
function waitFor(promise,signal) {
  return new Promise((resolve,reject)=>{
    const abort=()=>reject(signal.reason);
    if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});
    promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));
  });
}
/** @param {Options} options */
function createExitHandler(options) {
  /** @type {Promise<void> | null} */ let pending = null;
  /** @type {AbortController | null} */ let controller = null;
  function requestExit() {
    if (pending) return pending;
    controller=new AbortController();
    const signal=controller.signal;
    pending = Promise.resolve().then(async () => {
      const active=!options.active || await waitFor(options.active(),signal);
      signal.throwIfAborted();
      if (!active) {options.quit();return;}
      const choice = await options.confirm();
      signal.throwIfAborted();
      if (choice.response !== 0) return;
      if (choice.checkboxChecked) await waitFor(options.stop(signal),signal);
      signal.throwIfAborted();
      options.quit();
    }).catch(error => {if(signal.aborted)options.cancelled?.();else options.failed(error);}).finally(() => {pending=null;controller=null;});
    return pending;
  }
  return Object.assign(requestExit,{cancel:()=>{controller?.abort();return pending || Promise.resolve();}});
}
module.exports = {createExitHandler,waitFor};
