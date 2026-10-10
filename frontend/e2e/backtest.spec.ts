import { expect, test } from "@playwright/test";

test("六策略对比、曲线、买卖点、真实Worker及历史恢复", async ({ page }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/backtest");
  await expect(page.getByText(/测试数据模式/)).toBeVisible();
  await expect(page.getByLabel(/数据版本/)).toHaveCount(0);
  const shortcuts = page.getByRole("button", { name: "模拟2年", exact: true });
  expect(
    await shortcuts.evaluate(
      (button) =>
        button.compareDocumentPosition(
          document.querySelector('input[aria-label="选择买入持有"]')!,
        ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ),
  ).toBeTruthy();
  await expect(
    page.getByRole("heading", { name: "单股回测", exact: true }),
  ).toBeVisible();
  for (const name of [
    "买入持有",
    "双均线交叉",
    "MACD交叉",
    "RSI超卖回升",
    "唐奇安突破",
    "布林带回归",
  ])
    await expect(
      page.getByRole("checkbox", { name: `选择${name}` }),
    ).toBeChecked();
  await page.getByLabel("股票代码", { exact: true }).fill("cszq1");
  await page.getByRole("option").filter({ hasText: "000001.SZ" }).click();
  await page.getByLabel("开始交易日", { exact: true }).fill("2024-06-03");
  await page.getByLabel("结束交易日", { exact: true }).fill("2024-06-28");
  const compactTitle = page.getByRole("checkbox", {name:"选择买入持有"}).locator("..");
  expect(await compactTitle.evaluate(label => label.getBoundingClientRect().height)).toBeLessThan(30);
  const strategyGrid = compactTitle.locator("../..");
  expect(await strategyGrid.evaluate(grid => getComputedStyle(grid).gridTemplateColumns.split(" ").length)).toBe(3);
  let lastJobId = "";
  for (const count of [6, 1]) {
    if (count === 1) await page.getByRole("tab", { name: "回测", exact: true }).click();
    if (count === 1)
      for (const name of [
        "买入持有",
        "MACD交叉",
        "RSI超卖回升",
        "唐奇安突破",
        "布林带回归",
      ])
        await page.getByRole("checkbox", { name: `选择${name}` }).uncheck();
    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/backtest/compare") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "开始回测", exact: true }).click();
    const response = await submitted;
    expect(response.status()).toBe(202);
    const job = await response.json();
    lastJobId = job.id;
    await expect(
      page.getByText(`任务 ${job.id.slice(0, 8)}`, { exact: false }),
    ).toContainText("已完成");
    await expect(
      page.getByRole("table", { name: "策略对比汇总" }),
    ).toBeVisible();
    await expect(
      page.getByRole("table", { name: "策略对比汇总" }).locator("tbody tr"),
    ).toHaveCount(count);
    for (const name of [
      "多策略累计收益率曲线",
      "多策略回撤曲线",
      "累计收益率对比柱状图",
      "最大回撤对比柱状图",
    ])
      await expect(page.getByRole("img", { name, exact: true })).toBeVisible();
    await expect(page.getByRole("img", { name: /股价与买卖点/ })).toBeVisible();
    await expect(page.getByRole("tab", { name: "回测结果", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("heading", { name: /累计收益与回撤.*000001.SZ/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /指标对比.*000001.SZ/ })).toBeVisible();
    if (count === 6) {
      const toggle = page.getByRole("button", { name: "双均线交叉", exact: true });
      const toggleWidth = await toggle.evaluate((button) => button.getBoundingClientRect().width);
      await expect(page.getByText(/部分策略完整收益未知/)).toBeVisible();
      await page
        .getByRole("button", { name: "双均线交叉", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "双均线交叉", exact: true }),
      ).toHaveAttribute("aria-pressed", "false");
      await expect(
        page.getByRole("button", { name: "双均线交叉", exact: true }),
      ).toHaveClass(/line-through/);
      expect(await toggle.evaluate((button) => button.getBoundingClientRect().width)).toBe(toggleWidth);
      await expect(toggle.locator("svg")).toHaveCount(0);
      const sort = page.getByRole("button", {
        name: "累计收益率",
        exact: true,
      });
      await sort.click();
      await expect(sort.locator("..")).toHaveAttribute(
        "aria-sort",
        "ascending",
      );
      await sort.click();
      await expect(sort.locator("..")).toHaveAttribute(
        "aria-sort",
        "descending",
      );
      const strategyRadio = page.getByRole("radio", { name: "RSI超卖回升", exact: true });
      await strategyRadio.scrollIntoViewIfNeeded();
      const strategyScroll = await page.evaluate(() => window.scrollY);
      await strategyRadio.check();
      await expect(page.getByRole("img", { name: "RSI超卖回升股价与买卖点", exact: true })).toBeVisible();
      expect(Math.abs((await page.evaluate(() => window.scrollY)) - strategyScroll)).toBeLessThan(5);
      await expect(
        page.getByRole("radio", { name: "RSI超卖回升", exact: true }),
      ).toBeChecked();
      await page.getByRole("tab", { name: "历史任务", exact: true }).click();
      await expect(
        page.getByRole("img", { name: "多策略累计收益率曲线", exact: true }),
      ).not.toBeVisible();
      await page.getByRole("tab", { name: "回测结果", exact: true }).click();
      await expect(
        page.getByRole("button", { name: "双均线交叉", exact: true }),
      ).toHaveAttribute("aria-pressed", "false");
      await expect(
        page.getByRole("radio", { name: "RSI超卖回升", exact: true }),
      ).toBeChecked();
      await expect(
        page.getByRole("img", { name: "RSI超卖回升股价与买卖点", exact: true }),
      ).toBeVisible();
      await page.screenshot({
        path: "test-results/comparison-six-desktop.png",
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: "test-results/comparison-six-mobile.png",
        fullPage: true,
      });
      await page.setViewportSize({ width: 1440, height: 1050 });
    }
    for (const [view, label] of [
      ["summary", "指标汇总"],
      ["curves", "每日曲线"],
      ["ledger", "完整账本"],
    ]) {
      const download = page.waitForEvent("download");
      await page
        .getByRole("button", { name: `导出${label} CSV`, exact: true })
        .click();
      expect((await download).suggestedFilename()).toBe(
        `backtest-${job.id}-${view}.csv`,
      );
    }
    await page.reload();
    await expect(
      page.getByRole("tab", { name: "回测结果", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(
      page.getByRole("checkbox", { name: "选择双均线交叉", includeHidden: true }),
    ).toBeChecked();
    await expect(page.getByLabel("股票代码", { exact: true })).toHaveValue(
      "000001.SZ",
    );
    await expect(
      page.getByRole("table", { name: "策略对比汇总" }).locator("tbody tr"),
    ).toHaveCount(count);
  }
  await page.screenshot({
    path: "test-results/comparison-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "单股回测", exact: true }).click();
  await page.getByRole("tab", { name: "历史任务", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: lastJobId.slice(0, 8) })
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await expect(
    page.getByRole("img", { name: "多策略累计收益率曲线" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/comparison-mobile.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "历史任务", exact: true }).click();
  await page
    .getByRole("button", { name: "删除全部历史任务", exact: true })
    .click();
  await page.getByRole("button", { name: "保留任务", exact: true }).click();
  await page
    .getByRole("button", { name: "删除全部历史任务", exact: true })
    .click();
  await page.getByRole("button", { name: "确认删除全部", exact: true }).click();
  await expect(page.getByText(/已删除 2 个历史任务/)).toBeVisible();
  await expect(
    page.getByRole("img", { name: "多策略累计收益率曲线" }),
  ).not.toBeVisible();
  await expect(page.getByLabel("股票代码", { exact: true })).toHaveValue(
    "000001.SZ",
  );
  await page.getByRole("tab", { name: "回测", exact: true }).click();
  await expect(
    page.getByRole("img", { name: "多策略累计收益率曲线" }),
  ).toHaveCount(0);
  expect(errors).toEqual([]);
});
