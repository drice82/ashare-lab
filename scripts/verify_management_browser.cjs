// Read-only production browser check. No traces, screenshots or credential logging.
const { chromium } = require('../frontend/node_modules/@playwright/test');
(async () => {
  const password = process.env.DATA_MANAGEMENT_PASSWORD;
  if (!password) throw new Error('Management password environment variable is required');
  const access = process.env.API_ACCESS_TOKEN;
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      extraHTTPHeaders: access ? { Authorization: `Bearer ${access}` } : {},
    });
    const page = await context.newPage();
    await page.route('**/api/v1/analytics/pageviews', route => route.abort());
    const errors = [];
    const requests = [];
    page.on('pageerror', () => errors.push('pageerror'));
    page.on('request', request => {
      if (/\/api\/v1\/(data\/|jobs)/.test(request.url())) requests.push(request.url());
    });
    await page.goto(process.env.E2E_BASE_URL || 'http://127.0.0.1:18080/data');
    await page.getByLabel('后台管理密码').waitFor();
    if (requests.length) throw new Error('Locked page loaded management data');
    await page.getByLabel('后台管理密码').fill('invalid-test-password');
    await page.getByRole('button', { name: '解锁后台管理' }).click();
    await page.getByText('后台管理密码缺失或错误', { exact: true }).waitFor();
    if (requests.length !== 1) throw new Error('Rejected password started management polling');
    await page.getByLabel('后台管理密码').fill(password);
    await page.getByRole('button', { name: '解锁后台管理' }).click();
    await page.getByRole('heading', { name: '本地数据集' }).waitFor({ timeout: 60000 });
    await page.getByRole('cell', { name: /^未复权日线/ }).waitFor({ timeout: 60000 });
    if (await page.getByRole('heading', { name: '访问密钥', exact: true }).count()) throw new Error('Management error opened global access dialog');
    requests.length = 0;
    await page.reload();
    await page.getByLabel('后台管理密码').waitFor();
    if (await page.getByLabel('后台管理密码').inputValue()) throw new Error('Password persisted across reload');
    if (requests.length) throw new Error('Reloaded locked page loaded management data');
    await page.getByLabel('后台管理密码').fill(password);
    await page.getByRole('button', { name: '解锁后台管理' }).click();
    await page.getByRole('heading', { name: '本地数据集' }).waitFor({ timeout: 60000 });
    await page.getByRole('link', { name: '定投模拟', exact: true }).click();
    await page.getByRole('heading', { name: '定投模拟', exact: true }).waitFor();
    await page.getByRole('link', { name: '后台管理', exact: true }).click();
    await page.getByLabel('后台管理密码').waitFor();
    if (errors.length) throw new Error('Browser page errors');
    console.log(JSON.stringify({ locked_without_requests: true, wrong_password_locked: true,
      correct_password_unlocked: true, reload_relocks: true, leaving_relocks: true,
      dca_available: true, page_errors: 0 }));
  } finally { await browser.close(); }
})().catch(() => { console.error('Management browser validation failed (credentials omitted)'); process.exit(1); });
