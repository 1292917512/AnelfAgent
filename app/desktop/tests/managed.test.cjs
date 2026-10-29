// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {register,removeOwned,verifyOwnership,removeAppData}=require('../managed.cjs');

test('uninstall removes owned clones but preserves existing projects and rejects changed ownership',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-owned-'));
  try{
    const data=path.join(root,'data'),existing=path.join(root,'existing'),owned=path.join(data,'project');
    fs.mkdirSync(existing);fs.writeFileSync(path.join(existing,'user.txt'),'preserve');
    const user=register(data,existing),installed=register(data,owned);
    assert.equal(user.owned,false);assert.throws(()=>verifyOwnership(data,user));
    fs.writeFileSync(path.join(owned,'project.txt'),'downloaded');
    assert.throws(()=>verifyOwnership(data,{...installed,id:'wrong'}));
    await removeOwned(data,installed);
    assert.equal(fs.existsSync(owned),false);assert.equal(fs.readFileSync(path.join(existing,'user.txt'),'utf8'),'preserve');
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('offline and stopped states cannot open a workspace; a busy installation can still cancel',async()=>{
  const {controls}=await import('../../ui/model.js');
  /** @type {import('../../ui/lib/types').State} */
  const state={root:'project',phase:'stopped',busy:false,ready:true,running:false,pid:null,external_pid:0,web_url:'http://127.0.0.1:8092',resolved_source:'github',storage:{path:'project',free:10e9,required:5e9,sufficient:true,error:''},settings:{source:'auto',download_source:'auto',pypi_source:'auto',timezone:'UTC',web_host:'127.0.0.1',web_port:8092,auto_start:false,stop_timeout:600},operation:{steps:[]},last:null,events:[]};
  assert.equal(controls(state,true).workspace,false);assert.equal(controls(state,true).start,true);
  assert.equal(controls({...state,running:true,phase:'running'},false).workspace,false);
  assert.equal(controls({...state,busy:true,operation:{cancellable:true,steps:[]}},true).cancel,true);
  assert.equal(controls({...state,busy:true,operation:{cancellable:true,cancelling:true,steps:[]}},true).cancel,false);
  const healthy={...state,running:true,phase:'running',web_ready:true,controllable:false};
  assert.equal(controls(healthy,true).workspace,true);
  assert.equal(controls(healthy,true).start,false);
  assert.equal(controls(healthy,true).stop,false);
  assert.equal(controls(healthy,true).restart,false);
  assert.equal(controls({...healthy,web_ready:false},true).workspace,false);
});

test('POSIX owned paths retain their original filesystem target through parent aliases',{skip:process.platform==='win32'},async()=>{
  const root=fs.realpathSync.native(fs.mkdtempSync(path.join(os.tmpdir(),'anelf-alias-')));
  try {
    const original=path.join(root,'original'),other=path.join(root,'other'),alias=path.join(root,'alias'),data=path.join(root,'data');
    fs.mkdirSync(original);fs.mkdirSync(other);
    fs.symlinkSync(original,alias,'dir');
    const first=register(data,path.join(alias,'first'));
    await removeOwned(data,first);
    assert.equal(fs.existsSync(path.join(original,'first')),false);
    const second=register(data,path.join(alias,'second'));
    fs.cpSync(path.join(original,'second'),path.join(other,'second'),{recursive:true});
    fs.unlinkSync(alias);fs.symlinkSync(other,alias,'dir');
    await assert.rejects(removeOwned(data,second),/path has changed/);
    assert.ok(fs.existsSync(path.join(other,'second')));
    assert.ok(fs.existsSync(path.join(original,'second')));
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('cleanup retains imported projects even inside the app data folder',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-import-cleanup-'));
  try {
    const data=path.join(root,'data'),project=path.join(data,'imports/project');
    fs.mkdirSync(project,{recursive:true});fs.writeFileSync(path.join(project,'user.db'),'keep');
    register(data,project);
    fs.mkdirSync(path.join(data,'tools'));fs.writeFileSync(path.join(data,'tools/cache'),'remove');
    await removeAppData(data);
    assert.equal(fs.readFileSync(path.join(project,'user.db'),'utf8'),'keep');
    assert.equal(fs.existsSync(path.join(data,'tools')),false);
  }finally{fs.rmSync(root,{recursive:true,force:true});}
});
