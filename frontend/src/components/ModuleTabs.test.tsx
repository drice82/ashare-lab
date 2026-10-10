import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { expect, it, vi } from "vitest";
import ModuleTabs from "./ModuleTabs";
import { TaskHistory } from "./TaskHistory";

function Navigation() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="地址">{location.search}</output>
      <button onClick={() => navigate(-1)}>后退</button>
      <button onClick={() => navigate(1)}>前进</button>
    </>
  );
}

it("页签URL保留run和其他参数，键盘及浏览器历史恢复选择", () => {
  render(
    <MemoryRouter initialEntries={["/backtest?run=old&extra=keep"]}>
      <Navigation />
      <ModuleTabs mainLabel="回测" history={() => <p>历史列表</p>}>
        <input aria-label="草稿" defaultValue="原始" />
      </ModuleTabs>
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("草稿"), {
    target: { value: "编辑" },
  });
  const main = screen.getByRole("tab", { name: "回测" });
  main.focus();
  fireEvent.keyDown(main, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "历史任务" })).toHaveFocus();
  expect(screen.getByLabelText("地址")).toHaveTextContent(
    "run=old&extra=keep&tab=history",
  );
  expect(screen.getByLabelText("草稿")).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "后退" }));
  expect(main).toHaveAttribute("aria-selected", "true");
  expect(screen.getByLabelText("草稿")).toHaveValue("编辑");
  fireEvent.click(screen.getByRole("button", { name: "前进" }));
  expect(screen.getByRole("tab", { name: "历史任务" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

it("刷新直达历史，切换后仍保留列表页码", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      Response.json(
        Array.from({ length: 20 }, (_, i) => ({
          id: `${url.includes("offset=20") ? "old" : "new"}-${i}`,
          kind: "backtest",
          status: "succeeded",
          progress: 1,
          created_at: "2024-06-28",
          payload: {},
          result: null,
        })),
      ),
    ),
  );
  render(
    <MemoryRouter initialEntries={["/backtest?tab=history"]}>
      <ModuleTabs
        mainLabel="回测"
        history={(active) => <TaskHistory kind="backtest" active={active} />}
      >
        主功能
      </ModuleTabs>
    </MemoryRouter>,
  );
  await screen.findByRole("button", { name: "删除任务 new-0" });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: "删除任务 old-0" });
  fireEvent.click(screen.getByRole("tab", { name: "回测" }));
  fireEvent.click(screen.getByRole("tab", { name: "历史任务" }));
  await screen.findByRole("button", { name: "删除任务 old-0" });
  expect(screen.getByText("第 2 页")).toBeVisible();
});

it("历史停用期间停止请求和轮询，重新激活读取最新列表", async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json([])),
  );
  try {
    const view = render(<TaskHistory kind="backtest" active={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(fetch).not.toHaveBeenCalled();
    view.rerender(<TaskHistory kind="backtest" active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    view.rerender(<TaskHistory kind="backtest" active={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(fetch).toHaveBeenCalledTimes(2);
    view.rerender(<TaskHistory kind="backtest" active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetch).toHaveBeenCalledTimes(3);
    view.unmount();
  } finally {
    vi.useRealTimers();
  }
});

it("结果分页独立展示并保留表单草稿，刷新可直达结果", () => {
  render(
    <MemoryRouter initialEntries={["/backtest?run=old&tab=result"]}>
      <ModuleTabs
        mainLabel="回测"
        resultLabel="回测结果"
        result={<p>独立结果</p>}
        history={() => <p>历史列表</p>}
      >
        <input aria-label="参数草稿" defaultValue="保留" />
      </ModuleTabs>
    </MemoryRouter>,
  );
  expect(screen.getByText("独立结果")).toBeVisible();
  expect(screen.getByLabelText("参数草稿")).not.toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "回测" }));
  expect(screen.getByLabelText("参数草稿")).toHaveValue("保留");
  expect(screen.getByText("独立结果")).not.toBeVisible();
  fireEvent.keyDown(screen.getByRole("tab", { name: "回测" }), { key: "End" });
  expect(screen.getByRole("tab", { name: "历史任务" })).toHaveFocus();
});
