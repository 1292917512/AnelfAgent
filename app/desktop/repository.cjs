// @ts-check
/** Read a checkout's effective Git origin without installing tools or contacting the network. */
const path=require('node:path');
const fs=require('node:fs');
const {spawnSync}=require('node:child_process');
/** @param {string} value */
function identity(value) {
  const scp=/^[^/]+@([^/:]+):(.+)$/.exec(value);
  try {
    const url=new URL(scp?'ssh://'+scp[1]+'/'+scp[2]:value);
    return {host:url.hostname.toLowerCase(),repository:url.pathname.replace(/^\/+|\/+$/g,'').replace(/\.git$/,'').toLowerCase(),
      public:scp?value:url.protocol+'//'+url.host+url.pathname};
  }catch{return {host:'',repository:'',public:value};}
}
/** @param {string} root @param {Record<string,string>} repositories @returns {import('../ui/lib/types').RepositoryInfo} */
function inspectRepository(root,repositories) {
  const suffix=process.platform==='win32'?'.exe':'';
  const candidates=[path.join(root,'logs/manager/tools/git/cmd/git'+suffix),path.join(root,'logs/manager/tools/posix/bin/git'),'git'];
  for(const git of candidates) {
    if(git!=='git' && !fs.existsSync(git))continue;
    if(git==='git' && process.platform==='darwin' && spawnSync('/usr/bin/xcode-select',['-p'],{windowsHide:true,stdio:'ignore',timeout:3000}).status!==0)continue;
    const result=spawnSync(git,['--no-optional-locks','-C',root,'remote','get-url','origin'],{windowsHide:true,encoding:'utf8',timeout:5000});
    if(result.status!==0)continue;
    const value=identity(result.stdout.trim());
    const source=value.host==='github.com'?'github':value.host==='gitee.com'?'gitee':'custom';
    const switchable=Object.values(repositories).some(url=>{const known=identity(url);return known.host===value.host && known.repository===value.repository;});
    return {origin:value.public,source,switchable};
  }
  return {origin:'',source:'',switchable:false};
}
module.exports={inspectRepository};
