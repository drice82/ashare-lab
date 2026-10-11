import { useId, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

/** Keep panels mounted so drafts, result controls and history pagination survive. */
export default function ModuleTabs({
  mainLabel,
  historyLabel = "历史任务",
  resultLabel = "研究结果",
  result,
  history,
  children,
}: {
  mainLabel: string;
  historyLabel?: string;
  resultLabel?: string;
  result?: ReactNode;
  history: (active: boolean) => ReactNode;
  children: ReactNode;
}) {
  const [query] = useSearchParams();
  const tabs = [
    { key: "main", label: mainLabel, content: children },
    ...(result !== undefined
      ? [{ key: "result", label: resultLabel, content: result }]
      : []),
    {
      key: "history",
      label: historyLabel,
      content: history(query.get("tab") === "history"),
    },
  ];
  return <Tabs label={`${mainLabel}功能分页`} tabs={tabs} defaultKey="main" />;
}

export function Tabs({
  label,
  tabs,
  defaultKey,
}: {
  label: string;
  tabs: { key: string; label: string; content: ReactNode }[];
  defaultKey: string;
}) {
  const [query, setQuery] = useSearchParams();
  const id = useId();
  const active = tabs.some((tab) => tab.key === query.get("tab"))
    ? query.get("tab")
    : defaultKey;
  function choose(key: string) {
    setQuery((current) => {
      const next = new URLSearchParams(current);
      if (key === defaultKey) next.delete("tab");
      else next.set("tab", key);
      return next;
    });
  }
  return (
    <div className="min-w-0 space-y-5">
      <div
        role="tablist"
        aria-label={label}
        className="flex flex-wrap gap-2 border-b border-border pb-2"
      >
        {tabs.map((tab, index) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            id={`${id}-tab-${index}`}
            aria-controls={`${id}-panel-${index}`}
            aria-selected={active === tab.key}
            tabIndex={active === tab.key ? 0 : -1}
            className={`rounded-md px-4 py-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-primary ${active === tab.key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"}`}
            onClick={() => choose(tab.key)}
            onKeyDown={(event) => {
              if (
                !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
              )
                return;
              event.preventDefault();
              const next =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? tabs.length - 1
                    : (index +
                        (event.key === "ArrowRight" ? 1 : -1) +
                        tabs.length) %
                      tabs.length;
              choose(tabs[next].key);
              document.getElementById(`${id}-tab-${next}`)?.focus();
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab, index) => (
        <div
          key={tab.key}
          role="tabpanel"
          id={`${id}-panel-${index}`}
          aria-labelledby={`${id}-tab-${index}`}
          hidden={active !== tab.key}
          className="min-w-0 space-y-5"
        >
          {tab.content}
        </div>
      ))}
    </div>
  );
}
