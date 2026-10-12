import { expect, test } from "@playwright/test";

// Run only against the isolated fixture API/Worker; never submit production tasks.
test.use({ trace: "off" });
for (const width of [1440, 390]) {
  test(`策略信号筛选与历史条件说明 ${width}px`, async ({ page, request }) => {
    const status = await request.get("/api/v1/market/status");
    expect((await status.json()).fixture_mode).toBe(true);
    await page.setViewportSize({ width, height: 1050 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/screening");
    await expect(
      page.getByText("fixture 模式：当前使用合成测试数据。"),
    ).toBeVisible();
    await page.getByLabel("筛选方式", { exact: true }).selectOption("signal");
    await expect(page.getByLabel("最低股息率")).toHaveCount(0);
    await expect(page.getByLabel("选择买入持有")).toHaveCount(0);
    await page.getByText("双均线交叉参数", { exact: true }).click();
    await page.getByLabel("双均线交叉 短周期", { exact: true }).fill("2");
    await page.getByLabel("双均线交叉 长周期", { exact: true }).fill("3");
    await page.getByLabel("选择MACD交叉", { exact: true }).check();
    await page.getByLabel("信号方向", { exact: true }).selectOption("sell");
    await page.getByLabel("方案名称").fill(`信号方案${width}`);
    await page.getByRole("button", { name: "保存方案", exact: true }).click();
    await expect(page.getByText("筛选方案已保存")).toBeVisible();
    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/screening/run") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "执行筛选", exact: true }).click();
    const job = await (await submitted).json();
    await expect(
      page.getByRole("tab", { name: "筛选结果", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(
      page.getByRole("link", { name: "000001.SZ", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("双均线交叉 · 卖出", { exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "000001.SZ", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "策略信号详情" }),
    ).toBeVisible();
    await expect(page.getByText("前日短均线", { exact: true })).toBeVisible();
    await expect(
      page.getByText("策略预热交易日不足", { exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "返回筛选", exact: true }).click();
    await page.getByRole("tab", { name: "历史任务", exact: true }).click();
    const history = page
      .getByRole("row")
      .filter({ hasText: job.id.slice(0, 8) });
    await expect(history).toContainText(
      "策略信号：双均线交叉 / MACD交叉 · 卖出",
    );
    await expect(history).toContainText("短周期 2 / 长周期 3");
    await history.getByRole("button", { name: "查看", exact: true }).click();
    await page.getByRole("tab", { name: "股票筛选", exact: true }).click();
    await expect(page.getByLabel("信号方向")).toHaveValue("sell");
    await page.getByLabel("筛选方式").selectOption("rule_signal");
    await page.getByLabel("最低股息率").fill("3.5");
    const combined = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/screening/run") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "执行筛选", exact: true }).click();
    const combinedJob = await (await combined).json();
    await expect(
      page.getByRole("tab", { name: "筛选结果", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page.getByRole("tab", { name: "历史任务", exact: true }).click();
    const combinedHistory = page
      .getByRole("row")
      .filter({ hasText: combinedJob.id.slice(0, 8) });
    await expect(combinedHistory).toContainText("规则 A：高股息 + 单日下跌");
    await expect(combinedHistory).toContainText("股息率 ≥ 3.50%");
    await combinedHistory.locator("summary").click();
    await expect(combinedHistory).toContainText("规则与信号同时满足");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const title = await combinedHistory
      .locator("td")
      .first()
      .locator("p")
      .first()
      .boundingBox();
    expect(title).not.toBeNull();
    expect(title!.x).toBeGreaterThanOrEqual(0);
    expect(title!.width).toBeLessThanOrEqual(width);
    await page.screenshot({
      path: `test-results/signal-screening-history-${width}.png`,
      fullPage: true,
    });
    expect(errors).toEqual([]);
  });
}
