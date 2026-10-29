// @ts-check
/** Installation-owned paths; user checkouts are registered without ownership. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

/** @typedef {{root:string, realRoot?:string, owned:boolean, id:string}} Project */
/** @param {string} value */
function comparable(value) { const resolved=path.resolve(value);return process.platform==='win32' ? resolved.toLowerCase() : resolved; }
/** @param {string} data @returns {Project[]} */
function projects(data) {
  try {
    const value = JSON.parse(fs.readFileSync(path.join(data,'projects.json'),'utf8'));
    if (!Array.isArray(value) || value.some(item => typeof item.root !== 'string' || typeof item.owned !== 'boolean' || typeof item.id !== 'string' || item.realRoot!==undefined && typeof item.realRoot!=='string')) throw new Error('Invalid project ownership record');
    return value;
  } catch (error) { if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') return []; throw error; }
}
/** @param {string} data @param {string} root */
function register(data, root) {
  root = path.resolve(root);
  const entries = projects(data), existing = entries.find(item => item.root === root);
  if (existing) return existing;
  const owned = !fs.existsSync(root) || fs.readdirSync(root).length === 0;
  fs.mkdirSync(root,{recursive:true}); fs.mkdirSync(data,{recursive:true});
  const entry = {root,realRoot:fs.realpathSync(root),owned,id:crypto.randomUUID()};
  if (owned) {
    const state = path.join(root,'logs/manager'); fs.mkdirSync(state,{recursive:true});
    fs.writeFileSync(path.join(state,'ownership.json'),JSON.stringify({id:entry.id,owner:path.resolve(data)}));
  }
  entries.push(entry); fs.writeFileSync(path.join(data,'projects.json'),JSON.stringify(entries,null,2));
  return entry;
}
/** @param {string} data @param {Project} entry */
function verifyOwnership(data, entry) {
  const root = path.resolve(entry.root), owner = path.resolve(data);
  if (!entry.owned || root === path.parse(root).root || root === owner || owner.startsWith(root + path.sep)) throw new Error('Refusing to delete a non-owned project');
  if (!fs.existsSync(root)) return;
  if (projects(data).some(item=>!item.owned && fs.existsSync(item.root) && comparable(fs.realpathSync(item.root)).startsWith(comparable(fs.realpathSync(root))+path.sep))) throw new Error('An imported project is inside this directory; preserve it before cleanup');
  const realRoot=comparable(fs.realpathSync(root)),realOwner=comparable(fs.realpathSync(owner));
  if (realRoot===realOwner || realOwner.startsWith(realRoot+path.sep)) throw new Error('Refusing to delete the application data directory');
  if (fs.lstatSync(root).isSymbolicLink() || realRoot !== comparable(entry.realRoot || root)) throw new Error('Project path has changed');
  const marker = JSON.parse(fs.readFileSync(path.join(root,'logs/manager/ownership.json'),'utf8'));
  if (marker.id !== entry.id || path.resolve(marker.owner) !== owner) throw new Error('Project ownership could not be verified');
}
/** @param {string} data @param {Project} entry */
async function removeOwned(data, entry) {
  verifyOwnership(data,entry);
  await fs.promises.rm(entry.root,{recursive:true,force:true,maxRetries:4,retryDelay:300});
}
/** @param {string} data */
function retainedProjects(data) {
  const root=comparable(fs.realpathSync(data));
  return projects(data).filter(entry=>!entry.owned && fs.existsSync(entry.root) && comparable(fs.realpathSync(entry.root)).startsWith(root+path.sep));
}
/** Remove app files while retaining imported projects and their parent directories.
 * @param {string} data */
async function removeAppData(data) {
  const retained=retainedProjects(data).map(entry=>comparable(fs.realpathSync(entry.root)));
  /** @param {string} folder */
  async function clean(folder) {
    if(retained.includes(comparable(fs.realpathSync(folder))))return;
    for(const item of await fs.promises.readdir(folder,{withFileTypes:true})) {
      const full=path.join(folder,item.name),real=comparable(item.isSymbolicLink()?full:fs.realpathSync(full));
      if(retained.some(root=>root===real || root.startsWith(real+path.sep))) {if(item.isDirectory())await clean(full);}
      else await fs.promises.rm(full,{recursive:true,force:true,maxRetries:4,retryDelay:300});
    }
  }
  if(!retained.length)await fs.promises.rm(data,{recursive:true,force:true,maxRetries:8,retryDelay:500});
  else await clean(data);
}
module.exports = {projects,register,verifyOwnership,removeOwned,retainedProjects,removeAppData};
