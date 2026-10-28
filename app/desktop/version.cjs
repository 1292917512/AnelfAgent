// @ts-check
/** Project release metadata is the source for the desktop's displayed and packaged version. */
const fs=require('node:fs');
const path=require('node:path');
/** @param {string} [root] */
function projectVersion(root=path.resolve(__dirname,'../..')) {
  const source=fs.readFileSync(path.join(root,'pyproject.toml'),'utf8');
  const project=source.split(/^\[project\][ \t]*$/m)[1]?.split(/^\[/m)[0];
  const version=project?.match(/^[ \t]*version[ \t]*=[ \t]*["']([^"'\r\n]+)["'][ \t]*(?:#.*)?\r?$/m)?.[1];
  if (!version || !/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Project release version must use major.minor.patch');
  return version;
}
function syncVersion() {
  const version=projectVersion(),file=path.join(__dirname,'package.json');
  const source=fs.readFileSync(file,'utf8');
  if (JSON.parse(source).version!==version) fs.writeFileSync(file,source.replace(/("version"\s*:\s*")[^"]+(")/,(_match,before,after)=>before+version+after));
  return version;
}
module.exports={projectVersion,syncVersion};
if(require.main===module)console.log('AnelfAgent '+syncVersion());
