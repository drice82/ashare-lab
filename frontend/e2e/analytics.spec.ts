import { expect, test } from "@playwright/test";

// Run only against an isolated fixture API: these checks create browsing records.
test.use({ trace: "off" });

for (const width of [1440, 390]) {
  test(`真实API浏览采集与后台统计 ${width}px`, async ({
    page,
    browser,
    request,
  }) => {
    expect(
      (await (await request.get("/api/v1/market/status")).json()).fixture_mode,
    ).toBe(true);
    const password = process.env.ANALYTICS_TEST_PASSWORD;
    if (!password)
      throw new Error(
        "Set ANALYTICS_TEST_PASSWORD for the isolated fixture server",
      );
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width, height: 1000 });
    const headers = { "X-Data-Management-Password": password };
    const before = (
      await (await request.get("/api/v1/data/analytics", { headers })).json()
    ).totals;
    async function open(path: string) {
      const sent = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/analytics/pageviews") &&
          response.request().method() === "POST",
      );
      await page.goto(path);
      expect((await sent).status()).toBe(204);
    }
    await open("/knowledge/");
    await open("/KNOWLEDGE"); // reload of the same page: another view, same browser
    await open("/screening");
    let additional = 0;
    page.on("request", (req) => {
      if (req.url().endsWith("/api/v1/analytics/pageviews")) additional++;
    });
    await page.getByRole("tab", { name: "历史任务", exact: true }).click();
    await expect(page).toHaveURL(/tab=history/);
    await page.goto("/data?tab=analytics");
    await page.getByLabel("后台管理密码").fill(password);
    await page.getByRole("button", { name: "解锁后台管理" }).click();
    await expect(
      page.getByRole("heading", { name: "后台管理", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("img", { name: "每日浏览次数与独立访客趋势" }),
    ).toBeVisible();
    await expect(
      page.getByText("固定测试环境（fixture）", { exact: true }),
    ).toBeVisible();
    const after = (
      await (await request.get("/api/v1/data/analytics", { headers })).json()
    ).totals;
    expect(after.pageviews - before.pageviews).toBe(3);
    expect(after.visitors - before.visitors).toBe(1);
    expect(additional).toBe(0);

    const other = await browser.newContext();
    try {
      const otherPage = await other.newPage();
      const sent = otherPage.waitForResponse((response) =>
        response.url().endsWith("/api/v1/analytics/pageviews"),
      );
      await otherPage.goto(new URL("/knowledge", page.url()).href);
      expect((await sent).status()).toBe(204);
    } finally {
      await other.close();
    }
    const final = (
      await (await request.get("/api/v1/data/analytics", { headers })).json()
    ).totals;
    expect(final.pageviews - before.pageviews).toBe(4);
    expect(final.visitors - before.visitors).toBe(2);

    await page.getByRole("button", { name: "最近7天" }).click();
    const start = await page.getByLabel("浏览开始日期").inputValue();
    await page.getByRole("tab", { name: "数据管理", exact: true }).click();
    await page.getByRole("tab", { name: "浏览统计", exact: true }).click();
    await expect(page.getByLabel("浏览开始日期")).toHaveValue(start);
    await page.getByRole("button", { name: "刷新统计" }).click();
    await expect(
      page.getByRole("img", { name: "每日浏览次数与独立访客趋势" }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/analytics-${width}.png`,
      fullPage: true,
    });
    await page.reload();
    await expect(page.getByLabel("后台管理密码")).toHaveValue("");
    expect(errors).toEqual([]);
  });
}
