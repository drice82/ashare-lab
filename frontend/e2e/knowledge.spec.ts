import { expect, test } from "@playwright/test";

test.use({ trace: "off" });

test("知识库无需研究API：上报失败、章节定位、键盘、刷新、历史与响应式目录", async ({
  page,
}) => {
  const errors: string[] = [];
  const apiRequests: string[] = [];
  await page.route("**/api/v1/analytics/pageviews", (route) => route.abort());
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.url().includes("/api/") &&
      !request.url().endsWith("/api/v1/analytics/pageviews") &&
      !request.url().endsWith("/api/v1/notifications/session")
    )
      apiRequests.push(request.url());
  });
  await page.goto("/knowledge#rsi_reversal");
  await expect(
    page.getByRole("heading", { level: 2, name: "RSI超卖回升" }),
  ).toBeFocused();
  await expect(
    page.getByRole("heading", { level: 2, name: "RSI超卖回升" }),
  ).toBeInViewport();
  await page.reload();
  await expect(
    page.getByRole("heading", { level: 2, name: "RSI超卖回升" }),
  ).toBeFocused();
  const directory = page.getByRole("navigation", { name: "策略目录" });
  const macd = directory.getByRole("link", { name: "MACD交叉" });
  await macd.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#macd_cross$/);
  await expect(
    page.getByRole("heading", { level: 2, name: "MACD交叉" }),
  ).toBeFocused();
  await page.goBack();
  await expect(
    page.getByRole("heading", { level: 2, name: "RSI超卖回升" }),
  ).toBeFocused();
  await page.goForward();
  await expect(
    page.getByRole("heading", { level: 2, name: "MACD交叉" }),
  ).toBeFocused();
  await expect(directory).toBeInViewport();
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await macd.click();
  await expect(
    page.getByRole("heading", { level: 2, name: "MACD交叉" }),
  ).toBeInViewport();
  await page.goto("/knowledge#unknown");
  await expect(
    page.getByRole("heading", { level: 1, name: "知识库" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /延伸阅读/ })).toHaveCount(6);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/knowledge");
    await expect(
      page.getByRole("heading", { level: 1, name: "知识库" }),
    ).toBeVisible();
    await page.getByText("费用、现金分红与模拟边界", { exact: true }).click();
    const geometry = await directory.evaluate((element) => {
      const directoryBox = element.getBoundingClientRect();
      const contentBox = document
        .querySelector('[aria-labelledby="common-rules"]')!
        .getBoundingClientRect();
      return {
        directoryRight: directoryBox.right,
        directoryBottom: directoryBox.bottom,
        contentLeft: contentBox.left,
        contentTop: contentBox.top,
      };
    });
    if (width === 1440)
      expect(geometry.directoryRight).toBeLessThan(geometry.contentLeft);
    else expect(geometry.directoryBottom).toBeLessThan(geometry.contentTop);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: `test-results/knowledge-${width}.png`,
      fullPage: false,
      animations: "disabled",
    });
  }
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "知识库", exact: true }).click();
  await expect(page.getByRole("button", { name: "关闭导航遮罩" })).toHaveCount(
    0,
  );
  await directory.getByRole("link", { name: "布林带回归" }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: "布林带回归" }),
  ).toBeInViewport();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/knowledge-chapter-390.png",
    animations: "disabled",
  });
  expect(apiRequests).toEqual([]);
  expect(errors).toEqual([]);
});

test("实际fixture API接入回测：新窗口阅读策略说明后原参数保留", async ({
  page,
}) => {
  const writes: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (
      request.url().includes("/api/") &&
      !request.url().endsWith("/api/v1/analytics/pageviews") &&
      request.method() !== "GET"
    )
      writes.push(request.method());
  });
  await page.goto("/backtest");
  await expect(
    page.getByText("测试数据模式（fixture）", { exact: false }),
  ).toBeVisible();
  await expect(page.getByLabel("结束交易日")).toHaveValue("2024-06-28");
  await page.getByLabel("股票代码", { exact: true }).fill("000001.SZ");
  await page.getByLabel("开始交易日").fill("2024-06-03");
  await page.getByLabel("初始资金（元）").fill("123456");
  await page.getByRole("checkbox", { name: "选择MACD交叉" }).uncheck();
  const popupEvent = page.waitForEvent("popup");
  await page.getByRole("link", { name: /策略说明.*新窗口/ }).click();
  const popup = await popupEvent;
  await expect(
    popup.getByRole("heading", { level: 1, name: "知识库" }),
  ).toBeVisible();
  await popup
    .getByRole("navigation", { name: "策略目录" })
    .getByRole("link", { name: "唐奇安突破" })
    .click();
  await expect(
    popup.getByRole("heading", { level: 2, name: "唐奇安突破" }),
  ).toBeInViewport();
  await popup.close();
  await expect(page.getByLabel("股票代码", { exact: true })).toHaveValue(
    "000001.SZ",
  );
  await expect(page.getByLabel("开始交易日")).toHaveValue("2024-06-03");
  await expect(page.getByLabel("结束交易日")).toHaveValue("2024-06-28");
  await expect(page.getByLabel("初始资金（元）")).toHaveValue("123456");
  await expect(
    page.getByRole("checkbox", { name: "选择MACD交叉" }),
  ).not.toBeChecked();
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});
