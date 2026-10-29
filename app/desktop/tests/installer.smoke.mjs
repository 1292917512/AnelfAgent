/** Native, silent NSIS upgrade fixture. Never reads or edits installed-app registry keys. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url);
if(process.platform!=='win32'){console.log('Native NSIS upgrade check requires Windows.');process.exit(0);}
const desktop=fileURLToPath(new URL('../',import.meta.url));
const {register}=require('../managed.cjs');
const cache=process.env.ELECTRON_BUILDER_CACHE || path.join(process.env.LOCALAPPDATA || '', 'electron-builder/Cache');
function compiler(folder,depth=0){
  if(depth>4 || !fs.existsSync(folder))return;
  const candidate=path.join(folder,'makensis.exe');if(fs.existsSync(candidate))return candidate;
  for(const item of fs.readdirSync(folder,{withFileTypes:true}))if(item.isDirectory() && (depth || item.name.startsWith('nsis'))){const found=compiler(path.join(folder,item.name),depth+1);if(found)return found;}
}
const makensis=process.env.MAKENSIS || compiler(cache);
assert.ok(makensis,'Build the Windows installer first, or set MAKENSIS to its compiler.');
const electron=require('electron');
const builder=createRequire(require.resolve('electron-builder'));
const templates=path.join(path.dirname(builder.resolve('app-builder-lib/package.json')),'templates/nsis/include');
const plugin=fs.readdirSync(cache,{recursive:true,withFileTypes:true}).find(item=>item.name==='nsProcess.dll' && item.parentPath.includes('x86-unicode'));
assert.ok(plugin,'Missing NSIS process plugin');
const currentAsar=path.resolve(desktop,'../build/package/win-unpacked/resources/app.asar');
assert.ok(fs.existsSync(currentAsar),'Build the desktop package before testing replacement.');
const fixture=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-native-upgrade-'));
const install=path.join(fixture,'installed'),data=path.join(install,'data');
const output=path.join(fixture,'mode.json'),probe=path.join(fixture,'maintenance.cjs');
// This helper has no BrowserWindow and exits immediately in either Electron mode.
fs.writeFileSync(probe,`const fs=require('node:fs');const e=require('node:module').createRequire(${JSON.stringify(path.join(desktop,'package.json'))})('electron');
const result={mode:typeof e==='string'?'node':'desktop',hasNodeMode:Object.hasOwn(process.env,'ELECTRON_RUN_AS_NODE'),policy:process.env.PSExecutionPolicyPreference};
if(result.mode==='node')fs.writeFileSync(${JSON.stringify(output)},JSON.stringify(result));
else{e.app.setPath('userData',${JSON.stringify(path.join(fixture,'profile'))});e.app.disableHardwareAcceleration();e.app.whenReady().then(()=>{fs.writeFileSync(${JSON.stringify(output)},JSON.stringify(result));e.app.exit(0);});}`);
const quoted=value=>value.replaceAll('$','$$').replaceAll('"','$\\"');
const executable=path.join(data,'downloads/AnelfAgent-setup.exe'),source=path.join(fixture,'upgrade-fixture.nsi');
fs.mkdirSync(path.dirname(executable),{recursive:true});
fs.writeFileSync(source,`
Unicode true
!include LogicLib.nsh
!include FileFunc.nsh
!define APP_EXECUTABLE_FILENAME "AnelfAgent.exe"
!define isUpdated '\"\" == \"\"'
!include "${quoted(path.join(desktop,'installer.nsh'))}"
!addplugindir /x86-unicode "${quoted(plugin.parentPath)}"
!addincludedir "${quoted(templates)}"
!include allowOnlyOneInstallerInstance.nsh
Name "AnelfAgent isolated upgrade test"
OutFile "${quoted(executable)}"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
Var anelfDeleteData
Section
  StrCpy $INSTDIR "${quoted(install)}"
  !insertmacro CHECK_APP_RUNNING
  ; Even a stale deletion preference must never purge project data during upgrade.
  StrCpy $anelfDeleteData 1
  !insertmacro anelfStagePreservation
  !insertmacro anelfRunPreservation "${quoted(electron)}" "${quoted(data)}" "protect"
  ExecWait '\"${quoted(electron)}\" \"${quoted(probe)}\" --prepare-upgrade' $R0
  IntCmp $R0 0 +2
  Abort
  !insertmacro customRemoveFiles
  SetOutPath "$INSTDIR\\resources"
  File /oname=app.asar "${quoted(currentAsar)}"
  SetOutPath "$TEMP"
  SetErrorLevel 0
SectionEnd
`);
try {
  const build=spawnSync(makensis,['/V2',source],{windowsHide:true,encoding:'utf8',timeout:60000});
  assert.equal(build.status,0,build.stdout+'\n'+build.stderr);
  const project=path.join(data,'project'),imported=path.join(install,'existing-project');
  register(data,project);fs.mkdirSync(imported,{recursive:true});register(data,imported);
  const sentinels={
    'data/project/config/app_config.json':'{"kept":"settings"}',
    'data/project/memory/user.db':'existing user data',
    'data/project/.venv/Scripts/python.exe':'existing environment fixture',
    'data/project/.git/config':'existing upstream fixture',
    'existing-project/user.db':'imported data',
    'data/bootstrap.json':JSON.stringify({root:project,configured:true}),
  };
  for(const [name,value] of Object.entries(sentinels)){const file=path.join(install,name);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,value);}
  const registry=fs.readFileSync(path.join(data,'projects.json'));
  const owner=fs.readFileSync(path.join(project,'logs/manager/ownership.json'));
  for(const inherited of [undefined,'0','1','']) {
    fs.mkdirSync(path.join(install,'resources'),{recursive:true});
    fs.writeFileSync(path.join(install,'resources/app.asar'),'old application');
    fs.writeFileSync(path.join(install,'obsolete.dll'),'obsolete application file');
    fs.rmSync(output,{force:true});
    const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
    if(inherited!==undefined)env.ELECTRON_RUN_AS_NODE=inherited;
    env.PSExecutionPolicyPreference='RemoteSigned';
    const run=spawnSync(executable,['/S'],{env,windowsHide:true,encoding:'utf8',timeout:30000});
    assert.equal(run.status,0,run.error?.message || run.stdout+'\n'+run.stderr);
    assert.deepEqual(JSON.parse(fs.readFileSync(output,'utf8')),{mode:'desktop',hasNodeMode:false,policy:'RemoteSigned'});
    for(const [name,value] of Object.entries(sentinels))assert.equal(fs.readFileSync(path.join(install,name),'utf8'),value,name);
    assert.deepEqual(fs.readFileSync(path.join(data,'projects.json')),registry);
    assert.deepEqual(fs.readFileSync(path.join(project,'logs/manager/ownership.json')),owner);
    assert.deepEqual(fs.readFileSync(path.join(install,'resources/app.asar')),fs.readFileSync(currentAsar));
    assert.equal(fs.existsSync(path.join(install,'obsolete.dll')),false);
    console.log('Native upgrade passed: inherited Node mode '+JSON.stringify(inherited??'<unset>')+'; data intact, old files removed, current app replaced.');
  }
  const blocker=path.join(install,'AnelfAgent.exe'),blockerSource=path.join(fixture,'running-app.nsi');
  fs.writeFileSync(blockerSource,`Unicode true\nName "Isolated desktop process"\nOutFile "${quoted(blocker)}"\nSilentInstall silent\nRequestExecutionLevel user\nSection\nSleep 45000\nSectionEnd\n`);
  const buildBlocker=spawnSync(makensis,['/V2',blockerSource],{windowsHide:true,encoding:'utf8',timeout:30000});
  assert.equal(buildBlocker.status,0,buildBlocker.stderr);
  const child=spawn(blocker,['/S'],{windowsHide:true,stdio:'ignore'});
  try {
    await new Promise(resolve=>setTimeout(resolve,300));
    const result=spawnSync(executable,['/S'],{windowsHide:true,encoding:'utf8',timeout:30000});
    assert.equal(result.status,2,'A real running desktop must block replacement without killing it.');
    assert.equal(child.exitCode,null);
    for(const [name,value] of Object.entries(sentinels))assert.equal(fs.readFileSync(path.join(install,name),'utf8'),value);
    console.log('Real desktop blocks upgrade safely; installer inside data is ignored; caller policy unchanged.');
  } finally {const exited=new Promise(resolve=>child.once('exit',resolve));child.kill();await exited;}
} finally {
  assert.ok(path.resolve(fixture).startsWith(path.resolve(os.tmpdir())+path.sep));
  fs.rmSync(fixture,{recursive:true,force:true,maxRetries:5,retryDelay:200});
}
