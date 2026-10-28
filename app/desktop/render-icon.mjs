/** Rasterize the canonical SVG for installers and macOS template trays using the existing headless browser. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require('../../web/frontend/node_modules/playwright');
const directory=path.dirname(fileURLToPath(import.meta.url));
const browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage({viewport:{width:1024,height:1024},deviceScaleFactor:1});
  await page.setContent('<style>html,body{margin:0;background:transparent}svg{display:block;width:100vw;height:100vh}</style>'+await fs.readFile(path.join(directory,'icon.svg'),'utf8'));
  await page.screenshot({path:path.join(directory,'icon.png'),omitBackground:true});
  await page.evaluate(()=>{
    document.querySelector('#background')?.remove();
    document.querySelector('svg')?.setAttribute('viewBox','44 52 176 166');
    for(const element of document.querySelectorAll('#symbol *')) {
      if(element.hasAttribute('fill') && element.getAttribute('fill')!=='none')element.setAttribute('fill','#000');
      if(element.hasAttribute('stroke'))element.setAttribute('stroke','#000');
      element.removeAttribute('opacity');
    }
  });
  for(const [size,name] of [[16,'trayTemplate.png'],[32,'trayTemplate@2x.png']]) {
    await page.setViewportSize({width:size,height:size});
    await page.screenshot({path:path.join(directory,name),omitBackground:true});
  }
  console.log('Rendered application and template tray icons from icon.svg.');
} finally {await browser.close();}
