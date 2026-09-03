export interface TableStat {
  name: string;
  exists: boolean;
  row_count: number | null;
  size_pretty: string;
}

export interface DbHealth {
  ok: boolean;
  latency_ms: number;
  tables: TableStat[];
  error: string | null;
}
