import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { TaskHistory } from "./TaskHistory";

const job = {
  id: "old-job",
  kind: "screening",
  status: "failed",
  progress: 0,
  created_at: "2024-01-01",
  payload: {
    members: [{ stock_code: "000001.SZ" }],
    rule_id: "A",
    as_of_date: "2024-01-01",
  },
  result: null,
};
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});
it.each(["sync", "screening", "dca", "backtest", "portfolio"])(
  "一键清空 %s 全部历史并回调非当前页任务",
  async (kind) => {
    let deleted = false;
    vi.mocked(fetch).mockImplementation(async (_url, options) => {
      if (options?.method === "DELETE") {
        deleted = true;
        return Response.json({
          deleted: true,
          ids: ["old-job", "other-page"],
          skipped_active: 1,
        });
      }
      return Response.json(deleted ? [] : [{ ...job, kind }]);
    });
    const onDeleted = vi.fn();
    render(
      <MemoryRouter>
        <TaskHistory kind={kind as "sync"} onDeleted={onDeleted} />
      </MemoryRouter>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "删除全部历史任务" }),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("所有分页");
    expect(
      vi.mocked(fetch).mock.calls.every(([, o]) => o?.method !== "DELETE"),
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "确认删除全部" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("other-page"));
    expect(onDeleted).toHaveBeenCalledWith("old-job");
    expect(
      vi.mocked(fetch).mock.calls.find(([, o]) => o?.method === "DELETE")?.[0],
    ).toBe(`/api/v1/${kind === "sync" ? "data/sync" : `${kind}/runs`}`);
    expect(await screen.findByText(/保留了 1 个活动任务/)).toBeInTheDocument();
  },
);
it.each(["sync", "screening", "dca", "backtest", "portfolio"])(
  "删除 %s 历史并回调清除详情",
  async (kind) => {
    let deleted = false;
    vi.mocked(fetch).mockImplementation(async (_url, options) => {
      if (options?.method === "DELETE") {
        deleted = true;
        return Response.json({ deleted: true });
      }
      return Response.json(deleted ? [] : [{ ...job, kind }]);
    });
    const onDeleted = vi.fn();
    render(
      <MemoryRouter>
        <TaskHistory
          kind={kind as "sync"}
          password="test-management"
          onDeleted={onDeleted}
        />
      </MemoryRouter>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "删除任务 old-job" }),
    );
    expect(fetch).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ method: "DELETE" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("old-job"));
    const call = vi
      .mocked(fetch)
      .mock.calls.find(([, options]) => options?.method === "DELETE")!;
    expect(call[0]).toBe(
      `/api/v1/${kind === "sync" ? "data/sync" : `${kind}/runs`}/old-job`,
    );
    expect(
      (call[1]?.headers as Record<string, string>)[
        "X-Data-Management-Password"
      ],
    ).toBe("test-management");
    expect(await screen.findByText(/暂无任务/)).toBeInTheDocument();
  },
);
it("取消确认且活动任务不能删除", async () => {
  vi.mocked(fetch).mockResolvedValue(
    Response.json([job, { ...job, id: "running", status: "running" }]),
  );
  render(
    <MemoryRouter>
      <TaskHistory kind="screening" />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "删除任务 old-job" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "保留任务" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "删除任务 running" }),
  ).toBeDisabled();
  expect(
    vi.mocked(fetch).mock.calls.every(([, o]) => o?.method !== "DELETE"),
  ).toBe(true);
});
it("删除错误保留记录，可重试", async () => {
  vi.mocked(fetch).mockImplementation(async (_url, options) =>
    options?.method === "DELETE"
      ? Response.json({ message: "清理失败" }, { status: 503 })
      : Response.json([job]),
  );
  render(
    <MemoryRouter>
      <TaskHistory kind="screening" />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "删除任务 old-job" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  expect(await screen.findByText("清理失败")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认删除" })).toBeEnabled();
});
it("分页访问较早历史并在空页回退", async () => {
  let deleted = false;
  vi.mocked(fetch).mockImplementation(async (url, options) => {
    if (options?.method === "DELETE") {
      deleted = true;
      return Response.json({ deleted: true });
    }
    if (String(url).includes("offset=20"))
      return Response.json(deleted ? [] : [job]);
    return Response.json(
      Array.from({ length: 20 }, (_, i) => ({ ...job, id: `job-${i}` })),
    );
  });
  render(
    <MemoryRouter>
      <TaskHistory kind="screening" />
    </MemoryRouter>,
  );
  await screen.findByRole("button", { name: "删除任务 job-0" });
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "删除任务 old-job" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  await screen.findByRole("button", { name: "删除任务 job-0" });
  expect(screen.getByText("第 1 页")).toBeInTheDocument();
});
