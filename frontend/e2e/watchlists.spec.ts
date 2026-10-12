import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

test.use({ trace: "off" });
test.setTimeout(150000);
test.skip(
  !process.env.WATCHLIST_E2E || !process.env.NOTIFICATION_E2E_CODE_FILE,
  "Requires isolated actual API/Worker and private fixture verification capture.",
);
const lastCodeSent = new Map<string, number>();

async function login(page: Page, address: string) {
  await page
    .getByRole("banner")
    .getByRole("button", { name: "登录", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "邮箱登录" });
  await dialog.getByLabel("登录邮箱", { exact: true }).fill(address);
  const previous = lastCodeSent.get(address);
  if (previous)
    await page.waitForTimeout(Math.max(0, 60000 - (Date.now() - previous)));
  await dialog.getByRole("button", { name: "发送登录验证码" }).click();
  await expect(dialog.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  lastCodeSent.set(address, Date.now());
  const codes = JSON.parse(
    readFileSync(process.env.NOTIFICATION_E2E_CODE_FILE!, "utf8"),
  ) as Record<string, string>;
  await dialog.getByLabel("登录验证码", { exact: true }).fill(codes[address]);
  await dialog.getByRole("button", { name: "验证码登录" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "账号列表", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
}
async function create(page: Page, name: string, codes: string[]) {
  await page.getByRole("button", { name: "新建列表" }).click();
  await page.getByLabel("列表名称").fill(name);
  if (codes.length) {
    await page
      .getByRole("combobox", { name: "列表股票", exact: true })
      .fill(codes.join(" "));
    await page
      .getByRole("combobox", { name: "列表股票", exact: true })
      .press("Enter");
  }
  await page.getByRole("button", { name: "保存列表" }).click();
  await expect(
    page.getByRole("button", { name: `${name} · ${codes.length}只` }),
  ).toBeVisible();
}
async function runId(page: Page) {
  await expect
    .poll(() => new URL(page.url()).searchParams.get("run"))
    .toBeTruthy();
  return new URL(page.url()).searchParams.get("run")!;
}

test("本机多列表、实际筛选与组合任务、快照及手机布局", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/watchlists");
  await expect(page.getByText(/当前列表保存在本机/)).toBeVisible();
  await expect(page.getByText(/测试数据模式（fixture）/)).toBeVisible();
  await create(page, "本机银行", ["000001.SZ", "000002.SZ"]);
  await create(page, "空列表", []);
  await create(
    page,
    "超限",
    Array.from({ length: 21 }, (_, i) => `600${String(i).padStart(3, "0")}.SH`),
  );
  await page.reload();
  await expect(
    page.getByRole("button", { name: "本机银行 · 2只" }),
  ).toBeVisible();
  await page.goto("/screening");
  await page.getByLabel("股票池", { exact: true }).selectOption("watchlist");
  await page
    .getByLabel("选择自选列表")
    .selectOption({ label: "本机银行 · 2只" });
  await page.getByRole("button", { name: "载入自选列表" }).click();
  await page.getByRole("button", { name: "执行筛选" }).click();
  const screenId = await runId(page);
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(`/api/v1/screening/runs/${screenId}`)
          ).json()
        ).status,
    )
    .toBe("succeeded");
  const screenTask = await (
    await page.request.get(`/api/v1/screening/runs/${screenId}`)
  ).json();
  expect(screenTask.payload.universe).toEqual({
    type: "custom",
    codes: ["000001.SZ", "000002.SZ"],
  });
  await page.goto("/portfolio");
  await expect(page.getByLabel("结束交易日")).toHaveValue("2024-06-28");
  await page.getByLabel("开始交易日").fill("2024-06-03");
  await page.getByLabel("初始资金", { exact: true }).fill("123456");
  await page
    .getByRole("combobox", { name: "组合股票", exact: true })
    .fill("000002.SZ");
  await page
    .getByRole("combobox", { name: "组合股票", exact: true })
    .press("Enter");
  await page.getByLabel("000002.SZ 策略").selectOption("ma_cross");
  await page.getByLabel("资金分配").selectOption("custom");
  for (const label of ["空列表 · 0只", "超限 · 21只"]) {
    await page.getByLabel("选择自选列表").selectOption({ label });
    await expect(
      page.getByRole("button", { name: "应用自选列表" }),
    ).toBeDisabled();
    await expect(page.getByLabel("000002.SZ 策略")).toHaveValue("ma_cross");
  }
  await page
    .getByLabel("选择自选列表")
    .selectOption({ label: "本机银行 · 2只" });
  await page.getByRole("button", { name: "应用自选列表" }).click();
  await page.getByRole("button", { name: "取消替换" }).click();
  await expect(page.getByLabel("000002.SZ 策略")).toHaveValue("ma_cross");
  await page.getByRole("button", { name: "应用自选列表" }).click();
  await page.getByRole("button", { name: "确认替换" }).click();
  await expect(page.getByLabel("000002.SZ 策略")).toHaveValue("buy_hold");
  await expect(page.getByLabel("资金分配")).toHaveValue("equal");
  await page.getByRole("button", { name: "开始组合回测" }).click();
  const portfolioId = await runId(page);
  await expect(page.getByRole("table", { name: "组合回测汇总" })).toBeVisible();
  const portfolioTask = await (
    await page.request.get(`/api/v1/portfolio/runs/${portfolioId}`)
  ).json();
  expect(portfolioTask.payload.initial_cash).toBe("123456");
  expect(
    portfolioTask.payload.members.map(
      (member: { stock_code: string }) => member.stock_code,
    ),
  ).toEqual(["000001.SZ", "000002.SZ"]);
  await page.goto("/watchlists");
  await page.getByRole("button", { name: "本机银行 · 2只" }).click();
  await page
    .getByRole("button", { name: "删除 000002.SZ", exact: true })
    .click();
  await page.getByRole("button", { name: "保存列表" }).click();
  await expect(
    page.getByRole("button", { name: "本机银行 · 1只" }),
  ).toBeVisible();
  const unchanged = await (
    await page.request.get(`/api/v1/portfolio/runs/${portfolioId}`)
  ).json();
  expect(unchanged.payload).toEqual(portfolioTask.payload);
  expect(unchanged.snapshot).toEqual(portfolioTask.snapshot);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    if (width === 390)
      await expect
        .poll(() =>
          page
            .locator("aside")
            .evaluate((element) => element.getBoundingClientRect().right),
        )
        .toBeLessThanOrEqual(0);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `test-results/watchlists-local-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
  }
  expect(errors).toEqual([]);
});

test("页内登录、账号与本机独立、多设备冲突与退出隔离", async ({
  page,
  browser,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/watchlists");
  await create(page, "独立本机", ["000001.SZ"]);
  const local = await page.evaluate(() =>
    localStorage.getItem("ashare-watchlists-v1"),
  );
  const address = `watchlist-${Date.now()}@fixture.example.org`;
  await login(page, address);
  await expect(
    page.getByRole("button", { name: "独立本机 · 1只" }),
  ).toHaveCount(0);
  await create(page, "账号银行", ["000001.SZ", "000002.SZ"]);
  expect(
    await page.evaluate(() => localStorage.getItem("ashare-watchlists-v1")),
  ).toBe(local);
  const secondContext = await browser.newContext({
    viewport: { width: 390, height: 900 },
  });
  const second = await secondContext.newPage();
  second.on("pageerror", (error) => errors.push(error.message));
  await second.goto("/watchlists");
  await login(second, address);
  await second.getByRole("button", { name: "账号银行 · 2只" }).click();
  await page.getByRole("button", { name: "账号银行 · 2只" }).click();
  await page.getByLabel("列表名称").fill("账号改名");
  await page.getByRole("button", { name: "保存列表" }).click();
  await expect(
    page.getByRole("button", { name: "账号改名 · 2只" }),
  ).toBeVisible();
  await second.getByLabel("列表名称").fill("冲突写入");
  await second.getByRole("button", { name: "保存列表" }).click();
  await expect(second.getByText(/列表已在其他页面或设备修改/)).toBeVisible();
  await second.getByRole("button", { name: "刷新列表", exact: true }).click();
  await expect(
    second.getByRole("button", { name: "账号改名 · 2只" }),
  ).toBeVisible();
  await second.getByRole("button", { name: "账号改名 · 2只" }).click();
  expect(
    await second.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await second.screenshot({
    path: "test-results/watchlists-account-390.png",
    fullPage: true,
  });
  await page
    .getByRole("banner")
    .getByRole("button", { name: "退出", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "独立本机 · 1只" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "账号改名 · 2只" }),
  ).toHaveCount(0);
  expect((await second.request.get("/api/v1/watchlists")).status()).toBe(200);
  await second
    .getByRole("banner")
    .getByRole("button", { name: "退出", exact: true })
    .click();
  await expect(
    second.getByRole("button", { name: "账号改名 · 2只" }),
  ).toHaveCount(0);
  await expect(
    second.getByRole("button", { name: "独立本机 · 1只" }),
  ).toHaveCount(0);
  await secondContext.close();
  expect(errors).toEqual([]);
});
