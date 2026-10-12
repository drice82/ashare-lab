import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

test.use({ trace: "off" });
test.setTimeout(150000);
test.skip(
  !process.env.NOTIFICATION_E2E || !process.env.NOTIFICATION_E2E_CODE_FILE,
  "Requires an isolated fixture API with a private code capture file; never run against production.",
);

const lastCodeSent = new Map<string, number>();

async function emailCode(page: Page, address: string, change = false) {
  await page
    .getByLabel(change ? "新邮箱" : "登录邮箱", { exact: true })
    .fill(address);
  const previous = lastCodeSent.get(address.toLowerCase());
  if (previous)
    await page.waitForTimeout(Math.max(0, 60000 - (Date.now() - previous)));
  await page
    .getByRole("button", { name: change ? "发送更换验证码" : "发送登录验证码" })
    .click();
  await expect(page.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  lastCodeSent.set(address.toLowerCase(), Date.now());
  const codes = JSON.parse(
    readFileSync(process.env.NOTIFICATION_E2E_CODE_FILE!, "utf8"),
  ) as Record<string, string>;
  await page
    .getByLabel(change ? "新邮箱验证码" : "登录验证码", { exact: true })
    .fill(codes[address.toLowerCase()]);
  await page
    .getByRole("button", { name: change ? "确认更换邮箱" : "验证码登录" })
    .click();
  await expect(page.getByRole("tab", { name: "订阅管理" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
}

test("邮箱登录、订阅、多设备、邮箱更换和移动管理", async ({
  page,
  browser,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/notifications?stock_code=000001.SZ");
  await expect(page.getByText(/fixture 数据/)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "访问密钥", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("恢复码", { exact: true })).toHaveCount(0);
  await page.screenshot({
    path: "test-results/notification-email-login-1440.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.locator("aside").evaluate((el) => el.getBoundingClientRect().right),
    )
    .toBeLessThanOrEqual(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/notification-email-login-390.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await emailCode(page, "alice@fixture.example.org");
  await expect(
    page.getByRole("button", { name: "创建订阅", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "创建订阅", exact: true }).click();
  await expect(page.getByText("我的订阅 · 1 / 20")).toBeVisible();
  await expect(page.getByLabel("启用的订阅")).toContainText("测试证券1");
  await expect(page.getByLabel("启用的订阅")).toContainText(
    "短周期 20 个交易日",
  );
  await page.getByRole("tab", { name: "发送记录" }).click();
  await expect(
    page.getByRole("heading", { name: "发送记录", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("股票代码", { exact: true })).toBeHidden();
  await page.getByRole("tab", { name: "账号与渠道" }).click();
  await expect(
    page.getByRole("heading", { name: "Webhook 通知" }),
  ).toBeVisible();
  await expect(
    page.getByText("https://api.day.app/<Device Key>"),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Bark 设备" })).toHaveCount(0);
  await page.getByRole("tab", { name: "订阅管理" }).click();
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(page.getByLabel("暂停的订阅")).toBeVisible();
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("tab", { name: "订阅管理" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);

  const other = await browser.newContext({
    viewport: { width: 390, height: 844 },
  });
  const second = await other.newPage();
  second.on("pageerror", (error) => errors.push(error.message));
  await second.goto("/notifications");
  // Respect the real persistent target cooldown before a second sign-in.
  await second.waitForTimeout(61000);
  await emailCode(second, "alice@fixture.example.org");
  await expect(second.getByText("我的订阅 · 1 / 20")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("tab", { name: "订阅管理" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await second.getByRole("tab", { name: "账号与渠道" }).click();
  await second.getByText("更换登录与通知邮箱", { exact: true }).click();
  await emailCode(second, "new@fixture.example.org", true);
  await expect(
    second.getByText("邮箱已更换，其他设备需重新登录"),
  ).toBeVisible();
  await expect(second.getByText("我的订阅 · 1 / 20")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("登录邮箱", { exact: true })).toBeVisible();
  await second.getByRole("button", { name: "删除", exact: true }).click();
  await expect(second.getByText("尚无订阅。")).toBeVisible();
  await second.getByRole("tab", { name: "账号与渠道" }).click();
  await second.getByRole("button", { name: "退出登录" }).click();
  await expect(second.getByLabel("登录邮箱", { exact: true })).toBeVisible();
  await other.close();
  expect(errors).toEqual([]);
});

test("三个通知页签、紧凑订阅卡片和菜单在桌面与手机可用", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/notifications?stock_code=000001.SZ");
  await emailCode(page, "layout@fixture.example.org");
  await expect(
    page.getByText("测试证券1 · 000001.SZ", { exact: true }),
  ).toBeVisible();
  for (const strategy of ["ma_cross", "rsi_reversal", "donchian_breakout"]) {
    await page.getByLabel("策略", { exact: true }).selectOption(strategy);
    await page.getByRole("button", { name: "创建订阅", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("已保存");
  }
  await expect(page.getByText("我的订阅 · 3 / 20")).toBeVisible();
  const cards = page.locator("article");
  await expect(cards).toHaveCount(3);
  const desktop = await cards.evaluateAll((els) =>
    els.map((el) => ({
      x: el.getBoundingClientRect().x,
      y: el.getBoundingClientRect().y,
    })),
  );
  expect(new Set(desktop.map((rect) => rect.y)).size).toBe(1);
  expect(new Set(desktop.map((rect) => rect.x)).size).toBe(3);
  await cards
    .first()
    .getByRole("button", { name: "暂停", exact: true })
    .click();
  await expect(page.getByLabel("暂停的订阅")).toBeVisible();
  await expect(
    page.getByText("周期 14 个交易日 · 下阈值 30 · 上阈值 70", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/notifications-subscriptions-1440.png",
    fullPage: true,
  });
  const nav = page.locator("aside nav");
  await expect(nav.getByRole("link", { name: "后台管理" })).toHaveCount(0);
  await expect(
    page.locator("aside").getByRole("link", { name: "后台管理" }),
  ).toBeVisible();
  await page.getByLabel("策略", { exact: true }).selectOption("ma_cross");
  await page.getByLabel("短周期").fill("10");
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1050 });
    for (const [label, file] of [
      ["订阅管理", "subscriptions"],
      ["发送记录", "records"],
      ["账号与渠道", "settings"],
    ]) {
      await page.getByRole("tab", { name: label, exact: true }).click();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `test-results/notifications-${file}-${width}.png`,
        fullPage: true,
      });
    }
    await page.getByRole("tab", { name: "订阅管理" }).click();
    await expect(page.getByLabel("短周期")).toHaveValue("10");
    if (width === 390) {
      const mobile = await cards.evaluateAll((els) =>
        els.map((el) => el.getBoundingClientRect().x),
      );
      expect(new Set(mobile).size).toBe(1);
    }
  }
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.locator("aside").getByRole("link", { name: "后台管理" }).click();
  await expect(
    page.getByRole("button", { name: "解锁后台管理" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
