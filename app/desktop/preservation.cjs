// @ts-check
/** Shared by the desktop and the installer, including upgrades from older installers. */
const fs=require('node:fs');
const path=require('node:path');
const {projects,removeAppData}=require('./managed.cjs');
/** @param {string} value */
function key(value) {const resolved=path.resolve(value);return process.platform==='win32'?resolved.toLowerCase():resolved;}
/** @param {string} data @param {boolean} [removeData] */
function preserveProjects(data,removeData=false) {
  const installRoot=path.dirname(path.resolve(data)),entries=projects(data),folders=new Set();
  const retained=entries.filter(entry=>(!removeData || !entry.owned) && fs.existsSync(entry.root));
  // Also protect a selected checkout whose registration was interrupted.
  const preferences=path.join(data,'bootstrap.json');
  if(fs.existsSync(preferences)) {
    const selected=JSON.parse(fs.readFileSync(preferences,'utf8')).root;
    if(typeof selected==='string' && fs.existsSync(selected) && !entries.some(entry=>key(entry.root)===key(selected)))retained.push({root:selected,owned:false,id:'retained-selection'});
  }
  for(const entry of retained)for(const root of [path.resolve(entry.root),fs.realpathSync(entry.root)]) {
    const relative=path.relative(installRoot,root);
    if(!relative || relative==='..' || key(installRoot).startsWith(key(root)+path.sep))throw new Error('A project contains the app installation directory. Move the app outside the project before upgrading or uninstalling.');
    if(relative.startsWith('..'+path.sep) || path.isAbsolute(relative))continue;
    const folder=relative.split(path.sep)[0];
    if(['resources','locales'].includes(folder.toLowerCase()))throw new Error('A project overlaps application files. Move it outside resources/locales before upgrading or uninstalling.');
    folders.add(folder);
  }
  fs.mkdirSync(data,{recursive:true});
  // Write the retention marker first: interrupted preparation must fail safe.
  const marker=path.join(data,'preserved-projects.json');
  if(!removeData || retained.length)fs.writeFileSync(marker,JSON.stringify({retained,folders:[...folders],keepData:!removeData}));
  else fs.rmSync(marker,{force:true});
  for(const folder of folders)fs.writeFileSync(path.join(data,'preserve-'+folder+'.flag'),'');
  return retained;
}
/** Runs outside Electron only after the app and its managers have exited.
 * @param {string} data */
async function purgeData(data) {
  const root=path.resolve(data);
  if(root===path.parse(root).root)throw new Error('Invalid cleanup target');
  const ownership=JSON.parse(fs.readFileSync(path.join(root,'app-owner.json'),'utf8'));
  if(ownership.app!=='cn.anelf.launcher' || key(ownership.path)!==key(root))throw new Error('Data ownership mismatch');
  if(projects(root).some(entry=>entry.owned && fs.existsSync(entry.root)))throw new Error('Project cleanup has not completed');
  const retained=preserveProjects(root,true);
  const preferenceFile=path.join(root,'bootstrap.json');
  const preferences=fs.existsSync(preferenceFile)?JSON.parse(fs.readFileSync(preferenceFile,'utf8')):null;
  // Register an interrupted selection too, so the recursive cleaner protects it.
  fs.writeFileSync(path.join(root,'projects.json'),JSON.stringify(retained));
  await removeAppData(root);
  if(retained.length) {
    fs.mkdirSync(root,{recursive:true});
    fs.writeFileSync(path.join(root,'projects.json'),JSON.stringify(retained));
    fs.writeFileSync(path.join(root,'app-owner.json'),JSON.stringify(ownership));
    if(preferences && typeof preferences.root==='string' && retained.some(entry=>key(entry.root)===key(preferences.root)))fs.writeFileSync(preferenceFile,JSON.stringify(preferences));
    preserveProjects(root,true);
  }
}
if(require.main===module) {
  const [mode,data]=process.argv.slice(2);
  Promise.resolve().then(async()=>{
    if(!data || !['protect','purge'].includes(mode))throw new Error('Invalid installer operation');
    if(mode==='purge')await purgeData(data);else preserveProjects(data);
  }).catch(error=>{console.error(String(error));process.exitCode=1;});
}
module.exports={preserveProjects,purgeData};
