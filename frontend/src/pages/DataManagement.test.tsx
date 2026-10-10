import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import DataManagement from "./DataManagement";
import { MemoryRouter } from "react-router-dom";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/data/status"))
        return Response.json({
          token_configured: true,
          fixture_mode: false,
          datasets: [],
          missing_trade_dates: [],
          incremental_schedule: {
            enabled: true,
            schedule: "每天 16:00",
            timezone: "Asia/Shanghai",
            next_run: "2026-10-10T16:00:00+08:00",
            last_completed: null,
            latest_job: null,
            limitation: "增量同步不包含分红与历史名称",
          },
          dividend_schedule: {
            enabled: true,
            schedule: "每天 01:00",
            timezone: "Asia/Shanghai",
            next_run: "2026-10-10T01:00:00+08:00",
            last_completed: null,
            latest_job: { id: "dividend-last", status: "partial" },
            limitation: "已有分红继续使用，未确认事件保持未知",
          },
          namechange_schedule: {
            enabled: true,
            schedule: "每月第一个星期六 05:00",
            timezone: "Asia/Shanghai",
            next_run: "2026-11-07T05:00:00+08:00",
            last_completed: null,
            latest_job: null,
            limitation: "月内新增或撤销 ST 可能尚未收录",
          },
        });
      if (url.endsWith("/data/sync") && options?.method === "POST")
        return Response.json({ id: "names-1" });
      if (url.includes("/data/sync/names-1"))
        return Response.json({
          id: "names-1",
          status: "queued",
          progress: 0,
          logs: [],
        });
      return Response.json([]);
    }),
  );
});

it("显示月度计划并独立提交名称任务，不使用行情日期", async () => {
  render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "test-management" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  await screen.findByText(/每月第一个星期六 05:00/);
  expect(await screen.findByText(/月内新增或撤销 ST/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "立即更新历史名称" }));
  await waitFor(() => {
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/data/sync"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      mode: "namechange",
      history_refresh: "incremental",
    });
  });
});

it("显示每日分红计划并提交独立分红，不使用行情日期", async () => {
  render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "test-management" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  await screen.findByText(/每天 01:00/);
  expect(await screen.findByText(/最近任务：部分完成/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "立即更新分红" }));
  await waitFor(() => {
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).endsWith("/data/sync"));
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      mode: "dividend",
      history_refresh: "incremental",
    });
  });
});

it("显示收盘后每日增量计划", async () => {
  render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "test-management" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  await screen.findByText(/每天 16:00/);
  expect(screen.getByText(/增量同步不包含分红与历史名称/)).toBeInTheDocument();
});

it("错误密码保持锁定，重新挂载需要重新输入", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    Response.json(
      { code: "data_management_locked", message: "数据管理密码缺失或错误" },
      { status: 403 },
    ),
  );
  const view = render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "wrong" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  await screen.findByText("数据管理密码缺失或错误");
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "test-management" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  await screen.findByText(/每天 16:00/);
  for (const [, options] of vi.mocked(fetch).mock.calls.slice(1)) {
    expect(
      (options?.headers as Record<string, string>)[
        "X-Data-Management-Password"
      ],
    ).toBe("test-management");
  }
  view.unmount();
  render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  expect(screen.getByLabelText("数据管理密码")).toHaveValue("");
});

it("认证失效锁回页面并停止轮询", async () => {
  render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "test-management" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  await screen.findByText(/每天 16:00/);
  window.dispatchEvent(new Event("ashare-data-locked"));
  await screen.findByLabelText("数据管理密码");
  expect(screen.queryByText("本地数据集")).not.toBeInTheDocument();
});

it("同步任务独立页签，解锁后主页面不读取历史", async () => {
  render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "test-management" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  await screen.findByText(/每天 16:00/);
  expect(
    vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/jobs?")),
  ).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "同步任务" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) => String(url).includes("/jobs?kind=sync")),
    ).toBe(true),
  );
  expect(screen.getByText("本地数据集")).not.toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "数据管理" }));
  expect(screen.getByText("本地数据集")).toBeVisible();
});

it("数据管理保留版本编号，同步恢复在历史页签操作", async () => {
  const base = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    if (String(url).endsWith("/data/status")) {
      const body = await (await base(url, options)).json();
      body.datasets = [
        {
          dataset: "daily",
          version_id: "version-original",
          rows: 1,
          start_date: "2024-06-03",
          end_date: "2024-06-28",
          source: "fixture",
          updated_at: "2024-06-28",
        },
      ];
      return Response.json(body);
    }
    if (String(url).includes("/jobs?"))
      return Response.json([
        {
          id: "sync-old",
          kind: "sync",
          status: "failed",
          progress: 0.5,
          payload: { mode: "incremental" },
          created_at: "2024-06-28",
          result: null,
        },
      ]);
    if (String(url).endsWith("/data/sync/sync-old"))
      return Response.json({
        id: "sync-old",
        status: "failed",
        progress: 0.5,
        logs: [{ level: "info", at: "2024-06-28", message: "同步中断" }],
      });
    return base(url, options);
  });
  render(
    <MemoryRouter>
      <DataManagement />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("数据管理密码"), {
    target: { value: "test-management" },
  });
  fireEvent.click(screen.getByRole("button", { name: "解锁数据管理" }));
  expect(await screen.findByText("daily · version-")).toHaveAttribute(
    "title",
    "version-original",
  );
  fireEvent.click(screen.getByRole("tab", { name: "同步任务" }));
  fireEvent.click(await screen.findByRole("button", { name: "查看日志" }));
  expect(await screen.findByText(/同步中断/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "从断点恢复" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([url, options]) =>
            String(url).endsWith("/sync-old/resume") &&
            options?.method === "POST",
        ),
    ).toBe(true),
  );
  expect(screen.getByRole("tab", { name: "同步任务" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
