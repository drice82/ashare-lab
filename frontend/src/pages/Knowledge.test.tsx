import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import App from "../App";
import { strategies } from "../comparison";

afterEach(() => vi.unstubAllGlobals());

function HistoryControls() {
  const navigate = useNavigate();
  const location = useLocation();
  return (
    <>
      <output aria-label="当前位置">{location.pathname + location.hash}</output>
      <button onClick={() => navigate(-1)}>后退</button>
      <button onClick={() => navigate(1)}>前进</button>
    </>
  );
}

function openKnowledge(path = "/knowledge") {
  const fetch = vi.fn<typeof globalThis.fetch>(() =>
    Promise.reject(new Error("离线环境")),
  );
  vi.stubGlobal("fetch", fetch);
  const scroll = vi.fn();
  const original = HTMLElement.prototype.scrollIntoView;
  HTMLElement.prototype.scrollIntoView = scroll;
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <HistoryControls />
    </MemoryRouter>,
  );
  return {
    fetch,
    scroll,
    restore: () => {
      HTMLElement.prototype.scrollIntoView = original;
    },
  };
}

it("知识库无需研究 API 或密钥，上报失败时六策略及手核示例完整可读", async () => {
  const { fetch, restore } = openKnowledge();
  try {
    await screen.findByRole("heading", { level: 1, name: "知识库" });
    expect(screen.getByRole("navigation", { name: "策略目录" })).toBeVisible();
    for (const strategy of strategies) {
      const chapter = screen.getByRole("region", { name: strategy.label });
      expect(
        within(chapter).getByText(
          "教学示例 · 指标数值为人工设定，不代表真实行情",
        ),
      ).toBeVisible();
      for (const value of Object.values(strategy.defaults)) {
        expect(
          within(chapter).getAllByText(String(value)).length,
        ).toBeGreaterThan(0);
      }
      expect(
        within(chapter).getByRole("heading", { name: "局限" }),
      ).toBeVisible();
    }
    expect(screen.getByText(/开始日前 139 个交易日/)).toBeVisible();
    expect(screen.getByText(/开始日前 71 个交易日/)).toBeVisible();
    expect(screen.getByText(/总体标准差/)).toBeVisible();
    expect(screen.getByText(/窗口均不含当天/)).toBeVisible();
    expect(screen.getByText(/幸存者偏差/)).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "访问密钥" }),
    ).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/notifications/session",
      expect.anything(),
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/analytics/pageviews",
      expect.objectContaining({ credentials: "omit" }),
    );
  } finally {
    restore();
  }
});

it("章节深链接、目录点击与前进后退会定位并聚焦对应标题", async () => {
  const { scroll, restore } = openKnowledge("/knowledge#rsi_reversal");
  try {
    const rsi = await screen.findByRole("heading", {
      level: 2,
      name: "RSI超卖回升",
    });
    await waitFor(() => expect(rsi).toHaveFocus());
    const directory = screen.getByRole("navigation", { name: "策略目录" });
    fireEvent.click(within(directory).getByRole("link", { name: "MACD交叉" }));
    const macd = screen.getByRole("heading", { level: 2, name: "MACD交叉" });
    await waitFor(() => expect(macd).toHaveFocus());
    expect(screen.getByLabelText("当前位置")).toHaveTextContent(
      "/knowledge#macd_cross",
    );
    fireEvent.click(screen.getByRole("button", { name: "后退" }));
    await waitFor(() => expect(rsi).toHaveFocus());
    fireEvent.click(screen.getByRole("button", { name: "前进" }));
    await waitFor(() => expect(macd).toHaveFocus());
    expect(scroll).toHaveBeenCalledWith({ block: "start" });
  } finally {
    restore();
  }
});

it("未知章节仍显示知识库，外部资料均标明语言并安全打开新窗口", async () => {
  const { fetch, restore } = openKnowledge("/knowledge#unknown");
  try {
    await screen.findByRole("heading", { level: 1, name: "知识库" });
    const external = screen
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href")?.startsWith("https://"));
    expect(external).toHaveLength(6);
    for (const link of external) {
      expect(link).toHaveTextContent("英文");
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
      expect(link).toHaveAccessibleName(/新窗口/);
    }
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/notifications/session",
      expect.anything(),
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/analytics/pageviews",
      expect.objectContaining({ credentials: "omit" }),
    );
  } finally {
    restore();
  }
});
