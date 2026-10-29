// @ts-check
/** Existing checkouts are selected explicitly; relocation always asks before stopping services. */
const fs=require('node:fs');
const path=require('node:path');
const {hasRunningServices,drain}=require('./maintenance.cjs');
const {runTask}=require('./backend.cjs');

/** @param {string} data @returns {{source:string,target:string}|null} */
function pendingMigration(data) {
  const file=path.join(data,'migration.json');
  if(!fs.existsSync(file))return null;
  const record=JSON.parse(fs.readFileSync(file,'utf8'));
  return record.resumable && typeof record.source==='string' && typeof record.target==='string' && fs.existsSync(record.source)
    ? {source:record.source,target:record.target} : null;
}

/** @param {string} root */
function inspectProject(root) {
  for(const name of ['pyproject.toml','uv.lock','launch.py']) {
    if(!fs.existsSync(path.join(root,name)) || !fs.statSync(path.join(root,name)).isFile())throw new Error('请选择 AnelfAgent 项目根目录 / Select an AnelfAgent checkout.');
  }
  if(!fs.existsSync(path.join(root,'.git')))throw new Error('所选目录不是 Git 项目 / The folder is not a Git checkout.');
  return fs.realpathSync(root);
}

/** @typedef {{root:string,target:string,move:boolean}} Selection */
/** @param {Selection} selection @param {{active:(root:string)=>Promise<boolean>,confirm:()=>Promise<boolean>,stop:(root:string)=>Promise<void>,move:(root:string,target:string)=>Promise<void>}} actions */
async function importProject(selection,actions) {
  if (!selection.move) return {accepted:true,root:selection.root};
  if(await actions.active(selection.root) && !await actions.confirm())return {accepted:false,root:selection.root};
  await actions.stop(selection.root);
  await actions.move(selection.root,selection.target);
  return {accepted:true,root:selection.target};
}

/** @param {Selection} selection @param {import('./backend.cjs').Options} options @param {string} data @param {()=>Promise<boolean>} confirm */
async function transferProject(selection,options,data,confirm) {
  if(selection.move) {
    const original=fs.realpathSync(selection.root);
    let ancestor=path.resolve(selection.target);
    const suffix=[];
    while(!fs.existsSync(ancestor)){suffix.unshift(path.basename(ancestor));ancestor=path.dirname(ancestor);}
    const destination=path.join(fs.realpathSync(ancestor),...suffix);
    const normalize=(/** @type {string} */ value)=>process.platform==='win32'?value.toLowerCase():value;
    const source=normalize(original),target=normalize(destination),owner=normalize(fs.realpathSync(data));
    if(source===target || target.startsWith(source+path.sep) || source.startsWith(target+path.sep) || owner===source || owner.startsWith(source+path.sep) || owner===target || owner.startsWith(target+path.sep))throw new Error('原目录与目标不能相同或互相包含 / Source and destination must be separate.');
    if(fs.existsSync(destination) && (!fs.statSync(destination).isDirectory() || fs.readdirSync(destination).length))throw new Error('目标目录已有文件，请选择空目录 / Choose an empty destination.');
  }
  const cancel=path.join(data,'migration.cancel');
  fs.rmSync(cancel,{force:true});
  const abort=()=>fs.writeFileSync(cancel,'cancel');
  options.signal?.addEventListener('abort',abort,{once:true});
  try {
    const result=await importProject(selection,{
      active:root=>hasRunningServices([root]),confirm,
      stop:async root=>{
        if(options.signal?.aborted)throw new Error('Cancelled');
        options.progress?.('正在安全停止旧项目服务 / Stopping the existing project safely…');
        await drain(root,true,()=>runTask({...options,root},['stop']));
      },
      move:async(root,target)=>{
        if(options.signal?.aborted)throw new Error('Cancelled');
        await runTask({...options,root},['migrate','--target',target,'--data',data,'--cancel-file',cancel]);
      },
    });
    if(result.accepted && selection.move)result.root=JSON.parse(fs.readFileSync(path.join(data,'bootstrap.json'),'utf8')).root;
    return result;
  } finally {options.signal?.removeEventListener('abort',abort);fs.rmSync(cancel,{force:true});}
}
module.exports={inspectProject,importProject,transferProject,pendingMigration};
