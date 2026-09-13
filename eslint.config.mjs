/**
 * 仓库根 ESLint 配置 — 实体面板源码（entities/**）门禁。
 *
 * 实体面板经 @entities 别名以真实路径被前端消费后，已脱离
 * web/frontend flat config 的 base path（ESLint 拒绝 lint 配置 base path
 * 之外的文件，官方指引即"把配置放到父目录"），故在仓库根放置本配置。
 *
 * 规则条目复用 web/frontend/eslint.config.js（依赖解析锚定在前端目录，
 * 与核心源码同口径），仅按本配置所在目录（仓库根）重写 files 基座：
 *   "src/**"          → "web/frontend/src/**"
 *   "../../entities/**" → "entities/**"
 *
 * 使用：cd web/frontend && npx eslint --config ../../eslint.config.mjs src ../../entities
 * （已收编进 web/frontend 的 npm run lint）
 */
import frontendConfig from "./web/frontend/eslint.config.js";

export default frontendConfig.map((entry) => {
  if (!entry.files) return entry;
  return {
    ...entry,
    files: entry.files.map((pattern) =>
      pattern
        .replace(/^\.\.\/\.\.\//, "")
        .replace(/^src\//, "web/frontend/src/"),
    ),
  };
});
