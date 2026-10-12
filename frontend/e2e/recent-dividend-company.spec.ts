import { expect, test } from "@playwright/test";

// Isolated fixture API/Worker only; these tests create research and company tasks.
test.use({ trace: "off" });
for (const width of [1440, 390]) {
  test(`近期分红与企业详情 ${width}px`, async ({ page, request }) => {
    expect(
      (await (await request.get("/api/v1/market/status")).json()).fixture_mode,
    ).toBe(true);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width, height: 1050 });
    await page.goto("/screening");
    await page.getByLabel("筛选方式", { exact: true }).selectOption("dividend");
    await expect(page.getByLabel("最低股息率")).toHaveCount(0);
    await page.getByLabel("登记窗口天数").fill("14");
    await page.getByLabel("方案名称").fill(`近期分红${width}`);
    await page.getByRole("button", { name: "保存方案", exact: true }).click();
    await expect(page.getByText("筛选方案已保存")).toBeVisible();
    await page.getByRole("button", { name: "执行筛选", exact: true }).click();
    await expect(
      page.getByRole("tab", { name: "筛选结果", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("2024-07-01", { exact: true })).toBeVisible();
    await expect(page.getByText("200.00 元", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("columnheader", { name: "规则", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("columnheader", { name: "结果", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("columnheader", { name: "有效行情日", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByText(/有效行情日：2024-06-27 至 2024-06-28/),
    ).toBeVisible();
    await page.getByRole("link", { name: "000001.SZ", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: /最新企业资料/ }),
    ).toBeVisible();
    await expect(page.getByText("测试公司", { exact: true })).toBeVisible();
    await expect(page.getByText("12.50%", { exact: true })).toBeVisible();
    await expect(
      page.getByText("fixture 模式：企业资料来自合成测试数据。", {
        exact: true,
      }),
    ).toBeVisible();
    await page.getByLabel("业务构成维度").selectOption("regions");
    await expect(page.getByText("主营", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "现金流", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    expect(errors).toEqual([]);
    await expect(page.getByLabel("日K线、MA20、MA60及成交量图")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: /^近期分红 ·/ }),
    ).toBeVisible();
    await page.screenshot({
      path: `test-results/recent-dividend-company-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("link", { name: "返回筛选", exact: true }).click();
    await page.getByRole("tab", { name: "股票筛选", exact: true }).click();
    await expect(page.getByLabel("筛选方式")).toHaveValue("dividend");
    await expect(page.getByLabel("登记窗口天数")).toHaveValue("14");
    await page.getByLabel("筛选方式").selectOption("rule");
    await expect(page.getByLabel("近期分红", { exact: true })).toBeChecked();
  });
}
