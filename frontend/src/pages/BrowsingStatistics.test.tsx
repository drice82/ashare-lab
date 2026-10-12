import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import BrowsingStatistics from "./BrowsingStatistics";

const summary = {
  start_date: "2026-09-12",
  end_date: "2026-10-11",
  today: "2026-10-11",
  earliest_date: "2026-07-14",
  timezone: "Asia/Shanghai",
  retention_days: 90,
  totals: { pageviews: 4, visitors: 2 },
  daily: [{ date: "2026-10-11", pageviews: 4, visitors: 2 }],
  pages: [
    {
      page: "screening",
      name: "股票筛选",
      pageviews: 3,
      visitors: 2,
      share: 0.75,
    },
    {
      page: "knowledge",
      name: "知识库",
      pageviews: 1,
      visitors: 1,
      share: 0.25,
    },
  ],
};

it("shows hand-counted statistics, uses protected requests and preserves dates while hidden", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => summary }),
  );
  const view = render(
    <BrowsingStatistics password="test-only" active fixtureMode />,
  );
  await screen.findByText("75.0%");
  expect(screen.getByText("固定测试环境（fixture）")).toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByLabelText("浏览开始日期")).toHaveValue("2026-09-12"),
  );
  expect(
    screen.getByRole("img", { name: "每日浏览次数与独立访客趋势" }),
  ).toBeVisible();
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toMatchObject({
    "X-Data-Management-Password": "test-only",
  });
  fireEvent.click(screen.getByRole("button", { name: "最近7天" }));
  await waitFor(() =>
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/v1/data/analytics?start_date=2026-10-05&end_date=2026-10-11",
      expect.anything(),
    ),
  );
  fireEvent.change(screen.getByLabelText("浏览开始日期"), {
    target: { value: "2026-10-01" },
  });
  const calls = vi.mocked(fetch).mock.calls.length;
  view.rerender(<BrowsingStatistics password="test-only" active={false} />);
  expect(fetch).toHaveBeenCalledTimes(calls);
  expect(screen.getByLabelText("浏览开始日期")).toHaveValue("2026-10-01");
  view.rerender(<BrowsingStatistics password="test-only" active />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(calls + 1));
  expect(screen.getByLabelText("浏览开始日期")).toHaveValue("2026-10-01");
});

it("reports an empty range and rejects reversed dates before fetching", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          ...summary,
          totals: { pageviews: 0, visitors: 0 },
          pages: [],
          daily: [],
        }),
      }),
  );
  render(<BrowsingStatistics password="test-only" active />);
  await screen.findByText("所选日期范围暂无浏览记录。");
  fireEvent.change(screen.getByLabelText("浏览开始日期"), {
    target: { value: "2026-10-11" },
  });
  fireEvent.change(screen.getByLabelText("浏览结束日期"), {
    target: { value: "2026-10-10" },
  });
  fireEvent.click(screen.getByRole("button", { name: "查询统计" }));
  expect(screen.getByText("请选择最近90天内的有效日期范围。")).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("renders request failures and allows a manual retry", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValueOnce(new Error("统计暂不可用"))
      .mockResolvedValue({ ok: true, json: async () => summary }),
  );
  render(<BrowsingStatistics password="test-only" active />);
  await screen.findByText("统计暂不可用");
  fireEvent.click(screen.getByRole("button", { name: "刷新统计" }));
  await screen.findByText("75.0%");
});
