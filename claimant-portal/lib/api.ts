/**
 * Unauthenticated API fetch wrapper for the claimant portal.
 * Reads the API base URL from window.__CLAIMANT_CONFIG__.API_URL,
 * which is injected at build time by CodeBuild into /public/config.js.
 */

declare global {
  interface Window {
    __CLAIMANT_CONFIG__?: { API_URL: string };
  }
}

function getApiUrl(): string {
  if (typeof window !== "undefined" && window.__CLAIMANT_CONFIG__?.API_URL) {
    return window.__CLAIMANT_CONFIG__.API_URL.replace(/\/$/, "");
  }
  // Fallback for local development
  return process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "";
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  const contentType = res.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  if (!res.ok) {
    const body = isJson ? await res.json() : await res.text();
    const msg =
      typeof body === "object" && body !== null && "error" in body
        ? String((body as { error: unknown }).error)
        : String(body);
    throw new ApiError(res.status, msg);
  }
  if (isJson) return res.json() as Promise<T>;
  return res.text() as unknown as T;
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit & { token?: string },
): Promise<T> {
  const base = getApiUrl();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options?.headers as Record<string, string>),
  };
  if (options?.token) {
    headers["X-Claimant-Token"] = options.token;
  }
  const { token: _token, ...rest } = options ?? {};
  void _token;
  const res = await fetch(`${base}${path}`, { ...rest, headers });
  return handleResponse<T>(res);
}
