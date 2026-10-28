// @ts-check
/** Stage the management backend, its dependency manifest and verified uv, never user data. */
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const extractZip = require('extract-zip');
const root = path.resolve(__dirname, '../..');
const stage = path.resolve(__dirname, '../build');

/** @param {string} url */
async function download(url) {
  const response = await fetch(url, {headers:{'User-Agent':'AnelfAgent-build'},signal:AbortSignal.timeout(180000)});
  if (!response.ok || !response.url.startsWith('https://')) throw new Error('Download failed (' + response.status + '): ' + url);
  return Buffer.from(await response.arrayBuffer());
}

async function main() {
  require('./version.cjs').syncVersion();
  const backend = path.join(stage,'backend');
  if (!backend.startsWith(path.join(root,'app','build') + path.sep)) throw new Error('Invalid staging path');
  await fs.rm(backend,{recursive:true,force:true});
  for (const folder of ['app','core','app/ui','app/desktop']) await fs.mkdir(path.join(backend,folder),{recursive:true});
  for (const name of ['theme.css','icon.svg']) await fs.copyFile(path.join(__dirname,name),path.join(backend,'app/desktop',name));
  for (const name of await fs.readdir(path.join(root,'app'))) {
    if (name.endsWith('.py') || name.endsWith('.json') || name === 'panel.html' || name === 'runtime-requirements.txt') await fs.copyFile(path.join(root,'app',name),path.join(backend,'app',name));
  }
  for (const name of await fs.readdir(path.join(root,'app/ui'))) if (name.endsWith('.js') || name.endsWith('.css')) await fs.copyFile(path.join(root,'app/ui',name),path.join(backend,'app/ui',name));
  for (const name of ['__init__.py','host_protocol.py','host_control.py']) await fs.copyFile(path.join(root,'core',name),path.join(backend,'core',name));
  for (const name of ['start.bat','start.sh']) await fs.copyFile(path.join(root,name),path.join(backend,name));
  const digest = crypto.createHash('sha256');
  digest.update(require('./package.json').anelf.uvVersion);
  for (const entry of (await fs.readdir(backend,{recursive:true,withFileTypes:true})).filter(item => item.isFile()).sort((a,b) => path.join(a.parentPath,a.name).localeCompare(path.join(b.parentPath,b.name)))) {
    const filename = path.join(entry.parentPath,entry.name);
    digest.update(path.relative(backend,filename)); digest.update(await fs.readFile(filename));
  }
  await fs.writeFile(path.join(backend,'manifest.json'),JSON.stringify({sha256:digest.digest('hex')}));
  const architecture = process.arch === 'arm64' ? 'aarch64' : 'x86_64';
  const targets = /** @type {Record<string, string>} */ ({win32:'pc-windows-msvc.zip',darwin:'apple-darwin.tar.gz',linux:'unknown-linux-gnu.tar.gz'});
  const target = targets[process.platform];
  if (!target) throw new Error('Unsupported build platform');
  const name = `uv-${architecture}-${target}`;
  const version = require('./package.json').anelf.uvVersion;
  const release = JSON.parse(await fs.readFile(path.join(root,'app/tool-releases.json'),'utf8'))['astral-sh/uv'];
  if (release.tag_name !== version) throw new Error('Pinned uv release does not match the desktop version');
  const asset = release.assets.find(/** @param {{name:string}} item */ item => item.name === name);
  if (!asset || !asset.digest?.startsWith('sha256:')) throw new Error('Verified uv release not found');
  const tools = path.join(stage,'tools'); await fs.mkdir(tools,{recursive:true});
  const archive = path.join(stage,name);
  const data = await download(asset.browser_download_url);
  if ('sha256:' + crypto.createHash('sha256').update(data).digest('hex') !== asset.digest) throw new Error('uv checksum mismatch');
  await fs.writeFile(archive,data);
  const temporary = await fs.mkdtemp(path.join(stage,'uv-'));
  try {
    if (process.platform === 'win32') await extractZip(archive,{dir:temporary});
    else { const result = spawnSync('tar',['-xf',archive,'-C',temporary],{stdio:'inherit'}); if (result.status !== 0) throw new Error('Unable to unpack uv'); }
    const executable = process.platform === 'win32' ? 'uv.exe' : 'uv';
    /** @param {string} directory @returns {Promise<string | null>} */
    async function find(directory) { for(const item of await fs.readdir(directory,{withFileTypes:true})) { const full=path.join(directory,item.name); if(item.isDirectory()) { const found=await find(full);if(found)return found; } else if(item.name===executable)return full; } return null; }
    const source = await find(temporary); if (!source) throw new Error('uv executable missing');
    await fs.copyFile(source,path.join(tools,executable)); await fs.chmod(path.join(tools,executable),0o755);
    await fs.writeFile(path.join(tools,'version.json'),JSON.stringify({version:release.tag_name,sha256:asset.digest},null,2));
    for (const license of ['LICENSE-MIT','LICENSE-APACHE']) await fs.writeFile(path.join(tools,license + '.txt'),await download(`https://raw.githubusercontent.com/astral-sh/uv/${version}/${license}`));
  } finally { await fs.rm(temporary,{recursive:true,force:true}); await fs.rm(archive); }
  console.log('Staged backend and uv at ' + stage);
}
main().catch(error => { console.error(error); process.exitCode=1; });
