// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {register,projects}=require('../managed.cjs');
const {prepare}=require('../maintenance.cjs');
const {preserveProjects,purgeData}=require('../preservation.cjs');
/** @param {(root:string,data:string)=>Promise<void>|void} action */
async function fixture(action) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-preserve-')),data=path.join(root,'data');
  fs.mkdirSync(data);
  fs.writeFileSync(path.join(data,'app-owner.json'),JSON.stringify({app:'cn.anelf.launcher',path:data}));
  try {await action(root,data);}finally{fs.rmSync(root,{recursive:true,force:true});}
}

for(const mode of /** @type {const} */ (['upgrade','uninstall']))test(mode+' retains environments, settings and ownership for reinstall',()=>fixture(async(root,data)=>{
  const project=path.join(data,'project'),outside=path.join(root,'my-project');
  const owned=register(data,project);register(data,outside);
  fs.mkdirSync(path.join(project,'.venv'));fs.writeFileSync(path.join(project,'.venv/python'),'existing runtime');
  fs.writeFileSync(path.join(project,'user.db'),'user data');
  const preferences=JSON.stringify({root:project,configured:true,source:'gitee',pypiSource:'tuna'});
  fs.writeFileSync(path.join(data,'bootstrap.json'),preferences);
  const registry=fs.readFileSync(path.join(data,'projects.json'));
  await prepare(data,mode);
  assert.equal(fs.readFileSync(path.join(project,'user.db'),'utf8'),'user data');
  assert.equal(fs.readFileSync(path.join(project,'.venv/python'),'utf8'),'existing runtime');
  assert.equal(fs.readFileSync(path.join(data,'bootstrap.json'),'utf8'),preferences);
  assert.deepEqual(fs.readFileSync(path.join(data,'projects.json')),registry);
  assert.ok(fs.existsSync(path.join(data,'preserve-my-project.flag')));
  assert.deepEqual(register(data,project),owned); // Reinstall keeps the same owner, not a new clone.
  assert.equal(JSON.parse(fs.readFileSync(path.join(data,'preserved-projects.json'),'utf8')).keepData,true);
}));

test('explicit deletion removes owned projects and caches but retains imported project and reconnect settings',()=>fixture(async(root,data)=>{
  const owned=path.join(data,'project'),imported=path.join(data,'imports/old'),sibling=path.join(root,'old-project');
  register(data,owned);
  for(const project of [imported,sibling]) {
    fs.mkdirSync(project,{recursive:true});fs.writeFileSync(path.join(project,'user.db'),'keep');register(data,project);
  }
  fs.mkdirSync(path.join(data,'tools'));fs.writeFileSync(path.join(data,'tools/cache'),'delete');
  fs.writeFileSync(path.join(data,'bootstrap.json'),JSON.stringify({root:imported,configured:true}));
  await prepare(data,'purge');await purgeData(data);
  assert.equal(fs.existsSync(owned),false);assert.equal(fs.existsSync(path.join(data,'tools')),false);
  for(const project of [imported,sibling])assert.equal(fs.readFileSync(path.join(project,'user.db'),'utf8'),'keep');
  assert.equal(projects(data).length,2);assert.ok(projects(data).every(entry=>!entry.owned));
  assert.equal(JSON.parse(fs.readFileSync(path.join(data,'bootstrap.json'),'utf8')).root,imported);
  assert.ok(fs.existsSync(path.join(data,'preserve-old-project.flag')));
  assert.ok(fs.existsSync(path.join(data,'preserved-projects.json')));
}));

test('explicit deletion without imports removes the complete app data tree',()=>fixture(async(root,data)=>{
  const owned=path.join(root,'owned');register(data,owned);
  await prepare(data,'purge');await purgeData(data);
  assert.equal(fs.existsSync(owned),false);assert.equal(fs.existsSync(data),false);
}));

test('all ownership records must validate before any project can be deleted',()=>fixture(async(root,data)=>{
  const first=path.join(data,'first'),second=path.join(data,'second');
  register(data,first);register(data,second);
  fs.writeFileSync(path.join(second,'logs/manager/ownership.json'),'{}');
  await assert.rejects(prepare(data,'purge'),/ownership/);
  assert.ok(fs.existsSync(first));assert.ok(fs.existsSync(second));
  await assert.rejects(purgeData(data),/not completed/);
}));

test('pending migration blocks destructive cleanup but permits data-preserving uninstall',()=>fixture(async(root,data)=>{
  const project=path.join(data,'project');register(data,project);
  fs.writeFileSync(path.join(data,'migration.json'),JSON.stringify({resumable:true,source:'old',target:project}));
  await assert.rejects(prepare(data,'purge'),/migration/);
  await prepare(data);
  assert.ok(fs.existsSync(project));assert.ok(fs.existsSync(path.join(data,'migration.json')));
}));

test('legacy installer helper protects an unregistered selected project without launching Electron',()=>fixture((root,data)=>{
  const project=path.join(root,'old-project');fs.mkdirSync(project);
  fs.writeFileSync(path.join(project,'user.db'),'keep');
  fs.writeFileSync(path.join(data,'bootstrap.json'),JSON.stringify({root:project,configured:true}));
  const result=spawnSync(process.execPath,[path.resolve(__dirname,'../preservation.cjs'),'protect',data],{windowsHide:true,encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);assert.ok(fs.existsSync(path.join(data,'preserve-old-project.flag')));
  assert.equal(fs.readFileSync(path.join(project,'user.db'),'utf8'),'keep');
}));

test('projects overlapping application binaries and corrupt registries fail closed',()=>fixture((root,data)=>{
  const project=path.join(root,'resources/project');register(data,project);
  assert.throws(()=>preserveProjects(data),/overlaps/);
  fs.writeFileSync(path.join(data,'projects.json'),'invalid');
  assert.throws(()=>preserveProjects(data));assert.ok(fs.existsSync(project));
}));

test('interrupted import selection is also protected during explicit cleanup',()=>fixture(async(root,data)=>{
  const project=path.join(data,'unregistered');fs.mkdirSync(project);fs.writeFileSync(path.join(project,'user.db'),'keep');
  fs.writeFileSync(path.join(data,'bootstrap.json'),JSON.stringify({root:project,configured:false}));
  await prepare(data,'purge');await purgeData(data);
  assert.equal(fs.readFileSync(path.join(project,'user.db'),'utf8'),'keep');
  assert.equal(projects(data)[0].root,project);
}));

for(const registered of [true,false])test('nested imported projects outside data prevent deletion, registered='+registered,()=>fixture(async(root,data)=>{
  const owned=path.join(root,'owned'),imported=path.join(owned,'user-checkout');
  register(data,owned);fs.mkdirSync(imported);fs.writeFileSync(path.join(imported,'user.db'),'keep');
  if(registered)register(data,imported);
  else fs.writeFileSync(path.join(data,'bootstrap.json'),JSON.stringify({root:imported,configured:false}));
  await assert.rejects(prepare(data,'purge'),/inside this directory/);
  assert.equal(fs.readFileSync(path.join(imported,'user.db'),'utf8'),'keep');
}));

test('interrupted cleanup keeps retention records intact so retry cannot delete imports',()=>fixture(async(root,data)=>{
  const imported=path.join(data,'old-project');fs.mkdirSync(imported);fs.writeFileSync(path.join(imported,'user.db'),'keep');register(data,imported);
  fs.writeFileSync(path.join(data,'bootstrap.json'),JSON.stringify({root:imported,configured:true}));
  const cache=path.join(data,'z-cache');fs.mkdirSync(cache);
  await prepare(data,'purge');
  const original=fs.promises.rm;
  fs.promises.rm=async(full,options)=>{if(String(full)===cache)throw new Error('Fixture file is busy');await original(full,options);};
  try {await assert.rejects(purgeData(data),/Fixture file is busy/);}finally{fs.promises.rm=original;}
  assert.equal(projects(data)[0].root,imported);
  assert.equal(JSON.parse(fs.readFileSync(path.join(data,'bootstrap.json'),'utf8')).root,imported);
  assert.ok(fs.existsSync(path.join(data,'preserved-projects.json')));
  await prepare(data,'purge');await purgeData(data);
  assert.equal(fs.readFileSync(path.join(imported,'user.db'),'utf8'),'keep');assert.equal(fs.existsSync(cache),false);
}));
