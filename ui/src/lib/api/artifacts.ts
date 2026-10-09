import { del, get, post, type RequestOptions } from "./fetch";
import type {
  DeliveryArtifact,
  DeliveryArtifactContent,
  DeliveryArtifactsData,
  DeliveryArtifactShare,
} from "./types";

export const artifactsApi = {
  listBySession: (sessionId: string, options?: RequestOptions): Promise<DeliveryArtifactsData> => {
    return get<DeliveryArtifactsData>(`/sessions/${sessionId}/artifacts`, undefined, options);
  },

  get: (artifactId: string, options?: RequestOptions): Promise<DeliveryArtifact> => {
    return get<DeliveryArtifact>(`/artifacts/${artifactId}`, undefined, options);
  },

  getContent: (
    artifactId: string,
    version?: number,
    options?: RequestOptions,
  ): Promise<DeliveryArtifactContent> => {
    return get<DeliveryArtifactContent>(
      `/artifacts/${artifactId}/content`,
      version != null ? { version } : undefined,
      options,
    );
  },

  share: (artifactId: string, options?: RequestOptions): Promise<DeliveryArtifactShare> => {
    return post<DeliveryArtifactShare>(`/artifacts/${artifactId}/share`, {}, options);
  },

  revokeShare: (artifactId: string, options?: RequestOptions): Promise<{ revoked: boolean }> => {
    return del<{ revoked: boolean }>(`/artifacts/${artifactId}/share`, options);
  },

  getPublicContent: (token: string): Promise<DeliveryArtifactContent> => {
    return get<DeliveryArtifactContent>(`/share/artifact/${token}`);
  },
};
