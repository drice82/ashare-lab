import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import StockInput from "./StockInput";

const stocks = [
  { stock_code: "000001.SZ", name: "平安银行" },
  { stock_code: "600000.SH", name: "浦发银行" },
];
function Harness({ multiple = false }: { multiple?: boolean }) {
  const [value, setValue] = useState("");
  return (
    <>
      <StockInput
        label="股票"
        value={value}
        onChange={setValue}
        multiple={multiple}
        dataVersion="pinned"
        asOfDate="2024-06-28"
      />
      <output data-testid="selection">{value}</output>
    </>
  );
}

describe("股票联想", () => {
  it("拼音联想、键盘选择并提交标准代码和指定版本", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ items: stocks })),
    );
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "股票" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "payh" } });
    await screen.findByRole("option", { name: /平安银行/ });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(screen.getByTestId("selection")).toHaveTextContent("000001.SZ");
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain(
      "data_version=pinned",
    );
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain(
      "as_of_date=2024-06-28",
    );
  });
  it("多选去重、删除及批量粘贴代码", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ items: stocks })),
    );
    render(<Harness multiple />);
    const input = screen.getByRole("combobox", { name: "股票" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "银行" } });
    fireEvent.click(await screen.findByRole("option", { name: /平安银行/ }));
    expect(screen.getByTestId("selection")).toHaveTextContent("000001.SZ");
    fireEvent.paste(input, {
      clipboardData: { getData: () => "000001.SZ, 600000.SH" },
    });
    expect(screen.getByTestId("selection")).toHaveTextContent(
      "000001.SZ, 600000.SH",
    );
    fireEvent.click(screen.getByRole("button", { name: /删除.*000001.SZ/ }));
    expect(screen.getByTestId("selection")).toHaveTextContent("600000.SH");
  });
  it("空结果与错误可见，旧查询响应不能覆盖新查询", async () => {
    let resolveOld!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.includes("search=old")
          ? new Promise<Response>((resolve) => {
              resolveOld = resolve;
            })
          : Promise.resolve(Response.json({ items: [] })),
      ),
    );
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "股票" });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "old" } });
    await waitFor(() => expect(resolveOld).toBeDefined());
    fireEvent.change(input, { target: { value: "new" } });
    await screen.findByText("未找到匹配股票");
    resolveOld(Response.json({ items: stocks }));
    await waitFor(() =>
      expect(
        screen.queryByRole("option", { name: /平安银行/ }),
      ).not.toBeInTheDocument(),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ message: "数据暂不可用" }, { status: 409 }),
      ),
    );
    fireEvent.change(input, { target: { value: "error" } });
    await screen.findByText("数据暂不可用");
  });
});
