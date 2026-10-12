import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import StockDetail from "./StockDetail";

it("详情移除通知订阅入口，保留返回筛选", () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ factors: [] })));
  render(<MemoryRouter initialEntries={["/stock/000001.SZ"]}>
    <Routes><Route path="/stock/:code" element={<StockDetail />} /></Routes>
  </MemoryRouter>);
  expect(screen.queryByRole("link", { name: "订阅信号通知" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "返回筛选" })).toHaveAttribute("href", "/screening");
});
