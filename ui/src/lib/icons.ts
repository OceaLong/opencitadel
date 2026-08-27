/**
 * Semantic icon registry — each meaning maps to exactly one lucide icon.
 * Import from here instead of lucide-react directly for app-level semantics.
 */
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  BookOpen,
  Bot,
  Brain,
  CircuitBoard,
  ClipboardList,
  Clock,
  Code2,
  Coins,
  Copy,
  Cpu,
  Download,
  FileSearch,
  FileText,
  Layers,
  LayoutDashboard,
  Loader2,
  MailPlus,
  MessageCircleQuestion,
  MoreHorizontal,
  PhoneCall,
  Plug,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Shield,
  Trash2,
  Users,
  Wand2,
  Wrench,
} from "lucide-react";

/** Code repository / codebase context */
export const IconCodebase: LucideIcon = Code2;
/** Document knowledge base context */
export const IconKnowledge: LucideIcon = BookOpen;
/** Scheduled / automation jobs */
export const IconAutomation: LucideIcon = Clock;
/** System / quick settings */
export const IconSettings: LucideIcon = Settings;
/** Admin dashboard */
export const IconAdmin: LucideIcon = LayoutDashboard;
/** Delete action */
export const IconDelete: LucideIcon = Trash2;
/** Skill templates */
export const IconSkill: LucideIcon = Wand2;
/** Inference control plane */
export const IconModel: LucideIcon = Cpu;
/** Layered resources / teams grouping */
export const IconLayers: LucideIcon = Layers;
/** Voice / call metrics */
export const IconPhoneCall: LucideIcon = PhoneCall;
/** Long-term memory */
export const IconMemory: LucideIcon = Brain;
/** MCP / protocol integrations */
export const IconIntegration: LucideIcon = Plug;
/** Generic agent / bot */
export const IconAgent: LucideIcon = Bot;
/** Generic task (no special context) */
export const IconTask: LucideIcon = CircuitBoard;
/** File preview / document body */
export const IconFilePreview: LucideIcon = FileText;
/** File search in tools */
export const IconFileSearch: LucideIcon = FileSearch;
/** Loading spinner */
export const IconLoading: LucideIcon = Loader2;
/** New / add */
export const IconAdd: LucideIcon = Plus;
/** Refresh / reanalyze */
export const IconRefresh: LucideIcon = RefreshCw;
/** Download */
export const IconDownload: LucideIcon = Download;
/** Search tool */
export const IconSearch: LucideIcon = Search;
/** Security / regulatory badge */
export const IconSecurity: LucideIcon = Shield;
/** Activity / debug */
export const IconActivity: LucideIcon = Activity;
/** Token usage / billing */
export const IconCoins: LucideIcon = Coins;
/** Ask / Q&A mode */
export const IconAsk: LucideIcon = MessageCircleQuestion;
/** Users admin */
export const IconUsers: LucideIcon = Users;
/** Invitations */
export const IconInvitation: LucideIcon = MailPlus;
/** Copy to clipboard */
export const IconCopy: LucideIcon = Copy;
/** Overflow menu */
export const IconMore: LucideIcon = MoreHorizontal;
/** Audit log */
export const IconAudit: LucideIcon = ClipboardList;
/** Generic tool / MCP server row */
export const IconTool: LucideIcon = Wrench;

export type SessionContextKind = "general" | "codebase" | "knowledge" | "hybrid";

export function getSessionContextKind(session: {
  resource_bindings?: Array<{
    resource_kind: "codebase" | "knowledge_base";
  }>;
}): SessionContextKind {
  const bindings = session.resource_bindings ?? [];
  const hasCode = bindings.some((binding) => binding.resource_kind === "codebase");
  const hasKb = bindings.some((binding) => binding.resource_kind === "knowledge_base");
  if (hasCode && hasKb) return "hybrid";
  if (hasCode) return "codebase";
  if (hasKb) return "knowledge";
  return "general";
}
