import { del, get, post } from "./fetch";

export type ServiceApiKey = {
  id: string;
  name: string;
  prefix: string;
  last_used_at?: string | null;
  revoked_at?: string | null;
  created_at: string;
};

export type CreatedServiceApiKey = ServiceApiKey & { plaintext: string };

export const serviceKeysApi = {
  list: () => get<{ keys: ServiceApiKey[] }>("/service-keys"),
  create: (name: string) => post<CreatedServiceApiKey>("/service-keys", { name }),
  revoke: (id: string) => del(`/service-keys/${id}`),
};
