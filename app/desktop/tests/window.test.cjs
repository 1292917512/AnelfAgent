// @ts-check
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {closeWindow,showWindow}=require('../window.cjs');

test('closing keeps the window session in the tray; reopening restores a minimized window',()=>{
  let visible=true,minimized=true,prevented=0,exits=0,focused=0;
  const window={isDestroyed:()=>false,isMinimized:()=>minimized,restore:()=>{minimized=false;},show:()=>{visible=true;},focus:()=>{focused++;},hide:()=>{visible=false;}};
  const options={window,quitting:false,trayAvailable:true,exit:async()=>{exits++;}};
  closeWindow({preventDefault:()=>{prevented++;}},options);
  assert.equal(visible,false);assert.equal(exits,0);assert.equal(prevented,1);
  showWindow(window);
  assert.equal(visible,true);assert.equal(minimized,false);assert.equal(focused,1);
  options.quitting=true;
  closeWindow({preventDefault:()=>{prevented++;}},options);
  assert.equal(prevented,1);assert.equal(visible,true);
  options.quitting=false;options.trayAvailable=false;
  closeWindow({preventDefault:()=>{prevented++;}},options);
  assert.equal(exits,1);assert.equal(visible,true);
});
