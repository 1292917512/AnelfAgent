import {el, button, input} from './dom.js';
import {t} from './i18n.js';

/** Web access uses the project's existing authentication configuration; passwords are never read back. */
export class WebAccessPanel {
  /** @type {import('./lib/types').WebAccess | null} */ value=null;
  /** @type {import('./lib/types').State | null} */ state=null;
  editable=false;
  busy=false;
  error='';
  removePending=false;
  /** @param {(path:string,body?:object)=>Promise<import('./lib/types').WebAccess>} api */
  constructor(api) {
    this.api=api;
    input('access-password').oninput=()=>{this.removePending=false;this.render();};
    button('password-clear').onclick=()=>{this.removePending=!this.removePending;input('access-password').value='';this.render();};
    el('lan-details').ontoggle=()=>{if(el('lan-details').hasAttribute('open'))void this.refresh();};
  }
  async refresh() {
    if (this.busy) return;
    this.busy=true;this.error='';this.render();
    try {this.value=await this.api('web-access');}
    catch {this.value=null;this.error=t('accessUnavailable');}
    finally {this.busy=false;this.render();}
  }
  /** Save only an explicit replacement or removal; an untouched masked field preserves the password. */
  async savePending() {
    const password=input('access-password').value;
    if (!password && !this.removePending) return true;
    if (this.busy || !this.value?.available) {this.error=t('accessUnavailable');this.render();return false;}
    this.busy=true;this.error='';this.render();
    try {this.value=await this.api('web-access',{password});input('access-password').value='';this.removePending=false;return true;}
    catch {this.error=t('accessSaveFailed');return false;}
    finally {this.busy=false;this.render();}
  }
  /** @param {import('./lib/types').State | null} state @param {boolean} editable */
  update(state,editable) {this.state=state;this.editable=editable;this.render();}
  render() {
    const disabled=this.busy || !this.editable || !this.value?.available;
    input('access-password').disabled=disabled;
    input('access-password').setAttribute('placeholder',this.value?.password_set && !this.removePending ? '••••••••' : '');
    input('access-password').title=t(this.value?.password_set ? 'passwordSet' : 'passwordUnset');
    button('password-clear').hidden=!this.value?.password_set;
    button('password-clear').disabled=disabled;
    button('password-clear').title=t(this.removePending ? 'undoPasswordClear' : 'clearPassword');
    button('password-clear').setAttribute('aria-label',button('password-clear').title);
    el('password-status').textContent=this.error ? t('passwordUnknown') : this.removePending ? t('passwordRemovePending') : this.value?.password_set ? t('passwordConfigured') : '';
    el('password-status').title=this.error || t(this.busy ? 'accessChecking' : !this.value ? 'accessUnknown' : !this.value.available ? 'accessNeedsInstall' : this.value.password_set ? 'passwordSet' : 'passwordUnset');
    const state=this.state,host=state?.settings.web_host;
    let addresses=this.value?.addresses || [];
    addresses=host==='0.0.0.0' ? addresses.filter(value=>!value.includes(':')) : host==='::' ? addresses.filter(value=>value.includes(':')) : host && !['127.0.0.1','::1'].includes(host) ? [host] : [];
    const port=state?.web_url ? new URL(state.web_url).port || '80' : '';
    el('lan-details').hidden=state?.phase!=='running' || !host || ['127.0.0.1','::1'].includes(host);
    el('lan-addresses').replaceChildren(...addresses.map(host=>{
      const code=document.createElement('code');code.textContent=`http://${host.includes(':')?'['+host+']':host}:${port}/webui/`;return code;
    }));
    if (!addresses.length) el('lan-addresses').textContent=t('lanUnavailable');
  }
}
