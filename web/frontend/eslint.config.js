import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 项目约定：显式 any 已由 tsc 与代码评审约束，eslint 不重复报警
      "@typescript-eslint/no-explicit-any": "off",
      // 允许以 _ 前缀标记有意未使用的变量
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // zustand selector 等场景需要非空断言
      "@typescript-eslint/no-non-null-assertion": "off",
      // 允许空 catch（静默忽略可预期的异步失败，如 SSE 解析兜底）
      "no-empty": ["error", { allowEmptyCatch: true }],
      // 以下为 react-hooks v7 新增的 compiler 时代严格规则，与本项目既有
      // 惯用法（props→state 同步、渲染期时间差显示、ref 镜像等）冲突，
      // 按任务约定降级/关闭，不追求零 warning：
      // props 变化时同步重置本地 state 是本项目普遍模式，误报率高
      "react-hooks/set-state-in-effect": "off",
      // 渲染期 Date.now() 用于倒计时/耗时展示，属预期行为
      "react-hooks/purity": "warn",
      // render 期间写 ref（pausedRef 镜像）是有意模式，降为警告
      "react-hooks/refs": "warn",
      // 组件内定义小组件（EntityDetail 等）历史代码较多，降为警告
      "react-hooks/static-components": "warn",
    },
  },
);
