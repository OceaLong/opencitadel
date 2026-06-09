/**
 * API 模块统一导出
 */

// 核心 fetch 封装
export {
  ApiError,
  createSSEConnection,
  createSSEStream,
  del,
  get,
  parseSSEStream,
  patch,
  post,
  put,
  request,
} from "./fetch";

// 类型定义
export type {
  A2AServersData,
  AgentConfig,
  ApiResponse,
  ChatMessage,
  ChatParams,
  Codebase,
  CodebaseArtifact,
  ConsumptionAnalysisData,
  CreateA2AServerParams,
  CreateCodebaseParams,
  CreateSessionParams,
  SessionMode,
  ExecutionStatus,
  FileInfo,
  FileUploadParams,
  ListA2AServerItem,
  ListMCPServerItem,
  LLMConfig,
  MarketplaceApp,
  MarketplaceAppsData,
  MCPConfig,
  MCPServerConfig,
  MCPServersData,
  MCPTransport,
  NutritionAnalysisData,
  PlanEvent,
  PlanStep,
  Session,
  SessionDetail,
  SessionFile,
  SessionsData,
  SessionStatus,
  SSEEventData,
  SSEEventHandler,
  SSEEventType,
  StepEvent,
  ToolEvent,
  ToolEventStatus,
  VideoSearchData,
  ViewFileParams,
  ViewShellParams,
} from "./types";

// 模块 API
export { codebaseApi } from "./codebase";
export { configApi } from "./config";
export { fileApi } from "./file";
export { marketplaceApi } from "./marketplace";
export { memoryApi } from "./memory";
export { modelsApi } from "./models";
export { sessionApi } from "./session";
export { skillsApi } from "./skills";
export { questionnaireApi } from "./questionnaire";
export { roomApi } from "./room";
