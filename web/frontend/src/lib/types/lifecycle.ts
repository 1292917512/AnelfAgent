/** Lifecycle 宿主服务清单与启动时间线（/status/services、/status/startup）。 */

export interface LifecycleService {
  name: string;
  order: number;
  instance_type: string;
  has_cleanup: boolean;
  has_on_start: boolean;
  has_on_tick: boolean;
}

export interface StartupNode {
  name: string;
  state: string;
  duration: number;
  attempts: number;
  error: string | null;
}
