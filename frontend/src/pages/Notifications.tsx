import { CheckCircle2, PauseCircle, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import {
  parameterLabels,
  strategies,
  type StrategyConfig,
} from "../comparison";
import { PageTitle, ErrorBanner } from "../components/common";
import { Tabs } from "../components/ModuleTabs";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";

type Channel = "webhook" | "email";
type HistoricalChannel = Channel | "bark";
const channelLabels: Record<HistoricalChannel, string> = {
  webhook: "Webhook",
  email: "邮件",
  bark: "Bark（旧渠道）",
};
type BoundChannel = {
  type: Channel;
  verified: boolean;
  status: string;
  target: string;
};
type Options = {
  channels: Record<Channel, boolean>;
  fixture_mode: boolean;
  auto_send: boolean;
  subscription_limit: number;
  auth?: { email_available: boolean; session_days: number };
};
type Subscription = {
  id: string;
  stock_code: string;
  stock_name: string | null;
  strategy: StrategyConfig;
  directions: string[];
  channels: Channel[];
  status: string;
  last_check: string | null;
  waiting_reason: string | null;
  effective_after: string;
};
type Delivery = {
  id: string;
  is_test?: boolean;
  created_at?: string;
  subscription_id: string;
  trade_date: string;
  direction: string;
  channel: HistoricalChannel;
  status: string;
  error: string | null;
  attempts: number;
  outcome: {
    reason: string;
    strategy?: StrategyConfig;
    stock_code?: string;
    stock_name?: string;
    evidence?: Record<string, number | string>;
    strategy_version: string;
    data_versions: Record<string, string>;
  };
};
const labels: Record<string, string> = {
  unknown: "发送状态未知",
  skipped: "已跳过",
  pending: "等待发送",
  sending: "正在发送",
  accepted: "服务端已接受",
  failed: "发送失败",
  cancelled: "已取消",
  expired: "已过期",
  active: "启用",
  paused: "暂停",
  disabled: "渠道停用",
};
const errors: Record<string, string> = {
  network_error: "网络错误，按计划重试",
  smtp_configuration_error: "SMTP认证或发件配置错误",
  smtp_tls_error: "SMTP TLS 校验失败",
  smtp_recipient_rejected: "收件地址被拒绝",
  bark_rejected: "旧 Bark 渠道拒绝凭据",
  webhook_rejected: "Webhook 接收服务拒绝请求",
  webhook_unavailable: "Webhook 接收服务暂不可用",
  webhook_invalid_response: "Webhook 接收服务响应无效",
  invalid_webhook_url: "Webhook 地址必须使用公网 HTTPS",
  email_unavailable: "邮件服务不可用",
  interrupted_delivery: "发送中断，可能重复投递",
  rate_limited: "上游限流，等待重试",
};
function ChannelStatus({ channel }: { channel?: BoundChannel }) {
  const active = channel?.status === "active" && channel.verified;
  const label = !channel
    ? "未绑定"
    : channel.status === "disabled"
      ? "已停用"
      : active
        ? "已启用"
        : "待验证";
  return (
    <span
      className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${active ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-slate-100 text-slate-600"}`}
    >
      {label}
    </span>
  );
}

const initial = {
  id: "ma_cross",
  short_window: 20,
  long_window: 60,
} as StrategyConfig;

export function strategySummary(config: StrategyConfig): string {
  const definition = strategies.find((item) => item.id === config.id);
  if (!definition) return "策略参数未知";
  return Object.keys(definition.defaults)
    .map((key) => {
      const value =
        config[key] ??
        definition.defaults[key as keyof typeof definition.defaults];
      const unit =
        key.includes("window") || key === "period"
          ? " 个交易日"
          : key === "deviations"
            ? " 倍"
            : "";
      return `${parameterLabels[key]} ${value}${unit}`;
    })
    .join(" · ");
}

type SessionInfo = { owner_id: string; email: string; expires_at: string };

function EmailCodeForm({
  change = false,
  busy,
  available,
  onSend,
  onVerify,
}: {
  change?: boolean;
  busy: boolean;
  available: boolean;
  onSend: (address: string) => Promise<number | null>;
  onVerify: (address: string, code: string) => Promise<void>;
}) {
  const [address, setAddress] = useState("");
  const [code, setCode] = useState("");
  const [pendingAddress, setPendingAddress] = useState("");
  const [resendAt, setResendAt] = useState(0);
  const [remaining, setRemaining] = useState(0);
  useEffect(() => {
    if (!resendAt) return;
    const tick = () =>
      setRemaining(Math.max(0, Math.ceil((resendAt - Date.now()) / 1000)));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [resendAt]);
  async function send() {
    const requestedAddress = address.trim();
    const seconds = await onSend(requestedAddress);
    if (seconds !== null) {
      setPendingAddress(requestedAddress);
      setCode("");
      setRemaining(seconds);
      setResendAt(Date.now() + seconds * 1000);
    }
  }
  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        className="flex flex-wrap items-end gap-3"
      >
        <label>
          {change ? "新邮箱" : "登录邮箱"}
          <input
            required
            type="email"
            autoComplete="email"
            maxLength={254}
            value={address}
            disabled={busy}
            onChange={(e) => {
              setAddress(e.target.value);
              setCode("");
              setPendingAddress("");
            }}
          />
        </label>
        <Button
          disabled={busy || !available || !address.trim() || remaining > 0}
        >
          {remaining > 0
            ? `${remaining}秒后重发`
            : change
              ? "发送更换验证码"
              : "发送登录验证码"}
        </Button>
      </form>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void onVerify(pendingAddress, code);
        }}
        className="flex flex-wrap items-end gap-3"
      >
        <label>
          {change ? "新邮箱验证码" : "登录验证码"}
          <input
            required
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            value={code}
            disabled={busy || !pendingAddress}
            onChange={(e) => setCode(e.target.value.replace(/[^0-9]/g, ""))}
          />
        </label>
        <Button
          disabled={
            busy || !available || !pendingAddress || !/^[0-9]{6}$/.test(code)
          }
        >
          {change ? "确认更换邮箱" : "验证码登录"}
        </Button>
      </form>
      <p className="text-xs">
        验证码有效10分钟，最多五次尝试；重发后旧码失效。
      </p>
    </div>
  );
}

export default function Notifications({
  unsubscribe = false,
}: {
  unsubscribe?: boolean;
}) {
  const [query, setQuery] = useSearchParams();
  const unsubscribeToken = useRef(window.location.hash.slice(1));
  const [options, setOptions] = useState<Options>();
  const [owner, setOwner] = useState(false);
  const [accountEmail, setAccountEmail] = useState("");
  const identityEpoch = useRef(0);
  const [bound, setBound] = useState<BoundChannel[]>([]);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [records, setRecords] = useState<Delivery[]>([]);
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [stock, setStock] = useState(query.get("stock_code") ?? "");
  const [matches, setMatches] = useState<
    { stock_code: string; stock_name: string }[]
  >([]);
  const [search, setSearch] = useState("");
  const [config, setConfig] = useState<StrategyConfig>(() => {
    try {
      const parsed = JSON.parse(
        query.get("strategy") ?? "null",
      ) as StrategyConfig | null;
      return parsed && parsed.id !== "buy_hold" ? parsed : initial;
    } catch {
      return initial;
    }
  });
  const initialConfig = useRef(config);
  const [selected, setSelected] = useState<Channel[]>([]);
  const [directions, setDirections] = useState(["buy", "sell"]);
  const [testChoice, setTestChoice] = useState<string | null>(null);
  const [edit, setEdit] = useState<string | null>(null);
  const [webhook, setWebhook] = useState("");
  const [stockIdentity, setStockIdentity] = useState<{
    stock_code: string;
    stock_name: string;
  }>();

  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const selectedInitialized = useRef(false);
  const strategy =
    strategies.find((item) => item.id === config.id) ?? strategies[1];
  const available = bound
    .filter((item) => item.verified && item.status === "active")
    .map((item) => item.type);
  async function reload() {
    const epoch = identityEpoch.current;
    try {
      const [ch, subs, deliveries, latestOptions] = await Promise.all([
        api<BoundChannel[]>("/notifications/channels"),
        api<Subscription[]>("/notifications/subscriptions"),
        api<{ items: Delivery[]; total: number }>(
          `/notifications/deliveries?limit=20&offset=${offset}${filter ? `&channel=${filter}` : ""}`,
        ),
        api<Options>("/notifications/options"),
      ]);
      if (epoch !== identityEpoch.current) return;
      setOptions(latestOptions);
      setBound(ch);
      setSubscriptions(subs);
      setRecords(deliveries.items);
      setTotal(deliveries.total);
      if (!selectedInitialized.current) {
        setSelected(
          ch
            .filter((item) => item.verified && item.status === "active")
            .map((item) => item.type),
        );
        selectedInitialized.current = true;
      }
    } catch (e) {
      if (epoch === identityEpoch.current) throw e;
    }
  }
  function clearPrivate() {
    identityEpoch.current += 1;
    setOwner(false);
    setAccountEmail("");
    setBound([]);
    setSubscriptions([]);
    setRecords([]);
    setMatches([]);
    setStockIdentity(undefined);
    setSelected([]);
    setTestChoice(null);
    setTotal(0);
    setOffset(0);
    setFilter("");
    setEdit(null);
    setWebhook("");
    setSearch("");
    setStock(query.get("stock_code") ?? "");
    setConfig(initialConfig.current);
    setDirections(["buy", "sell"]);
    selectedInitialized.current = false;
    setQuery(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("tab", "settings");
        return next;
      },
      { replace: true },
    );
  }
  function reportError(e: unknown) {
    if (e instanceof ApiError && e.status === 401) {
      clearPrivate();
      setNotice("");
    }
    setError(e instanceof Error ? e.message : "请求失败");
  }
  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (e) {
      reportError(e);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (unsubscribe) {
      if (window.location.hash.length > 1)
        unsubscribeToken.current = window.location.hash.slice(1);
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
      return;
    }
    let disposed = false;
    const epoch = identityEpoch.current;
    void api<Options>("/notifications/options")
      .then((value) => {
        if (!disposed) setOptions(value);
      })
      .catch((e) => setError(String(e)));
    void api<SessionInfo>("/notifications/session")
      .then((value) => {
        if (!disposed && epoch === identityEpoch.current) {
          setOwner(true);
          setAccountEmail(value.email);
        }
      })
      .catch((e) => {
        if (
          !disposed &&
          epoch === identityEpoch.current &&
          !(e instanceof ApiError && e.status === 401)
        )
          reportError(e);
      });
    return () => {
      disposed = true;
    };
  }, [unsubscribe]);
  useEffect(() => {
    if (owner && !unsubscribe) void reload().catch(reportError);
  }, [owner, filter, offset, unsubscribe]);
  useEffect(() => {
    if (!owner || !search) {
      setMatches([]);
      return;
    }
    const abort = new AbortController();
    const epoch = identityEpoch.current;
    const timer = setTimeout(() => {
      void api<{ items: typeof matches }>(
        `/notifications/instruments?search=${encodeURIComponent(search)}`,
        { signal: abort.signal },
      )
        .then((value) => {
          if (!abort.signal.aborted && epoch === identityEpoch.current)
            setMatches(value.items);
        })
        .catch(() => {});
    }, 250);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [search, owner]);
  useEffect(() => {
    if (!owner || !/^[036]\d{5}\.(SH|SZ)$/.test(stock)) return;
    const abort = new AbortController();
    const epoch = identityEpoch.current;
    void api<{ items: { stock_code: string; stock_name: string }[] }>(
      `/notifications/instruments?search=${encodeURIComponent(stock)}`,
      { signal: abort.signal },
    )
      .then((value) => {
        if (!abort.signal.aborted && epoch === identityEpoch.current)
          setStockIdentity(
            value.items.find((item) => item.stock_code === stock),
          );
      })
      .catch(() => {
        if (!abort.signal.aborted && epoch === identityEpoch.current)
          setStockIdentity(undefined);
      });
    return () => abort.abort();
  }, [stock, owner]);
  useEffect(() => {
    if (!notice || unsubscribe) return;
    const timer = window.setTimeout(() => setNotice(""), 4000);
    return () => window.clearTimeout(timer);
  }, [notice, unsubscribe]);
  async function sendCode(
    address: string,
    change = false,
  ): Promise<number | null> {
    let seconds: number | null = null;
    await action(async () => {
      const value = await api<{ message: string; resend_after: number }>(
        change
          ? "/notifications/account/email/verification"
          : "/notifications/auth/email/verification",
        { method: "POST", body: JSON.stringify({ address }) },
      );
      setNotice(value.message);
      seconds = value.resend_after;
    });
    return seconds;
  }
  async function verifyCode(address: string, code: string, change = false) {
    await action(async () => {
      const value = await api<SessionInfo>(
        change
          ? "/notifications/account/email/verify"
          : "/notifications/auth/email/verify",
        { method: "POST", body: JSON.stringify({ address, code }) },
      );
      clearPrivate();
      setOwner(true);
      setAccountEmail(value.email);
      setQuery(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("tab");
          return next;
        },
        { replace: true },
      );
      setNotice(change ? "邮箱已更换，其他设备需重新登录" : "邮箱登录成功");
      await reload();
    });
  }
  async function signOut() {
    await action(async () => {
      await api("/notifications/session", { method: "DELETE" });
      clearPrivate();
      setNotice("已退出当前设备的登录");
    });
  }
  async function channelAction(path: string, body?: object, method = "POST") {
    await action(async () => {
      const value = await api<{ message?: string }>(`/notifications${path}`, {
        method,
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      setNotice(value.message ?? "渠道已更新");
      setWebhook("");
      selectedInitialized.current = false;
      await reload();
    });
  }
  async function testSubscription(sub: Subscription, direction: string) {
    await action(async () => {
      const result = await api<{
        results: { channel: Channel; status: string; message?: string }[];
      }>(`/notifications/subscriptions/${sub.id}/test`, {
        method: "POST",
        body: JSON.stringify({ direction }),
      });
      setNotice(
        result.results
          .map(
            (row) =>
              `${channelLabels[row.channel]}：${row.status === "accepted" ? "测试通知已发送" : (row.message ?? labels[row.status] ?? "已跳过")}`,
          )
          .join("；"),
      );
      setTestChoice(null);
      await reload();
    });
  }
  async function save() {
    await action(async () => {
      await api(`/notifications/subscriptions${edit ? `/${edit}` : ""}`, {
        method: edit ? "PATCH" : "POST",
        body: JSON.stringify({
          stock_code: stock,
          strategy: config,
          directions,
          channels: selected,
        }),
      });
      setEdit(null);
      setNotice("已保存，从下一交易日起生效");
      await reload();
    });
  }
  function toggle<T extends string>(
    value: T,
    values: T[],
    setter: (items: T[]) => void,
  ) {
    setter(
      values.includes(value)
        ? values.filter((item) => item !== value)
        : [...values, value],
    );
  }
  if (unsubscribe)
    return (
      <Card className="p-6 space-y-4">
        <h1 className="text-xl font-semibold">退订此订阅的邮件通知</h1>
        <p>确认后仅取消此订阅的邮件通知，Webhook 通知保留。</p>
        <ErrorBanner message={error} />
        {notice ? (
          <p role="status">{notice}</p>
        ) : (
          <Button
            disabled={busy || !unsubscribeToken.current}
            onClick={() =>
              void action(async () => {
                await api("/notifications/email/unsubscribe", {
                  method: "POST",
                  body: JSON.stringify({ token: unsubscribeToken.current }),
                });
                unsubscribeToken.current = "";
                setNotice("已退订此订阅的邮件通知");
              })
            }
          >
            确认退订邮件
          </Button>
        )}
      </Card>
    );
  const loginPrompt = (
    <Card className="p-5">
      <p>请先到「账号与渠道」使用邮箱验证码登录。</p>
    </Card>
  );
  const settingsPanel = (
    <div className="space-y-5">
      <Card className="p-5 space-y-3">
        <h2 className="font-semibold">邮箱账号</h2>
        <p className="text-sm">
          无需设置密码或网站访问密钥。登录状态最多保留
          {options?.auth?.session_days ?? 180}天，手机与电脑可同时登录。
        </p>
        {owner ? (
          <>
            <p>已登录：{accountEmail}</p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void signOut()}
            >
              退出登录
            </Button>
            <details>
              <summary>更换登录与通知邮箱</summary>
              <p className="my-3 text-sm">
                验证新邮箱后保留订阅与 Webhook
                渠道，其他设备需重新登录。新邮箱不能属于其他账号。
              </p>
              <EmailCodeForm
                key={accountEmail}
                change
                busy={busy}
                available={
                  options?.auth?.email_available ??
                  options?.channels.email ??
                  false
                }
                onSend={(address) => sendCode(address, true)}
                onVerify={(address, code) => verifyCode(address, code, true)}
              />
            </details>
          </>
        ) : (
          <>
            {options &&
              !(options.auth?.email_available ?? options.channels.email) && (
                <p>邮箱登录服务暂不可用，请联系管理员检查 SMTP 配置。</p>
              )}
            <EmailCodeForm
              busy={busy}
              available={
                options?.auth?.email_available ??
                options?.channels.email ??
                false
              }
              onSend={sendCode}
              onVerify={verifyCode}
            />
            <p className="text-sm">
              首次验证成功自动创建账号；登录邮箱同时用于接收邮件通知。
            </p>
          </>
        )}
      </Card>

      {owner && (
        <div className="grid gap-5 md:grid-cols-2">
          <Card className="p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-semibold">邮件渠道</h2>
              <ChannelStatus
                channel={bound.find((item) => item.type === "email")}
              />
            </div>
            {!options?.channels.email && (
              <p>邮件渠道尚未启用或 SMTP 服务不可用。</p>
            )}
            <p>收件地址使用已验证的登录邮箱。停用邮件通知不会影响邮箱登录。</p>
            <Button
              variant="outline"
              disabled={
                busy || !options?.channels.email || available.includes("email")
              }
              onClick={() => void channelAction("/channels/email/enable")}
            >
              启用邮件通知
            </Button>
            {bound
              .filter((item) => item.type === "email")
              .map((item) => (
                <p key={item.type}>
                  {item.target} ·{" "}
                  {item.status === "disabled"
                    ? "配置已保留"
                    : item.verified
                      ? "已验证"
                      : "待验证"}
                </p>
              ))}
            <Button
              variant="outline"
              disabled={busy || !available.includes("email")}
              onClick={() => void channelAction("/channels/email/test")}
            >
              测试邮件
            </Button>{" "}
            <Button
              variant="outline"
              disabled={busy || !available.includes("email")}
              onClick={() =>
                void channelAction("/channels/email", undefined, "DELETE")
              }
            >
              停用邮件通知
            </Button>
          </Card>
          <Card className="p-5 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <h2 className="font-semibold">Webhook 通知</h2>
              <ChannelStatus
                channel={bound.find((item) => item.type === "webhook")}
              />
            </div>
            {!options?.channels.webhook && (
              <p>管理员尚未配置有效加密密钥，Webhook 绑定不可用。</p>
            )}
            <p className="text-sm text-muted-foreground">
              填写公网 HTTPS 地址，接收服务需支持 POST
              JSON（title、body、group）。
            </p>
            <p className="text-xs leading-5 text-muted-foreground">
              对接 Bark：从 App 获取 Device Key，填写{" "}
              <code className="break-all">
                https://api.day.app/&lt;Device Key&gt;
              </code>
              ，然后绑定并发送测试。自建 Bark 使用对应公网 HTTPS 地址。
              <a
                className="ml-1 underline"
                href="https://github.com/Finb/Bark/blob/master/docs/tutorial.md"
                target="_blank"
                rel="noreferrer"
              >
                对接说明
              </a>
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void channelAction(
                  "/channels/webhook",
                  { target: webhook },
                  "PUT",
                );
              }}
            >
              <label>
                Webhook 地址
                <input
                  type="password"
                  placeholder="https://example.com/webhook"
                  maxLength={2048}
                  autoComplete="off"
                  value={webhook}
                  onChange={(e) => setWebhook(e.target.value)}
                />
              </label>
              <Button
                className="mt-3"
                disabled={busy || !webhook || !options?.channels.webhook}
              >
                绑定并发送测试
              </Button>
            </form>
            {bound
              .filter((item) => item.type === "webhook")
              .map((item) => (
                <p key={item.type}>
                  {item.target} ·{" "}
                  {item.status === "disabled"
                    ? "配置已保留"
                    : item.verified
                      ? "已验证"
                      : "待验证"}
                </p>
              ))}
            <Button
              variant="outline"
              disabled={busy || !available.includes("webhook")}
              onClick={() => void channelAction("/channels/webhook/test")}
            >
              测试 Webhook
            </Button>{" "}
            <Button
              variant="outline"
              disabled={
                busy ||
                !options?.channels.webhook ||
                available.includes("webhook") ||
                !bound.some((item) => item.type === "webhook")
              }
              onClick={() => void channelAction("/channels/webhook/enable")}
            >
              启用 Webhook
            </Button>{" "}
            <Button
              variant="outline"
              disabled={busy || !available.includes("webhook")}
              onClick={() =>
                void channelAction("/channels/webhook", undefined, "DELETE")
              }
            >
              停用 Webhook
            </Button>
          </Card>
        </div>
      )}
    </div>
  );
  const subscriptionsPanel = owner ? (
    <div className="space-y-5">
      <Card className="p-5 space-y-4">
        <h2 className="font-semibold">{edit ? "修改订阅" : "创建订阅"}</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
          className="space-y-4"
        >
          <div className="grid gap-3 md:grid-cols-2">
            <label>
              搜索股票
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="代码、名称或拼音"
              />
            </label>
            <label>
              股票代码
              <input
                required
                pattern="[036][0-9]{5}\.(SH|SZ)"
                value={stock}
                onChange={(e) => setStock(e.target.value.toUpperCase())}
              />
            </label>
          </div>
          {stock && (
            <p className="text-sm font-medium">
              {stockIdentity?.stock_code === stock && stockIdentity.stock_name
                ? stockIdentity.stock_name
                : "名称未知"}{" "}
              · {stock}
            </p>
          )}
          {matches.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {matches.map((item) => (
                <button
                  type="button"
                  className="rounded border p-2"
                  key={item.stock_code}
                  onClick={() => {
                    setStock(item.stock_code);
                    setStockIdentity(item);
                    setSearch("");
                  }}
                >
                  {item.stock_name} · {item.stock_code}
                </button>
              ))}
            </div>
          )}
          <label>
            策略
            <select
              aria-label="策略"
              value={config.id}
              onChange={(e) => {
                const item = strategies.find(
                  (item) => item.id === e.target.value,
                )!;
                setConfig({ id: item.id, ...item.defaults });
              }}
            >
              {strategies
                .filter((item) => item.id !== "buy_hold")
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
            </select>
          </label>
          <p className="text-sm">{strategy.rule}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            {Object.keys(strategy.defaults).map((key) => (
              <label key={key}>
                {parameterLabels[key]}
                <input
                  type="number"
                  required
                  step={
                    ["deviations", "lower", "upper"].includes(key) ? 0.1 : 1
                  }
                  value={
                    config[key] ??
                    strategy.defaults[key as keyof typeof strategy.defaults] ??
                    ""
                  }
                  onChange={(e) =>
                    setConfig({ ...config, [key]: Number(e.target.value) })
                  }
                />
              </label>
            ))}
          </div>
          <fieldset>
            <legend>信号方向</legend>
            {["buy", "sell"].map((side) => (
              <label
                key={side}
                className="!inline-flex !items-center gap-2 mr-4"
              >
                <input
                  className="!w-auto"
                  type="checkbox"
                  checked={directions.includes(side)}
                  onChange={() => toggle(side, directions, setDirections)}
                />
                {side === "buy" ? "买入" : "卖出"}
              </label>
            ))}
          </fieldset>
          <fieldset>
            <legend>发送渠道（至少选择一个已验证渠道）</legend>
            {(["email", "webhook"] as Channel[]).map((kind) => (
              <label
                key={kind}
                className="!inline-flex !items-center gap-2 mr-4"
              >
                <input
                  className="!w-auto"
                  type="checkbox"
                  checked={selected.includes(kind)}
                  disabled={
                    !available.includes(kind) && !selected.includes(kind)
                  }
                  onChange={() => toggle(kind, selected, setSelected)}
                />
                {channelLabels[kind]}
              </label>
            ))}
          </fieldset>
          {available.length === 0 && (
            <p className="text-sm text-muted-foreground">
              请先在{" "}
              <button
                type="button"
                className="underline"
                onClick={() =>
                  setQuery((current) => {
                    const next = new URLSearchParams(current);
                    next.set("tab", "settings");
                    return next;
                  })
                }
              >
                账号与渠道
              </button>{" "}
              中启用通知渠道。
            </p>
          )}
          <Button
            disabled={
              busy ||
              !stock ||
              directions.length === 0 ||
              selected.length === 0 ||
              selected.some((kind) => !available.includes(kind))
            }
          >
            {edit ? "保存修改" : "创建订阅"}
          </Button>{" "}
          {edit && (
            <Button
              type="button"
              variant="outline"
              onClick={() => setEdit(null)}
            >
              取消修改
            </Button>
          )}
        </form>
      </Card>
      <Card className="p-5 space-y-3">
        <h2 className="font-semibold">
          我的订阅 · {subscriptions.length} /{" "}
          {options?.subscription_limit ?? 20}
        </h2>
        {!subscriptions.length && <p>尚无订阅。</p>}
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {subscriptions.map((sub) => (
            <article
              key={sub.id}
              aria-label={`${labels[sub.status]}的订阅`}
              className={`min-w-0 rounded-lg border p-3 space-y-3 ${sub.status === "active" ? "border-emerald-200 bg-white" : "border-border bg-muted/50"}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold truncate">
                    {sub.stock_name || "名称未知"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {sub.stock_code}
                  </p>
                </div>
                <span
                  className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-1 text-xs font-medium ${sub.status === "active" ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}
                >
                  {sub.status === "active" ? (
                    <CheckCircle2 size={13} />
                  ) : (
                    <PauseCircle size={13} />
                  )}
                  {labels[sub.status]}
                </span>
              </div>
              <p className="text-sm font-medium">
                {strategies.find((item) => item.id === sub.strategy.id)
                  ?.label ?? "策略未知"}
              </p>
              <p className="text-xs break-all">
                {strategySummary(sub.strategy)}
              </p>
              <p className="text-xs text-muted-foreground">
                {sub.directions
                  .map((side) => (side === "buy" ? "买入" : "卖出"))
                  .join("、")}{" "}
                · 渠道：
                {sub.channels.map((kind) => channelLabels[kind]).join("、") ||
                  "无，请重新配置"}
              </p>
              <p className="text-xs leading-5 text-muted-foreground">
                最近检查：{sub.last_check ?? "尚未检查"} ·{" "}
                {sub.waiting_reason ??
                  `生效：${sub.effective_after}之后的交易日`}
              </p>
              <Button
                size="sm"
                variant="outline"
                disabled={
                  busy || !sub.channels.some((kind) => available.includes(kind))
                }
                onClick={() => {
                  if (sub.directions.length === 1)
                    void testSubscription(sub, sub.directions[0]);
                  else setTestChoice(testChoice === sub.id ? null : sub.id);
                }}
              >
                测试通知
              </Button>{" "}
              {!sub.channels.some((kind) => available.includes(kind)) && (
                <p className="text-xs text-muted-foreground">
                  请先启用并验证订阅的发送渠道。
                </p>
              )}
              {testChoice === sub.id && (
                <div
                  className="my-2 flex flex-wrap items-center gap-2 rounded border p-2"
                  aria-label="选择测试信号方向"
                >
                  <span className="text-xs">选择示例方向：</span>
                  {sub.directions.map((direction) => (
                    <Button
                      key={direction}
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() => void testSubscription(sub, direction)}
                    >
                      测试{direction === "buy" ? "买入" : "卖出"}信号
                    </Button>
                  ))}
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => setTestChoice(null)}
                  >
                    取消
                  </Button>
                </div>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setEdit(sub.id);
                  setStock(sub.stock_code);
                  setStockIdentity({
                    stock_code: sub.stock_code,
                    stock_name: sub.stock_name ?? "",
                  });
                  setConfig(sub.strategy);
                  setDirections(sub.directions);
                  setSelected(sub.channels);
                }}
              >
                修改
              </Button>{" "}
              <Button
                size="sm"
                variant="outline"
                disabled={
                  busy || (sub.status === "paused" && sub.channels.length === 0)
                }
                onClick={() =>
                  void action(async () => {
                    await api(`/notifications/subscriptions/${sub.id}`, {
                      method: "PATCH",
                      body: JSON.stringify({
                        status: sub.status === "active" ? "paused" : "active",
                      }),
                    });
                    await reload();
                  })
                }
              >
                {sub.status === "active" ? "暂停" : "恢复"}
              </Button>{" "}
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void action(async () => {
                    await api(`/notifications/subscriptions/${sub.id}`, {
                      method: "DELETE",
                    });
                    await reload();
                  })
                }
              >
                删除
              </Button>
            </article>
          ))}
        </div>
      </Card>
    </div>
  ) : (
    loginPrompt
  );
  const recordsPanel = owner ? (
    <Card className="p-5 space-y-3">
      <h2 className="font-semibold">发送记录</h2>
      <p className="text-sm">
        “服务端已接受”不表示手机收到、邮件进入收件箱或已读。网络超时后重试可能重复投递。
      </p>
      <label>
        按渠道查询
        <select
          aria-label="按渠道查询"
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">全部渠道</option>
          <option value="webhook">Webhook</option>
          <option value="email">邮件</option>
        </select>
      </label>
      {records.map((row) => (
        <div key={row.id} className="rounded border p-3 text-sm">
          <p>
            {row.is_test && (
              <span className="mr-2 rounded bg-slate-100 px-2 py-1 text-xs">
                测试
              </span>
            )}
            {row.outcome.stock_name} {row.outcome.stock_code} ·{" "}
            {row.is_test ? row.created_at : row.trade_date} ·{" "}
            {row.direction === "buy" ? "买入" : "卖出"} ·{" "}
            {channelLabels[row.channel]} · {labels[row.status]} · 已尝试
            {row.attempts}次
          </p>
          {row.error && (
            <p>{errors[row.error] ?? "渠道发送失败，请检查配置"}</p>
          )}
          <details>
            <summary>{row.is_test ? "示例内容" : "触发依据及版本"}</summary>
            <p>
              {row.outcome.reason} · 算法 {row.outcome.strategy_version}
            </p>
            {row.is_test && row.outcome.strategy && (
              <p>策略：{strategySummary(row.outcome.strategy)}</p>
            )}
            <p>指标：{JSON.stringify(row.outcome.evidence ?? {})}</p>
            <pre className="overflow-auto text-xs">
              {JSON.stringify(row.outcome.data_versions, null, 2)}
            </pre>
          </details>
        </div>
      ))}
      {!records.length && <p>暂无发送记录。</p>}
      <div className="flex gap-3">
        <Button
          variant="outline"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 20))}
        >
          上一页
        </Button>
        <span>{total}条</span>
        <Button
          variant="outline"
          disabled={offset + 20 >= total}
          onClick={() => setOffset(offset + 20)}
        >
          下一页
        </Button>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void action(reload)}
        >
          刷新记录
        </Button>
      </div>
    </Card>
  ) : (
    loginPrompt
  );
  return (
    <div className="space-y-5">
      <PageTitle
        title="信号通知"
        subtitle="邮箱验证码登录 · 收盘后日线技术信号 · Webhook 与邮件独立投递"
      />
      <ErrorBanner message={error} />
      {notice && (
        <div
          role="status"
          className="flex items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800"
        >
          <span>{notice}</span>
          <button
            type="button"
            aria-label="关闭操作提示"
            onClick={() => setNotice("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {options?.fixture_mode && (
        <p className="rounded-lg bg-amber-50 p-3">
          fixture 数据 · 自动信号推送已禁用；测试通知带“测试”标识。
        </p>
      )}
      {options && !options.auto_send && (
        <p className="text-sm text-muted-foreground">
          自动发送尚未启用，需管理员完成真实行情、Webhook 和邮件实收验收。
        </p>
      )}
      <Tabs
        label="信号通知功能分页"
        defaultKey={owner ? "subscriptions" : "settings"}
        tabs={[
          {
            key: "subscriptions",
            label: "订阅管理",
            content: subscriptionsPanel,
          },
          { key: "records", label: "发送记录", content: recordsPanel },
          { key: "settings", label: "账号与渠道", content: settingsPanel },
        ]}
      />
    </div>
  );
}
