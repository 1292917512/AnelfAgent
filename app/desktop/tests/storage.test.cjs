// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {inspectStorage,requireStorage}=require('../storage.cjs');
const {project_free_bytes:required}=require('../../install-requirements.json');

test('first-run checks the selected volume and rejects insufficient or unreadable space', context => {
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-space-'));
  const target=path.join(directory,'new-project');
  let free=required - 1;
  context.mock.method(fs,'statfsSync',(/** @type {string} */ actual)=>{
    assert.equal(actual,directory);
    if(free < 0)throw new Error('unreadable');
    return {bsize:1,bavail:free};
  });
  try {
    assert.throws(()=>requireStorage([inspectStorage(target,required)]),/Insufficient disk space/);
    free=required;assert.doesNotThrow(()=>requireStorage([inspectStorage(target,required)]));
    free=-1;assert.equal(inspectStorage(target,required).free,null);
    assert.throws(()=>requireStorage([inspectStorage(target,required)]),/unreadable/);
    assert.equal(fs.existsSync(target),false);
  }finally{fs.rmSync(directory,{recursive:true,force:true});}
});
