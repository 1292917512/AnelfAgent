// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {createExitHandler}=require('../exit.cjs');
const {ensureBackend}=require('../backend.cjs');
const {drain,request,hasRunningServices}=require('../maintenance.cjs');

test('unchecked exit keeps services; cancellation keeps the app open',async()=>{
  let choice={response:0,checkboxChecked:false},stops=0,quits=0;
  const exit=createExitHandler({confirm:async()=>choice,stop:async()=>{stops++;},quit:()=>{quits++;},failed:error=>assert.fail(String(error))});
  await exit();assert.equal(stops,0);assert.equal(quits,1);
  choice={response:1,checkboxChecked:true};await exit();assert.equal(stops,0);assert.equal(quits,1);
});

test('checked exit waits for shutdown, coalesces repeated closes and allows retry after failure',async()=>{
  let release=()=>{},prompts=0,quits=0;
  const waiting=new Promise(/** @param {(value?:unknown)=>void} resolve */ resolve=>{release=resolve;});
  /** @type {unknown[]} */ const errors=[];
  let fail=true;
  const exit=createExitHandler({confirm:async()=>{prompts++;return {response:0,checkboxChecked:true};},
    stop:async()=>{await waiting;if(fail)throw new Error('still stopping');},quit:()=>{quits++;},failed:error=>errors.push(error)});
  const first=exit(),second=exit();assert.equal(first,second);
  await Promise.resolve();assert.equal(quits,0);assert.equal(prompts,1);
  release();await first;assert.equal(errors.length,1);assert.equal(quits,0);
  fail=false;await exit();assert.equal(quits,1);assert.equal(prompts,2);
});

test('background manager is reused on reopen and exits only after checked shutdown',async()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-exit-'));
  const helper=spawn(process.execPath,['-e',`
    const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
    const root=process.argv[1],events=[];let running=true;
    const state=path.join(root,'logs/manager');fs.mkdirSync(state,{recursive:true});
    const server=http.createServer(async(req,res)=>{
      if(req.headers.authorization!=='Bearer '+ 'x'.repeat(32)){res.writeHead(401);res.end();return;}
      let body='';for await(const chunk of req)body+=chunk;
      if(req.url==='/api/action'){const action=JSON.parse(body).action;events.push(action);running=action==='start';}
      if(req.url==='/api/shutdown'){events.push('shutdown');fs.writeFileSync(path.join(root,'events.json'),JSON.stringify(events));}
      res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/api/status'?{root,running,busy:false,phase:running?'running':'stopped',last:{ok:true}}:{ok:true}));
      if(req.url==='/api/shutdown')server.close();
    });
    server.listen(0,'127.0.0.1',()=>fs.writeFileSync(path.join(state,'connection.json'),JSON.stringify({url:'http://127.0.0.1:'+server.address().port,token:'x'.repeat(32),pid:process.pid})));
  `,root],{windowsHide:true,stdio:'ignore'});
  try {
    const file=path.join(root,'logs/manager/connection.json');
    for(let i=0;i<100&&!fs.existsSync(file);i++)await new Promise(resolve=>setTimeout(resolve,50));
    assert.ok(fs.existsSync(file));
    const options={root,backend:root,uv:'must-not-spawn',timezone:'UTC'};
    const first=await ensureBackend(options);
    let quits=0,prompts=0,stop=false;
    const exit=createExitHandler({active:()=>hasRunningServices([root]),confirm:async()=>{prompts++;return {response:0,checkboxChecked:stop};},stop:()=>drain(root,true),quit:()=>{quits++;},failed:error=>assert.fail(String(error))});
    await exit();assert.equal(quits,1);assert.equal(helper.exitCode,null);
    const reopened=await ensureBackend(options);assert.deepEqual(reopened,first);
    await request(reopened,'action',{action:'stop'});
    await exit();assert.equal(quits,2);assert.equal(prompts,1);assert.equal(helper.exitCode,null);
    await request(reopened,'action',{action:'start'});
    stop=true;await exit();assert.equal(quits,3);assert.equal(prompts,2);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(root,'events.json'),'utf8')),['stop','start','stop','shutdown']);
    assert.notEqual(helper.exitCode,null);
  }finally{if(helper.exitCode===null)helper.kill();fs.rmSync(root,{recursive:true,force:true});}
});
