import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import ResultsTable from "./ResultsTable";

it("分红逐笔显示，行情日移入副标题，不显示规则及结果列", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        items: [
          {
            stock_code: "000001.SZ",
            stock_name: "测试",
            factor_values: { close: 8 },
            match_status: "matched",
            rule_id: "A",
            recent_dividend: {
              events: [
                {
                  record_date: "2026-10-15",
                  cash_per_10: 2,
                  estimated_cash_10000: 250,
                },
              ],
            },
          },
        ],
        total: 1,
        summary: {
          matched: 1,
          insufficient_data: 0,
          as_of_date: "2026-10-09",
          effective_market_dates: ["2026-10-09"],
          missing_market_dates: 0,
        },
      }),
    ),
  );
  render(
    <MemoryRouter>
      <ResultsTable runId="run" />
    </MemoryRouter>,
  );
  await screen.findByText("2026-10-15");
  expect(screen.getByText("250.00 元")).toBeInTheDocument();
  const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers).toContain("10股派息");
  expect(headers).toContain("万元分红");
  expect(headers.indexOf("股权登记日")).toBe(headers.indexOf("股息率") + 1);
  expect(headers.join(" ")).not.toContain("税前");
  expect(screen.getByText(/万元分红按筛选日/)).toHaveTextContent("预计税前");
  expect(screen.getByText(/有效行情日：2026-10-09/)).toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "规则" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "结果" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "有效行情日" }),
  ).not.toBeInTheDocument();
});
