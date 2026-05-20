import { get, post, put, del } from "./fetch";
import type { Skill, SkillsData, CreateSkillParams } from "./types";

export const skillsApi = {
  list: (enabledOnly = false): Promise<SkillsData> =>
    get<SkillsData>(`/skills?enabled_only=${enabledOnly}`),

  get: (id: string): Promise<Skill> => get<Skill>(`/skills/${id}`),

  create: (params: CreateSkillParams): Promise<Skill> =>
    post<Skill>("/skills", params),

  update: (id: string, params: Partial<CreateSkillParams>): Promise<Skill> =>
    put<Skill>(`/skills/${id}`, params),

  delete: (id: string): Promise<void> => del<void>(`/skills/${id}`),
};
