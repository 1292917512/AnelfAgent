import { Navigate } from "react-router-dom";

/** 旧的实体管理面板书签转到独立插件页。 */
export default function PluginsPanel() {
  return <Navigate to="/plugins" replace />;
}
