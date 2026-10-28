// @ts-check
/** Window visibility is independent of service lifetime and explicit application exit. */
/** @typedef {Pick<import('electron').BrowserWindow,'isDestroyed'|'isMinimized'|'restore'|'show'|'focus'|'hide'>} WindowPort */
/** @param {WindowPort | null} window */
function showWindow(window) {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
/** @param {{preventDefault:()=>void}} event
 * @param {{window:WindowPort|null,quitting:boolean,trayAvailable:boolean,exit:()=>Promise<void>}} options */
function closeWindow(event, options) {
  if (options.quitting) return;
  event.preventDefault();
  if (options.trayAvailable && options.window && !options.window.isDestroyed()) options.window.hide();
  else void options.exit();
}
module.exports = {showWindow,closeWindow};
