// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {importProject,pendingMigration}=require('../project-import.cjs');
const {drain,prepare}=require('../maintenance.cjs');
const {register}=require('../managed.cjs');

test('declining shutdown goes back without stopping or moving; in-place import never stops',async()=>{
  /** @type {string[]} */const calls=[];
  const actions={active:async()=>{calls.push('check');return true;},confirm:async()=>{calls.push('confirm');return false;},
    stop:async()=>{calls.push('stop');},move:async()=>{calls.push('move');}};
  const selection={root:'old',target:'new',move:true};
  assert.deepEqual(await importProject(selection,actions),{accepted:false,root:'old'});
  assert.deepEqual(calls,['check','confirm']);calls.length=0;
  assert.deepEqual(await importProject({...selection,move:false},actions),{accepted:true,root:'old'});
  assert.deepEqual(calls,[]);
});

test('confirmed migration waits for safe shutdown; failure never moves files',async()=>{
  /** @type {string[]} */const calls=[];
  const selection={root:'old',target:'new',move:true};
  const actions={active:async()=>true,confirm:async()=>true,
    stop:/** @returns {Promise<void>} */ async()=>{calls.push('stop');throw new Error('still running');},move:async()=>{calls.push('move');}};
  await assert.rejects(importProject(selection,actions),/still running/);assert.deepEqual(calls,['stop']);
  actions.stop=async()=>{calls.push('stopped');};
  assert.deepEqual(await importProject(selection,actions),{accepted:true,root:'new'});
  assert.deepEqual(calls,['stop','stopped','move']);
});

test('saved migration is offered for explicit resume without moving files on startup',()=>{
  const data=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-import-resume-'));
  try {
    const source=path.join(data,'source'),target=path.join(data,'destination');
    fs.mkdirSync(source);fs.writeFileSync(path.join(source,'keep.txt'),'user data');
    assert.equal(pendingMigration(data),null);
    fs.writeFileSync(path.join(data,'migration.json'),JSON.stringify({resumable:true,source,target}));
    assert.deepEqual(pendingMigration(data),{source,target});
    assert.equal(fs.readFileSync(path.join(source,'keep.txt'),'utf8'),'user data');
    assert.equal(fs.existsSync(target),false);
  } finally {fs.rmSync(data,{recursive:true,force:true});}
});

test('migration can use the existing runtime stop channel without a manager',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-import-control-'));
  try{
    fs.mkdirSync(path.join(root,'logs'));
    const record=path.join(root,'logs/anelf.pid');fs.writeFileSync(record,String(process.pid));
    let stopped=false;
    await drain(root,true,async()=>{stopped=true;fs.unlinkSync(record);});
    assert.equal(stopped,true);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('Windows uninstall marks imported folders outside data for preservation',{skip:process.platform!=='win32'},async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-import-installer-'));
  try{
    const data=path.join(root,'data'),project=path.join(root,'my-project');
    fs.mkdirSync(project);fs.writeFileSync(path.join(project,'user.db'),'keep');
    register(data,project);await prepare(data,true);
    assert.equal(fs.readFileSync(path.join(project,'user.db'),'utf8'),'keep');
    assert.equal(fs.existsSync(path.join(data,'preserve-my-project.flag')),true);
    assert.equal(fs.existsSync(path.join(data,'preserved-projects.json')),true);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
