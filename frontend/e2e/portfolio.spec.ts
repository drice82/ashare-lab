import { expect, test } from "@playwright/test";

test("组合真实API/Worker、持仓、CSV、历史恢复及移动页面", async ({ page }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/portfolio");
  await expect(
    page.getByRole("heading", { name: "组合回测", exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/测试数据模式/)).toBeVisible();
  await page
    .getByLabel("组合股票", { exact: true })
    .fill("000001.SZ 000002.SZ");
  await page.getByLabel("组合股票", { exact: true }).press("Enter");
  await expect(
    page.getByLabel("000001.SZ 策略", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("开始交易日", { exact: true }).fill("2024-06-03");
  await page.getByLabel("结束交易日", { exact: true }).fill("2024-06-28");
  await page.getByLabel("资金分配", { exact: true }).selectOption("custom");
  await page.getByLabel("000001.SZ 权重百分比", { exact: true }).fill("30");
  await page.getByLabel("000002.SZ 权重百分比", { exact: true }).fill("70");
  await page
    .getByLabel("000001.SZ 策略", { exact: true })
    .selectOption("ma_cross");
  await page.getByLabel("000001.SZ 短周期", { exact: true }).fill("2");
  await page.getByLabel("000001.SZ 长周期", { exact: true }).fill("5");
  const submitted = page.waitForResponse(
    (r) =>
      r.url().endsWith("/portfolio/run") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "开始组合回测", exact: true }).click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  const job = await response.json();
  await expect(
    page.getByRole("table", { name: "组合回测汇总", exact: true }),
  ).toContainText("完整");
  await expect(
    page.getByRole("tab", { name: "回测结果", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("img", { name: "组合累计收益率曲线", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "组合回撤曲线", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("table", { name: "每日持仓", exact: true })
      .locator("tbody tr"),
  ).toHaveCount(2);
  await page.getByLabel("持仓日期", { exact: true }).selectOption("2024-06-04");
  await expect(
    page.getByRole("table", { name: "每日持仓", exact: true }),
  ).toContainText("2024-06-04");
  await page.getByLabel("查看股票", { exact: true }).selectOption("000002.SZ");
  await expect(
    page.getByRole("img", { name: "000002.SZ股价与买卖点", exact: true }),
  ).toBeVisible();
  await page.getByLabel("查看账户", { exact: true }).selectOption("benchmark");
  await expect(
    page.getByRole("table", { name: "组合交易账本", exact: true }),
  ).toContainText("买入");
  for (const [view, label] of [
    ["summary", "指标汇总"],
    ["curves", "每日曲线"],
    ["positions", "每日持仓"],
    ["ledger", "完整账本"],
  ]) {
    const download = page.waitForEvent("download");
    await page
      .getByRole("button", { name: `导出${label} CSV`, exact: true })
      .click();
    expect((await download).suggestedFilename()).toBe(
      `portfolio-${job.id}-${view}.csv`,
    );
  }
  await page.reload();
  await expect(
    page.getByRole("table", { name: "组合回测汇总", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "组合回测", exact: true }).click();
  await expect(page.getByLabel("000001.SZ 策略", { exact: true })).toHaveValue(
    "ma_cross",
  );
  await expect(
    page.getByLabel("000001.SZ 权重百分比", { exact: true }),
  ).toHaveValue("30");
  await page.getByRole("tab", { name: "历史任务", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: job.id.slice(0, 8) })
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await expect(
    page.getByRole("img", { name: "组合累计收益率曲线", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/portfolio-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page
        .locator("aside")
        .evaluate((element) => element.getBoundingClientRect().right <= 0),
    )
    .toBe(true);
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    )
    .toBe(true);
  await page.screenshot({
    path: "test-results/portfolio-mobile.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "历史任务", exact: true }).click();
  await page
    .getByRole("button", { name: "删除全部历史任务", exact: true })
    .click();
  await page.getByRole("button", { name: "保留任务", exact: true }).click();
  await expect(
    page.getByRole("row").filter({ hasText: job.id.slice(0, 8) }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "删除全部历史任务", exact: true })
    .click();
  await page.getByRole("button", { name: "确认删除全部", exact: true }).click();
  await expect(page.getByText(/已删除 .* 个历史任务/)).toBeVisible();
  await page.getByRole("tab", { name: "组合回测", exact: true }).click();
  await expect(page.getByLabel("000001.SZ 策略", { exact: true })).toHaveValue(
    "ma_cross",
  );
  expect(errors).toEqual([]);
});
