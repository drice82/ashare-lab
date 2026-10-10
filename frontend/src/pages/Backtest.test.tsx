import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Backtest from "./Backtest";

vi.mock("../components/DcaChart", () => ({ default: () => <div>资产曲线</div> }));
const definition = {
  stock_code: "000001.SZ", start_date: "2024-06-03", end_date: "2024-06-28",
  initial_cash: "100000", strategy: "ma_cross", short_window: 20, long_window: 60,
  commission_rate: "0.0003", minimum_commission: "5", transfer_rate: "0.00001",
  stamp_rate: "0.0005", data_versions: { daily: "pinned" },
};
function mockFetch(complete = true, running = false) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/backtest/options")) return Response.json({ data_versions: {daily:"latest"},
      latest_market_date: "2024-06-28", fixture_mode: true, defaults: definition });
    if (url.includes("/jobs?")) return Response.json([]);
    if (url.endsWith("/backtest/run")) return Response.json({id:"b1"});
    if (url.endsWith("/cancel")) return Response.json({status:"cancelled"});
    if (url.endsWith("/backtest/runs/b1")) return Response.json({id:"b1", kind:"backtest",
      status:running?"running":"succeeded", progress:0.5,payload:definition,result:null,error:null,logs:[]});
    if (url.includes("/results?")) return Response.json({summary:{complete,contributed:100000,cash:1000,
      shares:1000,position_value:10000,receivable:0,dividends:0,fees:10,dividend_tax_paid:20,
      tax_liability:0,total_assets:complete?11000:null,profit:complete?-89000:null,
      return_rate:complete?-0.89:null,max_drawdown:complete?0.89:null,pending_orders:0,
      buy_count:1,sell_count:1,confirmed_through:"2024-06-28",valuation_date:"2024-06-28"},
      items:[],total:0,curve:[],fixture_mode:true,warnings:complete?[]:["分红口径未知"],
      parameters:definition,data_versions:definition.data_versions,assumptions:{}});
    return Response.json([]);
  }));
}
describe("单股回测", () => {
  it("提交策略、费用及固定版本，显示测试模式", async () => {
    mockFetch();
    render(<MemoryRouter><Backtest /></MemoryRouter>);
    await screen.findByText(/测试数据模式/);
    expect(screen.getByRole("heading", {name:"单股回测"})).toBeInTheDocument();
    expect(screen.getByText("交易费用与数据版本").closest("details")).not.toHaveAttribute("open");
    fireEvent.change(screen.getByLabelText("股票代码"),{target:{value:"000001.SZ"}});
    fireEvent.change(screen.getByLabelText("开始交易日"),{target:{value:"2024-06-03"}});
    fireEvent.click(screen.getByRole("button",{name:"开始回测"}));
    await screen.findByText("最大回撤");
    const call=vi.mocked(fetch).mock.calls.find(([url])=>String(url).endsWith("/backtest/run"));
    const body=JSON.parse(call![1]!.body as string);
    expect(body.strategy).toBe("ma_cross");
    expect(body.data_versions).toEqual({daily:"latest"});
    expect(body.stamp_rate).toBe("0.0005");
  });
  it("刷新恢复历史参数，明确未知收益", async () => {
    mockFetch(false);
    render(<MemoryRouter initialEntries={["/backtest?run=b1"]}><Backtest /></MemoryRouter>);
    await screen.findByText(/完整收益未知/);
    await waitFor(()=>expect(screen.getByLabelText("股票代码")).toHaveValue("000001.SZ"));
    expect(screen.getByDisplayValue("pinned")).toBeInTheDocument();
    expect(screen.getByText("分红口径未知")).toBeInTheDocument();
  });
  it("取消运行任务，不提前请求结果",async()=>{
    mockFetch(true,true);
    render(<MemoryRouter initialEntries={["/backtest?run=b1"]}><Backtest /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button",{name:"取消任务"}));
    await waitFor(()=>expect(vi.mocked(fetch).mock.calls.some(([url])=>String(url).endsWith("/cancel"))).toBe(true));
    expect(vi.mocked(fetch).mock.calls.some(([url])=>String(url).includes("/results?"))).toBe(false);
  });
});

it("删除当前历史回测清除结果并保留参数", async () => {
  mockFetch();
  const base = vi.mocked(fetch).getMockImplementation()!;
  let deleted = false;
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    if (options?.method === "DELETE") {deleted = true;return Response.json({deleted:true});}
    if (String(url).includes("/jobs?")) return Response.json(deleted ? [] : [{id:"b1",kind:"backtest",status:"succeeded",progress:1,
      payload:definition,created_at:"2024-06-28",result:null}]);
    return base(url, options);
  });
  render(<MemoryRouter initialEntries={["/backtest?run=b1"]}><Backtest /></MemoryRouter>);
  await screen.findByText("最大回撤");
  fireEvent.click(await screen.findByRole("button",{name:"删除任务 b1"}));
  fireEvent.click(screen.getByRole("button",{name:"确认删除"}));
  await waitFor(() => expect(screen.queryByText("最大回撤")).not.toBeInTheDocument());
  expect(screen.getByLabelText("股票代码")).toHaveValue("000001.SZ");
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/runs/null"))).toBe(false);
});
