import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import StrategySelection from "./StrategySelection";
import { strategyDefaults } from "../comparison";

it("六策略默认全选，取消一项并调整独立参数", () => {
  const change = vi.fn();
  render(<StrategySelection selected={strategyDefaults} onChange={change} />);
  expect(screen.getAllByRole("checkbox")).toHaveLength(6);
  expect(
    screen
      .getAllByRole("checkbox")
      .every((input) => (input as HTMLInputElement).checked),
  ).toBe(true);
  fireEvent.click(screen.getByRole("checkbox", { name: "选择MACD交叉" }));
  expect(change.mock.calls[0][0]).toHaveLength(5);
  fireEvent.change(screen.getByLabelText("RSI超卖回升 周期"), {
    target: { value: "21" },
  });
  expect(
    change.mock.calls[1][0].find(
      (item: { id: string }) => item.id === "rsi_reversal",
    ).period,
  ).toBe(21);
  expect(screen.getByText(/MACD.*139/)).toBeInTheDocument();
});
