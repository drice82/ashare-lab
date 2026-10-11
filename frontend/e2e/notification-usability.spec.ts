import { expect, test } from "@playwright/test";

// UI fixtures verify browser behavior; they do not certify production delivery.
for (const width of [1440, 390]) {
  test(`notification controls and decimal editing at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1050 });
    let emailActive = true;
    let webhookActive = false;
    const samples: string[] = [];
    let dividend: number | undefined;
    await page.route("**/api/v1/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      let body: unknown = [];
      if (path.endsWith("/options"))
        body = {
          channels: { email: true, webhook: true },
          fixture_mode: true,
          auto_send: false,
          subscription_limit: 20,
        };
      if (path.endsWith("/session"))
        body = { owner_id: "owner", email: "a***@example.org" };
      if (path.endsWith("/channels/email") && request.method() === "DELETE")
        emailActive = false;
      if (path.endsWith("/channels/email/enable")) emailActive = true;
      if (path.endsWith("/channels/webhook/enable")) webhookActive = true;
      if (path.endsWith("/channels/webhook") && request.method() === "DELETE")
        webhookActive = false;
      if (path.endsWith("/channels"))
        body = [
          {
            type: "email",
            verified: emailActive,
            status: emailActive ? "active" : "disabled",
            target: "a***@example.org",
          },
          {
            type: "webhook",
            verified: webhookActive,
            status: webhookActive ? "active" : "disabled",
            target: "已加密保存",
          },
        ];
      if (path.endsWith("/subscriptions"))
        body = [
          {
            id: "sample-sub",
            stock_code: "000001.SZ",
            stock_name: "示例证券",
            strategy: { id: "ma_cross", short_window: 20, long_window: 60 },
            channels: ["email", "webhook"],
            directions: ["buy", "sell"],
            status: "paused",
            last_check: null,
            effective_after: "2026-10-09",
          },
        ];
      if (path.endsWith("/sample-sub/test")) {
        samples.push(request.postDataJSON().direction);
        body = {
          results: [
            { channel: "email", status: "accepted" },
            { channel: "webhook", status: "accepted" },
          ],
        };
      }
      if (path.endsWith("/deliveries")) body = { items: [], total: 0 };
      if (path.endsWith("/screening/rules"))
        body = {
          rules: [
            {
              id: "A",
              name: "股息",
              defaults: { dividend_min: 0.03 },
              schema: {
                properties: { dividend_min: { minimum: 0, maximum: 1 } },
              },
            },
          ],
          factors: [],
        };
      if (path.endsWith("/market/status"))
        body = { latest_market_date: "2026-10-09", datasets: {} };
      if (path.endsWith("/screening/run")) {
        dividend = request.postDataJSON().parameters.dividend_min;
        body = { id: "test-job", status: "queued" };
      }
      if (path.endsWith("/screening/runs/test-job"))
        body = {
          id: "test-job",
          status: "queued",
          progress: 0,
          payload: {
            rule_id: "A",
            parameters: { dividend_min: 0.035 },
            as_of_date: "2026-10-09",
            universe: { type: "all", codes: [] },
          },
        };
      await route.fulfill({ json: body });
    });
    await page.goto("/notifications");
    await page.getByRole("tab", { name: "账号与渠道" }).click();
    const emailCard = page
      .getByRole("heading", { name: "邮件渠道", exact: true })
      .locator("../..");
    const webhookCard = page
      .getByRole("heading", { name: "Webhook 通知", exact: true })
      .locator("../..");
    await expect(emailCard.getByText("已启用", { exact: true })).toBeVisible();
    await expect(
      webhookCard.getByText("已停用", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "停用邮件通知" }).click();
    await expect(emailCard.getByText("已停用", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "启用邮件通知" }).click();
    await expect(emailCard.getByText("已启用", { exact: true })).toBeVisible();
    await page
      .getByRole("button", { name: "启用 Webhook", exact: true })
      .click();
    await expect(
      webhookCard.getByText("已启用", { exact: true }),
    ).toBeVisible();
    await page.screenshot({
      path: `/tmp/notification-channels-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("tab", { name: "订阅管理" }).click();
    await page.getByRole("button", { name: "测试通知", exact: true }).click();
    await page.getByRole("button", { name: "测试卖出信号" }).click();
    await expect(page.getByText(/邮件：测试通知已发送/)).toBeVisible();
    expect(samples).toEqual(["sell"]);
    await page.screenshot({
      path: `/tmp/notification-subscriptions-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("tab", { name: "发送记录" }).click();
    expect(
      await page.getByLabel("按渠道查询").locator("option").allTextContents(),
    ).not.toContain("Bark（旧渠道）");
    await expect(
      page.getByText("个人研究 · 本地数据", { exact: true }),
    ).toHaveCount(0);
    const footer = page.getByRole("contentinfo");
    const centered = await footer.evaluate((el) => {
      const bounds = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return bounds.width > 0 && style.textAlign === "center";
    });
    expect(centered).toBe(true);
    await page.screenshot({
      path: `/tmp/notification-usability-${width}.png`,
      fullPage: true,
    });
    await page.goto("/screening");
    const input = page.getByLabel("最低股息率", { exact: true });
    await input.fill("3");
    await input.press("End");
    await input.press(".");
    await expect(input).toHaveValue("3.");
    await input.press("5");
    await expect(input).toHaveValue("3.5");
    await page.getByRole("button", { name: "执行筛选" }).click();
    await expect.poll(() => dividend).toBe(0.035);
  });
}
