export { apiClient, useAuthStore, type EaosUser } from "./client";
export {
  getApiBaseUrl,
  getBackendRootUrl,
  setBackendUrl,
  resolveBackendUrl,
  isTauri,
} from "./backend-url";
export type { paths, components } from "./generated/openapi";
