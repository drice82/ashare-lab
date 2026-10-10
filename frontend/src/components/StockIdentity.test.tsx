import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import StockIdentity from "./StockIdentity";

it("名称来自任务固定版本和日期，代码始终展示", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ items: [{ stock_code: "000001.SZ", name: "测试证券" }] }),
    ),
  );
  render(
    <StockIdentity code="000001.SZ" date="2024-06-03" version="pinned-basic" />,
  );
  expect(await screen.findByText("测试证券 · 000001.SZ")).toBeVisible();
  const url = new URL(
    String(vi.mocked(fetch).mock.calls[0][0]),
    "http://localhost",
  );
  expect(url.searchParams.get("data_version")).toBe("pinned-basic");
  expect(url.searchParams.get("as_of_date")).toBe("2024-06-03");
});

it("缺少固定版本时不读取当前名称", () => {
  vi.stubGlobal("fetch", vi.fn());
  render(<StockIdentity code="000001.SZ" date="2024-06-03" />);
  expect(screen.getByText("名称未知 · 000001.SZ")).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});
