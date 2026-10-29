/** Localized user-facing errors shared by setup, the manager page and native dialogs. */
/** @type {Array<[RegExp,string,string]>} */
const rules=[
  [/cancelled|canceled|已取消/i,'操作已取消，已完成的内容会保留。','Cancelled. Completed work is kept.'],
  [/still shutting down|service has not stopped|application did not acknowledge|shutdown.*timed out/i,'服务尚未确认停止。可查看日志、返回界面或仅退出应用；不会强制结束服务。','The service has not confirmed shutdown. Check logs, return to the app or quit the app only; services will not be forcibly stopped.'],
  [/control channel|control is unavailable|控制记录不可用|no local control|without its manager/i,'服务控制连接不可用。请通过原启动入口停止服务后重试，也可以仅退出应用。','Service control is unavailable. Stop it through its original entry point and retry, or quit the app only.'],
  [/previous manager is busy|旧管理器仍在处理|Wait for the current|current operation has not stopped|Please wait for the current/i,'当前操作尚未结束，请等待完成后重试连接。','An operation is still in progress. Wait for it to finish, then reconnect.'],
  [/previous manager has not exited|manager has not exited/i,'旧管理器尚未退出，请稍后重新连接。项目文件已保留。','The previous manager has not exited. Reconnect shortly. Project files are kept.'],
  [/backend startup timed out|manager exited|backend manifest|uv is unavailable/i,'本地管理环境未能启动。请展开详情检查原因，必要时重新安装 APP。','The local management environment could not start. Expand details to check the cause, or reinstall the app.'],
  [/port.*(occupied|unavailable|in use)|端口.*(占用|不可用)|EADDRINUSE/i,'所选端口已被占用或不可用，请更换端口后重试。','The selected port is in use or unavailable. Choose another port and retry.'],
  [/another anelf instance|already running|正在运行|仍在运行/i,'检测到已有运行实例，请先确认其状态，避免重复启动。','An instance is already running. Check its status before starting another.'],
  [/stop.*before.*(settings|changing)|先停止.*配置/i,'请先停止服务，再修改配置。','Stop the service before changing settings.'],
  [/Install the project environment first|environment is not ready/i,'项目环境尚未准备完成，请先完成安装。','The project environment is not ready. Complete installation first.'],
  [/WinError 32|EBUSY|目录被占用|being used by another process|resource busy/i,'文件或目录仍被占用。请关闭相关编辑器、终端或服务后重试；已完成的迁移进度会保留。','A file or folder is in use. Close the relevant editor, terminal or service and retry. Completed migration progress is kept.'],
  [/permission denied|access.*denied|WinError 5|EACCES|EPERM|无.*权限/i,'无法访问所选目录，请检查权限或选择其他目录。','Cannot access the selected folder. Check permissions or choose another location.'],
  [/not enough.*space|insufficient.*space|ENOSPC|空间不足/i,'磁盘可用空间不足，请清理空间或更换安装目录。','Not enough disk space. Free space or choose another installation folder.'],
  [/hash.check|checksum|hashes were provided|校验和|摘要不匹配/i,'下载内容未通过完整性校验。请更换下载源后重试；不会跳过校验。','The download failed integrity verification. Select another source and retry; verification will not be skipped.'],
  [/uncommitted|local changes|dirty working|未提交/i,'项目含未提交的修改，请先保存并处理这些修改，再更新。','The project has uncommitted changes. Save and resolve them before updating.'],
  [/not an AnelfAgent|not.*checkout|不是.*项目|项目.*不完整/i,'所选目录不是完整的 AnelfAgent 项目，请重新选择。','The selected folder is not a complete AnelfAgent project. Select another folder.'],
  [/non-empty|not empty|already contains|目标.*非空|目录.*为空/i,'目标目录已有文件，请选择空目录，或通过“接入已有项目”继续。','The destination already contains files. Choose an empty folder or connect an existing project.'],
  [/worktree|submodule|子模块|Git 工作目录|links? checkout|外部.*链接/i,'项目包含关联的 Git 工作目录或链接。请展开详情确认路径，或保留原目录接入。','The project contains linked Git worktrees or paths. Expand details to inspect them, or use the original folder.'],
  [/fetch failed|failed to fetch|ECONNREFUSED|connection refused|连接.*失败/i,'连接失败，请检查本地管理服务和网络后重试。','Connection failed. Check the local manager and network, then retry.'],
  [/download.*failed|failed to download|HTTP [45]\d\d|下载.*失败/i,'下载未完成，请检查网络或更换来源后继续，已有环境和缓存会复用。','The download did not finish. Check the network or change sources and continue; existing environments and caches are reused.'],
  [/timed? ?out|timeout|超时/i,'操作等待超时，请检查网络和日志后重试。已完成的内容会保留。','The operation timed out. Check the network and logs, then retry. Completed work is kept.'],
  [/Command failed|command exited|安装.*失败|构建.*失败/i,'安装或构建命令未完成。请展开详情检查原因，修复后继续。','An installation or build command failed. Expand details, resolve the cause and continue.'],
];
/** @param {unknown} error @param {string} language */
export function describeError(error,language='en') {
  const detail=(error instanceof Error?error.message:String(error || '')).replace(/^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/,'').replace(/^Error:\s*/,'');
  const zh=language.startsWith('zh');
  const rule=rules.find(([pattern])=>pattern.test(detail));
  return {message:rule?rule[zh?1:2]:zh?'操作未完成，请展开详情查看原因后重试。':'The operation did not finish. Expand details to check the cause and retry.',detail};
}
