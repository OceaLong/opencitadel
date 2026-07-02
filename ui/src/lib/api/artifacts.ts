import { get, post } from "./fetch";
import type {
  DeliveryArtifact,
  DeliveryArtifactContent,
  DeliveryArtifactShare,
  DeliveryArtifactsData,
} from "./types";

export const artifactsApi = {
  listBySession: (sessionId: string): Promise<DeliveryArtifactsData> => {
    return get<DeliveryArtifactsData>(`/sessions/${sessionId}/artifacts`);
  },

  get: (artifactId: string): Promise<DeliveryArtifact> => {
    return get<DeliveryArtifact>(`/artifacts/${artifactId}`);
  },

  getContent: (artifactId: string, version?: number): Promise<DeliveryArtifactContent> => {
    return get<DeliveryArtifactContent>(
      `/artifacts/${artifactId}/content`,
      version != null ? { version } : undefined,
    );
  },

  share: (artifactId: string): Promise<DeliveryArtifactShare> => {
    return post<DeliveryArtifactShare>(`/artifacts/${artifactId}/share`, {});
  },

  getPublicContent: (token: string): Promise<DeliveryArtifactContent> => {
    return get<DeliveryArtifactContent>(`/share/artifact/${token}`);
  },
};
