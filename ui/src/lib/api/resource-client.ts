import { del, get, post } from "./fetch";

/**
 * 通用资源 CRUD/build 客户端工厂。
 *
 * codebase.ts 与 knowledge.ts 中的 create/list/get/listVersions/getVersion/
 * createBuild/retryBuild/cancelBuild/delete/createSession 十个方法结构完全同构，
 * 仅路径前缀（`/codebases` vs `/knowledge-bases`）与各自的资源/版本/构建/参数类型不同，
 * 因此收敛为本工厂，避免两处重复维护相同的包装逻辑。
 *
 * 领域专有方法（如 codebase 的 tree/symbols/source、knowledge 的 documents/graph、
 * 以及两者共用但已下沉到 fetch.ts 的 ingestStream）不属于这个同构集合，继续留在
 * 各自的 codebase.ts / knowledge.ts 中。
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
