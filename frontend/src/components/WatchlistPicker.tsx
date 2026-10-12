import { useState } from "react";
import { Link } from "react-router-dom";
import { useUserSession } from "../userSession";
import { useWatchlists, type WatchlistSource } from "../watchlists";
import { ErrorBanner } from "./common";
import { Button } from "./ui/button";

export default function WatchlistPicker({
  onApply,
  maximum,
  replace = false,
  hasMembers = false,
}: {
  onApply: (codes: string[]) => void;
  maximum?: number;
  replace?: boolean;
  hasMembers?: boolean;
}) {
  const user = useUserSession();
  const lists = useWatchlists();
  const [selection, setSelection] = useState("");
  const [confirm, setConfirm] = useState(false);
  const available = [
    ...lists.account.map((row) => ({
      row,
      source: "account" as WatchlistSource,
    })),
    ...lists.local.map((row) => ({ row, source: "local" as WatchlistSource })),
  ];
  const selected = available.find(
    (item) => `${item.source}:${item.row.id}` === selection,
  );
  const tooMany =
    maximum !== undefined && (selected?.row.codes.length ?? 0) > maximum;
  const valid = selected && selected.row.codes.length > 0 && !tooMany;
  function apply() {
    if (valid) {
      onApply([...selected.row.codes]);
      setConfirm(false);
    }
  }
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-0 flex-1">
          选择自选列表
          <select
            aria-label="选择自选列表"
            value={selected ? selection : ""}
            onChange={(e) => {
              setSelection(e.target.value);
              setConfirm(false);
            }}
          >
            <option value="">请选择列表</option>
            {(["account", "local"] as const).map((source) => (
              <optgroup
                key={source}
                label={source === "account" ? "账号列表" : "本机列表"}
              >
                {available
                  .filter((item) => item.source === source)
                  .map(({ row }) => (
                    <option key={row.id} value={`${source}:${row.id}`}>
                      {row.name} · {row.codes.length}只
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </label>
        <Button
          type="button"
          variant="outline"
          disabled={!valid}
          onClick={() => {
            if (replace && hasMembers) setConfirm(true);
            else apply();
          }}
        >
          {replace ? "应用自选列表" : "载入自选列表"}
        </Button>
        <Link to="/watchlists" className="text-xs underline">
          维护列表
        </Link>
      </div>
      {selected && (
        <p className="text-xs break-all">
          {selected.row.codes.length
            ? selected.row.codes.join("、")
            : "此列表为空，请先添加股票。"}
        </p>
      )}
      {tooMany && (
        <p role="alert" className="text-xs text-red-700">
          单个组合最多{maximum}只股票，请先调整自选列表。
        </p>
      )}
      <ErrorBanner
        message={lists.accountError || lists.localError || user.error}
      />
      {user.session && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={lists.loading}
          onClick={() => void lists.reload()}
        >
          刷新账号列表
        </Button>
      )}
      {!user.session && (
        <p className="text-xs text-muted-foreground">
          当前列表保存在本机；登录后可维护账号自选列表并跨设备使用。{" "}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={user.openLogin}
          >
            登录
          </Button>
        </p>
      )}
      {confirm && valid && (
        <div role="alertdialog" aria-label="替换组合确认" className="space-y-2">
          <p className="text-sm">
            应用列表将替换当前组合，逐股策略重置为买入持有，资金分配重置为等权。
          </p>
          <div className="flex gap-2">
            <Button type="button" onClick={apply}>
              确认替换
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirm(false)}
            >
              取消替换
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
