// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {createExitHandler}=require('../exit.cjs');
const {ensureBackend}=require('../backend.cjs');
const {drain,request,hasRunningServices,prepare}=require('../maintenance.cjs');
const {register}=require('../managed.cjs');

test('return interrupts the wait, never forces services, and permits another exit',async()=>{
  let quits=0,cancelled=0,stops=0;
  const exit=createExitHandler({confirm:async()=>({response:0,checkboxChecked:true}),
    stop:async signal=>{stops++;await new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}));},
    quit:()=>{quits++;},cancelled:()=>{cancelled++;},failed:error=>assert.fail(String(error))});
  const first=exit();
  await new Promise(resolve=>setImmediate(resolve));
  await exit.cancel();await first;
  assert.equal(quits,0);assert.equal(cancelled,1);assert.equal(stops,1);
  const second=exit();await new Promise(resolve=>setImmediate(resolve));await exit.cancel();await second;
  assert.equal(quits,0);assert.equal(cancelled,2);assert.equal(stops,2);
});

test('shutdown reports actual steps, can leave an accepted stop pending, and rejects stale legacy control',async()=>{
  const http=require('node:http');
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-stop-progress-'));
  const directory=path.join(root,'logs/manager');fs.mkdirSync(directory,{recursive:true});
  /** @type {string[]} */ const events=[];
  let status={root,pid:123,running:true,busy:false,controllable:true,phase:'running',last:{ok:true}};
  const server=http.createServer(async(req,res)=>{
    assert.equal(req.headers.authorization,'Bearer '+'x'.repeat(32));
    let body='';for await(const chunk of req)body+=chunk;
    if(req.method==='POST'){events.push(req.url || '');if(req.url==='/api/cancel')status={...status,busy:false};if(req.url==='/api/action'){assert.equal(JSON.parse(body).action,'stop');status={...status,busy:true,phase:'stop'};}}
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.url==='/api/status'?status:{ok:true}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(undefined)));
  const address=server.address();assert.ok(address && typeof address!=='string');
  fs.writeFileSync(path.join(directory,'connection.json'),JSON.stringify({url:'http://127.0.0.1:'+address.port,token:'x'.repeat(32),pid:process.pid}));
  try {
    const controller=new AbortController();
    /** @type {string[]} */ const stages=[];
    await assert.rejects(drain(root,true,undefined,{signal:controller.signal,progress:stage=>{stages.push(stage);if(stage==='wait-service')controller.abort();}}),/abort/i);
    assert.deepEqual(stages,['checking','stop-service','wait-service']);
    assert.deepEqual(events,['/api/action']);assert.equal(status.busy,true);
    status={...status,controllable:false};
    await assert.rejects(drain(root,true),/Service control is unavailable/);
    // A pre-upgrade manager may report pid=null while its launcher and project remain alive.
    Object.assign(status,{controllable:undefined,pid:null,running:false});
    fs.writeFileSync(path.join(root,'logs/anelf.pid'),String(process.pid));
    await assert.rejects(drain(root,true),/Service control is unavailable/);
    assert.deepEqual(events,['/api/action']);
    Object.assign(status,{controllable:true,pid:123,running:true,busy:true,phase:'install',operation:{action:'install',cancellable:true}});
    stages.length=0;events.length=0;
    const installing=new AbortController();
    await assert.rejects(drain(root,true,undefined,{signal:installing.signal,progress:stage=>{stages.push(stage);if(stage==='wait-service')installing.abort();}}),/abort/i);
    assert.deepEqual(stages,['checking','cancel-install','stop-service','wait-service']);
    assert.deepEqual(events,['/api/cancel','/api/action']);
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));fs.rmSync(root,{recursive:true,force:true});}
});

test('unchecked exit keeps services; cancellation keeps the app open',async()=>{
  let choice={response:0,checkboxChecked:false},stops=0,quits=0;
  const exit=createExitHandler({confirm:async()=>choice,stop:async()=>{stops++;},quit:()=>{quits++;},failed:error=>assert.fail(String(error))});
  await exit();assert.equal(stops,0);assert.equal(quits,1);
  choice={response:1,checkboxChecked:true};await exit();assert.equal(stops,0);assert.equal(quits,1);
});

for(const mode of /** @type {const} */ (['upgrade','uninstall']))test(mode+' safely detaches or stops the owned service while retaining its files',async()=>{
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-maintenance-'));
  const data=path.join(folder,'data'),root=path.join(data,'project'),events=path.join(folder,'events.json');
  register(data,root);fs.writeFileSync(path.join(root,'user.db'),'keep');
  const helper=spawn(process.execPath,['-e',`
    const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
    const [root,file]=process.argv.slice(1),events=[];let running=true;
    const server=http.createServer(async(req,res)=>{
      if(req.headers.authorization!=='Bearer '+'x'.repeat(32)){res.writeHead(401);res.end();return;}
      let body='';for await(const chunk of req)body+=chunk;
      if(req.url==='/api/action'){events.push(JSON.parse(body).action);running=false;}
      const exit=['/api/shutdown','/api/detach'].includes(req.url);
      if(exit){events.push(req.url);fs.writeFileSync(file,JSON.stringify({events,running}));}
      res.setHeader('Content-Type','application/json');
      res.end(JSON.stringify(req.url==='/api/status'?{root,running,busy:false,phase:running?'running':'stopped',last:{ok:true}}:{ok:true}));
      if(exit)server.close();
    });
    server.listen(0,'127.0.0.1',()=>fs.writeFileSync(path.join(root,'logs/manager/connection.json'),JSON.stringify({url:'http://127.0.0.1:'+server.address().port,token:'x'.repeat(32),pid:process.pid})));
  `,root,events],{windowsHide:true,stdio:'ignore'});
  try {
    const connectionFile=path.join(root,'logs/manager/connection.json');
    for(let i=0;i<100&&!fs.existsSync(connectionFile);i++)await new Promise(resolve=>setTimeout(resolve,50));
    assert.ok(fs.existsSync(connectionFile));
    await prepare(data,mode);
    assert.deepEqual(JSON.parse(fs.readFileSync(events,'utf8')),mode==='upgrade'?{events:['/api/detach'],running:true}:{events:['stop','/api/shutdown'],running:false});
    assert.equal(fs.readFileSync(path.join(root,'user.db'),'utf8'),'keep');
  } finally {if(helper.exitCode===null)helper.kill();fs.rmSync(folder,{recursive:true,force:true});}
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
