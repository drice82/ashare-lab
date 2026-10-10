import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";

interface Instrument {
  stock_code: string;
  name: string;
}
interface Props {
  label: string;
  value: string;
  onChange: (value: string) => void;
  multiple?: boolean;
  required?: boolean;
  searchOnly?: boolean;
  asOfDate?: string;
  dataVersion?: string;
  className?: string;
}
const codePattern = /^[036]\d{5}\.(SH|SZ)$/;
const splitCodes = (value: string) =>
  value.split(/[,，;；\s]+/).filter(Boolean);

export default function StockInput({
  label,
  value,
  onChange,
  multiple = false,
  required = false,
  searchOnly = false,
  asOfDate,
  dataVersion,
  className = "",
}: Props) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [composing, setComposing] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [items, setItems] = useState<Instrument[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const text = multiple ? query : value;
  const selected = multiple ? splitCodes(value) : [];
  const open = focused && !dismissed && !!text.trim() && !composing;
  useEffect(() => {
    setItems([]);
    setError("");
    setActive(0);
    setLoading(false);
    if (!open) return;
    setLoading(true);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      const params = new URLSearchParams({ search: text, page_size: "10" });
      if (asOfDate) params.set("as_of_date", asOfDate);
      if (dataVersion) params.set("data_version", dataVersion);
      void api<{ items: Instrument[] }>(`/instruments?${params}`, {
        signal: controller.signal,
      })
        .then((result) => {
          if (!controller.signal.aborted) setItems(result.items ?? []);
        })
        .catch((e: unknown) => {
          if (!controller.signal.aborted)
            setError(e instanceof Error ? e.message : "股票联想暂不可用");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [open, text, asOfDate, dataVersion]);
  const candidates = items.filter(
    (item) => !selected.includes(item.stock_code),
  );
  function select(item: Instrument) {
    setNames((old) => ({ ...old, [item.stock_code]: item.name }));
    onChange(
      multiple
        ? [...new Set([...selected, item.stock_code])].join(", ")
        : item.stock_code,
    );
    setQuery("");
    setItems([]);
    setDismissed(true);
    input.current?.focus();
  }
  function addCodes(raw: string): boolean {
    const codes = splitCodes(raw.toUpperCase());
    if (!codes.length || !codes.every((code) => codePattern.test(code)))
      return false;
    onChange([...new Set([...selected, ...codes])].join(", "));
    setQuery("");
    setDismissed(true);
    return true;
  }
  return (
    <div
      className={`relative min-w-0 ${className}`}
      ref={root}
      onBlur={(e) => {
        if (!root.current?.contains(e.relatedTarget as Node | null)) {
          setFocused(false);
          setDismissed(false);
        }
      }}
    >
      {multiple && selected.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {selected.map((code) => (
            <span
              key={code}
              className="inline-flex items-center gap-2 rounded border bg-muted px-2 py-1 text-xs text-black"
            >
              {names[code] ? `${names[code]} · ` : ""}
              {code}
              <button
                type="button"
                aria-label={`删除 ${code}`}
                onClick={() =>
                  onChange(selected.filter((c) => c !== code).join(", "))
                }
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        ref={input}
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-activedescendant={
          open && candidates[active] ? `${id}-option-${active}` : undefined
        }
        autoComplete="off"
        required={required && !multiple}
        pattern={
          !multiple && !searchOnly ? "[036][0-9]{5}\\.(SH|SZ)" : undefined
        }
        placeholder="名称、代码、拼音首字母或全拼"
        value={text}
        onFocus={() => {
          setFocused(true);
          setDismissed(false);
        }}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
        onChange={(e) => {
          setDismissed(false);
          if (multiple) setQuery(e.target.value);
          else onChange(e.target.value);
        }}
        onPaste={(e) => {
          if (multiple && addCodes(e.clipboardData.getData("text")))
            e.preventDefault();
        }}
        onKeyDown={(e) => {
          if (composing || e.nativeEvent.isComposing) return;
          if (e.key === "Escape") {
            setDismissed(true);
            return;
          }
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setDismissed(false);
            if (candidates.length)
              setActive(
                (index) =>
                  (index +
                    (e.key === "ArrowDown" ? 1 : -1) +
                    candidates.length) %
                  candidates.length,
              );
          }
          if (e.key === "Enter" && open && candidates[active]) {
            e.preventDefault();
            select(candidates[active]);
          } else if (e.key === "Enter" && multiple && text.trim()) {
            e.preventDefault();
            if (!addCodes(text))
              setError("请从联想列表选择股票，或输入完整代码");
          }
        }}
      />
      {open && (
        <div className="absolute left-0 right-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-lg border bg-white shadow-lg">
          <ul id={`${id}-list`} role="listbox" aria-label={`${label}联想结果`}>
            {candidates.map((item, index) => (
              <li
                key={item.stock_code}
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={active === index}
                onClick={() => select(item)}
              >
                <button
                  type="button"
                  tabIndex={-1}
                  className={`flex w-full justify-between gap-3 px-3 py-2 text-left text-sm text-black ${active === index ? "bg-muted" : "hover:bg-muted"}`}
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseEnter={() => setActive(index)}
                >
                  <span>{item.name}</span>
                  <span className="font-mono text-xs">{item.stock_code}</span>
                </button>
              </li>
            ))}
          </ul>
          {!candidates.length && (
            <p
              className="p-3 text-xs text-muted-foreground"
              role={error ? "alert" : "status"}
            >
              {error || (loading ? "正在查找股票…" : "未找到匹配股票")}
            </p>
          )}
        </div>
      )}
      {multiple && (
        <p className="mt-2 text-xs text-muted-foreground">
          选择后加入股票池；支持粘贴逗号或空格分隔的完整代码。
        </p>
      )}
    </div>
  );
}
