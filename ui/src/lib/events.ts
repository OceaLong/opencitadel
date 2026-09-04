/**
 * 跨组件的轻量事件总线：基于 `window` 的 CustomEvent，用于"某处发生了写操作，
 * 其它常驻组件（顶栏角标/状态芯片等）应立即刷新"的场景。
 *
 * 命名约定与 `auth-events.ts` 的 AUTH_REQUIRED_EVENT 一致；SSR 环境下
 * dispatch/subscribe 均为 no-op。
 */

/** 审批集合发生变化（新审批到达、被决定、过期或取消）。 */
export const APPROVALS_CHANGED_EVENT = "opencitadel:approvals-changed";

/** 能力快照可能变化（推理端点/模型/绑定被创建、更新或删除）。 */
export const CAPABILITIES_CHANGED_EVENT = "opencitadel:capabilities-changed";

/** 派发一个应用级事件；服务端渲染时为 no-op。 */
export function dispatchAppEvent(name: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(name));
}

/** 订阅一个应用级事件，返回取消订阅函数；服务端渲染时为 no-op。 */
export function subscribeAppEvent(name: string, listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(name, listener);
  return () => window.removeEventListener(name, listener);
}
