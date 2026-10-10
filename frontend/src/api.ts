import { useCallback, useEffect, useRef, useState } from "react";

class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

let accessToken = "";
export function setAccessToken(value: string) {
  accessToken = value;
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
  managementPassword?: string,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(managementPassword ? { "X-Data-Management-Password": managementPassword } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      message?: string;
      detail?: unknown;
      code?: string;
    };
    if (response.status === 401)
      window.dispatchEvent(new Event("ashare-auth-required"));
    if (response.status === 403 && body.code === "data_management_locked")
      window.dispatchEvent(new Event("ashare-data-locked"));
    throw new ApiError(
      body.message ??
        (response.status === 422
          ? "输入参数无效，请检查日期、股票代码和条件范围"
          : `请求失败 (${response.status})`),
      response.status,
    );
  }
  return response.json() as Promise<T>;
}
export async function exportCsv(id: string) {
  return downloadCsv(`/screening/runs/${id}/export`, `screening-${id}.csv`);
}
export async function downloadCsv(path: string, filename: string) {
  const response = await fetch(`/api/v1${path}`, {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  });
  if (!response.ok) throw new Error("导出失败，请检查任务状态及访问权限");
  const url = URL.createObjectURL(await response.blob());
  const element = document.createElement("a");
  element.href = url;
  element.download = filename;
  element.click();
  URL.revokeObjectURL(url);
}
export function useResource<T>(path: string | null, pollMs = 0, managementPassword?: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const refresh = useRef<() => void>(() => {});
  const reload = useCallback(() => refresh.current(), []);
  useEffect(() => {
    if (!path) {
      setData(undefined);
      setError("");
      refresh.current = () => {};
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    let pendingRefresh = false;
    let stopped = false;
    setData(undefined);
    setError("");
    const read = async () => {
      if (stopped || controller.signal.aborted) return;
      if (inFlight) { pendingRefresh = true; return; }
      if (timer) clearTimeout(timer);
      inFlight = true;
      try {
        const value = await api<T>(path, { signal: controller.signal }, managementPassword);
        if (!controller.signal.aborted) { setData(value); setError(""); }
      } catch (e: unknown) {
        if (!controller.signal.aborted) {
          setError(e instanceof Error ? e.message : "请求失败");
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) stopped = true;
        }
      } finally {
        inFlight = false;
        if (!stopped && !controller.signal.aborted) {
          if (pendingRefresh) { pendingRefresh = false; void read(); }
          else if (pollMs) timer = setTimeout(() => void read(), pollMs);
        }
      }
    };
    refresh.current = () => { void read(); };
    void read();
    return () => {
      stopped = true;
      controller.abort();
      refresh.current = () => {};
      if (timer) clearTimeout(timer);
    };
  }, [path, pollMs, managementPassword]);
  return { data, error, reload };
}
