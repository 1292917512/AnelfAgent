// @ts-check
/** Remove the app-owned data tree only after the parent desktop process exits. */
const fs = require('node:fs');
const path = require('node:path');
const {projects,removeAppData} = require('./managed.cjs');
/** @param {string} data @param {number} parent */
async function cleanup(data,parent) {
  const root = path.resolve(data);
  if (root === path.parse(root).root || !Number.isInteger(parent) || parent <= 0) throw new Error('Invalid cleanup target');
  const ownership = JSON.parse(fs.readFileSync(path.join(root,'app-owner.json'),'utf8'));
  if (ownership.app !== 'cn.anelf.launcher' || ownership.path !== root) throw new Error('Data ownership mismatch');
  if (projects(root).some(entry=>entry.owned && fs.existsSync(entry.root))) throw new Error('Project cleanup has not completed');
  const deadline = Date.now()+30000;
  while(Date.now()<deadline) {
    try {process.kill(parent,0);} catch {await removeAppData(root);return;}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw new Error('Desktop is still running');
}
if(require.main===module) cleanup(process.argv[2],Number(process.argv[3])).catch(()=>{process.exitCode=1;});
module.exports={cleanup};
