/** Full parent -> replacement uninstaller -> packaged maintenance regression.
 * Uses a temporary application/data tree and a unique test registry key only.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
if(process.platform!=='win32'){console.log('Native upgrade chain requires Windows.');process.exit(0);}
const desktop=fileURLToPath(new URL('../',import.meta.url));
const builder=createRequire(require.resolve('electron-builder'));
const templates=path.join(path.dirname(builder.resolve('app-builder-lib/package.json')),'templates/nsis/include');
assert.match(fs.readFileSync(path.join(templates,'installUtil.nsh'),'utf8'),/!insertmacro customUninstallPrevious/);
const cache=process.env.ELECTRON_BUILDER_CACHE || path.join(process.env.LOCALAPPDATA,'electron-builder/Cache');
const entries=fs.readdirSync(cache,{recursive:true,withFileTypes:true});
const compiler=entries.find(item=>item.name==='makensis.exe');
const plugin=entries.find(item=>item.name==='nsProcess.dll' && item.parentPath.includes('x86-unicode'));
assert.ok(compiler && plugin);
const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-upgrade-chain-'));
const install=path.join(fixture,'installed'),stage=path.join(fixture,'stage'),data=path.join(install,'data');
const key='Software\\AnelfAgentInstallerTests\\'+randomUUID();
const quoted=value=>value.replaceAll('$','$$').replaceAll('"','$\\"');
let holder;
const invoke=(command,args,options={})=>spawnSync(command,args,{windowsHide:true,encoding:'utf8',timeout:60000,...options});
function compile(name,source){
  const filename=path.join(stage,name+'.nsi');fs.writeFileSync(filename,source);
  const result=invoke(path.join(compiler.parentPath,compiler.name),['/V2',filename]);
  assert.equal(result.status,0,result.stdout+result.stderr);
}
const base=`Unicode true
!include LogicLib.nsh
!include FileFunc.nsh
!addincludedir "${quoted(templates)}"
!addplugindir /x86-unicode "${quoted(plugin.parentPath)}"
!define APP_EXECUTABLE_FILENAME "AnelfAgent.exe"
!define isUpdated '\"\" == \"\"'
Name "Isolated AnelfAgent upgrade chain"
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
`;
try {
  fs.mkdirSync(stage);fs.mkdirSync(path.join(data,'downloads'),{recursive:true});
  // Maintenance runs inside the directory being replaced, exactly as in an
  // installed APP. Earlier smoke tests used Electron outside that directory.
  fs.cpSync(path.resolve(desktop,'../build/package/win-unpacked'),install,{recursive:true});
  const root=path.join(data,'project');fs.mkdirSync(root);
  fs.writeFileSync(path.join(data,'projects.json'),JSON.stringify([{root,realRoot:root,owned:false,id:'fixture'}]));
  fs.writeFileSync(path.join(data,'bootstrap.json'),JSON.stringify({root,configured:true,projectChoice:'existing'}));
  const sentinels={'data/project/user.db':'user database','data/project/.venv/python':'retained environment','data/project/config.json':'retained settings'};
  for(const [name,value] of Object.entries(sentinels)){const file=path.join(install,name);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,value);}
  // Deliberately unusable legacy cleanup: the parent must use its own component.
  const legacy=path.join(install,'Uninstall AnelfAgent.exe');fs.writeFileSync(legacy,'do not execute historical cleanup');
  const component=path.join(stage,'replacement-uninstaller.exe');
  compile('component',base+`
!define BUILD_UNINSTALLER
!include "${quoted(path.join(desktop,'installer.nsh'))}"
OutFile "${quoted(path.join(stage,'create-component.exe'))}"
Section
  WriteUninstaller "${quoted(component)}"
SectionEnd
Function un.onInit
  StrCpy $INSTDIR "${quoted(install)}"
  SetOutPath $INSTDIR
  !insertmacro customUnInit
FunctionEnd
Section "Uninstall"
  !insertmacro customCheckAppRunning
  !insertmacro customUnInstall
  !insertmacro customRemoveFiles
SectionEnd
`);
  assert.equal(invoke(path.join(stage,'create-component.exe'),['/S']).status,0);
  const parent=path.join(data,'downloads/setup.exe');
  compile('parent',base+`
!define UNINSTALLER_OUT_FILE "${quoted(component)}"
!define INSTALL_REGISTRY_KEY "${key}"
!define UNINSTALL_REGISTRY_KEY "${key}"
!define isDeleteAppData '\"\" != \"\"'
!include "${quoted(path.join(desktop,'installer.nsh'))}"
!include allowOnlyOneInstallerInstance.nsh
Var appExe
Var installMode
!include installUtil.nsh
LangString uninstallFailed 1033 "Upgrade failed"
OutFile "${quoted(parent)}"
Section
  StrCpy $INSTDIR "${quoted(install)}"
  StrCpy $appExe "$INSTDIR\\AnelfAgent.exe"
  StrCpy $installMode "CurrentUser"
  WriteRegStr HKCU "${key}" InstallLocation "$INSTDIR"
  WriteRegStr HKCU "${key}" UninstallString '$\\"${quoted(legacy)}$\\"'
  !insertmacro CHECK_APP_RUNNING
  !insertmacro uninstallOldVersion HKEY_CURRENT_USER
  !insertmacro handleUninstallResult HKEY_CURRENT_USER
  DeleteRegKey HKCU "${key}"
  SetOutPath "$INSTDIR\\resources"
  File /oname=app.asar "${quoted(path.resolve(desktop,'../build/package/win-unpacked/resources/app.asar'))}"
  !insertmacro customInstall
  SetOutPath "$TEMP"
SectionEnd
`);
  const locked=path.join(install,'zzz-busy.dll'),ready=path.join(stage,'lock-ready'),release=path.join(stage,'release');
  fs.writeFileSync(locked,'temporarily locked application file');
  const literal=value=>"'"+value.replaceAll("'","''")+"'";
  const script=`$handle=[IO.File]::Open(${literal(locked)},[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);try{[IO.File]::WriteAllText(${literal(ready)},'ready');while(-not (Test-Path -LiteralPath ${literal(release)})){Start-Sleep -Milliseconds 50}}finally{$handle.Dispose()}`;
  holder=spawn('powershell.exe',['-NoProfile','-NonInteractive','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')],{windowsHide:true,stdio:'ignore'});
  for(let n=0;!fs.existsSync(ready) && n<100;n++)await new Promise(resolve=>setTimeout(resolve,100));
  assert.ok(fs.existsSync(ready),'The fixture must hold a real Windows file lock.');
  const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
  const failed=invoke(parent,['/S'],{env,cwd:stage});
  assert.equal(failed.status,2,'A persistent file lock must cancel cleanly, without a misleading app-running retry loop.');
  assert.match(fs.readFileSync(path.join(data,'installer-error.log'),'utf8'),/zzz-busy\.dll/);
  assert.equal(fs.existsSync(path.join(data,'installer-prepared.flag')),true);
  for(const [name,value] of Object.entries(sentinels))assert.equal(fs.readFileSync(path.join(install,name),'utf8'),value);
  fs.writeFileSync(release,'release');await new Promise(resolve=>holder.once('exit',resolve));holder=undefined;
  // Resume a partially completed replacement after the old executable is gone.
  fs.rmSync(path.join(install,'AnelfAgent.exe'),{force:true});
  const marker=path.join(data,'installer-prepared.flag'),prepared=fs.readFileSync(marker);
  fs.rmSync(marker);
  assert.equal(invoke(parent,['/S'],{env,cwd:stage}).status,2,'Missing preparation record must not skip maintenance.');
  fs.writeFileSync(marker,path.join(fixture,'another-installation'));
  assert.equal(invoke(parent,['/S'],{env,cwd:stage}).status,2,'A different installation record must not authorize resuming.');
  fs.writeFileSync(marker,prepared);
  const resumed=invoke(parent,['/S'],{env,cwd:stage});
  assert.equal(resumed.status,0,resumed.stdout+resumed.stderr);
  for(const [name,value] of Object.entries(sentinels))assert.equal(fs.readFileSync(path.join(install,name),'utf8'),value);
  assert.equal(fs.existsSync(locked),false);
  assert.equal(fs.existsSync(path.join(data,'installer-prepared.flag')),false);
  assert.deepEqual(fs.readFileSync(path.join(install,'resources/app.asar')),fs.readFileSync(path.resolve(desktop,'../build/package/win-unpacked/resources/app.asar')));
  console.log('Full native upgrade chain passed: replacement cleanup, real file-lock failure, clean cancellation, preserved data, resumed replacement.');
} finally {
  if(holder && holder.exitCode===null && holder.signalCode===null){const exited=new Promise(resolve=>holder.once('exit',resolve));holder.kill();await exited;}
  invoke('reg.exe',['delete','HKCU\\'+key,'/f','/reg:32']);
  assert.ok(path.resolve(fixture).startsWith(path.resolve(os.tmpdir())+path.sep));
  fs.rmSync(fixture,{recursive:true,force:true,maxRetries:10,retryDelay:200});
}
