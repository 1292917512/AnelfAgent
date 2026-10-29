// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {describeError}=require('../errors.mjs');
test('setup and shutdown errors use the chosen language while retaining original diagnostics',()=>{
  for(const raw of ['Application is still shutting down; no process was killed.',
    '服务控制记录不可用 / Service control is unavailable',
    'Error invoking remote method \'anelf:bootstrap-start\': Error: [WinError 32] File is in use',
    'Failed to download onnxruntime: hash-checking is enabled',
    '项目目录被占用，已完成的迁移进度已保留。','Unknown failure: 0x123']) {
    const zh=describeError(raw,'zh'),en=describeError(raw,'en');
    assert.match(zh.message,/[\u3400-\u9fff]/);assert.doesNotMatch(en.message,/[\u3400-\u9fff]/);
    assert.ok(zh.detail && en.detail);assert.equal(zh.detail,en.detail);
  }
  assert.match(describeError('hash-checking failed','zh').message,/完整性校验/);
  assert.match(describeError('项目目录被占用','en').message,/in use/);
});
