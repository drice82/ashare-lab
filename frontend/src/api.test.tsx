import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useResource } from "./api";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("waits for a slow request and merges manual refreshes", async () => {
  vi.useFakeTimers();
  let finish!: (value: Response) => void;
  const fetchMock = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const { result, unmount } = renderHook(() =>
    useResource("/market/status", 15000),
  );
  await act(async () => {
    vi.advanceTimersByTime(60000);
    result.current.reload();
    result.current.reload();
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    finish(Response.json({ rows: 3 }));
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  unmount();
  expect(
    (fetchMock.mock.calls[1] as unknown as [string, RequestInit])[1].signal
      ?.aborted,
  ).toBe(true);
});

it("schedules the next poll after completion and stops on authentication failure", async () => {
  vi.useFakeTimers();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ rows: 3 }))
    .mockResolvedValue(
      Response.json(
        { code: "unauthorized", message: "locked" },
        { status: 401 },
      ),
    );
  vi.stubGlobal("fetch", fetchMock);
  const { unmount } = renderHook(() => useResource("/market/status", 15000));
  await act(async () => {});
  await act(async () => {
    vi.advanceTimersByTime(14999);
  });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => {
    vi.advanceTimersByTime(1);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await act(async () => {
    vi.advanceTimersByTime(60000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  unmount();
});

it("anonymous authentication failure does not open the shared-key dialog", async () => {
  const dispatch = vi.spyOn(window, "dispatchEvent");
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { code: "anonymous_unauthorized", message: "凭据失效" },
          { status: 401 },
        ),
      ),
  );
  const { api } = await import("./api");
  await expect(api("/notifications/session")).rejects.toThrow("凭据失效");
  expect(dispatch).not.toHaveBeenCalled();
});
