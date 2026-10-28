// @ts-check
/** Explicit application exits share one service-aware confirmation and shutdown path. */
/** @typedef {{active?:()=>Promise<boolean>,confirm:()=>Promise<{response:number,checkboxChecked:boolean}>,stop:()=>Promise<void>,quit:()=>void,failed:(error:unknown)=>void}} Options */
/** @param {Options} options */
function createExitHandler(options) {
  /** @type {Promise<void> | null} */ let pending = null;
  return function requestExit() {
    if (pending) return pending;
    pending = Promise.resolve().then(async () => {
      if (options.active && !await options.active()) {options.quit();return;}
      const choice = await options.confirm();
      if (choice.response !== 0) return;
      if (choice.checkboxChecked) await options.stop();
      options.quit();
    }).catch(error => options.failed(error)).finally(() => {pending=null;});
    return pending;
  };
}
module.exports = {createExitHandler};
