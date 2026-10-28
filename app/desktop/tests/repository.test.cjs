// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const {inspectRepository}=require('../repository.cjs');
const repositories=require('../../repositories.json');

test('existing repository inspection recognizes SSH mirrors and preserves custom forks without exposing credentials',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'anelf-origin-'));
  const git=(/** @type {string[]} */ args)=>execFileSync('git',['-C',root,...args],{windowsHide:true,encoding:'utf8',stdio:['ignore','pipe','pipe']});
  try {
    git(['init','-q']);
    const original='git@github.com:1292917512/AnelfAgent.git';
    git(['remote','add','origin',original]);
    assert.deepEqual(inspectRepository(root,repositories),{origin:original,source:'github',switchable:true});
    git(['remote','set-url','origin','https://user:secret@gitee.com/eflyz/AnelfAgent.git?token=secret']);
    assert.deepEqual(inspectRepository(root,repositories),{origin:repositories.gitee,source:'gitee',switchable:true});
    const fork='https://github.com/my-team/custom-agent.git';
    git(['remote','set-url','origin',fork]);
    assert.deepEqual(inspectRepository(root,repositories),{origin:fork,source:'github',switchable:false});
    assert.equal(git(['remote','get-url','origin']).trim(),fork);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
