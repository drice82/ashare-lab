import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { UserSessionProvider, useUserSession } from "../userSession";
import { WatchlistProvider } from "../watchlists";
import Watchlists from "./Watchlists";

function mount() {
  return render(
    <MemoryRouter>
      <UserSessionProvider>
        <WatchlistProvider>
          <Watchlists />
        </WatchlistProvider>
      </UserSessionProvider>
    </MemoryRouter>,
  );
}
beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/notifications/session"))
        return Response.json({ message: "尚未登录" }, { status: 401 });
      if (url.endsWith("/notifications/options"))
        return Response.json({
          fixture_mode: true,
          auth: { email_available: true },
        });
      return Response.json({ items: [] });
    }),
  );
});

async function draft(
  name = "本机银行",
  codes = "000001.SZ 600000.SH 000001.SZ",
) {
  fireEvent.click(screen.getByRole("button", { name: "新建列表" }));
  fireEvent.change(screen.getByLabelText("列表名称"), {
    target: { value: name },
  });
  fireEvent.change(screen.getByLabelText("列表股票"), {
    target: { value: codes },
  });
  fireEvent.keyDown(screen.getByLabelText("列表股票"), { key: "Enter" });
}

it("匿名多个列表保存、刷新保留、去重、改名与删除", async () => {
  const first = mount();
  await screen.findByText(/当前列表保存在本机/);
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await screen.findByRole("button", { name: "本机银行 · 2只" });
  await draft("空列表", "");
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await screen.findByRole("button", { name: "空列表 · 0只" });
  first.unmount();
  mount();
  await screen.findByRole("button", { name: "本机银行 · 2只" });
  fireEvent.click(screen.getByRole("button", { name: "本机银行 · 2只" }));
  fireEvent.change(screen.getByLabelText("列表名称"), {
    target: { value: "改名" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await screen.findByRole("button", { name: "改名 · 2只" });
  fireEvent.click(screen.getByRole("button", { name: "删除列表" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "改名 · 2只" }),
    ).not.toBeInTheDocument(),
  );
});

it("本机保存失败保留草稿并告知无法持久保存", async () => {
  mount();
  await screen.findByText(/当前列表保存在本机/);
  await draft();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("quota");
  });
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await screen.findByText(/无法持久保存/);
  expect(screen.getByLabelText("列表名称")).toHaveValue("本机银行");
  expect(screen.queryByText("列表已保存")).not.toBeInTheDocument();
});

it("登录查询未完成时仍可手动维护本机列表", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/notifications/session"))
        return new Promise<Response>(() => {});
      return Response.json({ items: [] });
    }),
  );
  mount();
  expect(screen.getByRole("button", { name: "新建列表" })).toBeEnabled();
  await draft("离线本机");
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await screen.findByRole("button", { name: "离线本机 · 2只" });
});

it("账号保存失败不转存本机，401清空账号列表且不弹访问密钥", async () => {
  const required = vi.fn();
  window.addEventListener("ashare-auth-required", required);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => {
      if (url.endsWith("/notifications/session"))
        return Response.json({
          owner_id: "alice",
          email: "a***@example.org",
          expires_at: "2099-01-01",
        });
      if (url.endsWith("/watchlists")) {
        if (options?.method === "POST")
          return Response.json({ message: "服务暂不可用" }, { status: 503 });
        return Response.json([
          { id: "cloud", name: "账号银行", codes: ["000001.SZ"], revision: 1 },
        ]);
      }
      if (url.includes("/watchlists/cloud"))
        return Response.json({ message: "登录已过期" }, { status: 401 });
      return Response.json({ items: [] });
    }),
  );
  mount();
  await screen.findByRole("button", { name: "账号银行 · 1只" });
  await draft("保存失败");
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await screen.findByText("服务暂不可用");
  expect(localStorage.length).toBe(0);
  expect(screen.getByLabelText("列表名称")).toHaveValue("保存失败");
  fireEvent.click(screen.getByRole("button", { name: "账号银行 · 1只" }));
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "账号银行 · 1只" }),
    ).not.toBeInTheDocument(),
  );
  expect(required).not.toHaveBeenCalled();
  window.removeEventListener("ashare-auth-required", required);
});

it("登录后本机与账号列表独立，不自动上传或合并", async () => {
  const first = mount();
  await screen.findByText(/当前列表保存在本机/);
  await draft();
  fireEvent.click(screen.getByRole("button", { name: "保存列表" }));
  await screen.findByRole("button", { name: "本机银行 · 2只" });
  first.unmount();
  const stored = localStorage.getItem("ashare-watchlists-v1");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/notifications/session"))
        return Response.json({
          owner_id: "alice",
          email: "a***@example.org",
          expires_at: "2099-01-01",
        });
      if (url.endsWith("/watchlists"))
        return Response.json([
          { id: "cloud", name: "账号银行", codes: ["000001.SZ"], revision: 1 },
        ]);
      return Response.json({ items: [] });
    }),
  );
  mount();
  await screen.findByRole("button", { name: "账号银行 · 1只" });
  expect(
    screen.queryByRole("button", { name: "本机银行 · 2只" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "本机列表" }));
  await screen.findByRole("button", { name: "本机银行 · 2只" });
  expect(localStorage.getItem("ashare-watchlists-v1")).toBe(stored);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([, options]) => options?.method === "POST"),
  ).toBe(false);
});

it("退出后忽略晚到的账号列表响应", async () => {
  let finish!: (value: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/notifications/session"))
        return Response.json({
          owner_id: "alice",
          email: "a***@example.org",
          expires_at: "2099-01-01",
        });
      if (url.endsWith("/watchlists"))
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      return Response.json({ items: [] });
    }),
  );
  mount();
  await screen.findByText(/已登录/);
  await waitFor(() => expect(finish).toBeDefined());
  act(() => {
    window.dispatchEvent(new Event("ashare-user-session-expired"));
  });
  await act(async () => {
    finish(
      Response.json([
        {
          id: "late",
          name: "已退出账号列表",
          codes: ["000001.SZ"],
          revision: 1,
        },
      ]),
    );
  });
  expect(
    screen.queryByRole("button", { name: "已退出账号列表 · 1只" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/已登录/)).not.toBeInTheDocument();
});

it("旧账号晚到的401不会退出新账号", async () => {
  let finish!: (value: Response) => void;
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/notifications/session"))
        return Response.json({
          owner_id: "alice",
          email: "a***@example.org",
          expires_at: "2099-01-01",
        });
      if (url.endsWith("/watchlists")) {
        if (++reads === 1)
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        return Response.json([
          { id: "bob-list", name: "新账号列表", codes: [], revision: 1 },
        ]);
      }
      return Response.json({ items: [] });
    }),
  );
  function SwitchAccount() {
    const user = useUserSession();
    return (
      <button
        onClick={() =>
          user.adopt({
            owner_id: "bob",
            email: "b***@example.org",
            expires_at: "2099-01-01",
          })
        }
      >
        切换测试账号
      </button>
    );
  }
  render(
    <MemoryRouter>
      <UserSessionProvider>
        <WatchlistProvider>
          <SwitchAccount />
          <Watchlists />
        </WatchlistProvider>
      </UserSessionProvider>
    </MemoryRouter>,
  );
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "切换测试账号" }));
  await screen.findByRole("button", { name: "新账号列表 · 0只" });
  await act(async () => {
    finish(Response.json({ message: "旧会话失效" }, { status: 401 }));
  });
  expect(screen.getByText(/已登录：b\*\*\*@example.org/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "新账号列表 · 0只" }),
  ).toBeInTheDocument();
});
