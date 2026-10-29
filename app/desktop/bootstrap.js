import {describeError} from './errors.mjs';
/** @type {Record<string,Record<string,string>>} */
const words = {
  zh:{exitStopping:'正在安全退出，请稍候…',step1:'1 · 位置与来源',step2:'2 · 准备环境',step3:'3 · 完成安装',title:'开始之前，选择适合你的来源。',hint:'先确认下载位置与网络，无需预装开发工具。',path:'项目工程路径',choose:'更换',source:'项目源码',downloads:'环境下载源',auto:'按时区自动推荐',china:'国内镜像',global:'国际官方源',continue:'下一步 · 准备环境',cancel:'取消',footer:'关闭窗口收至托盘，安装会继续。',reuse:'已有兼容环境会复用，缺失项才会下载。国内：npmmirror / 清华 / 中科大；国际：各工具官方分发。',preparing:'正在准备管理环境，请稍候…',cancelled:'已取消。可修改来源后重试。',uv:'检测到 uv',bundled:'使用 APP 内置 uv',timezone:'时区',recommended:'推荐',failed:'准备失败，请查看日志并更换来源重试。'},
  en:{exitStopping:'Stopping safely…',step1:'1 · Location & source',step2:'2 · Environment',step3:'3 · Ready',title:'Choose what works for your network.',hint:'Confirm your project folder and download source. No development tools required.',path:'PROJECT DIRECTORY',choose:'Change',source:'Project repository',downloads:'Environment downloads',auto:'Recommend by timezone',china:'China mirrors',global:'International sources',continue:'Next · Prepare environment',cancel:'Cancel',footer:'Closing the window keeps setup running in the tray.',reuse:'Compatible tools are reused; only missing tools are downloaded. China: npmmirror / Tsinghua / USTC; international: official distributions.',preparing:'Preparing the management environment…',cancelled:'Cancelled. Choose a source and retry.',uv:'Existing uv',bundled:'Bundled uv',timezone:'Timezone',recommended:'Recommended',failed:'Setup failed. Check the log or select another source.'}
};
Object.assign(words.zh,{exitReturn:'返回界面',exitOnly:'仅退出应用',exitHint:'仅退出应用不会强制结束服务。已发送的关停请求会继续执行。'});
Object.assign(words.en,{exitReturn:'Return to app',exitOnly:'Quit app only',exitHint:'Quitting does not force services to stop. Shutdown requests already sent will continue.'});
Object.assign(words.zh,{connecting:'正在连接项目…',connectionFailed:'未能连接项目',reconnect:'重新连接',connectingFooter:'关闭窗口收至托盘。'});
Object.assign(words.en,{connecting:'Connecting to your project…',connectionFailed:'Could not connect to the project',reconnect:'Reconnect',connectingFooter:'Closing the window hides the app in the tray.'});
/** @param {string} id */
const $ = id => {const node=document.getElementById(id);if(!node)throw new Error(id);return node;};
/** @param {string} id */
const control = id => {const node=$(id);if(!(node instanceof HTMLButtonElement || node instanceof HTMLSelectElement || node instanceof HTMLInputElement))throw new Error(id);return node;};
/** @param {unknown} error */
const errorText = error => (error instanceof Error ? error.message : String(error)).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/,'');
const desktop=window.anelfDesktop;
if(!desktop)throw new Error('Desktop bridge unavailable');
let locale = navigator.language.startsWith('zh')?words.zh:words.en, busy = false;
const connecting=new URLSearchParams(location.search).get('mode')==='resume';
let connectionError='';
let stoppingForExit = false;
let exitError='';
let enoughSpace = false;
/** @type {'question'|'new'|'existing'} */ let projectChoice='question';
let existingRoot='',targetRoot='';
let progressBuffer='';
/** @type {import('../ui/lib/types').RepositoryInfo | null} */ let existingRepository=null;
const moving=()=>{const node=$('move-project');return node instanceof HTMLInputElement && node.checked;};
const canContinue=()=>projectChoice==='existing' ? !!existingRoot : projectChoice==='new' && enoughSpace;
/** @type {import('../ui/lib/types').BootstrapInfo | null} */ let catalog=null;
Object.assign(words.zh,{pypi:'uv / Python 包镜像',followSource:'跟随环境来源',details:'点击进度展开或收起详情'});
Object.assign(words.en,{pypi:'uv / Python package index',followSource:'Follow environment source',details:'Expand or collapse progress details'});
Object.assign(words.zh,{title:'准备安装 AnelfAgent',hint:'确认安装位置，其余环境自动准备。',continue:'开始安装',installOptions:'调整安装选项',preparingTitle:'正在准备环境',preparingHint:'自动复用现有工具，准备好后继续安装项目。',preparing:'正在准备基础环境…'});
Object.assign(words.en,{title:'Install AnelfAgent',hint:'Confirm the folder. We will prepare the environment.',continue:'Install',installOptions:'Installation options',preparingTitle:'Preparing your environment',preparingHint:'Reusing existing tools before continuing with the project.',preparing:'Preparing the environment…'});
Object.assign(words.zh,{failed:'未能完成，请展开查看具体原因。',keepOrigin:'沿用原仓库',originUnknown:'暂时无法读取 origin，默认保留原仓库',customOrigin:'自定义仓库：保留当前 origin，镜像地址需通过 Git 配置。'});
Object.assign(words.en,{failed:'Unable to complete. Expand to see the reason.',keepOrigin:'Keep current origin',originUnknown:'Origin unavailable; keep the existing repository',customOrigin:'Custom repository: keep origin; configure its mirror through Git.'});
Object.assign(words.zh,{resumeMove:'继续迁移',resumeHint:'已完成进度已保留。关闭占用原目录的程序后继续，仅重新处理变化的文件。'});
Object.assign(words.en,{resumeMove:'Resume move',resumeHint:'Progress is saved. Close programs using the source folder and continue; only changed files need processing again.'});
Object.assign(words.zh,{question:'已有 AnelfAgent 项目？',questionHint:'接入之前通过 Git 下载的项目，或开始全新安装。',existingYes:'是，选择已有项目',existingNo:'否，全新安装',existingTitle:'接入已有项目',existingIntro:'以所选项目为准，保留代码、配置与数据。',oldPath:'原项目目录',moveProject:'迁移到安装目录',targetPath:'迁移目标目录',moveHint:'运行中会先询问是否停服。完整校验后移动文件，再用 uv 准备新路径的环境。',keepHint:'文件保留在原目录，直接接入管理；不会移动项目或停止服务。',selectHint:'请选择已有项目文件夹',import:'接入项目',move:'迁移并继续',back:'返回',backHint:'已返回上一步，项目文件未移动。'});
Object.assign(words.en,{question:'Already have an AnelfAgent project?',questionHint:'Connect a checkout you already have, or start a fresh installation.',existingYes:'Yes, select project',existingNo:'No, fresh install',existingTitle:'Connect your project',existingIntro:'Keep the selected project’s code, configuration and data.',oldPath:'EXISTING PROJECT',moveProject:'Move to the installation folder',targetPath:'DESTINATION',moveHint:'Ask before stopping a running service. Verify and move files, then prepare the environment with uv.',keepHint:'Use the original folder. No files are moved and no service is stopped.',selectHint:'Choose an existing project folder',import:'Connect project',move:'Move & continue',back:'Back',backHint:'Returned to the previous step. Project files were not moved.'});
/** @type {Record<string,string>} */
const migrationEnglish={'正在检查项目文件':'Checking project files','正在检查目录联接与符号链接':'Checking junctions and symbolic links','同盘快速移动':'Moving on the same volume','跨盘迁移':'Moving across volumes','正在迁移':'Moving files','正在校验文件完整性':'Verifying file integrity','正在切换项目目录':'Switching project folders','快速移动完成，接下来检查运行环境':'Move complete. Checking the environment next.','项目迁移完成，接下来检查运行环境':'Move complete. Checking the environment next.'};
Object.assign(migrationEnglish,{'正在恢复迁移进度':'Resuming the saved move','正在复用已完成的迁移文件':'Reusing completed files'});
const resuming=()=>moving() && catalog?.migration?.source===existingRoot && catalog.migration.target===targetRoot;
function showStep() {
  const exiting=stoppingForExit || !!exitError;
  $('setup-flow').hidden=connecting || !catalog || exiting;
  $('setup-actions').hidden=connecting || !catalog || exiting;
  $('opening').hidden=(!connecting && !!catalog) || exiting;
  $('opening').classList.toggle('failed',!!connectionError);
  $('opening-status').textContent=locale[connectionError?'connectionFailed':'connecting'];
  $('connection-actions').hidden=(!connecting && !!catalog) || !connectionError || exiting;
  control('retry-connect').disabled=busy || stoppingForExit;
  $('bootstrap-footer').hidden=!catalog;
  $('bootstrap-footer').textContent=connecting?locale.connectingFooter:locale.footer;
  for(const id of ['existing-yes','existing-no','choose-existing','choose-target','move-project','back-choice'])control(id).disabled=busy || stoppingForExit;
  if(catalog)void desktop?.trayState?.({ready:false,webReady:false,settingsAvailable:!connecting && !busy && !stoppingForExit,language:locale===words.zh?'zh':'en'}).catch(()=>{});
  const meter=$('bootstrap-meter');
  if(meter instanceof HTMLProgressElement){if(busy || connecting)meter.removeAttribute('value');else meter.value=0;meter.hidden=connecting && !!connectionError;}
  $('project-question').hidden=busy || projectChoice!=='question';
  $('existing-options').hidden=busy || projectChoice!=='existing';
  $('setup-options').hidden=busy || projectChoice==='question';
  $('install-location').hidden=projectChoice==='existing';
  $('back-choice').hidden=busy || projectChoice==='question';
  control('continue').hidden=busy || projectChoice==='question';
  control('continue').disabled=busy || stoppingForExit || !canContinue();
  control('continue').textContent=projectChoice==='existing' ? moving()?resuming()?locale.resumeMove:locale.move:locale.import : locale.continue;
  $('intro-title').textContent=busy ? locale.preparingTitle : projectChoice==='question'?locale.question:projectChoice==='existing'?locale.existingTitle:locale.title;
  $('intro-hint').textContent=busy ? locale.preparingHint : projectChoice==='question'?locale.questionHint:projectChoice==='existing'?locale.existingIntro:locale.hint;
  $('migration-target').hidden=!moving();
  $('existing-root').textContent=existingRoot || locale.selectHint;
  $('target-root').textContent=targetRoot;
  $('existing-hint').textContent=resuming()?locale.resumeHint:moving()?locale.moveHint:locale.keepHint;
  showRepository();
  document.querySelectorAll('ol li').forEach((node,index)=>node.classList.toggle('current',index===(busy?1:0)));
  $('exit-actions').hidden=!exiting;$('exit-hint').hidden=!exiting;
  if(exiting){
    for(const id of ['project-question','existing-options','setup-options','continue','back-choice','cancel'])$(id).hidden=true;
    $('intro-title').textContent=locale.exitStopping;$('intro-hint').textContent='';
  }
  requestAnimationFrame(fitWindow);
}
function showRepository() {
  const source=control('source');if(!(source instanceof HTMLSelectElement))return;
  const existing=projectChoice==='existing',info=existingRepository;
  source.options[0].textContent=existing ? locale.keepOrigin+(info?.source?' · '+info.source:'') : locale.auto;
  for(const option of source.options)option.disabled=existing && !!info?.origin && !info.switchable && option.value!=='auto' && option.value!==info.source;
  const selected=source.value;
  const address=selected==='auto' || !info?.switchable ? info?.origin : catalog?.repositories?.[selected];
  $('existing-origin').textContent=address || locale.originUnknown;
  $('existing-origin').title=address || '';
}
control('source').onchange=showRepository;
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
  control('continue').disabled=busy || stoppingForExit || !canContinue();
}
const refreshStorage = async () => { renderStorage(await desktop.bootstrapStorage()); };
desktop.onBootstrapProgress(value => {
  const first=$('progress').hidden;$('progress').hidden=false;
  $('status').textContent=value.startsWith('Error:')?locale.failed:connecting?locale.connecting:locale.preparing;
  $('output').textContent=(($('output').textContent||'')+value).slice(-12000);
  if(connecting && value.startsWith('Error:')) {
    connectionError=value;
    $('status').textContent=describeError(value,locale===words.zh?'zh':'en').message;
    showStep();return;
  }
  const lines=(progressBuffer+value).split('\n');progressBuffer=(lines.pop()||'').slice(-12000);
  for(const line of lines)try{
    const message=JSON.parse(line),meter=$('bootstrap-meter');
    if(typeof message.message==='string'){
      const phase=message.message.split(' · ')[0];$('status').textContent=locale===words.en ? migrationEnglish[phase]||(/[\u3400-\u9fff]/.test(phase)?locale.preparing:phase) : phase;
    }
    if(meter instanceof HTMLProgressElement && message.total>0){
      meter.max=message.total;meter.value=message.received;
      if(message.total>1)$('status').textContent+=' · '+Math.floor(message.received/message.total*100)+'% · '+(message.received/1024**2).toFixed(1)+' / '+(message.total/1024**2).toFixed(1)+' MiB';
    }
  }catch{ /* Management setup also emits plain-text tool output. */ }
  if(first)requestAnimationFrame(fitWindow);
});
const selectExisting=async()=>{try{const root=await desktop.chooseProject('existing');if(root){existingRoot=root;existingRepository=await desktop.projectRepository(root);control('source').value='auto';}showStep();}catch(error){$('progress').hidden=false;$('status').textContent=describeError(error,locale===words.zh?'zh':'en').message;$('output').textContent=errorText(error);requestAnimationFrame(fitWindow);}};
$('existing-yes').onclick=async()=>{projectChoice='existing';const checkbox=$('move-project');if(checkbox instanceof HTMLInputElement)checkbox.checked=true;showStep();await selectExisting();};
$('existing-no').onclick=()=>{projectChoice='new';showStep();};
$('choose-existing').onclick=selectExisting;
$('choose-target').onclick=async()=>{try{const root=await desktop.chooseProject('destination');if(root)targetRoot=root;showStep();}catch(error){$('progress').hidden=false;$('status').textContent=describeError(error,locale===words.zh?'zh':'en').message;$('output').textContent=errorText(error);}};
$('move-project').onchange=showStep;
$('back-choice').onclick=()=>{projectChoice='question';$('progress').hidden=true;showStep();};
$('choose').onclick = async () => { try { $('root').textContent = await desktop.chooseProject() || ''; await refreshStorage();requestAnimationFrame(fitWindow); } catch(error) { $('progress').hidden=false;$('status').textContent=locale.failed;$('output').textContent=errorText(error); } };
$('continue').onclick = async () => {
  if (busy || stoppingForExit || !canContinue()) return; busy=true;
  for (const id of ['continue','choose','source','downloads','pypi']) control(id).disabled=true;
  $('cancel').hidden=false;control('cancel').disabled=false;$('progress').hidden=false;$('status').textContent=locale.preparing;$('output').textContent=locale.preparing;
  showStep();
  try {
    const result=await desktop.bootstrapStart({source:control('source').value,downloadSource:control('downloads').value,pypiSource:control('pypi').value,
      ...(projectChoice==='existing'?{existing:{root:existingRoot,target:targetRoot,move:moving()}}:{})});
    if(result?.accepted===false){$('progress').hidden=true;$('output').textContent='';}
  }
  catch(error) {
    if(!stoppingForExit){const diagnostic=describeError(error,locale===words.zh?'zh':'en');$('status').textContent=diagnostic.message;$('status').title=diagnostic.message;$('output').textContent+='\n'+diagnostic.detail;}
    if(!stoppingForExit)try{
      const info=await desktop.bootstrapInfo();catalog=info;
      if(projectChoice==='existing' && info.projectChoice==='existing' && info.root===targetRoot){
        existingRoot=info.root;const checkbox=$('move-project');if(checkbox instanceof HTMLInputElement)checkbox.checked=false;
      }
    }catch(error){$('output').textContent+='\n'+errorText(error);}
  }
  finally {busy=false;for(const id of ['choose','source','downloads','pypi'])control(id).disabled=stoppingForExit;$('cancel').hidden=true;showStep();}
};
$('cancel').onclick = async () => { control('cancel').disabled=true;try{await desktop.bootstrapCancel();}catch(error){$('status').textContent=describeError(error,locale===words.zh?'zh':'en').message;$('output').textContent+='\n'+errorText(error);control('cancel').disabled=false;} };
$('retry-connect').onclick=async()=>{
  busy=true;connectionError='';showStep();
  try{await desktop.reconnect();}catch(error){connectionError=errorText(error);$('status').textContent=describeError(error,locale===words.zh?'zh':'en').message;$('output').textContent=connectionError;}
  finally{busy=false;showStep();}
};
(async()=>{try{const info=await desktop.bootstrapInfo();catalog=info;locale=words[info.language]||words.en;document.documentElement.lang=info.language;
document.querySelectorAll('[data-text]').forEach(node=>{if(node instanceof HTMLElement)node.textContent=locale[node.dataset.text || ''];});
$('bootstrap-progress-toggle').title=locale.details;
if(connecting){showStep();return;}
$('root').textContent=info.root;control('source').value=info.source;control('downloads').value=info.downloadSource;
targetRoot=info.defaultTarget;
if(info.projectChoice){projectChoice=info.projectChoice;if(projectChoice==='existing'){existingRoot=info.root;existingRepository=await desktop.projectRepository(info.root);const checkbox=$('move-project');if(checkbox instanceof HTMLInputElement)checkbox.checked=false;}}
if(info.migration){projectChoice='existing';existingRoot=info.migration.source;targetRoot=info.migration.target;existingRepository=await desktop.projectRepository(existingRoot);const checkbox=$('move-project');if(checkbox instanceof HTMLInputElement)checkbox.checked=true;}
control('pypi').replaceChildren(new Option(locale.followSource,'auto'),...Object.entries(info.pypiSources).map(([key,value])=>new Option(value.label[info.language] || key,key)));
control('pypi').value=info.pypiSource;showIndex();
renderStorage(info.storage);
$('recommendation').textContent=locale.timezone+': '+info.timezone+' · '+locale.recommended+': '+(info.china?'Gitee / '+locale.china:'GitHub / '+locale.global);
$('uv').textContent=(info.uv.bundled?locale.bundled:locale.uv)+' · '+info.uv.version+'\n'+info.uv.path;
showStep();
}catch(error){connectionError=errorText(error);$('progress').hidden=false;$('status').textContent=describeError(error,locale===words.zh?'zh':'en').message;$('output').textContent=connectionError;showStep();}})();
setInterval(()=>{if(!connecting && !busy && !stoppingForExit)void refreshStorage().catch(()=>{enoughSpace=false;control('continue').disabled=!canContinue();$('storage').textContent=locale.diskUnknown;});},5000);
desktop.onExitState?.(value=>{
  stoppingForExit=value.stopping;
  exitError=value.error;
  for(const id of ['continue','choose','source','downloads','pypi','cancel'])control(id).disabled=value.stopping || busy;
  $('progress').hidden=false;
  $('status').textContent=value.stopping ? locale.exitStopping : describeError(value.error,locale===words.zh?'zh':'en').message;
  $('output').textContent=value.error;
  if(!value.stopping)control('continue').disabled=busy || !canContinue();
  showStep();
  if(!value.stopping && !value.error){$('status').textContent='';$('progress').hidden=true;}
});
for(const [id,choice] of /** @type {const} */ ([['exit-return','return'],['exit-only','quit']]))$(id).onclick=async()=>{
  try{await desktop.exitChoice(choice);}catch(error){$('output').textContent=errorText(error);}
};
desktop.onNavigate?.(view=>{
  if(stoppingForExit || exitError)return;
  if(view==='settings' && !connecting && !busy){const options=$('source-options');if(options instanceof HTMLDetailsElement)options.open=true;}
  window.scrollTo(0,0);
});
const main=document.querySelector('main');
let previousHeight=0;
function fitWindow() {
  if(!desktop || !main || !catalog || stoppingForExit)return;
  const height=Math.ceil(main.getBoundingClientRect().height);
  if(Math.abs(height-previousHeight)>=2){previousHeight=height;void desktop.resize?.({view:connecting?'manage':'setup',logs:false,height}).catch(()=>{});}
}
document.addEventListener('toggle',()=>{requestAnimationFrame(fitWindow);},true);
if(main)new ResizeObserver(()=>{if([...document.querySelectorAll('details[open]')].some(node=>node.getClientRects().length>0))requestAnimationFrame(fitWindow);}).observe(main);
