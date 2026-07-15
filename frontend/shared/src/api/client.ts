import createClient from "openapi-fetch";
import type { Middleware } from "openapi-fetch";
import { useAuthStore } from "./auth-store";
import type { paths } from "./generated/openapi";
import { getApiBaseUrl } from "./backend-url";

/** JWT injection — adds Authorization header to every request. */
const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = useAuthStore.getState().token;
    if (token) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
};

/** Normalise error responses — backend returns { detail, code } on error. */
const errorMiddleware: Middleware = {
  async onResponse({ response }) {
    if (!response.ok && response.status === 401) {
      // Token expired or invalid — clear auth state so guards redirect to login.
      useAuthStore.getState().clear();
    }
    return response;
  },
};

export const apiClient = createClient<paths>({ baseUrl: getApiBaseUrl() });
apiClient.use(authMiddleware, errorMiddleware);

export { useAuthStore };
export {
  getApiBaseUrl,
  getBackendRootUrl,
  setBackendUrl,
  resolveBackendUrl,
  isTauri,
} from "./backend-url";
export type { EaosUser } from "./auth-store";
export type { paths, components } from "./generated/openapi";
