import { del, get, post } from "./fetch";

/**
 * 通用资源 CRUD/build 客户端工厂。
 *
 * 统一封装资源的 create/list/get/version/build/delete/session 请求。
 */
export function makeResourceClient<
  TResource,
  TResourceList,
  TVersion,
  TVersionList,
  TBuild,
  TCreateParams,
  TSessionParams,
  TSessionData,
>(basePath: string) {
  return {
    create: (params: TCreateParams): Promise<TResource> => {
      return post<TResource>(basePath, params);
    },

    list: (limit = 100, offset = 0): Promise<TResourceList> => {
      return get<TResourceList>(basePath, { limit, offset });
    },

    get: (id: string): Promise<TResource> => {
      return get<TResource>(`${basePath}/${id}`);
    },

    listVersions: (id: string): Promise<TVersionList> => {
      return get<TVersionList>(`${basePath}/${id}/versions`);
    },

    getVersion: (id: string, versionId: string): Promise<TVersion> => {
      return get<TVersion>(`${basePath}/${id}/versions/${versionId}`);
    },

    createBuild: (id: string): Promise<TVersion> => {
      return post<TVersion>(`${basePath}/${id}/builds`);
    },

    retryBuild: (id: string, buildId: string): Promise<TVersion> => {
      return post<TVersion>(`${basePath}/${id}/builds/${buildId}/retry`);
    },

    cancelBuild: (id: string, buildId: string): Promise<TBuild> => {
      return post<TBuild>(`${basePath}/${id}/builds/${buildId}/cancel`);
    },

    delete: (id: string): Promise<void> => {
      return del(`${basePath}/${id}`);
    },

    createSession: (id: string, params?: TSessionParams): Promise<TSessionData> => {
      return post<TSessionData>(`${basePath}/${id}/sessions`, params || {});
    },
  };
}
