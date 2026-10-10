import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { ErrorBanner } from "./common";

it("微小复权变化使用橙色提示，真实错误保留红色", () => {
  const view = render(
    <ErrorBanner message="2022-06-30：复权因子微小变化在0.1%容差内且前收盘参考价连续；按原始价格继续计算，不推定额外分红或送转股" />,
  );
  expect(screen.getByRole("alert").className).toContain("text-orange-800");
  view.rerender(<ErrorBanner message="数据读取失败" />);
  expect(screen.getByRole("alert").className).toContain("text-red-800");
});
