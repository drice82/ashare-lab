import { useEffect, useState } from "react";
import { Button } from "./ui/button";

export default function EmailCodeForm({
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
