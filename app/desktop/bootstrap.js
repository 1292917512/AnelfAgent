export {};
/** @type {Record<string,Record<string,string>>} */
const words = {
  zh:{exitStopping:'正在安全退出，请稍候…',step1:'1 · 位置与来源',step2:'2 · 准备环境',step3:'3 · 完成安装',title:'开始之前，选择适合你的来源。',hint:'先确认下载位置与网络，无需预装开发工具。',path:'项目工程路径',choose:'更换',source:'项目源码',downloads:'环境下载源',auto:'按时区自动推荐',china:'国内镜像',global:'国际官方源',continue:'下一步 · 准备环境',cancel:'取消',footer:'关闭窗口收至托盘，安装会继续。',reuse:'已有兼容环境会复用，缺失项才会下载。国内：npmmirror / 清华 / 中科大；国际：各工具官方分发。',preparing:'正在准备管理环境，请稍候…',cancelled:'已取消。可修改来源后重试。',uv:'检测到 uv',bundled:'使用 APP 内置 uv',timezone:'时区',recommended:'推荐',failed:'准备失败，请查看日志并更换来源重试。'},
  en:{exitStopping:'Stopping safely…',step1:'1 · Location & source',step2:'2 · Environment',step3:'3 · Ready',title:'Choose what works for your network.',hint:'Confirm your project folder and download source. No development tools required.',path:'PROJECT DIRECTORY',choose:'Change',source:'Project repository',downloads:'Environment downloads',auto:'Recommend by timezone',china:'China mirrors',global:'International sources',continue:'Next · Prepare environment',cancel:'Cancel',footer:'Closing the window keeps setup running in the tray.',reuse:'Compatible tools are reused; only missing tools are downloaded. China: npmmirror / Tsinghua / USTC; international: official distributions.',preparing:'Preparing the management environment…',cancelled:'Cancelled. Choose a source and retry.',uv:'Existing uv',bundled:'Bundled uv',timezone:'Timezone',recommended:'Recommended',failed:'Setup failed. Check the log or select another source.'}
};
/** @param {string} id */
const $ = id => {const node=document.getElementById(id);if(!node)throw new Error(id);return node;};
/** @param {string} id */
const control = id => {const node=$(id);if(!(node instanceof HTMLButtonElement || node instanceof HTMLSelectElement))throw new Error(id);return node;};
/** @param {unknown} error */
const errorText = error => error instanceof Error ? error.message : String(error);
const desktop=window.anelfDesktop;
if(!desktop)throw new Error('Desktop bridge unavailable');
let locale = words.zh, busy = false;
let stoppingForExit = false;
let enoughSpace = false;
/** @type {import('../ui/lib/types').BootstrapInfo | null} */ let catalog=null;
Object.assign(words.zh,{pypi:'uv / Python 包镜像',followSource:'跟随环境来源',details:'点击进度展开或收起详情'});
Object.assign(words.en,{pypi:'uv / Python package index',followSource:'Follow environment source',details:'Expand or collapse progress details'});
Object.assign(words.zh,{title:'准备安装 AnelfAgent',hint:'确认安装位置，其余环境自动准备。',continue:'开始安装',installOptions:'调整安装选项',preparingTitle:'正在准备环境',preparingHint:'自动复用现有工具，准备好后继续安装项目。',preparing:'正在准备基础环境…'});
Object.assign(words.en,{title:'Install AnelfAgent',hint:'Confirm the folder. We will prepare the environment.',continue:'Install',installOptions:'Installation options',preparingTitle:'Preparing your environment',preparingHint:'Reusing existing tools before continuing with the project.',preparing:'Preparing the environment…'});
function showStep() {
  void desktop?.trayState?.({ready:false,webReady:false,settingsAvailable:!busy && !stoppingForExit,language:locale===words.zh?'zh':'en'}).catch(()=>{});
  const meter=$('bootstrap-meter');
  if(meter instanceof HTMLProgressElement){if(busy)meter.removeAttribute('value');else meter.value=0;}
  $('setup-options').hidden=busy;
  control('continue').hidden=busy;
  $('intro-title').textContent=busy ? locale.preparingTitle : locale.title;
  $('intro-hint').textContent=busy ? locale.preparingHint : locale.hint;
  document.querySelectorAll('ol li').forEach((node,index)=>node.classList.toggle('current',index===(busy?1:0)));
  requestAnimationFrame(fitWindow);
}
function showIndex() {
  if (!catalog) return;
  const source=control('pypi').value,region=control('downloads').value;
  $('index-url').textContent=source === 'auto' ? catalog.pypiDefaults[region === 'auto' ? catalog.china ? 'china' : 'global' : region] : catalog.pypiSources[source]?.url || '';
}
control('downloads').onchange=showIndex;control('pypi').onchange=showIndex;
Object.assign(words.zh,{diskAvailable:'磁盘可用',diskRequired:'至少需要',diskInsufficient:'空间不足，请清理或更换目录',diskUnknown:'无法读取磁盘空间',diskHint:'项目至少预留 5 GiB，建议 10 GiB；模型与用户数据另计。',management:'管理工具目录'});
Object.assign(words.en,{diskAvailable:'Disk available',diskRequired:'Required',diskInsufficient:'Not enough space; free space or change folder',diskUnknown:'Cannot read disk space',diskHint:'Reserve at least 5 GiB, preferably 10 GiB. Models and user data need additional space.',management:'Management tools'});
/** @param {import('../ui/lib/types').Storage[]} checks */
function renderStorage(checks) {
  enoughSpace = checks.every(check => check.sufficient);
  $('storage').textContent = checks.filter((check,index) => index === 0 || !check.sufficient).map((check,index) =>
    (index ? locale.management + ': ' : '') + locale.diskAvailable + ' ' + (check.free === null ? locale.diskUnknown : (check.free/1024**3).toFixed(1) + ' GiB') +
    ' · ' + locale.diskRequired + ' ' + check.required/1024**3 + ' GiB' + (check.sufficient ? '' : ' · ' + locale.diskInsufficient)).join('\n');
  $('storage').title=checks.map(check=>check.path + (check.error?'\n'+check.error:'')).join('\n');
  $('storage').classList.toggle('danger',!enoughSpace);
  control('continue').disabled=busy || stoppingForExit || !enoughSpace;
}
const refreshStorage = async () => { renderStorage(await desktop.bootstrapStorage()); };
desktop.onBootstrapProgress(value => { const first=$('progress').hidden;$('progress').hidden = false; $('status').textContent=value.startsWith('Error:') ? locale.failed : locale.preparing; $('output').textContent = value;if(first)requestAnimationFrame(fitWindow); });
$('choose').onclick = async () => { try { $('root').textContent = await desktop.chooseProject() || ''; await refreshStorage();requestAnimationFrame(fitWindow); } catch(error) { $('progress').hidden=false;$('status').textContent=locale.failed;$('output').textContent=errorText(error); } };
$('continue').onclick = async () => {
  if (busy || stoppingForExit || !enoughSpace) return; busy=true;
  for (const id of ['continue','choose','source','downloads','pypi']) control(id).disabled=true;
  $('cancel').hidden=false;control('cancel').disabled=false;$('progress').hidden=false;$('status').textContent=locale.preparing;$('output').textContent=locale.preparing;
  showStep();
  try { await desktop.bootstrapStart({source:control('source').value,downloadSource:control('downloads').value,pypiSource:control('pypi').value}); }
  catch(error) { if(!stoppingForExit){$('status').textContent=errorText(error).includes('Cancelled')?locale.cancelled:locale.failed;$('output').textContent=errorText(error);} }
  finally {busy=false;for(const id of ['choose','source','downloads','pypi'])control(id).disabled=stoppingForExit;control('continue').disabled=stoppingForExit || !enoughSpace;$('cancel').hidden=true;showStep();}
};
$('cancel').onclick = async () => { control('cancel').disabled=true;await desktop.bootstrapCancel(); };
(async()=>{try{const info=await desktop.bootstrapInfo();catalog=info;locale=words[info.language]||words.en;document.documentElement.lang=info.language;
document.querySelectorAll('[data-text]').forEach(node=>{if(node instanceof HTMLElement)node.textContent=locale[node.dataset.text || ''];});
$('bootstrap-progress-toggle').title=locale.details;
$('root').textContent=info.root;control('source').value=info.source;control('downloads').value=info.downloadSource;
control('pypi').replaceChildren(new Option(locale.followSource,'auto'),...Object.entries(info.pypiSources).map(([key,value])=>new Option(value.label[info.language] || key,key)));
control('pypi').value=info.pypiSource;showIndex();
renderStorage(info.storage);
$('recommendation').textContent=locale.timezone+': '+info.timezone+' · '+locale.recommended+': '+(info.china?'Gitee / '+locale.china:'GitHub / '+locale.global);
$('uv').textContent=(info.uv.bundled?locale.bundled:locale.uv)+' · '+info.uv.version+'\n'+info.uv.path;
showStep();
}catch(error){$('progress').hidden=false;$('status').textContent=errorText(error);}})();
setInterval(()=>{if(!busy && !stoppingForExit)void refreshStorage().catch(()=>{enoughSpace=false;control('continue').disabled=true;$('storage').textContent=locale.diskUnknown;});},5000);
desktop.onExitState?.(value=>{
  stoppingForExit=value.stopping;
  for(const id of ['continue','choose','source','downloads','pypi','cancel'])control(id).disabled=value.stopping || busy;
  $('progress').hidden=false;
  $('status').textContent=value.stopping ? locale.exitStopping : locale.failed;
  $('output').textContent=value.error;
  if(!value.stopping)control('continue').disabled=busy || !enoughSpace;
  showStep();
});
desktop.onNavigate?.(view=>{
  if(view==='settings' && !busy){const options=$('source-options');if(options instanceof HTMLDetailsElement)options.open=true;}
  window.scrollTo(0,0);
});
const main=document.querySelector('main');
let previousHeight=0;
function fitWindow() {
  if(!desktop || !main || !catalog || stoppingForExit)return;
  const height=Math.ceil(main.getBoundingClientRect().height);
  if(Math.abs(height-previousHeight)>=2){previousHeight=height;void desktop.resize?.({view:'setup',logs:false,height}).catch(()=>{});}
}
document.addEventListener('toggle',()=>{requestAnimationFrame(fitWindow);},true);
if(main)new ResizeObserver(()=>{if([...document.querySelectorAll('details[open]')].some(node=>node.getClientRects().length>0))requestAnimationFrame(fitWindow);}).observe(main);
