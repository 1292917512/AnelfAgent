// @ts-check
/** Keep intermediate products in build; publish only platform installers in dist. */
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {createRequire}=require('node:module');
const output = path.resolve(__dirname,'../build/package'), distribution = path.resolve(__dirname,'../dist');
const version = require('./version.cjs').syncVersion();
if(process.platform==='win32') {
  const builder=createRequire(require.resolve('electron-builder'));
  const template=path.join(path.dirname(builder.resolve('app-builder-lib/package.json')),'templates/nsis/include/installUtil.nsh');
  if(!fs.readFileSync(template,'utf8').includes('!insertmacro customUninstallPrevious'))throw new Error('Missing pinned NSIS upgrade hook. Install dependencies with the frozen lockfile before packaging.');
}
const result = spawnSync(process.execPath,[require.resolve('electron-builder/out/cli/cli.js'),'--publish','never',...process.argv.slice(2)],{stdio:'inherit',windowsHide:true});
if (result.status !== 0) process.exit(result.status || 1);
fs.mkdirSync(distribution,{recursive:true});
const extensions = process.platform === 'win32' ? ['.exe'] : process.platform === 'darwin' ? ['.dmg'] : ['.AppImage','.deb'];
for (const file of fs.readdirSync(distribution)) if (extensions.some(ext=>file.endsWith(ext))) fs.unlinkSync(path.join(distribution,file));
for (const file of fs.readdirSync(output)) if (extensions.some(ext=>file.endsWith(ext)) && file.startsWith(`AnelfAgent-${version}-`)) fs.copyFileSync(path.join(output,file),path.join(distribution,file));
console.log('Release installers: ' + distribution);
