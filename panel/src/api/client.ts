import { getRuntimeConfig } from "../config/runtime";

export interface ApiClient {
  get(path: string, init?: RequestInit): Promise<Response>;
  post(path: string, init?: RequestInit): Promise<Response>;
  put(path: string, init?: RequestInit): Promise<Response>;
  delete(path: string, init?: RequestInit): Promise<Response>;
}

export interface ApiConfig {
  apiBaseUrl: string;
}

export function createApiClient(
  config: ApiConfig = getRuntimeConfig(),
  fetchImplementation: typeof fetch = globalThis.fetch,
): ApiClient {
  function request(
    method: "GET" | "POST" | "PUT" | "DELETE",
    path: string,
    init?: RequestInit,
  ): Promise<Response> {
    if (!path.startsWith("/")) {
      throw new Error("API paths must begin with /");
    }
    const headers = new Headers(init?.headers);
    if (method !== "GET" && typeof document !== "undefined") {
      const cookie = document.cookie.split("; ").find((item) => item.startsWith("skid2_csrf="));
      if (cookie !== undefined) headers.set("X-CSRF-Token", cookie.slice("skid2_csrf=".length));
    }
    return fetchImplementation(`${config.apiBaseUrl}${path}`, {
      ...init,
      method,
      headers,
      credentials: "same-origin",
    }).then((response) => {
      if (
        response.status === 401 &&
        typeof window !== "undefined" &&
        path !== "/v1/session" &&
        path !== "/v1/login" &&
        path !== "/v1/logout" &&
        path !== "/v1/auth/login" &&
        path !== "/v1/auth/forgot-password" &&
        !path.startsWith("/v1/auth/invite")
      ) {
        window.dispatchEvent(new Event("session-ended"));
      }
      return response;
    });
  }

  return {
    get(path: string, init?: RequestInit): Promise<Response> {
      return request("GET", path, init);
    },
    delete(path: string, init?: RequestInit): Promise<Response> {
      return request("DELETE", path, init);
    },
    post(path: string, init?: RequestInit): Promise<Response> {
      return request("POST", path, init);
    },
    put(path: string, init?: RequestInit): Promise<Response> {
      return request("PUT", path, init);
    },
  };
}
