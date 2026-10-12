import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { X } from "lucide-react";
import { api, ApiError } from "./api";
import EmailCodeForm from "./components/EmailCodeForm";
import { ErrorBanner } from "./components/common";
import { Button } from "./components/ui/button";

export type SessionInfo = {
  owner_id: string;
  email: string;
  expires_at: string;
};
type SessionContext = {
  session: SessionInfo | null;
  status: "loading" | "guest" | "authenticated" | "error";
  error: string;
  openLogin: () => void;
  adopt: (session: SessionInfo | null) => void;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
};
const Context = createContext<SessionContext | null>(null);
export const useOptionalUserSession = () => useContext(Context);
const anonymous: SessionContext = {
  session: null,
  status: "guest",
  error: "",
  openLogin: () => {},
  adopt: () => {},
  refresh: async () => {},
  signOut: async () => {},
};
export const useUserSession = () => useContext(Context) ?? anonymous;

export function UserSessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [status, setStatus] = useState<SessionContext["status"]>("loading");
  const [error, setError] = useState("");
  const [loginOpen, setLoginOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [options, setOptions] = useState<{
    fixture_mode: boolean;
    auth?: { email_available: boolean };
  }>();
  const epoch = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const broadcast = useRef<BroadcastChannel | null>(null);
  const dialog = useRef<HTMLElement | null>(null);
  const loginTrigger = useRef<HTMLElement | null>(null);
  const adopt = useCallback((value: SessionInfo | null) => {
    epoch.current += 1;
    controller.current?.abort();
    setSession(value);
    setStatus(value ? "authenticated" : "guest");
    setError("");
    broadcast.current?.postMessage("changed");
  }, []);
  const refresh = useCallback(async () => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const current = ++epoch.current;
    try {
      const value = await api<SessionInfo>("/notifications/session", {
        signal: abort.signal,
      });
      if (!abort.signal.aborted && current === epoch.current) {
        setSession(value);
        setStatus("authenticated");
        setError("");
      }
    } catch (e) {
      if (abort.signal.aborted || current !== epoch.current) return;
      if (e instanceof ApiError && e.status === 401) {
        setSession(null);
        setStatus("guest");
        setError("");
      } else {
        setStatus("error");
        setError(e instanceof Error ? e.message : "登录状态查询失败");
      }
    }
  }, []);
  useEffect(() => {
    void refresh();
    const expired = () => adopt(null);
    const focus = () => {
      void refresh();
    };
    window.addEventListener("ashare-user-session-expired", expired);
    window.addEventListener("focus", focus);
    if (typeof BroadcastChannel !== "undefined") {
      broadcast.current = new BroadcastChannel("ashare-user-session");
      broadcast.current.onmessage = () => {
        epoch.current += 1;
        controller.current?.abort();
        setSession(null);
        setStatus("loading");
        void refresh();
      };
    }
    return () => {
      epoch.current += 1;
      controller.current?.abort();
      window.removeEventListener("ashare-user-session-expired", expired);
      window.removeEventListener("focus", focus);
      broadcast.current?.close();
      broadcast.current = null;
    };
  }, [adopt, refresh]);
  useEffect(() => {
    if (!loginOpen) return;
    const abort = new AbortController();
    setOptions(undefined);
    setError("");
    setNotice("");
    dialog.current?.querySelector<HTMLInputElement>("input")?.focus();
    void api<typeof options>("/notifications/options", { signal: abort.signal })
      .then((value) => {
        if (!abort.signal.aborted) setOptions(value);
      })
      .catch((e) => {
        if (!abort.signal.aborted)
          setError(e instanceof Error ? e.message : "登录服务查询失败");
      });
    return () => {
      abort.abort();
      if (loginTrigger.current?.isConnected) loginTrigger.current.focus();
    };
  }, [loginOpen]);
  async function signOut() {
    await api("/notifications/session", { method: "DELETE" });
    adopt(null);
  }
  async function send(address: string): Promise<number | null> {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const value = await api<{ message: string; resend_after: number }>(
        "/notifications/auth/email/verification",
        { method: "POST", body: JSON.stringify({ address }) },
      );
      setNotice(value.message);
      return value.resend_after;
    } catch (e) {
      setError(e instanceof Error ? e.message : "验证码发送失败");
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function verify(address: string, code: string) {
    setBusy(true);
    setError("");
    try {
      const value = await api<SessionInfo>("/notifications/auth/email/verify", {
        method: "POST",
        body: JSON.stringify({ address, code }),
      });
      adopt(value);
      setLoginOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Context.Provider
      value={{
        session,
        status,
        error,
        openLogin: () => {
          loginTrigger.current =
            document.activeElement instanceof HTMLElement
              ? document.activeElement
              : null;
          setLoginOpen(true);
        },
        adopt,
        refresh,
        signOut,
      }}
    >
      {children}
      {loginOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-5">
          <section
            ref={dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="user-login-title"
            className="w-full max-w-lg rounded-xl bg-white p-6 shadow-xl"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !busy) {
                event.preventDefault();
                setLoginOpen(false);
              }
              if (event.key !== "Tab") return;
              const elements = Array.from(
                dialog.current?.querySelectorAll<HTMLElement>(
                  'button:not(:disabled), input:not(:disabled), [href], [tabindex="0"]',
                ) ?? [],
              );
              const first = elements[0];
              const last = elements.at(-1);
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 id="user-login-title">邮箱登录</h2>
              <Button
                type="button"
                variant="ghost"
                aria-label="关闭登录"
                disabled={busy}
                onClick={() => setLoginOpen(false)}
              >
                <X size={16} />
              </Button>
            </div>
            <p className="mb-4 text-sm text-muted-foreground">
              登录后可跨设备维护自选列表。本机列表与账号列表保持独立。
            </p>
            {options?.fixture_mode && (
              <p className="mb-3 text-xs">
                当前使用 fixture 数据，邮件接入需单独验证。
              </p>
            )}
            {options && !options.auth?.email_available && (
              <p className="mb-3 text-sm">
                邮箱登录服务暂不可用，请联系管理员检查 SMTP 配置。
              </p>
            )}
            <ErrorBanner message={error} />
            {notice && (
              <p role="status" className="mb-3 text-sm">
                {notice}
              </p>
            )}
            <EmailCodeForm
              busy={busy}
              available={Boolean(options?.auth?.email_available)}
              onSend={send}
              onVerify={verify}
            />
          </section>
        </div>
      )}
    </Context.Provider>
  );
}
