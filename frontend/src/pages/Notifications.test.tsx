import {
  fireEvent,
  act,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import Notifications from "./Notifications";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      let value: unknown = [];
      if (url.endsWith("/options"))
        value = {
          channels: { webhook: true, email: false },
          fixture_mode: true,
          auto_send: false,
          subscription_limit: 20,
        };
      if (url.endsWith("/session")) value = { owner_id: "owner" };
      if (url.includes("/deliveries")) value = { items: [], total: 0 };
      return { ok: true, json: async () => value };
    }),
  );
});

it("shows fixture and unavailable SMTP, requires a verified selected channel", async () => {
  render(
    <MemoryRouter initialEntries={["/notifications?stock_code=000001.SZ"]}>
      <Notifications />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/fixture 数据/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "账号与渠道" }));
  expect(screen.getByText(/邮件渠道尚未启用/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "订阅管理" }));
  expect(screen.getByLabelText("股票代码")).toHaveValue("000001.SZ");
  expect(screen.getByRole("button", { name: "创建订阅" })).toBeDisabled();
});

it("unsubscribe requires an explicit POST and strips the fragment", async () => {
  window.location.hash = "secret-token";
  render(
    <MemoryRouter initialEntries={["/notifications/unsubscribe"]}>
      <Notifications unsubscribe />
    </MemoryRouter>,
  );
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认退订邮件" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/notifications/email/unsubscribe",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ token: "secret-token" }),
      }),
    ),
  );
  expect(window.location.hash).toBe("");
});

it("creates a two-channel subscription, pauses it and shows independent delivery outcomes", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    let value: unknown = [];
    if (url.endsWith("/options"))
      value = {
        channels: { webhook: true, email: true },
        fixture_mode: true,
        auto_send: false,
        subscription_limit: 20,
      };
    if (url.endsWith("/session")) value = { owner_id: "owner" };
    if (url.endsWith("/channels"))
      value = [
        { type: "webhook", verified: true, status: "active", target: "加密" },
        {
          type: "email",
          verified: true,
          status: "active",
          target: "t***@example.org",
        },
      ];
    if (url.endsWith("/subscriptions"))
      value = [
        {
          id: "sub-id",
          stock_code: "000001.SZ",
          strategy: { id: "ma_cross", short_window: 20, long_window: 60 },
          channels: ["webhook", "email"],
          directions: ["buy", "sell"],
          status: "active",
          last_check: "2026-10-09",
          waiting_reason: null,
          effective_after: "2026-10-08",
        },
      ];
    if (url.includes("/deliveries"))
      value = {
        total: 2,
        items: [
          {
            id: "b",
            trade_date: "2026-10-09",
            direction: "buy",
            channel: "webhook",
            status: "accepted",
            attempts: 1,
            error: null,
            outcome: {
              reason: "cross",
              strategy_version: "1",
              data_versions: {},
            },
          },
          {
            id: "e",
            trade_date: "2026-10-09",
            direction: "buy",
            channel: "email",
            status: "pending",
            attempts: 1,
            error: "network_error",
            outcome: {
              reason: "cross",
              strategy_version: "1",
              data_versions: {},
            },
          },
        ],
      };
    return { ok: true, json: async () => value };
  });
  vi.stubGlobal("fetch", fetchMock);
  render(
    <MemoryRouter initialEntries={["/notifications?stock_code=000001.SZ"]}>
      <Notifications />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "创建订阅" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "创建订阅" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/notifications/subscriptions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          stock_code: "000001.SZ",
          strategy: { id: "ma_cross", short_window: 20, long_window: 60 },
          directions: ["buy", "sell"],
          channels: ["webhook", "email"],
        }),
      }),
    ),
  );
  fireEvent.click(screen.getByRole("tab", { name: "发送记录" }));
  expect(await screen.findByText(/网络错误，按计划重试/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "订阅管理" }));
  fireEvent.click(screen.getByRole("button", { name: "暂停" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/notifications/subscriptions/sub-id",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: "paused" }),
      }),
    ),
  );
});

it("logs in by email code without a password or recovery code and signs out", async () => {
  let authenticated = false;
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    let value: unknown = [];
    if (url.endsWith("/options"))
      value = {
        channels: { webhook: true, email: true },
        fixture_mode: true,
        auto_send: false,
        subscription_limit: 20,
        auth: { email_available: true, session_days: 180 },
      };
    if (url.endsWith("/session") && init?.method === "DELETE")
      authenticated = false;
    else if (url.endsWith("/session") && !authenticated)
      return {
        ok: false,
        status: 401,
        json: async () => ({ message: "请使用邮箱验证码登录" }),
      };
    if (url.endsWith("/auth/email/verification")) value = { resend_after: 60 };
    if (url.endsWith("/auth/email/verify")) authenticated = true;
    if (url.endsWith("/auth/email/verify") || url.endsWith("/session"))
      value = {
        owner_id: "owner",
        email: "a***@example.org",
        expires_at: "2027-01-01",
      };
    if (url.endsWith("/channels"))
      value = [
        {
          type: "email",
          verified: true,
          status: "active",
          target: "a***@example.org",
        },
      ];
    if (url.includes("/deliveries")) value = { items: [], total: 0 };
    return { ok: true, json: async () => value };
  });
  vi.stubGlobal("fetch", mock);
  render(
    <MemoryRouter initialEntries={["/notifications?stock_code=000001.SZ"]}>
      <Notifications />
    </MemoryRouter>,
  );
  expect(await screen.findByLabelText("登录邮箱")).toBeInTheDocument();
  expect(screen.queryByLabelText("恢复码")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "创建免注册身份" }),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("登录邮箱"), {
    target: { value: "alice@example.org" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送登录验证码" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /秒后重发/ })).toBeDisabled(),
  );
  fireEvent.change(screen.getByLabelText("登录验证码"), {
    target: { value: "012345" },
  });
  fireEvent.click(screen.getByRole("button", { name: "验证码登录" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "创建订阅" })).toBeEnabled(),
  );
  expect(screen.getAllByText(/a\*\*\*@example.org/).length).toBeGreaterThan(0);
  expect(mock).toHaveBeenCalledWith(
    "/api/v1/notifications/auth/email/verify",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ address: "alice@example.org", code: "012345" }),
    }),
  );
  fireEvent.click(screen.getByRole("tab", { name: "账号与渠道" }));
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  expect(await screen.findByLabelText("登录邮箱")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "创建订阅" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("我的订阅 · 0 / 20")).not.toBeInTheDocument();
});

it("clears private data on a notification 401 without requesting the shared access key", async () => {
  let expired = false;
  const shared = vi.fn();
  window.addEventListener("ashare-auth-required", shared);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      let value: unknown = [];
      if (url.endsWith("/options"))
        value = {
          channels: { webhook: true, email: true },
          fixture_mode: true,
          auto_send: false,
          subscription_limit: 20,
        };
      if (url.endsWith("/session"))
        value = { owner_id: "owner", email: "a***@example.org" };
      if (url.endsWith("/channels"))
        value = [
          {
            type: "email",
            verified: true,
            status: "active",
            target: "a***@example.org",
          },
        ];
      if (url.includes("/deliveries")) value = { items: [], total: 0 };
      if (expired && url.endsWith("/subscriptions"))
        return {
          ok: false,
          status: 401,
          json: async () => ({ message: "登录已过期，请重新登录" }),
        };
      return { ok: true, json: async () => value };
    }),
  );
  render(
    <MemoryRouter initialEntries={["/notifications?stock_code=000001.SZ"]}>
      <Notifications />
    </MemoryRouter>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "创建订阅" })).toBeEnabled(),
  );
  expired = true;
  fireEvent.click(screen.getByRole("button", { name: "创建订阅" }));
  expect(await screen.findByLabelText("登录邮箱")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "创建订阅" }),
  ).not.toBeInTheDocument();
  expect(shared).not.toHaveBeenCalled();
  window.removeEventListener("ashare-auth-required", shared);
});

it("separates panels, retains the draft and resolves names for selected stocks", async () => {
  const original = fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/instruments"))
        return {
          ok: true,
          json: async () => ({
            items: [{ stock_code: "000001.SZ", stock_name: "平安银行" }],
          }),
        };
      return original(url, init);
    }),
  );
  render(
    <MemoryRouter initialEntries={["/notifications?stock_code=000001.SZ"]}>
      <Notifications />
    </MemoryRouter>,
  );
  expect(await screen.findByText("平安银行 · 000001.SZ")).toBeVisible();
  fireEvent.change(screen.getByLabelText("短周期"), {
    target: { value: "10" },
  });
  fireEvent.click(screen.getByRole("tab", { name: "发送记录" }));
  expect(screen.getByRole("heading", { name: "发送记录" })).toBeVisible();
  expect(screen.getByLabelText("短周期")).not.toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "账号与渠道" }));
  expect(screen.getByRole("heading", { name: "Webhook 通知" })).toBeVisible();
  expect(screen.getByText(/api.day.app/)).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Bark 设备" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "订阅管理" }));
  expect(screen.getByLabelText("短周期")).toHaveValue(10);
});

it("shows meaningful strategy parameters and distinct subscription states", async () => {
  const original = fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/subscriptions"))
        return {
          ok: true,
          json: async () =>
            ["active", "paused"].map((status) => ({
              id: status,
              stock_code: "000001.SZ",
              stock_name: "平安银行",
              status,
              strategy: {
                id: "rsi_reversal",
                period: 14,
                lower: 30,
                upper: 70,
              },
              directions: ["buy", "sell"],
              channels: ["email"],
              last_check: null,
              effective_after: "2026-10-09",
              waiting_reason: null,
            })),
        };
      return original(url, init);
    }),
  );
  render(
    <MemoryRouter>
      <Notifications />
    </MemoryRouter>,
  );
  expect(await screen.findAllByText("平安银行")).toHaveLength(2);
  expect(
    screen.getAllByText(/周期 14 个交易日 · 下阈值 30 · 上阈值 70/),
  ).toHaveLength(2);
  expect(screen.queryByText(/\{"id":/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("启用的订阅")).toHaveClass("border-emerald-200");
  expect(screen.getByLabelText("暂停的订阅")).toHaveClass("bg-muted/50");
});

it("dismisses success notices after four seconds and preserves unsubscribe confirmation", async () => {
  vi.useFakeTimers();
  try {
    render(
      <MemoryRouter initialEntries={["/notifications?stock_code=000001.SZ"]}>
        <Notifications />
      </MemoryRouter>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole("tab", { name: "账号与渠道" }));
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "已退出当前设备的登录",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("formats all five strategy definitions with trading-day and multiplier units", async () => {
  const { strategySummary } = await import("./Notifications");
  expect(
    strategySummary({ id: "ma_cross", short_window: 10, long_window: 60 }),
  ).toBe("短周期 10 个交易日 · 长周期 60 个交易日");
  expect(
    strategySummary({
      id: "macd_cross",
      fast_window: 12,
      slow_window: 26,
      signal_window: 9,
    }),
  ).toBe("快周期 12 个交易日 · 慢周期 26 个交易日 · 信号周期 9 个交易日");
  expect(
    strategySummary({ id: "rsi_reversal", period: 14, lower: 30, upper: 70 }),
  ).toBe("周期 14 个交易日 · 下阈值 30 · 上阈值 70");
  expect(
    strategySummary({
      id: "donchian_breakout",
      entry_window: 20,
      exit_window: 10,
    }),
  ).toBe("买入窗口 20 个交易日 · 卖出窗口 10 个交易日");
  expect(
    strategySummary({ id: "bollinger_reversion", period: 20, deviations: 2.5 }),
  ).toBe("周期 20 个交易日 · 标准差倍数 2.5 倍");
});

it("orders email first, exposes channel states and sends a selected sample direction", async () => {
  const mock = vi.fn(async (url: string) => {
    let value: unknown = [];
    if (url.endsWith("/options"))
      value = {
        channels: { email: true, webhook: true },
        fixture_mode: true,
        auto_send: false,
        subscription_limit: 20,
      };
    if (url.endsWith("/session"))
      value = { owner_id: "owner", email: "a@example.org" };
    if (url.endsWith("/channels"))
      value = [
        {
          type: "email",
          verified: true,
          status: "active",
          target: "a***@example.org",
        },
        {
          type: "webhook",
          verified: false,
          status: "disabled",
          target: "已加密保存",
        },
      ];
    if (url.endsWith("/subscriptions"))
      value = [
        {
          id: "sample-sub",
          stock_code: "000001.SZ",
          stock_name: "示例证券",
          strategy: { id: "ma_cross", short_window: 20, long_window: 60 },
          channels: ["email", "webhook"],
          directions: ["buy", "sell"],
          status: "paused",
          last_check: null,
          effective_after: "2026-10-09",
        },
      ];
    if (url.includes("/deliveries")) value = { items: [], total: 0 };
    if (url.endsWith("/sample-sub/test"))
      value = {
        results: [
          { channel: "email", status: "accepted" },
          {
            channel: "webhook",
            status: "skipped",
            message: "渠道未启用或未验证",
          },
        ],
      };
    return { ok: true, json: async () => value };
  });
  vi.stubGlobal("fetch", mock);
  render(
    <MemoryRouter>
      <Notifications />
    </MemoryRouter>,
  );
  await screen.findByText(/fixture 数据/);
  fireEvent.click(screen.getByRole("tab", { name: "账号与渠道" }));
  const headings = screen
    .getAllByRole("heading", { level: 2 })
    .map((el) => el.textContent);
  expect(headings.indexOf("邮件渠道")).toBeLessThan(
    headings.indexOf("Webhook 通知"),
  );
  expect(screen.getByText("已启用")).toHaveClass("bg-emerald-50");
  expect(screen.getByText("已停用")).toHaveClass("bg-slate-100");
  expect(screen.getByRole("button", { name: "启用 Webhook" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "停用邮件通知" })).toBeEnabled();
  fireEvent.click(screen.getByRole("tab", { name: "订阅管理" }));
  const channelChoices = screen
    .getAllByRole("checkbox")
    .filter((el) =>
      ["邮件", "Webhook"].includes(el.parentElement?.textContent?.trim() ?? ""),
    );
  expect(
    channelChoices.map((el) => el.parentElement?.textContent?.trim()),
  ).toEqual(["邮件", "Webhook"]);
  fireEvent.click(screen.getByRole("button", { name: "测试通知" }));
  fireEvent.click(screen.getByRole("button", { name: "测试卖出信号" }));
  await waitFor(() =>
    expect(mock).toHaveBeenCalledWith(
      "/api/v1/notifications/subscriptions/sample-sub/test",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ direction: "sell" }),
      }),
    ),
  );
  expect(await screen.findByText(/邮件：测试通知已发送/)).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "发送记录" }));
  expect(
    screen.queryByRole("option", { name: "Bark（旧渠道）" }),
  ).not.toBeInTheDocument();
});

it("marks sample delivery records without presenting them as real signals", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/deliveries"))
        return {
          ok: true,
          json: async () => ({
            items: [
              {
                id: "sample",
                subscription_id: "sub",
                is_test: true,
                created_at: "2026-10-11T12:00:00Z",
                trade_date: "示例日期",
                direction: "buy",
                channel: "email",
                status: "unknown",
                attempts: 1,
                error: null,
                outcome: {
                  stock_code: "000001.SZ",
                  stock_name: "示例证券",
                  strategy: {
                    id: "ma_cross",
                    short_window: 20,
                    long_window: 60,
                  },
                  reason: "示例信号，仅用于测试通知",
                  strategy_version: "1",
                  data_versions: {},
                },
              },
            ],
            total: 1,
          }),
        };
      return original(url, init);
    }),
  );
  render(
    <MemoryRouter initialEntries={["/notifications?tab=records"]}>
      <Notifications />
    </MemoryRouter>,
  );
  expect(await screen.findByText("测试", { exact: true })).toBeVisible();
  expect(screen.getByText(/2026-10-11T12:00:00Z/)).toBeVisible();
  expect(screen.getByText(/发送状态未知/)).toBeVisible();
  expect(screen.getByText("示例内容")).toBeVisible();
  expect(screen.queryByText("触发依据及版本")).not.toBeInTheDocument();
});
