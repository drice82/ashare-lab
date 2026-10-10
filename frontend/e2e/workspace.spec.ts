import { expect, test } from "@playwright/test";

// Management credentials must never enter retained network traces.
test.use({ trace: "off" });

test("中文工作台、六规则、方案、导出与图表", async ({ page }) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "股票筛选" })).toBeVisible();
  await expect(page.getByLabel("筛选日期")).toHaveValue("2024-06-28");
  let latestRun = "";
  for (const rule of ["A", "B", "C", "D", "E", "F"]) {
    await page.getByLabel("筛选规则", { exact: true }).selectOption(rule);
    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/screening/run") &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "执行筛选", exact: true }).click();
    const job = (await (await submitted).json()) as { id: string };
    latestRun = job.id;
    // The heading may still show the preceding run's completed state.
    // Wait for this run's response before editing the next rule.
    await page.waitForResponse(
      async (response) =>
        response.url().endsWith(`/api/v1/screening/runs/${job.id}`) &&
        response.request().method() === "GET" &&
        ((await response.json()) as { status: string }).status === "succeeded",
    );
    await expect(page.getByLabel("筛选规则", { exact: true })).toHaveValue(
      rule,
    );
    await expect(
      page.getByText(new RegExp(`任务 ${job.id.slice(0, 8)}`)),
    ).toContainText("已完成", { timeout: 30000 });
    await expect(page.getByRole("heading", { name: /筛选结果/ })).toBeVisible();
    await page.getByRole("tab", { name: "股票筛选", exact: true }).click();
  }
  await page.getByRole("tab", { name: "历史任务", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: latestRun.slice(0, 8) })
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await expect(
    page.getByRole("tab", { name: "筛选结果", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "股票筛选", exact: true }).click();
  await page.getByLabel("方案名称").fill("浏览器验收方案");
  await page.getByRole("button", { name: "保存方案" }).click();
  await expect(page.getByText("筛选方案已保存")).toBeVisible();
  await page.getByRole("tab", { name: "筛选结果", exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 CSV" }).click();
  expect((await download).suggestedFilename()).toContain("screening-");
  await page.getByRole("button", { name: /收盘价/ }).click();
  const code = page.getByRole("link", { name: "000003.SZ", exact: true });
  await expect(code).toBeVisible();
  await code.click();
  await expect(page.getByLabel("日K线、MA20、MA60及成交量图")).toBeVisible({
    timeout: 30000,
  });
  await expect(page.locator("canvas").first()).toBeVisible();
  await expect(page.getByText("具体触发数值", { exact: false })).toBeVisible();
  await page.screenshot({
    path: "test-results/stock-detail.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("数据页面与移动导航", async ({ page }) => {
  await page.goto("/data");
  await expect(page.getByLabel("数据管理密码")).toBeVisible();
  const password = process.env.DATA_MANAGEMENT_PASSWORD;
  if (!password)
    throw new Error("数据管理浏览器验收需要 DATA_MANAGEMENT_PASSWORD 环境变量");
  await page.getByLabel("数据管理密码").fill(password);
  await page.getByRole("button", { name: "解锁数据管理" }).click();
  await expect(page.getByRole("heading", { name: "本地数据集" })).toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByRole("cell", { name: /^未复权日线/ })).toBeVisible({
    timeout: 30000,
  });
  await expect(page.getByRole("button", { name: "增量同步" })).toBeDisabled();
  await page.screenshot({
    path: "test-results/data-management.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "同步任务", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "本地数据集" }),
  ).not.toBeVisible();
  await page.reload();
  await expect(page.getByLabel("数据管理密码")).toBeVisible();
  await page.getByLabel("数据管理密码").fill(password);
  await page.getByRole("button", { name: "解锁数据管理" }).click();
  await expect(
    page.getByRole("tab", { name: "同步任务", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "数据管理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "本地数据集" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "股票筛选", exact: true }).click();
  await expect(page.getByRole("heading", { name: "股票筛选" })).toBeVisible();
});
