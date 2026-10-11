import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./pages/Knowledge", () => ({ default: () => <p>知识页</p> }));

it("keeps data management in the menu footer with distinct research icons", async () => {
  render(
    <MemoryRouter initialEntries={["/knowledge"]}>
      <App />
    </MemoryRouter>,
  );
  await screen.findByText("知识页");
  expect(screen.queryByText("个人研究 · 本地数据")).not.toBeInTheDocument();
  expect(screen.getByRole("contentinfo")).toHaveClass("text-center", "w-full");
  const nav = screen.getByRole("navigation");
  expect(
    within(nav).queryByRole("link", { name: "数据管理" }),
  ).not.toBeInTheDocument();
  const data = screen.getByRole("link", { name: "数据管理" });
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
    screen.getByRole("button", { name: "解锁数据管理" }),
  ).toBeInTheDocument();
});
