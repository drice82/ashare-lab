import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./pages/Knowledge", () => ({ default: () => <p>知识页</p> }));
vi.mock("./pages/Watchlists", () => ({ default: () => <p>自选页</p> }));
vi.mock("./pages/Notifications", () => ({
  default: ({ unsubscribe }: { unsubscribe?: boolean }) => (
    <p>{unsubscribe ? "退订页" : "通知页"}</p>
  ),
}));

it("keeps data management in the menu footer with distinct research icons", async () => {
  render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <App />
    </MemoryRouter>,
  );
  await screen.findByText("知识页");
  expect(screen.queryByText("个人研究 · 本地数据")).not.toBeInTheDocument();
  expect(screen.getByRole("contentinfo")).toHaveClass("text-center", "w-full");
  const nav = screen.getByRole("navigation", { name: "工作空间" });
  expect(
    within(nav).queryByRole("link", { name: "后台管理" }),
  ).not.toBeInTheDocument();
  const data = screen.getByRole("link", { name: "后台管理" });
  expect(data).toHaveAttribute("href", "/data");
  expect(data).toHaveClass("text-[10px]");
  const icons = ["定投模拟", "单股回测", "组合回测"].map((name) =>
    within(nav)
      .getByRole("link", { name })
      .querySelector("svg")
      ?.getAttribute("class"),
  );
  expect(new Set(icons).size).toBe(3);
  fireEvent.click(screen.getByRole("button", { name: "打开导航" }));
  expect(screen.getByRole("complementary")).toHaveClass("translate-x-0");
  fireEvent.click(data);
  expect(screen.getByRole("complementary")).toHaveClass("-translate-x-full");
  expect(
    screen.getByRole("button", { name: "解锁后台管理" }),
  ).toBeInTheDocument();
});

it("groups research and personal pages into separate accessible spaces", async () => {
  render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <App />
    </MemoryRouter>,
  );
  await screen.findByText("知识页");
  const work = screen.getByRole("navigation", { name: "工作空间" });
  const user = screen.getByRole("navigation", { name: "用户空间" });
  expect(
    within(work).getAllByRole("link").map((link) => link.textContent),
  ).toEqual(["股票筛选", "定投模拟", "单股回测", "组合回测", "知识库"]);
  expect(
    within(user).getAllByRole("link").map((link) => link.textContent),
  ).toEqual(["信号通知", "自选列表"]);
  expect(within(work).getByRole("link", { name: "知识库" })).toHaveAttribute(
    "aria-current", "page",
  );
  expect(screen.getByRole("complementary")).toHaveClass("overflow-y-auto");
  for (const [name, path, page] of [
    ["信号通知", "/notifications", "通知页"],
    ["自选列表", "/watchlists", "自选页"],
  ]) {
    const link = within(user).getByRole("link", { name });
    expect(link).toHaveAttribute("href", path);
    fireEvent.click(screen.getByRole("button", { name: "打开导航" }));
    fireEvent.click(link);
    await screen.findByText(page);
    expect(link).toHaveAttribute("aria-current", "page");
    expect(within(work).getByRole("link", { name: "知识库" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByRole("complementary")).toHaveClass("-translate-x-full");
  }
});

it("preserves direct access to the notification unsubscribe route", async () => {
  render(
    <MemoryRouter initialEntries={["/notifications/unsubscribe"]}>
      <App />
    </MemoryRouter>,
  );
  await screen.findByText("退订页");
  expect(
    within(screen.getByRole("navigation", { name: "用户空间" })).getByRole(
      "link", { name: "信号通知" },
    ),
  ).toHaveAttribute("aria-current", "page");
});


it("collapses and restores the desktop sidebar without losing the page", async () => {
  render(<MemoryRouter initialEntries={["/knowledge"]}><App /></MemoryRouter>);
  await screen.findByText("知识页");
  const sidebar = screen.getByRole("complementary");
  const toggle = screen.getByRole("button", { name: "收起侧边栏" });
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(toggle).toHaveAttribute("aria-controls", sidebar.id);
  expect(screen.getByRole("banner").parentElement).toHaveClass("lg:ml-60");
  fireEvent.click(toggle);
  expect(sidebar).toHaveClass("lg:hidden");
  expect(screen.getByRole("banner").parentElement).not.toHaveClass("lg:ml-60");
  expect(screen.getByRole("button", { name: "展开侧边栏" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText("知识页")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "打开导航" }));
  expect(sidebar).toHaveClass("translate-x-0");
  fireEvent.click(screen.getByRole("button", { name: "关闭导航遮罩" }));
  fireEvent.click(screen.getByRole("button", { name: "展开侧边栏" }));
  expect(sidebar).not.toHaveClass("lg:hidden");
  expect(screen.getByRole("banner").parentElement).toHaveClass("lg:ml-60");
});
