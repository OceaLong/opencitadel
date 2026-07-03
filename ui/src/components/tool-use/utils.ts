import type { ToolEvent } from "@/lib/api/types";
import { translate } from "@/i18n/translate";

export type ToolKind =
  | "message"
  | "bash"
  | "file"
  | "search"
  | "browser"
  | "mcp"
  | "a2a"
  | "default";

export function getArg(args: Record<string, unknown>, ...keys: string[]): string {
  if (!args || typeof args !== "object") return "";
  for (const k of keys) {
    const v = args[k];
    if (typeof v === "string") return v;
  }
  return "";
}

export function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) + "…" : s;
}

export function getToolKind(data: ToolEvent | null | undefined): ToolKind {
  if (!data) return "default";
  const name = (data.name ?? "").toLowerCase();
  const fn = (data.function ?? "").toLowerCase();

  if (data.function === "message_notify_user" || data.function === "message_ask_user") {
    return "message";
  }
  if (
    name === "shell" ||
    name.includes("bash") ||
    fn === "shell_execute" ||
    fn === "run" ||
    fn === "execute" ||
    fn === "run_command"
  ) {
    return "bash";
  }
  if (name === "file" || name.includes("file")) {
    return "file";
  }
  if (name === "mcp" || name.startsWith("mcp_")) {
    return "mcp";
  }
  if (
    name === "search" ||
    fn === "search_web" ||
    fn.includes("search_web") ||
    fn.includes("search")
  ) {
    return "search";
  }
  if (name === "browser" || fn.startsWith("browser_")) {
    return "browser";
  }
  if (name === "a2a" || fn.startsWith("a2a_") || fn.includes("remote_agent")) {
    return "a2a";
  }
  return "default";
}

export function getFriendlyToolLabel(data: ToolEvent | null | undefined): string {
  if (!data) return translate("toolUse.defaultAction");
  const name = (data.name ?? "").toLowerCase();
  const fn = (data.function ?? "").toLowerCase();
  const args = data.args && typeof data.args === "object" ? data.args : {};

  if (data.function === "message_notify_user" || data.function === "message_ask_user") {
    const text = typeof args.text === "string" ? args.text : "";
    return text || "—";
  }

  const filepath = getArg(args, "filepath", "path", "pathname");
  const dirPath = getArg(args, "dir_path", "directory", "dir");
  const query = getArg(args, "query", "q");
  const command = getArg(args, "command", "cmd", "script");
  const url = getArg(args, "url", "href", "link");
  const key = getArg(args, "key");

  if (name === "file") {
    switch (fn) {
      case "read_file":
        return filepath
          ? translate("toolUse.readingFileWithPath", { path: truncate(filepath, 60) })
          : translate("toolUse.readingFile");
      case "write_file":
        return filepath
          ? translate("toolUse.writingFileWithPath", { path: truncate(filepath, 60) })
          : translate("toolUse.writingFile");
      case "replace_in_file":
        return filepath
          ? translate("toolUse.replacingFileWithPath", { path: truncate(filepath, 60) })
          : translate("toolUse.replacingFile");
      case "search_in_file":
        return filepath
          ? translate("toolUse.searchingInFileWithPath", { path: truncate(filepath, 60) })
          : translate("toolUse.searchingInFile");
      case "find_files":
        return dirPath
          ? translate("toolUse.findingFilesWithPath", { path: truncate(dirPath, 60) })
          : translate("toolUse.findingFiles");
      case "list_files":
        return dirPath
          ? translate("toolUse.listingDirWithPath", { path: truncate(dirPath, 60) })
          : translate("toolUse.listingDir");
      default:
        return filepath
          ? translate("toolUse.accessingFileWithPath", { path: truncate(filepath, 60) })
          : dirPath
            ? translate("toolUse.accessingDirWithPath", { path: truncate(dirPath, 60) })
            : translate("toolUse.accessingFile");
    }
  }

  if (name === "browser" || fn.startsWith("browser_")) {
    switch (fn) {
      case "browser_view":
        return translate("toolUse.browserView");
      case "browser_navigate":
        return url
          ? translate("toolUse.browserNavigateWithUrl", { url: truncate(url, 80) })
          : translate("toolUse.browserNavigate");
      case "browser_restart":
        return url
          ? translate("toolUse.browserRestartWithUrl", { url: truncate(url, 80) })
          : translate("toolUse.browserRestart");
      case "browser_click":
        return translate("toolUse.browserClick");
      case "browser_input":
        return translate("toolUse.browserInput");
      case "browser_move_mouse":
        return translate("toolUse.browserMoveMouse");
      case "browser_press_key":
        return key
          ? translate("toolUse.browserPressKeyWithKey", { key })
          : translate("toolUse.browserPressKey");
      case "browser_select_option":
        return translate("toolUse.browserSelectOption");
      case "browser_scroll_up":
        return translate("toolUse.browserScrollUp");
      case "browser_scroll_down":
        return translate("toolUse.browserScrollDown");
      case "browser_console_exec":
        return translate("toolUse.browserConsoleExec");
      case "browser_console_view":
        return translate("toolUse.browserConsoleView");
      default:
        return url
          ? translate("toolUse.browserDefaultWithUrl", { url: truncate(url, 80) })
          : translate("toolUse.browserDefault");
    }
  }

  if (name === "search" || fn === "search_web" || fn.includes("search_web")) {
    return query
      ? translate("toolUse.searchingWithQuery", { query: truncate(query, 60) })
      : translate("toolUse.searching");
  }

  if (name === "shell") {
    switch (fn) {
      case "shell_execute":
        return command
          ? translate("toolUse.runningCommandWithCmd", { cmd: truncate(command, 60) })
          : translate("toolUse.runningCommand");
      case "shell_read_output":
        return translate("toolUse.shellReadOutput");
      case "shell_wait":
        return translate("toolUse.shellWait");
      case "shell_write_input":
        return translate("toolUse.shellWriteInput");
      case "shell_kill_process":
        return translate("toolUse.shellKillProcess");
      default:
        return command
          ? translate("toolUse.runningCommandWithCmd", { cmd: truncate(command, 60) })
          : translate("toolUse.runningCommand");
    }
  }

  if (name.includes("bash") || fn === "run" || fn === "execute" || fn === "run_command") {
    const cmd = command || (typeof args.input === "string" ? args.input : "");
    return cmd
      ? translate("toolUse.runningCommandWithCmd", { cmd: truncate(cmd, 60) })
      : translate("toolUse.runningCommand");
  }

  if (name === "a2a") {
    switch (fn) {
      case "get_remote_agent_cards":
        return translate("toolUse.a2aListAgents");
      case "call_remote_agent":
        return query
          ? translate("toolUse.a2aCallAgentWithQuery", { query: truncate(query, 40) })
          : translate("toolUse.a2aCallAgent");
      default:
        return translate("toolUse.a2aDefault");
    }
  }

  if (name === "mcp" || name.startsWith("mcp_")) {
    if (fn.includes("search_web") || fn.includes("search")) {
      return query
        ? translate("toolUse.searchingWithQuery", { query: truncate(query, 60) })
        : translate("toolUse.searching");
    }
    return translate("toolUse.mcpAction");
  }

  return translate("toolUse.defaultAction");
}
