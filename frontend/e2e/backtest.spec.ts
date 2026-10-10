import { expect, test } from "@playwright/test";

test("单股回测策略、真实API任务、历史恢复、CSV及移动菜单", async ({ page }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/backtest");
  await expect(page.getByRole("heading", { name: "单股回测", exact: true })).toBeVisible();
  await expect(page.getByText(/测试数据模式/)).toBeVisible({ timeout: 30000 });
  await expect(page.getByLabel("初始资金（元）")).toHaveValue("100000");
  await page.getByLabel("股票代码", { exact: true }).fill("cszq1");
  await page.getByRole("option").filter({ hasText: "000001.SZ" }).click();
  await page.getByLabel("开始交易日", { exact: true }).fill("2024-06-03");
  await page.getByLabel("结束交易日", { exact: true }).fill("2024-06-28");
  for (const strategy of ["buy_hold", "ma_cross"]) {
    await page.getByLabel("策略", { exact: true }).selectOption(strategy);
    const submitted = page.waitForResponse((r) => r.url().endsWith("/backtest/run") && r.request().method() === "POST");
    await page.getByRole("button", { name: "开始回测", exact: true }).click();
    const response = await submitted;
    expect(response.status()).toBe(202);
    const job = (await response.json()) as { id: string };
    await expect(page.getByText(`任务 ${job.id.slice(0, 8)}`, { exact: false })).toContainText("已完成", { timeout: 30000 });
    await expect(page.getByText("最大回撤", { exact: true })).toBeVisible();
    await expect(page.getByLabel("总资产与累计投入曲线")).toBeVisible();
    if (strategy === "buy_hold") await expect(page.getByRole("cell", { name: "买入", exact: true })).toBeVisible();
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出完整 CSV", exact: true }).click();
    expect((await download).suggestedFilename()).toBe(`backtest-${job.id}.csv`);
    await page.reload();
    await expect(page.getByLabel("策略", { exact: true })).toHaveValue(strategy);
    await expect(page.getByLabel("股票代码", { exact: true })).toHaveValue("000001.SZ");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "单股回测", exact: true }).click();
  await expect(page.getByRole("heading", { name: "单股回测", exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/backtest-mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});
