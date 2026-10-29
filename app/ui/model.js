/** @param {import('./lib/types').State | null} state @param {boolean} connected */
export function controls(state, connected) {
  const idle = !!state && connected && !state.busy;
  const active = !!state && (state.running || ['starting','restarting','stopping'].includes(state.phase));
  return {start:idle && !!state?.ready && !active && !state?.external_pid, stop:idle && active && !state?.external_pid && state?.controllable!==false,
    restart:idle && !!state?.running && state.phase === 'running' && state.controllable!==false && !state.external_pid,
    workspace:idle && !!state?.running && state.phase === 'running' && state.web_ready!==false,
    configure:idle && !active, cancel:connected && !!state?.busy && !!state.operation.cancellable && !state.operation.cancelling};
}

/** @param {import('./lib/types').State | null} state */
export function installing(state) { return !!state && state.busy && ['install','setup','update'].includes(state.operation.action || ''); }
