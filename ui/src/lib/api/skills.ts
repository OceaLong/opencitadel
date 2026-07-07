import { del, get, post, put } from "./fetch";
import type { CreateSkillParams, Skill, SkillsData } from "./types";

export const skillsApi = {
  list: (enabledOnly = false): Promise<SkillsData> =>
    get<SkillsData>(`/skills?enabled_only=${enabledOnly}`),

  get: (id: string): Promise<Skill> => get<Skill>(`/skills/${id}`),

  create: (params: CreateSkillParams): Promise<Skill> => post<Skill>("/skills", params),

  update: (id: string, params: Partial<CreateSkillParams>): Promise<Skill> =>
    put<Skill>(`/skills/${id}`, params),

  delete: (id: string): Promise<void> => del<void>(`/skills/${id}`),

  import: (content: string, slug?: string): Promise<Skill> =>
    post<Skill>("/skills/import", { content, slug: slug ?? "" }),
};
