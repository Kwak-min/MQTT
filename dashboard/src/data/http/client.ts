export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly url: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, { signal, headers: { Accept: 'application/json' } });
  if (!res.ok) throw new ApiError(`GET ${path} 실패 (${res.status})`, res.status, url);
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new ApiError(`POST ${path} 실패 (${res.status})`, res.status, url);
  return (await res.json()) as T;
}

export const wsUrl = import.meta.env.VITE_WS_URL ?? '';
