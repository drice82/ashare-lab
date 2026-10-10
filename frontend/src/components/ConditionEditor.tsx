import { Plus, X } from "lucide-react";
import { Button } from "./ui/button";
import { percentFactors } from "../format";
import type { Condition, Factor, Group } from "../types";

const operators = [
  ["gt", "大于"],
  ["gte", "大于等于"],
  ["lt", "小于"],
  ["lte", "小于等于"],
  ["eq", "等于"],
];
export function ConditionEditor({
  group,
  factors,
  onChange,
  depth = 1,
}: {
  group: Group;
  factors: Factor[];
  onChange: (value: Group) => void;
  depth?: number;
}) {
  function update(index: number, node: Condition | Group) {
    onChange({
      ...group,
      children: group.children.map((child, i) => (i === index ? node : child)),
    });
  }
  return (
    <div className="rounded-lg border border-border bg-[#f9fbf7] p-3">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <label className="!flex !items-center !gap-2">
          <input
            type="checkbox"
            checked={group.enabled !== false}
            onChange={(e) => onChange({ ...group, enabled: e.target.checked })}
          />
          启用组
        </label>
        <select
          aria-label={`第${depth}层组合逻辑`}
          className="!w-auto !min-h-8 !py-1"
          value={group.logic}
          onChange={(e) =>
            onChange({ ...group, logic: e.target.value as "AND" | "OR" })
          }
        >
          <option value="AND">AND · 全部满足</option>
          <option value="OR">OR · 任一满足</option>
        </select>
        <span className="text-xs text-muted-foreground">缺失值按未知处理</span>
      </div>
      <div className="space-y-2">
        {group.children.map((child, i) =>
          "children" in child ? (
            <div key={i} className="relative pr-8">
              <ConditionEditor
                group={child}
                factors={factors}
                depth={depth + 1}
                onChange={(value) => update(i, value)}
              />
              <button
                aria-label="删除条件组"
                className="absolute right-0 top-3"
                onClick={() =>
                  onChange({
                    ...group,
                    children: group.children.filter((_, j) => j !== i),
                  })
                }
              >
                <X size={16} />
              </button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2" key={i}>
              <input
                type="checkbox"
                aria-label={`启用条件${i + 1}`}
                checked={child.enabled}
                onChange={(e) =>
                  update(i, { ...child, enabled: e.target.checked })
                }
              />
              <select
                aria-label={`条件${i + 1}因子`}
                className="!w-44"
                value={child.factor}
                onChange={(e) => {
                  const boolean =
                    factors.find((f) => f.id === e.target.value)?.unit ===
                    "布尔";
                  update(i, {
                    ...child,
                    factor: e.target.value,
                    operator: boolean ? "eq" : "gte",
                    value: boolean ? false : 0,
                  });
                }}
              >
                {factors.map((f) => (
                  <option value={f.id} key={f.id}>
                    {f.name}
                    {f.unit === "%" ? " (%)" : ""}
                  </option>
                ))}
              </select>
              <select
                aria-label={`条件${i + 1}运算符`}
                className="!w-28"
                value={child.operator}
                onChange={(e) =>
                  update(i, { ...child, operator: e.target.value })
                }
              >
                {operators
                  .filter(
                    ([id]) => typeof child.value !== "boolean" || id === "eq",
                  )
                  .map(([id, label]) => (
                    <option key={id} value={id}>
                      {label}
                    </option>
                  ))}
              </select>
              {typeof child.value === "boolean" ? (
                <select
                  className="!w-24"
                  aria-label={`条件${i + 1}阈值`}
                  value={String(child.value)}
                  onChange={(e) =>
                    update(i, { ...child, value: e.target.value === "true" })
                  }
                >
                  <option value="true">是</option>
                  <option value="false">否</option>
                </select>
              ) : (
                <input
                  aria-label={`条件${i + 1}阈值`}
                  className="!w-28"
                  type="number"
                  step="any"
                  value={
                    child.value * (percentFactors.has(child.factor) ? 100 : 1)
                  }
                  onChange={(e) =>
                    update(i, {
                      ...child,
                      value:
                        Number(e.target.value) /
                        (percentFactors.has(child.factor) ? 100 : 1),
                    })
                  }
                />
              )}
              <button
                aria-label={`删除条件${i + 1}`}
                onClick={() =>
                  onChange({
                    ...group,
                    children: group.children.filter((_, j) => j !== i),
                  })
                }
              >
                <X size={16} />
              </button>
            </div>
          ),
        )}
      </div>
      <div className="mt-3 flex gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            onChange({
              ...group,
              children: [
                ...group.children,
                {
                  factor: "daily_return",
                  operator: "gte",
                  value: 0,
                  enabled: true,
                },
              ],
            })
          }
        >
          <Plus size={13} />
          添加条件
        </Button>
        {depth < 3 && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              onChange({
                ...group,
                children: [
                  ...group.children,
                  {
                    logic: "OR",
                    children: [
                      {
                        factor: "daily_return",
                        operator: "gte",
                        value: 0,
                        enabled: true,
                      },
                    ],
                  },
                ],
              })
            }
          >
            添加条件组
          </Button>
        )}
      </div>
    </div>
  );
}
