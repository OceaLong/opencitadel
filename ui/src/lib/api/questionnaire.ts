import { get, patch, post } from "./fetch";
import type {
  CreateQuestionnaireParams,
  PublishQuestionnaireParams,
  QuestionnaireData,
  QuestionnaireStatsData,
  SubmitQuestionnaireResponseParams,
  SubmitQuestionnaireResponseResult,
  UpdateQuestionnaireParams,
} from "./types";

export const questionnaireApi = {
  create: (params: CreateQuestionnaireParams): Promise<QuestionnaireData> =>
    post<QuestionnaireData>("/marketplace/questionnaires", params),

  get: (id: string, manageToken: string): Promise<QuestionnaireData> =>
    get<QuestionnaireData>(`/marketplace/questionnaires/${id}`, { manage_token: manageToken }, {
      headers: { "X-Manage-Token": manageToken },
    }),

  update: (id: string, params: UpdateQuestionnaireParams): Promise<QuestionnaireData> =>
    patch<QuestionnaireData>(`/marketplace/questionnaires/${id}`, params),

  publish: (id: string, params: PublishQuestionnaireParams): Promise<QuestionnaireData> =>
    post<QuestionnaireData>(`/marketplace/questionnaires/${id}/publish`, params),

  close: (id: string, params: PublishQuestionnaireParams): Promise<QuestionnaireData> =>
    post<QuestionnaireData>(`/marketplace/questionnaires/${id}/close`, params),

  getPublic: (slug: string): Promise<QuestionnaireData> =>
    get<QuestionnaireData>(`/marketplace/questionnaires/public/${slug}`),

  submitResponse: (
    slug: string,
    params: SubmitQuestionnaireResponseParams,
  ): Promise<SubmitQuestionnaireResponseResult> =>
    post<SubmitQuestionnaireResponseResult>(
      `/marketplace/questionnaires/public/${slug}/responses`,
      params,
    ),

  getStats: (id: string, manageToken: string): Promise<QuestionnaireStatsData> =>
    get<QuestionnaireStatsData>(
      `/marketplace/questionnaires/${id}/stats`,
      { manage_token: manageToken },
      { headers: { "X-Manage-Token": manageToken } },
    ),
};
