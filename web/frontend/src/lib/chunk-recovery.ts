const RELOAD_KEY = "anelf:chunk-reload-at";
let lastAttempt = 0;

export function recoverChunk(): boolean {
  if (!navigator.onLine) return false;
  try { lastAttempt = Number(sessionStorage.getItem(RELOAD_KEY) ?? lastAttempt); } catch { /* Session storage is optional. */ }
  if (Date.now() - lastAttempt < 60_000) return false;
  lastAttempt = Date.now();
  try { sessionStorage.setItem(RELOAD_KEY, String(lastAttempt)); } catch { /* Session storage is optional. */ }
  window.location.reload();
  return true;
}
