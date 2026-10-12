import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { UserSessionProvider, useUserSession } from "./userSession";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.endsWith("/session")
        ? Response.json({ message: "未登录" }, { status: 401 })
        : Response.json({
            fixture_mode: true,
            auth: { email_available: true },
          }),
    ),
  );
});
function ResearchDraft() {
  const user = useUserSession();
  return (
    <>
      <input aria-label="研究草稿" defaultValue="123456" />
      <button onClick={user.openLogin}>打开邮箱登录</button>
    </>
  );
}
function open() {
  render(
    <UserSessionProvider>
      <ResearchDraft />
    </UserSessionProvider>,
  );
  const trigger = screen.getByRole("button", { name: "打开邮箱登录" });
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
}
it("页内登录自动聚焦邮箱，Escape关闭并返回焦点，保留研究草稿", async () => {
  const trigger = open();
  const email = await screen.findByLabelText("登录邮箱");
  await waitFor(() => expect(email).toHaveFocus());
  fireEvent.keyDown(email, { key: "Escape" });
  expect(
    screen.queryByRole("dialog", { name: "邮箱登录" }),
  ).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(screen.getByLabelText("研究草稿")).toHaveValue("123456");
});
it("登录弹窗中的Tab和Shift+Tab不会进入背景页面", async () => {
  open();
  const email = await screen.findByLabelText("登录邮箱");
  await waitFor(() => expect(email).toHaveFocus());
  fireEvent.keyDown(email, { key: "Tab" });
  const close = screen.getByRole("button", { name: "关闭登录" });
  expect(close).toHaveFocus();
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(email).toHaveFocus();
});
