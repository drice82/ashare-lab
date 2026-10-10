import {
  parameterLabels,
  strategies,
  warmupDays,
  type StrategyConfig,
} from "../comparison";

export default function StrategySelection({
  selected,
  onChange,
}: {
  selected: StrategyConfig[];
  onChange: (value: StrategyConfig[]) => void;
}) {
  return (
    <fieldset className="mt-4 space-y-2">
      <legend className="font-medium">
        策略对比 · 已选 {selected.length} / 6
      </legend>
      <p className="text-xs text-muted-foreground">
        各策略独立使用相同资金和费用；只选一个也可运行。预热只计算指标，不成交。
      </p>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {strategies.map((strategy) => {
          const current = selected.find((item) => item.id === strategy.id);
          const config: StrategyConfig = current ?? {
            id: strategy.id,
            ...strategy.defaults,
          };
          return (
            <div key={strategy.id} className="min-w-0 rounded-lg border p-2.5">
              <label className="!flex !items-center !gap-2 font-medium">
                <input
                  className="!w-auto"
                  type="checkbox"
                  aria-label={`选择${strategy.label}`}
                  checked={!!current}
                  onChange={() =>
                    onChange(
                      current
                        ? selected.filter((item) => item.id !== strategy.id)
                        : [...selected, config],
                    )
                  }
                />
                {strategy.label}
              </label>
              <p className="my-1 text-xs text-muted-foreground">
                {strategy.rule}
              </p>
              <p className="text-xs text-muted-foreground">
                {strategy.label}预热：开始日前 {warmupDays(config)} 个交易日
              </p>
              {Object.keys(strategy.defaults).length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs">
                    {strategy.label}参数
                  </summary>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    {Object.keys(strategy.defaults).map((key) => (
                      <label key={key} className="text-xs">
                        {parameterLabels[key]}
                        <input
                          disabled={!current}
                          aria-label={`${strategy.label} ${parameterLabels[key]}`}
                          type="number"
                          required={!!current}
                          min={
                            key === "deviations"
                              ? 0.5
                              : ["lower", "upper"].includes(key)
                                ? 0.1
                                : key === "long_window"
                                  ? 3
                                  : 2
                          }
                          max={
                            key === "deviations"
                              ? 5
                              : ["lower", "upper"].includes(key)
                                ? 99.9
                                : key === "short_window"
                                  ? 249
                                  : 250
                          }
                          step={
                            key === "deviations" ||
                            ["lower", "upper"].includes(key)
                              ? 0.1
                              : 1
                          }
                          value={config[key]}
                          onChange={(event) =>
                            onChange(
                              selected.map((item) =>
                                item.id === strategy.id
                                  ? {
                                      ...item,
                                      [key]: Number(event.target.value),
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                      </label>
                    ))}
                  </div>
                </details>
              )}
            </div>
          );
        })}
      </div>
      {!selected.length && (
        <p role="alert" className="text-sm text-red-700">
          请至少选择一个策略。
        </p>
      )}
    </fieldset>
  );
}
