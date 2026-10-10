import { expect, test } from "@playwright/test";

test("月度定投真实前后端流程、历史恢复和 CSV", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/dca");
  await expect(page.getByRole("heading", { name: "定投模拟" })).toBeVisible();
  await expect(page.getByText(/测试数据模式/)).toBeVisible();
  await expect(page.locator("details").first()).not.toHaveAttribute("open", "");
  await page.getByLabel("股票代码", { exact: true }).fill("cszq1");
  await page.getByRole("option").filter({ hasText: "000001.SZ" }).click();
  await expect(page.getByLabel("股票代码", { exact: true })).toHaveValue(
    "000001.SZ",
  );
  await page.getByLabel("开始交易日", { exact: true }).fill("2024-06-03");
  await page.getByLabel("结束交易日", { exact: true }).fill("2024-06-28");
  await page.getByLabel("每月投入日期", { exact: true }).fill("3");
  const submitted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/dca/run") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始模拟", exact: true }).click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  const job = (await response.json()) as { id: string };
  await expect(
    page.getByText(`任务 ${job.id.slice(0, 8)}`, { exact: false }),
  ).toContainText("已完成", { timeout: 30000 });
  await expect(
    page.getByRole("cell", { name: "买入", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("总资产与累计投入曲线")).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
  await expect(page.getByRole("table", { name: "定投汇总" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "年化收益（XIRR）" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "最大回撤" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "买入", exact: true }).locator("span")).toHaveClass(/bg-violet-100/);
  await expect(
    page.getByText("594.98 元", { exact: true }).first(),
  ).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出完整 CSV", exact: true }).click();
  expect((await download).suggestedFilename()).toBe(`dca-${job.id}.csv`);
  await page.reload();
  await expect(page.getByLabel("股票代码", { exact: true })).toHaveValue(
    "000001.SZ",
  );
  await expect(
    page.getByRole("cell", { name: "买入", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/dca-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "定投模拟", exact: true }).click();
  await expect(page.getByRole("heading", { name: "定投模拟" })).toBeVisible();
  await page.getByRole("button").filter({ hasText: job.id.slice(0, 8) }).click();
  await expect(page.getByRole("table", { name: "定投汇总" })).toBeVisible();
  expect(await page.getByRole("table", { name: "定投汇总" }).evaluate((table) => table.scrollWidth <= table.clientWidth)).toBe(true);
  await page.screenshot({
    path: "test-results/dca-mobile.png",
    fullPage: true,
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});
