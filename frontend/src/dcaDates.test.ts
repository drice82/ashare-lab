import { describe, expect, it } from "vitest";
import { dcaPeriodStart } from "./dcaDates";
import type { DcaOptions } from "./types";

const options: DcaOptions = {
  data_versions: {}, latest_market_date: "2024-02-29", fixture_mode: true,
  defaults: { monthly_amount: "3000", monthly_day: 1, commission_rate: "0", minimum_commission: "0", transfer_rate: "0" },
  calendar_start: "2020-01-01", trading_days: ["2021-03-01", "2023-02-28", "2024-02-29"],
};
describe("定投日期", () => {
  it("闰日按目标年月底处理、休市顺延，不缩短缺失历史", () => {
    expect(dcaPeriodStart("2024-02-29", 1, options)).toBe("2023-02-28");
    expect(dcaPeriodStart("2024-02-29", 3, options)).toBe("2021-03-01");
    expect(dcaPeriodStart("2024-02-29", 5, options)).toBe("");
    expect(dcaPeriodStart("", 1, options)).toBe("");
  });
});
