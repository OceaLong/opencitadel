export type ClientDataScope = Readonly<{
  userId: string;
  workspaceId: string;
}>;

export function clientDataScopeKey(scope: ClientDataScope): string {
  return `${encodeURIComponent(scope.userId)}:${encodeURIComponent(scope.workspaceId)}`;
}
