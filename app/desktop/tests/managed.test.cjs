// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {register,removeOwned,verifyOwnership}=require('../managed.cjs');

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
});
