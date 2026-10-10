import { Component, type ReactNode } from "react";
import { QueryErrorResetBoundary } from "@tanstack/react-query";
import { QueryError } from "./AsyncState";

class Boundary extends Component<{ children: ReactNode; onReset: () => void }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error("[SectionBoundary]", error); }
  render() {
    if (!this.state.error) return this.props.children;
    return <QueryError compact error={this.state.error} retry={() => {
      this.props.onReset();
      this.setState({ error: null });
    }} />;
  }
}

/** 隔离可独立恢复的数据区，保留同页其他操作入口。 */
export function SectionBoundary({ children }: { children: ReactNode }) {
  return <QueryErrorResetBoundary>{({ reset }) => <Boundary onReset={reset}>{children}</Boundary>}</QueryErrorResetBoundary>;
}
