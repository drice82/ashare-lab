import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError } from "./api";
import { useUserSession } from "./userSession";

export type Watchlist = {
  id: string;
  name: string;
  codes: string[];
  revision: number;
  created_at: string;
  updated_at: string;
};
export type WatchlistSource = "local" | "account";
export type WatchlistDraft = Pick<Watchlist, "name" | "codes">;
export const localListsKey = "ashare-watchlists-v1";
const codePattern =
  /^(?:(?:600|601|603|605|688)\d{3}\.SH|(?:000|001|002|003|300|301)\d{3}\.SZ)$/;
export function validateWatchlist(draft: WatchlistDraft): WatchlistDraft {
  const name = draft.name.trim();
  if (!name || [...name].length > 80) throw new Error("列表名称须为1–80个字符");
  if (draft.codes.length > 6000) throw new Error("单列表最多6000只股票");
  if (!draft.codes.every((code) => codePattern.test(code)))
    throw new Error("自选列表只支持沪深A股代码");
  return { name, codes: [...new Set(draft.codes)] };
}
function readLocal(): Watchlist[] {
  const raw = localStorage.getItem(localListsKey);
  if (!raw) return [];
  const value: unknown = JSON.parse(raw);
  if (!Array.isArray(value)) throw new Error("本机列表数据格式无效");
  const ids = new Set<string>();
  const names = new Set<string>();
  return value.map((row: Watchlist) => {
    if (
      !row ||
      typeof row.id !== "string" ||
      typeof row.name !== "string" ||
      !Array.isArray(row.codes) ||
      !Number.isInteger(row.revision) ||
      row.revision < 1 ||
      typeof row.created_at !== "string" ||
      typeof row.updated_at !== "string"
    )
      throw new Error("本机列表数据格式无效");
    const valid = validateWatchlist(row);
    if (ids.has(row.id) || names.has(valid.name))
      throw new Error("本机列表数据格式无效");
    ids.add(row.id);
    names.add(valid.name);
    return { ...row, ...valid };
  });
}
type ListsContext = {
  local: Watchlist[];
  account: Watchlist[];
  loading: boolean;
  accountError: string;
  localError: string;
  reload: () => Promise<void>;
  save: (
    source: WatchlistSource,
    draft: WatchlistDraft,
    existing?: Watchlist,
  ) => Promise<Watchlist>;
  remove: (source: WatchlistSource, row: Watchlist) => Promise<void>;
};
const Context = createContext<ListsContext>({
  local: [],
  account: [],
  loading: false,
  accountError: "",
  localError: "",
  reload: async () => {},
  save: async () => {
    throw new Error("列表服务未初始化");
  },
  remove: async () => {
    throw new Error("列表服务未初始化");
  },
});
export const useWatchlists = () => useContext(Context);

export function WatchlistProvider({ children }: { children: ReactNode }) {
  const user = useUserSession();
  const owner = user.session?.owner_id ?? null;
  const identity = useRef(owner);
  const identityEpoch = useRef(0);
  if (identity.current !== owner) {
    identityEpoch.current += 1;
    identity.current = owner;
  }
  const [local, setLocal] = useState<Watchlist[]>([]);
  const [localError, setLocalError] = useState("");
  const [account, setAccount] = useState<{
    owner: string | null;
    lists: Watchlist[];
  }>({ owner: null, lists: [] });
  const [accountError, setAccountError] = useState("");
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const abort = useRef<AbortController | null>(null);
  const read = useCallback(() => {
    try {
      setLocal(readLocal());
      setLocalError("");
    } catch {
      setLocalError(
        "无法读取本机列表，请检查浏览器存储设置；草稿仅在当前页面保留。",
      );
    }
  }, []);
  useEffect(() => {
    read();
    const changed = (event: StorageEvent) => {
      if (event.key === localListsKey || event.key === null) read();
    };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [read]);
  const reload = useCallback(async () => {
    read();
    abort.current?.abort();
    const currentOwner = identity.current;
    const current = ++generation.current;
    if (!currentOwner) {
      setAccount({ owner: null, lists: [] });
      setAccountError("");
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    abort.current = controller;
    setLoading(true);
    setAccountError("");
    try {
      const value = await api<Watchlist[]>("/watchlists", {
        signal: controller.signal,
      });
      if (
        !controller.signal.aborted &&
        current === generation.current &&
        identity.current === currentOwner
      )
        setAccount({ owner: currentOwner, lists: value });
    } catch (e) {
      if (
        !controller.signal.aborted &&
        current === generation.current &&
        identity.current === currentOwner
      ) {
        if (e instanceof ApiError && e.status === 401) user.adopt(null);
        setAccountError(e instanceof Error ? e.message : "账号列表读取失败");
      }
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, [read, user.adopt]);
  useEffect(() => {
    setAccount({ owner: null, lists: [] });
    void reload();
    const focus = () => {
      void reload();
    };
    window.addEventListener("focus", focus);
    return () => {
      generation.current += 1;
      abort.current?.abort();
      window.removeEventListener("focus", focus);
    };
  }, [owner, reload]);
  function writeLocal(next: Watchlist[]) {
    try {
      localStorage.setItem(localListsKey, JSON.stringify(next));
    } catch {
      setLocalError(
        "无法持久保存本机列表；请检查浏览器存储设置，草稿仅在当前页面保留。",
      );
      throw new Error("无法持久保存本机列表，草稿已保留，请重试。");
    }
    setLocal(next);
    setLocalError("");
  }
  async function save(
    source: WatchlistSource,
    draft: WatchlistDraft,
    existing?: Watchlist,
  ) {
    const valid = validateWatchlist(draft);
    if (source === "local") {
      let rows: Watchlist[];
      try {
        rows = readLocal();
      } catch {
        throw new Error("无法读取本机存储，草稿已保留，无法持久保存。");
      }
      if (
        existing &&
        !rows.some(
          (row) => row.id === existing.id && row.revision === existing.revision,
        )
      )
        throw new Error("列表已在其他页面修改，请刷新列表后重试");
      if (
        rows.some((row) => row.name === valid.name && row.id !== existing?.id)
      )
        throw new Error("已有同名列表，请修改名称");
      const stamp = new Date().toISOString();
      const row: Watchlist = {
        id: existing?.id ?? crypto.randomUUID(),
        ...valid,
        revision: (existing?.revision ?? 0) + 1,
        created_at: existing?.created_at ?? stamp,
        updated_at: stamp,
      };
      writeLocal(
        existing
          ? rows.map((item) => (item.id === row.id ? row : item))
          : [...rows, row],
      );
      return row;
    }
    const currentOwner = identity.current;
    if (!currentOwner) throw new Error("请先登录账号");
    const current = identityEpoch.current;
    let row: Watchlist;
    try {
      row = await api<Watchlist>(
        existing ? `/watchlists/${existing.id}` : "/watchlists",
        {
          method: existing ? "PUT" : "POST",
          body: JSON.stringify(
            existing ? { ...valid, revision: existing.revision } : valid,
          ),
        },
      );
    } catch (e) {
      if (
        identity.current === currentOwner &&
        identityEpoch.current === current &&
        e instanceof ApiError &&
        e.status === 401
      )
        user.adopt(null);
      throw e;
    }
    if (identity.current !== currentOwner || identityEpoch.current !== current)
      throw new Error("登录或列表状态已变化，请刷新后重试");
    abort.current?.abort();
    generation.current += 1;
    setLoading(false);
    setAccountError("");
    setAccount((value) => ({
      owner: currentOwner,
      lists: [
        ...(value.owner === currentOwner ? value.lists : []).filter(
          (item) => item.id !== row.id,
        ),
        row,
      ],
    }));
    return row;
  }
  async function remove(source: WatchlistSource, row: Watchlist) {
    if (source === "local") {
      const rows = readLocal();
      if (
        !rows.some(
          (item) => item.id === row.id && item.revision === row.revision,
        )
      )
        throw new Error("列表已在其他页面修改，请刷新列表后重试");
      writeLocal(rows.filter((item) => item.id !== row.id));
      return;
    }
    const currentOwner = identity.current;
    if (!currentOwner) throw new Error("请先登录账号");
    const current = identityEpoch.current;
    try {
      await api(`/watchlists/${row.id}?revision=${row.revision}`, {
        method: "DELETE",
      });
    } catch (e) {
      if (
        identity.current === currentOwner &&
        identityEpoch.current === current &&
        e instanceof ApiError &&
        e.status === 401
      )
        user.adopt(null);
      throw e;
    }
    if (identity.current !== currentOwner || identityEpoch.current !== current)
      throw new Error("登录或列表状态已变化，请刷新后重试");
    abort.current?.abort();
    generation.current += 1;
    setLoading(false);
    setAccountError("");
    setAccount((value) => ({
      owner: currentOwner,
      lists: value.lists.filter((item) => item.id !== row.id),
    }));
  }
  return (
    <Context.Provider
      value={{
        local,
        account: account.owner === owner ? account.lists : [],
        loading,
        accountError: owner ? accountError : "",
        localError,
        reload,
        save,
        remove,
      }}
    >
      {children}
    </Context.Provider>
  );
}
