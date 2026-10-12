import { StrictMode } from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { usePageViews } from "./analytics";

function Harness() {
  usePageViews();
  const navigate = useNavigate();
  return (
    <>
      {[
        "/screening?tab=history",
        "/knowledge",
        "/stock/000001.SZ",
        "/stock/600519.SH",
        "/data",
        "/missing",
      ].map((path) => (
        <button key={path} onClick={() => navigate(path)}>
          {path}
        </button>
      ))}
      <button onClick={() => navigate(-1)}>返回</button>
    </>
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
});

it("counts navigation and stock changes once, ignores query changes and excludes admin", async () => {
  render(
    <StrictMode>
      <MemoryRouter initialEntries={["/screening"]}>
        <Harness />
      </MemoryRouter>
    </StrictMode>,
  );
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByText("/screening?tab=history"));
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("/knowledge"));
  fireEvent.click(screen.getByText("/stock/000001.SZ"));
  fireEvent.click(screen.getByText("/stock/600519.SH"));
  fireEvent.click(screen.getByText("/data"));
  fireEvent.click(screen.getByText("/missing"));
  fireEvent.click(screen.getByText("返回"));
  fireEvent.click(screen.getByText("返回"));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(5));
  const payloads = vi
    .mocked(fetch)
    .mock.calls.map(([, init]) => JSON.parse(init!.body as string));
  expect(payloads.map((p) => p.page)).toEqual([
    "screening",
    "knowledge",
    "stock_detail",
    "stock_detail",
    "stock_detail",
  ]);
  expect(new Set(payloads.map((p) => p.visitor_id)).size).toBe(1);
  expect(new Set(payloads.map((p) => p.event_id)).size).toBe(5);
  expect(Object.keys(payloads[0]).sort()).toEqual([
    "event_id",
    "page",
    "visitor_id",
  ]);
});

it("keeps the browser visitor across reload-style mounts with new events", async () => {
  const first = render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <Harness />
    </MemoryRouter>,
  );
  first.unmount();
  render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <Harness />
    </MemoryRouter>,
  );
  const calls = vi.mocked(fetch).mock.calls;
  const a = JSON.parse(calls[0][1]!.body as string);
  const b = JSON.parse(calls[1][1]!.body as string);
  expect(a.visitor_id).toBe(b.visitor_id);
  expect(a.event_id).not.toBe(b.event_id);
});

it("recognizes page URLs with trailing slashes and case variations", () => {
  for (const path of ["/knowledge/", "/SCREENING", "/stock/000001.sz/"]) {
    const view = render(
      <MemoryRouter initialEntries={[path]}>
        <Harness />
      </MemoryRouter>,
    );
    view.unmount();
  }
  expect(
    vi
      .mocked(fetch)
      .mock.calls.map(([, init]) => JSON.parse(init!.body as string).page),
  ).toEqual(["knowledge", "screening", "stock_detail"]);
});

it("storage and network failures leave navigation usable without authentication events", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  const auth = vi.fn();
  window.addEventListener("ashare-auth-required", auth);
  render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <Harness />
    </MemoryRouter>,
  );
  await act(async () => {
    fireEvent.click(screen.getByText("/stock/000001.SZ"));
  });
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(auth).not.toHaveBeenCalled();
  const calls = vi.mocked(fetch).mock.calls;
  expect(JSON.parse(calls[0][1]!.body as string).visitor_id).toBe(
    JSON.parse(calls[1][1]!.body as string).visitor_id,
  );
  window.removeEventListener("ashare-auth-required", auth);
});
