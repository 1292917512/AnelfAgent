// @ts-check
/** Locate and start the same Python management API used by the browser and CLI. */
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

/** @typedef {{url: string, token: string, pid: number}} Connection */
/** @typedef {{root:string, backend:string, uv:string, python?:string, timezone:string, downloadSource?:string, profile?:Record<string,string>, indexEnvironment?:string[], tools?:string, signal?:AbortSignal, progress?:(text:string)=>void}} Options */
/** @typedef {{china_timezones:string[],china:Record<string,string>,global:Record<string,string>,pypi_sources:Record<string,{url:string,label:Record<string,string>}>}} SourceCatalog */

/** @param {SourceCatalog} catalog @param {string} downloads @param {string} pypi @param {string} timezone */
function resolveProfile(catalog, downloads, pypi, timezone) {
  const region = downloads === 'auto' ? catalog.china_timezones.includes(timezone) ? 'china' : 'global' : downloads;
  if (!['china','global'].includes(region) || pypi !== 'auto' && !Object.hasOwn(catalog.pypi_sources,pypi)) throw new Error('Invalid download source');
  const profile = {...(region === 'china' ? catalog.china : catalog.global)};
  if (pypi !== 'auto') profile.pypi=catalog.pypi_sources[pypi].url;
  return profile;
}

/** @param {unknown} value @returns {value is Connection} */
function validConnection(value) {
  if (!value || typeof value !== 'object') return false;
  const data = /** @type {Record<string, unknown>} */ (value);
  if (typeof data.url !== 'string' || typeof data.token !== 'string' || data.token.length < 16 || !Number.isInteger(data.pid)) return false;
  try { const url = new URL(data.url); return url.protocol === 'http:' && url.hostname === '127.0.0.1' && !!url.port && !url.username && !url.password && url.pathname === '/'; }
  catch { return false; }
}

/** @param {string} root @returns {Promise<Connection | null>} */
async function connectionAt(root) {
  try {
    const value = JSON.parse(await fs.promises.readFile(path.join(root, 'logs/manager/connection.json'), 'utf8'));
    if (!validConnection(value)) return null;
    const response = await fetch(value.url + '/api/status', {headers:{Authorization:'Bearer ' + value.token}, signal:AbortSignal.timeout(1500)});
    if (!response.ok) return null;
    const state = await response.json();
    return path.resolve(state.root) === path.resolve(root) ? value : null;
  } catch { return null; }
}

/** @param {Options} options */
function commandFor(options) {
  const tools = options.tools || path.join(options.root,'logs/manager/tools');
  const environment={...process.env};
  for (const key of options.indexEnvironment || []) delete environment[key];
  return { command: options.uv,
    args: ['run', '--no-project', '--isolated', '--python', options.python || '3.12',
      '--with-requirements', path.join(options.backend,'app/runtime-requirements.txt'),
      '--no-config', '--default-index', options.profile?.pypi || 'https://pypi.org/simple',
      'python', '-m', 'app', '--root', options.root, 'serve', '--port', '0'],
    cwd: options.backend,
    env: {...environment, PATH:path.dirname(options.uv) + path.delimiter + (process.env.PATH || ''),
      ANELF_LAUNCHER_TIMEZONE:options.timezone, PYTHONUTF8:'1', PYTHONUNBUFFERED:'1', PYTHONDONTWRITEBYTECODE:'1',
      ANELF_USER_PYTHON_INSTALL_DIR:process.env.UV_PYTHON_INSTALL_DIR || '',
      UV_PYTHON_INSTALL_MIRROR:options.profile?.python || 'https://github.com/astral-sh/python-build-standalone/releases/download',
      UV_CACHE_DIR:path.join(tools,'uv-cache'), UV_PYTHON_INSTALL_DIR:path.join(tools,'python'),
      UV_PYTHON_INSTALL_BIN:'0', UV_PYTHON_INSTALL_REGISTRY:'0'} };
}

/** @param {Options} options @returns {Promise<Connection>} */
async function ensureBackend(options) {
  const existing = await connectionAt(options.root);
  if (existing) return existing;
  const state = path.join(options.root, 'logs/manager');
  fs.mkdirSync(state, {recursive:true});
  const log = fs.openSync(path.join(state, 'manager.log'), 'a');
  const python = findPython(options.uv, options.backend);
  const command = commandFor({...options, python});
  // Windows DETACHED_PROCESS can defeat console hiding for uv's descendants.
  // File-backed stdio and unref keep the manager independent of the app there.
  const child = spawn(command.command, command.args, {cwd:command.cwd, env:command.env, detached:process.platform !== 'win32', windowsHide:true, stdio:['ignore',log,log]});
  fs.closeSync(log);
  /** @type {Error | null} */ let spawnError = null;
  child.on('error', error => { spawnError = error; });
  child.unref();
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    if (spawnError) throw spawnError;
    const found = await connectionAt(options.root);
    if (found) return found;
    if (options.signal?.aborted) { terminate(child); throw new Error('Cancelled'); }
    if (child.exitCode !== null) throw new Error('Manager exited (' + child.exitCode + '). ' + fs.readFileSync(path.join(state,'manager.log'),'utf8').slice(-3000));
    if (options.progress) {
      const file = fs.openSync(path.join(state,'manager.log'),'r'), size = fs.fstatSync(file).size;
      const buffer = Buffer.alloc(Math.min(size,6000)); fs.readSync(file,buffer,0,buffer.length,Math.max(0,size-buffer.length)); fs.closeSync(file);
      options.progress(buffer.toString('utf8'));
    }
    await new Promise(resolve => setTimeout(resolve, 300));
  }
  terminate(child);
  throw new Error('Backend startup timed out. See ' + path.join(state, 'manager.log'));
}

/** @param {import('node:child_process').ChildProcess} child */
function terminate(child) {
  if (!child.pid || child.exitCode !== null) return;
  if (process.platform === 'win32') spawnSync('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,timeout:15000});
  else { try { process.kill(-child.pid,'SIGTERM'); } catch { /* The owned bootstrap may already have exited. */ } }
}

/** @param {string} fallback */
function findUv(fallback) {
  const found = spawnSync(process.platform === 'win32' ? 'where.exe' : 'which',['uv'],{encoding:'utf8',windowsHide:true,timeout:5000});
  const executable = process.platform === 'win32' ? 'uv.exe' : 'uv';
  const userTools = ['.local/bin','.cargo/bin'].map(folder => path.join(os.homedir(),folder,executable));
  for (const candidate of new Set([...(found.stdout || '').trim().split(/\r?\n/),...userTools,fallback])) {
    if (!candidate || !fs.existsSync(candidate)) continue;
    const result = spawnSync(candidate,['--version'],{encoding:'utf8',windowsHide:true,timeout:5000});
    const version = result.stdout?.trim();
    if (result.status === 0 && /^uv \d+\./.test(version)) return {path:candidate,version,bundled:candidate === fallback};
  }
  throw new Error('uv is unavailable. Reinstall AnelfAgent.');
}

/** Find user-managed or system Python before redirecting new installs into app-owned storage.
 * @param {string} uv @param {string} cwd @returns {string | undefined} */
function findPython(uv, cwd) {
  const result = spawnSync(uv,['python','find','3.12','--system','--no-project','--no-config','--no-python-downloads'],
    {cwd,encoding:'utf8',windowsHide:true,timeout:10000});
  const candidate = result.stdout?.trim();
  return result.status === 0 && candidate && fs.existsSync(candidate) ? candidate : undefined;
}

module.exports = { validConnection, commandFor, connectionAt, ensureBackend, findUv, findPython, resolveProfile };
