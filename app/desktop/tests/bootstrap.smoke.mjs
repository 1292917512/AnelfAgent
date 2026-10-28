/** Offline first-run renderer checks; no native app is opened. */
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('../../../web/frontend/node_modules/playwright');
const downloads=require('../../downloads.json');
const directory=fileURLToPath(new URL('../',import.meta.url));
const server=http.createServer(async(req,res)=>{
  try{const name=path.basename(req.url || 'bootstrap.html');res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.svg')?'image/svg+xml':'text/html');res.end(await fs.readFile(path.join(directory,name)));}catch{res.writeHead(404);res.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:640,height:618},locale:'zh-CN'});
const errors=[];page.on('pageerror',error=>errors.push(error.message));
await page.addInitScript(catalog=>{
  let root='D:/AnelfAgent/project',free=4*1024**3;
  const storage=()=>[{path:root,free,required:5*1024**3,sufficient:free>=5*1024**3,error:''}];
  window.started=[];
  window.trayStates=[];
  window.anelfDesktop={
    trayState:async value=>{window.trayStates.push(value);},onNavigate:callback=>{window.navigateFromTray=callback;},
    bootstrapInfo:async()=>({root,defaultTarget:'E:/AnelfAgent/project',source:'auto',downloadSource:'china',pypiSource:'auto',pypiSources:catalog.pypi_sources,pypiDefaults:{china:catalog.china.pypi,global:catalog.global.pypi},timezone:'Asia/Shanghai',china:true,language:'zh',uv:{bundled:false,version:'uv 0.13.6',path:'C:/Users/test/.local/bin/uv.exe'},storage:storage()}),
    bootstrapStorage:async()=>storage(),
    projectRepository:async()=>({origin:'git@github.com:1292917512/AnelfAgent.git',source:'github',switchable:true}),
    chooseProject:async purpose=>{if(purpose==='existing')return 'D:/My old project';root='E:/AnelfAgent/project';free=20*1024**3;return root;},
    onBootstrapProgress:callback=>{window.progress=callback;},bootstrapStart:value=>{window.started.push(value);if(value.existing)return Promise.resolve({accepted:false});return new Promise((_resolve,reject)=>{window.finishBootstrap=()=>reject(new Error('Cancelled'));});},bootstrapCancel:async()=>window.finishBootstrap()
  };
},downloads);
try {
  await page.goto(`http://127.0.0.1:${server.address().port}/bootstrap.html`);
  await page.waitForFunction(()=>document.querySelector('#storage').textContent.includes('空间不足'));
  assert.equal(await page.locator('#project-question').isVisible(),true);
  assert.equal(await page.locator('#setup-options').isVisible(),false);
  await page.screenshot({animations:'disabled',path:path.join(directory,'../build/preview/project-question.png')});
  await page.locator('#existing-no').click();
  assert.equal(await page.locator('#continue').isDisabled(),true);
  assert.match(await page.locator('#uv').textContent(),/检测到 uv/);
  await page.locator('#choose').click();
  assert.equal(await page.locator('#root').textContent(),'E:/AnelfAgent/project');
  assert.equal(await page.locator('#continue').isDisabled(),false);
  assert.match(await page.locator('#storage').textContent(),/20.0 GiB/);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await page.evaluate(()=>[...document.querySelectorAll('main *')].filter(node=>['auto','scroll'].includes(getComputedStyle(node).overflowY) && node.scrollHeight>node.clientHeight+2).length),0);
  await page.setViewportSize({width:640,height:Math.ceil(await page.locator('main').evaluate(node=>node.getBoundingClientRect().height))});
  await page.screenshot({animations:'disabled',path:path.join(directory,'../build/preview/first-run.png')});
  assert.equal(await page.locator('#source-options').getAttribute('open'),null);
  assert.equal(await page.locator('#pypi').isVisible(),false);
  await page.evaluate(()=>window.navigateFromTray('settings'));
  assert.equal(await page.locator('#pypi').isVisible(),true);
  await page.locator('#pypi').selectOption('huawei');
  assert.equal(await page.locator('#index-url').textContent(),downloads.pypi_sources.huawei.url);
  await page.locator('#continue').click();
  await page.locator('#cancel').waitFor({state:'visible'});
  assert.equal(await page.evaluate(()=>window.trayStates.at(-1).settingsAvailable),false);
  assert.equal(await page.locator('#setup-options').isVisible(),false);
  assert.equal(await page.locator('#continue').isVisible(),false);
  assert.equal(await page.locator('#output').isVisible(),false);
  await page.locator('#bootstrap-meter').click();
  assert.equal(await page.locator('#output').isVisible(),true);
  await page.locator('#bootstrap-progress-toggle').focus();await page.keyboard.press('Space');
  assert.equal(await page.locator('#output').isVisible(),false);
  const cancelBounds=await page.locator('#cancel').boundingBox(),mainBounds=await page.locator('main').boundingBox();
  assert.ok(Math.abs(cancelBounds.x+cancelBounds.width/2-mainBounds.x-mainBounds.width/2)<2);
  await page.setViewportSize({width:640,height:Math.max(300,Math.ceil(await page.locator('main').evaluate(node=>node.getBoundingClientRect().height)))});
  await page.screenshot({animations:'disabled',path:path.join(directory,'../build/preview/bootstrap-preparing.png')});
  await page.locator('#cancel').click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent.includes('已取消'));
  assert.equal(await page.locator('#bootstrap-details').getAttribute('open'),null);
  assert.equal(await page.locator('#output').isVisible(),false);
  await page.locator('#bootstrap-details summary').click();assert.equal(await page.locator('#output').isVisible(),true);
  assert.equal(await page.locator('#continue').isDisabled(),false);
  assert.equal(await page.evaluate(()=>window.started[0].pypiSource),'huawei');
  await page.locator('#back-choice').click();await page.locator('#existing-yes').click();
  assert.equal(await page.locator('#existing-root').textContent(),'D:/My old project');
  assert.equal(await page.locator('#move-project').isChecked(),true);
  assert.equal(await page.locator('#source').inputValue(),'auto');
  assert.match(await page.locator('#source option[value="auto"]').textContent(),/github/);
  assert.match(await page.locator('#existing-origin').textContent(),/git@github.com/);
  await page.locator('#source-options').evaluate(node=>{node.open=false;});
  await page.setViewportSize({width:640,height:Math.ceil(await page.locator('main').evaluate(node=>node.getBoundingClientRect().height))});
  await page.screenshot({animations:'disabled',path:path.join(directory,'../build/preview/import-project.png')});
  await page.locator('#continue').click();
  await page.waitForFunction(()=>!document.querySelector('#existing-options').hidden);
  assert.equal(await page.locator('#progress').isVisible(),false);
  assert.equal(await page.evaluate(()=>window.started.at(-1).existing.move),true);
  await page.locator('#move-project').uncheck();
  assert.equal(await page.locator('#migration-target').isVisible(),false);
  assert.match(await page.locator('#existing-hint').textContent(),/文件保留在原目录/);
  await page.locator('#continue').click();
  await page.waitForFunction(()=>!document.querySelector('#existing-options').hidden);
  assert.equal(await page.evaluate(()=>window.started.at(-1).existing.move),false);
  assert.equal(await page.evaluate(()=>window.started.at(-1).existing.root),'D:/My old project');
  await page.evaluate(()=>window.progress(JSON.stringify({message:'跨盘迁移',received:512*1024**2,total:1024**3})+'\n'));
  assert.match(await page.locator('#status').textContent(),/50%.*512.0 \/ 1024.0 MiB/);
  await page.locator('#move-project').check();
  await page.setViewportSize({width:640,height:Math.ceil(await page.locator('main').evaluate(node=>node.getBoundingClientRect().height))});
  assert.deepEqual(errors,[]);
  console.log('First-run space checks, existing uv, folder changes and retry passed.');
} finally {await browser.close();server.close();}
