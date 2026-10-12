import { screeningSummary } from "../screeningSummary";
import type { ScreeningDefinition } from "../types";

export default function ScreeningDescription({
  definition,
  ruleVersion,
  compact = false,
}: {
  definition: ScreeningDefinition;
  ruleVersion?: string;
  compact?: boolean;
}) {
  const summary = screeningSummary(definition, ruleVersion);
  return (
    <div
      className={`min-w-0 max-w-xl whitespace-normal break-words ${compact ? "w-56 sm:w-80 lg:w-[28rem]" : ""}`}
    >
      <p>
        {summary.title} · {definition.as_of_date}
      </p>
      <details className="mt-1 text-xs text-muted-foreground">
        <summary className="cursor-pointer">
          <span className="line-clamp-2">{summary.conditions}</span>
        </summary>
        <p className="mt-2 whitespace-normal">{summary.conditions}</p>
      </details>
    </div>
  );
}
