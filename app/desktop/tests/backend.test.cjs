// @ts-check
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { validConnection, commandFor, resolveProfile } = require('../backend.cjs');
const downloads=require('../../downloads.json');

test('backend discovery accepts only authenticated loopback connections', () => {
  const record = {url:'http://127.0.0.1:8090',token:'a'.repeat(32),pid:123};
  assert.equal(validConnection(record),true);
  for(const url of ['https://example.com','http://evil.example:8090','http://user@127.0.0.1:8090','http://127.0.0.1:8090/other']) assert.equal(validConnection({...record,url}),false);
  assert.equal(validConnection({...record,token:''}),false);
});

test('bootstrap does not require a user Python or project environment', () => {
  const options = {root:'/home/user/中文 project',backend:'/app/backend',uv:'/app/tools/uv',timezone:'Asia/Shanghai'};
  const command = commandFor(options);
  assert.equal(command.command,options.uv);
  assert.deepEqual(command.args.slice(0,6),['run','--no-project','--isolated','--python','3.12','--with-requirements']);
  assert.ok(command.args[6].endsWith('runtime-requirements.txt'));
  assert.deepEqual(command.args.slice(-8),['python','-m','app','--root',options.root,'serve','--port','0']);
  assert.equal(command.cwd,options.backend);
  assert.equal(command.env.ANELF_LAUNCHER_TIMEZONE,'Asia/Shanghai');
});

test('every uv mirror applies to management dependencies without changing other sources', () => {
  for (const [key,mirror] of Object.entries(downloads.pypi_sources)) {
    const profile=resolveProfile(downloads,'global',key,'Asia/Shanghai');
    assert.equal(profile.pypi,mirror.url);assert.equal(profile.node,downloads.global.node);
    const command=commandFor({root:'/project',backend:'/backend',uv:'/tools/uv',timezone:'UTC',profile,indexEnvironment:downloads.uv_index_environment});
    assert.equal(command.args[command.args.indexOf('--default-index')+1],mirror.url);
    assert.ok(command.args.includes('--no-config'));
    for(const name of downloads.uv_index_environment)assert.equal(Object.hasOwn(command.env,name),false);
  }
  assert.equal(resolveProfile(downloads,'auto','auto','Asia/Shanghai').pypi,downloads.china.pypi);
  assert.equal(resolveProfile(downloads,'auto','auto','UTC').pypi,downloads.global.pypi);
  assert.throws(()=>resolveProfile(downloads,'china','not-a-source','UTC'));
});

test('desktop version follows the project release version',()=>{
  assert.equal(require('../package.json').version,require('../version.cjs').projectVersion());
});

test('packaged backends reuse only matching builds and detach idle old managers without stopping projects',async context=>{
  const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
  const {spawn}=require('node:child_process');
  const {compatibleBackend}=require('../backend.cjs');
  for(const mode of ['matching','old','busy'])await context.test(mode,async()=>{
    const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-manager-version-'));
    fs.writeFileSync(path.join(root,'manifest.json'),JSON.stringify({sha256:'a'.repeat(64)}));
    const helper=spawn(process.execPath,['-e',`
      const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
      const [root,mode]=process.argv.slice(1),directory=path.join(root,'logs/manager');fs.mkdirSync(directory,{recursive:true});
      const server=http.createServer((req,res)=>{
        if(req.headers.authorization!=='Bearer '+'x'.repeat(32)){res.writeHead(401);res.end();return;}
        if(req.method==='POST')fs.writeFileSync(path.join(root,'events'),req.url);
        res.setHeader('Content-Type','application/json');res.end(JSON.stringify({root,running:true,busy:mode==='busy',backend_id:mode==='matching'?'a'.repeat(64):undefined}));
        if(req.url==='/api/detach')server.close();
      });
      server.listen(0,'127.0.0.1',()=>fs.writeFileSync(path.join(directory,'connection.json'),JSON.stringify({url:'http://127.0.0.1:'+server.address().port,token:'x'.repeat(32),pid:process.pid})));
    `,root,mode],{windowsHide:true,stdio:'ignore'});
    try {
      const file=path.join(root,'logs/manager/connection.json');
      for(let i=0;i<100&&!fs.existsSync(file);i++)await new Promise(resolve=>setTimeout(resolve,50));
      assert.ok(fs.existsSync(file));
      const options={root,backend:root,uv:'must-not-spawn',timezone:'UTC'};
      if(mode==='busy')await assert.rejects(compatibleBackend(options),/previous manager is busy/);
      else if(mode==='matching')assert.equal((await compatibleBackend(options))?.pid,helper.pid);
      else {assert.equal(await compatibleBackend(options),null);assert.equal(fs.readFileSync(path.join(root,'events'),'utf8'),'/api/detach');}
      if(mode!=='old')assert.equal(fs.existsSync(path.join(root,'events')),false);
    } finally {if(helper.exitCode===null)helper.kill();fs.rmSync(root,{recursive:true,force:true});}
  });
});


test('user uv and Python discovery remain read-only and preserve explicit interpreter paths', (context) => {
  const childProcess = require('node:child_process');
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(),'anelf-reuse-'));
  const uv = path.join(directory,'uv'), python = path.join(directory,'python');
  fs.writeFileSync(uv,''); fs.writeFileSync(python,'');
  /** @type {{args:string[],options:object}[]} */ const calls=[];
  context.mock.method(childProcess,'spawnSync', (/** @type {string} */ command, /** @type {string[]} */ args, /** @type {object} */ options) => {
    calls.push({args,options});
    return {status:0,stdout:args[0]==='--version'?'uv 0.13.6':args[0]==='python'?python:uv,stderr:''};
  });
  delete require.cache[require.resolve('../backend.cjs')];
  const backend = require('../backend.cjs');
  try {
    assert.equal(backend.findUv('missing').path,uv);
    assert.equal(backend.findPython(uv,directory),python);
    const find=calls.find(item=>item.args[0]==='python');
    assert.ok(find && find.args.includes('--no-python-downloads') && find.args.includes('--system'));
    const command=backend.commandFor({root:directory,backend:directory,uv,python,timezone:'UTC'});
    assert.equal(command.args[4],python);
    assert.equal(command.env.ANELF_USER_PYTHON_INSTALL_DIR,process.env.UV_PYTHON_INSTALL_DIR || '');
  } finally { fs.rmSync(directory,{recursive:true,force:true}); }
});
