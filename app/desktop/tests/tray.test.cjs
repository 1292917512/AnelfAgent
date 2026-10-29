// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {isTrayState,menuFor,openWebConsole}=require('../tray.cjs');

test('tray actions follow installation, service availability and language',()=>{
  /** @type {string[]} */ const actions=[];
  const handlers={navigate:(/** @type {string} */ view)=>actions.push(view),web:()=>actions.push('web'),exit:()=>actions.push('exit')};
  const setup=menuFor({ready:false,webReady:false,settingsAvailable:true,language:'zh'},handlers);
  assert.deepEqual(setup.map(item=>item.label).filter(Boolean),['打开安装界面','打开设置','退出']);
  const settings=setup.find(item=>item.label==='打开设置');
  assert.ok(settings?.click);Reflect.apply(settings.click,null,[]);assert.deepEqual(actions,['settings']);
  const installed=menuFor({ready:true,webReady:false,settingsAvailable:true,language:'zh'},handlers);
  assert.equal(installed.find(item=>item.label==='打开 Web 控制台')?.enabled,false);
  const running=menuFor({ready:true,webReady:true,settingsAvailable:true,language:'en'},handlers);
  assert.equal(running.find(item=>item.label==='Open Web console')?.enabled,true);
  for(const invalid of [null,{}, {ready:true,webReady:'yes',settingsAvailable:true,language:'zh'}])assert.equal(isTrayState(invalid),false);
});

test('web shortcuts recheck state and reject stale or nonlocal destinations',async()=>{
  /** @type {string[]} */ const opened=[];
  let fallback=0;
  const open=async(/** @type {string} */ url)=>{opened.push(url);};
  const ready={ready:true,running:true,busy:false,phase:'running',web_url:'http://127.0.0.1:8092/webui/'};
  for(const state of [null,{...ready,ready:false},{...ready,running:false},{...ready,busy:true},{...ready,phase:'restarting'},{...ready,web_ready:false},{...ready,web_url:'https://example.com'}]){
    assert.equal(await openWebConsole(async()=>state,open,()=>{fallback++;}),false);
  }
  assert.equal(await openWebConsole(async()=>{throw new Error('offline');},open,()=>{fallback++;}),false);
  assert.equal(fallback,8);assert.deepEqual(opened,[]);
  assert.equal(await openWebConsole(async()=>ready,open,()=>{fallback++;}),true);
  assert.deepEqual(opened,[ready.web_url]);
});
