/**
 * Session events 公共入口（barrel）。
 *
 * 具体实现拆分为：
 * - `session-events/format`      时间/ID 等格式化辅助
 * - `session-events/normalize`   后端原始事件归一化 + 会话状态归约
 * - `session-events/projections` timeline / 观测摘要投影（含增量折叠积木）
 * - `session-events/event-store` 基于 event_id 有序、去重、增量投影的事件 store
 */

export * from "./session-events/event-store";
export * from "./session-events/format";
export * from "./session-events/normalize";
export * from "./session-events/projections";
