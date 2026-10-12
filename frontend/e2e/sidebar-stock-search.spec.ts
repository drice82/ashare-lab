import { expect, test } from "@playwright/test";

// UI fixtures only; this regression does not validate live data access.
test.use({ trace: "off" });
test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname.endsWith("/screening/rules"))
      body = {
        rules: [{ id: "A", name: "高股息", defaults: { dividend_min: 0.04 }, schema: { properties: {} } }],
        factors: [],
      };
    else if (url.pathname.endsWith("/market/status"))
      body = { latest_market_date: "2024-06-28", fixture_mode: true };
    else if (url.pathname.endsWith("/instruments")) {
      expect(url.searchParams.get("as_of_date")).toBe("2024-06-28");
      body = { items: [{ stock_code: "000001.SZ", name: "平安银行" }] };
    } else if (url.pathname.endsWith("/screening/presets") || url.pathname.endsWith("/jobs"))
      body = [];
    await route.fulfill({ json: body });
  });
});

test("desktop sidebar toggle preserves the screening draft and frees content width", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/screening");
  await expect(page.getByText("fixture 模式：当前使用合成测试数据。")).toBeVisible();
  const sidebar = page.locator("#workspace-sidebar");
  const main = page.locator("main");
  await expect(sidebar).toBeVisible();
  const initialWidth = (await main.boundingBox())!.width;
  await page.getByLabel("最低股息率", { exact: true }).fill("3.5");
  await page.getByRole("button", { name: "收起侧边栏" }).click();
  await expect(sidebar).toBeHidden();
  expect((await main.boundingBox())!.width).toBeGreaterThan(initialWidth);
  await expect(page.getByLabel("最低股息率", { exact: true })).toHaveValue("3.5");
  await page.getByRole("combobox", { name: "股票名称或代码", exact: true }).fill("平安");
  await page.getByRole("option", { name: /平安银行/ }).click();
  await expect(page.getByLabel("股票池", { exact: true })).toHaveValue("custom");
  await expect(page.getByRole("button", { name: "删除 000001.SZ" })).toBeVisible();
  await page.getByRole("button", { name: "展开侧边栏" }).click();
  await expect(sidebar).toBeVisible();
  await expect(page.getByLabel("最低股息率", { exact: true })).toHaveValue("3.5");
  await expect(page.getByRole("button", { name: "删除 000001.SZ" })).toBeVisible();
  await page.screenshot({ path: "test-results/sidebar-stock-search-desktop.png", fullPage: true });
  expect(errors).toEqual([]);
});

test("mobile navigation still opens after desktop collapse and stock codes can be selected", async ({ page }) => {
  await page.goto("/screening");
  await page.getByRole("button", { name: "收起侧边栏" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "展开侧边栏" })).toBeHidden();
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.locator("#workspace-sidebar")).toBeVisible();
  await page.getByRole("link", { name: "股票筛选", exact: true }).click();
  await expect(page.getByRole("button", { name: "关闭导航遮罩" })).toHaveCount(0);
  await page.getByRole("combobox", { name: "股票名称或代码", exact: true }).fill("000001");
  await page.getByRole("option", { name: /平安银行/ }).click();
  await expect(page.getByRole("button", { name: "删除 000001.SZ" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/sidebar-stock-search-mobile.png", fullPage: true });
});
