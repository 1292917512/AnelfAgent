# 模块界面扩展

核心提供挂载位置、排序、懒加载和错误隔离；模块持有组件、接口、状态和翻译。新增模块内容无需修改工作区或总览页面。

Tailwind 构建同时扫描实体面板和频道前端的 TypeScript 源码，模块内声明的完整工具类名直接生效；不要用字符串拼接生成类名。

实体在 `entities/<name>/panels/contributions.ts` 声明，频道在 `channels/<id>/frontend/contributions.ts` 声明。开发服务和构建脚本自动发现清单；生成文件位于 `web/frontend/src/generated/`，不提交。删除清单即可移除扩展，不要求实体同时提供 `panel.tsx`。

```tsx
import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  {
    id: "service-tools",
    slot: "workspace.tools",
    title: { ns: "devops", key: "serviceControl" },
    description: { ns: "devops", key: "quickControlsDesc" },
    order: 50,
    load: () => import("./ServiceControls"),
  },
]);
```

- `workspace.tools`：工作区工具栏的「工具」入口，适合低频操作，不挤占文件与对话空间。
- `dashboard.cards`：总览卡片区域，适合模块状态与摘要。
- `dashboard.actions`：总览页头「服务与维护」入口，重启、部署等低频操作在独立弹窗中呈现。
- `app.routes`：模块整页；声明 `path`（绝对路径）、`group`（导航分组）和可选 `icon`。频道与实体共用这一入口，核心路由及参数化路径不能被覆盖。
- `data.tabs`：数据页签；声明 `tab`，组件通过 `?tab=<tab>` 直接访问。
- `system.restart`：需要重启的界面只挂载该控件，重启 API、确认和进度由提供模块持有。
- `audio.overview.cards` / `audio.identify.after`：音频总览与识别页的扩展区域。
- `audio.recording.actions`：录制单元操作；组件收到类型明确的 `{ path: string }`。
- `activity.tool`：本模块的工具执行渲染器，每个模块最多一个；接收 `ToolRendererProps`（脱敏的 `entry`、`summary/detail` 视图和通用 `fallback`）。
- `chat.message`：模块消息渲染器；声明 `messageType`，组件收到 `{ payload: JsonObject }` 并自行校验业务字段。
- `id` 在模块内唯一，使用小写字母、数字和连字符；不同模块自动加归属前缀。
- `order` 默认 100，小值在前，同序按归属和 ID 排序。
- `title` / `description` 引用模块自己的翻译命名空间。实体翻译放在 `panels/locales/{zh,en}.json`；频道复用自身插件注册的翻译。
- `load` 返回默认导出的 React 组件。清单保持轻量，组件到挂载时才加载；单个模块加载或渲染失败不影响其他模块。

扩展组件使用共享 UI 组件和请求客户端。后台接口仍由模块自己的路由提供并经过现有 Web 鉴权，清单本身不授予权限。跨页面长操作使用模块内共享状态，不能依赖卡片挂载维持流程，也不能把断网当作操作成功。

服务端实体使用 `entities._sdk.emit_chat_extension(type, payload, fallback)` 向发起操作的 Web 会话发送内容。没有 Web 会话归属时不投递；前端缺少对应模块时显示 `fallback`。核心协议不持有分享等模块的字段和事件分支。

模块的 `router.py::build_router()` 随目录发现自动挂载到 `/api/entity/<name>`。需要独立凭据的推送或分享端点可在路由装饰器下面声明 `@core.http_endpoints.self_authenticated`，处理器须自行校验令牌、有效期与访问范围。认证例外跟随实际注册的路径和 HTTP 方法，卸载路由即失效，不修改服务器白名单；其他管理端点仍受 Web 登录保护。

工具声明的实际实现位于 `core.tool_registry`，实体 SDK 导出同一接口。`agent` 内核直接使用核心接口，具体实体和频道的装配依赖仅允许出现在 `agent/runtime`。CI 的 import-linter 和前端架构测试检查这些边界。

运维实现参考 `entities/devops/panels/`：同一组件装配到工作区、总览和实体详情。更新操作后台执行，服务端串行处理更新、构建和重启；前端通过操作 ID 跟踪，并确认进程 ID 变化后提示手动刷新以保留草稿。

模块的 Vitest 测试放在 `panels/**/*.test.ts(x)` 或 `frontend/**/*.test.ts(x)`；Playwright 用例放在对应的 `e2e/*.spec.mts`（模块目录用 ESM 与前端共享测试设施）。统一测试命令自动发现，不需修改核心测试文件。

## 工具执行视图

实体和频道在同一清单中注册 `activity.tool`。服务端从 EntityRegistry 中实际工具函数的模块路径生成 `entity:<id>` / `channel:<id>` 归属；频道桥接工具读取注册来源。前端不根据工具名或用户参数猜测来源，不影响工具路由和执行。

```tsx
// contributions.ts
export default defineUiContributions([
  { id: "execution", slot: "activity.tool", title: { ns: "workbench", key: "activity.result" },
    load: () => import("./ExecutionView") },
]);

// ExecutionView.tsx
import type { ToolRendererProps } from "@/lib/ui-contributions";
import { ToolPresentation } from "@/components/activity/ToolPresentation";

export default function ExecutionView(props: ToolRendererProps) {
  return <ToolPresentation {...props} fields={[{ key: "path" }, { key: "pattern" }]} />;
}
```

摘要只选对操作有帮助的字段；业务含义由模块持有。命令类视图可复用 `CommandExecution`，原样呈现命令、标准输出、错误输出、目标、工作目录和退出码，输出不解释为 Markdown。宿主始终保留标签指代、耗时、调用标识和完整参数/回执入口。模块缺失、加载失败或渲染失败时使用通用视图，不隐藏记录。

开发服务中，组件源码修改交给 Vite Fast Refresh；清单、入口和语言资源变动重新发现并刷新页面。生产构建的前端资源仍需重新构建并刷新页面，Python 模块热重载不会把 TypeScript 编译到已发布资源中。
生产构建将启动必读的轻量扩展清单合并为一个资源，组件实现保持按需加载；增加模块不会逐个增加首屏清单请求。
