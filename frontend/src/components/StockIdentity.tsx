import { useResource } from "../api";

/** Display metadata uses the same snapshot and date as the research task. */
export default function StockIdentity({
  code,
  date,
  version,
}: {
  code: string;
  date?: string;
  version?: string;
}) {
  const params = new URLSearchParams({ search: code, page_size: "10" });
  if (date) params.set("as_of_date", date);
  if (version) params.set("data_version", version);
  const resource = useResource<{
    items: { stock_code: string; name: string }[];
  }>(code && date && version ? `/instruments?${params}` : null);
  const name = resource.data?.items?.find(
    (item) => item.stock_code === code,
  )?.name;
  return (
    <span>
      {name ?? "名称未知"} · {code}
    </span>
  );
}
