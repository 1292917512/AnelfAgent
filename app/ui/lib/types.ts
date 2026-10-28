export interface Settings { web_host: string; web_port: number; stop_timeout: number; auto_start: boolean; source: string; download_source: string; pypi_source:string; timezone: string; }
export interface Stage { key: string; status: string; }
export interface UvProgress { phase: string; package: string; downloads: number; active: string[]; packages?: {name:string;received:number;total:number}[]; resolved?: number; prepared?: number; installed?: number; audited?: number; }
export interface Operation { action?: string; status?: string; stage?: string; detail?: string; started_at?: number; stage_started_at?: number; updated_at?: number; finished_at?: number; received?: number; total?: number; progress?: UvProgress | null; cancellable?: boolean; cancelling?: boolean; existing_packages?:number; steps: Stage[]; }
export interface Storage {path:string;free:number|null;required:number;sufficient:boolean;error:string;}
export interface State { root: string; phase: string; running: boolean; busy: boolean; ready: boolean; resumable?: boolean; pid: number | null; external_pid: number; web_url: string; resolved_source: string; downloads?: Record<string,string>; storage:Storage; settings: Settings; operation: Operation; last: {ok: boolean; message?: string; cancelled?: boolean} | null; events: string[]; }
export interface NetworkCheck { source:string;results:{name:string;url:string;ok:boolean;detail:string;elapsed_ms:number}[]; }
export interface Schema { name: keyof Settings; type: string; advanced?: boolean; options?: string[]; option_labels?: Record<string,Record<string,string>>; min?: number; max?: number; label: Record<string, string>; }
export interface WebAccess {available:boolean;password_set:boolean;addresses:string[];}
export interface Environment { tools: Record<string, {path: string | null; version: string}>; python: string; python_installed: boolean; python_reusable?:string|null; python_version?:string; frontend_built: boolean; project_present: boolean; }
export interface PypiSource {url:string;label:Record<string,string>;}
export interface TrayState {ready:boolean;webReady:boolean;settingsAvailable:boolean;language:'zh'|'en';}
export interface BootstrapInfo {root:string;source:string;downloadSource:string;pypiSource:string;pypiSources:Record<string,PypiSource>;pypiDefaults:Record<string,string>;timezone:string;china:boolean;language:string;storage:Storage[];uv:{bundled:boolean;version:string;path:string};}
declare global { interface Window { anelfDesktop?: {
  trayState(value:TrayState):Promise<void>;
  onNavigate(callback:(view:'home'|'settings')=>void):()=>void;
  chooseProject(): Promise<string|undefined>; version(): Promise<string>; reconnect(): Promise<void>; cleanup(): Promise<void>;
  bootstrapInfo():Promise<BootstrapInfo>; bootstrapStart(value:{source:string;downloadSource:string;pypiSource:string}):Promise<void>;
  bootstrapStorage():Promise<Storage[]>;
  resize(value:{view:'setup'|'manage'|'settings';logs:boolean;height?:number}):Promise<void>;
  onExitState(callback:(value:{stopping:boolean;error:string})=>void):()=>void;
  bootstrapCancel():Promise<void>; onBootstrapProgress(callback:(value:string)=>void):()=>void;
}; } }
