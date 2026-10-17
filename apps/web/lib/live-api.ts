import { API } from "@/lib/api";

export async function liveApi<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API + path, {
    ...init, credentials: "include", cache: "no-store",
    headers: init?.body instanceof FormData
      ? init.headers
      : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* retain HTTP message */ }
    throw new Error(`${response.status}: ${message}`);
  }
  return response.json();
}
