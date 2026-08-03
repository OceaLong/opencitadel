// ==================== Skill 管理 ====================

type SkillAgentParams = {
  max_iterations?: number;
  max_retries?: number;
  max_search_results?: number;
  temperature_override?: number;
};

export type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string;
  icon: string;
  category: string;
  system_prompt: string;
  allowed_tools: string[];
  mcp_server_refs?: string[];
  a2a_server_refs?: string[];
  recommended_model_id?: string | null;
  agent_params: SkillAgentParams;
  examples: string[];
  is_builtin: boolean;
  enabled: boolean;
  auto_recommend?: boolean;
  visibility?: "global" | "private";
  owner_user_id?: string | null;
  team_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type SkillsData = {
  skills: Skill[];
};

export type SkillSummary = {
  id: string;
  name: string;
  icon: string;
  examples: string[];
};

export type CreateSkillParams = {
  name: string;
  slug?: string;
  description?: string;
  icon?: string;
  category?: string;
  system_prompt?: string;
  allowed_tools?: string[];
  mcp_server_refs?: string[];
  a2a_server_refs?: string[];
  recommended_model_id?: string | null;
  agent_params?: SkillAgentParams;
  examples?: string[];
  enabled?: boolean;
  auto_recommend?: boolean;
};
