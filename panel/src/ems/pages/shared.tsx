import {
  Button,
  DataTable,
  NumberInput,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
} from "@carbon/react";
import { useEffect, useState } from "react";

import type { ConfigAuditRow, ConfigItem, TelemetryPoint } from "../api";

export function displayValue(point: TelemetryPoint | undefined, percent = false): string {
  if (!point || point.stale) {
    return "0";
  }
  if (percent) {
    return `${(point.value * 100).toFixed(1)}%`;
  }
  if (point.unit === "boolean") {
    return point.value >= 0.5 ? "closed" : "open";
  }
  return Number.isInteger(point.value) ? String(point.value) : point.value.toFixed(2);
}

export function PointTable({ title, points }: { title: string; points: TelemetryPoint[] }) {
  return (
    <div className="ems-table">
      <h2>{title}</h2>
      <DataTable
        rows={points.map((point) => ({
          id: point.point_id,
          point: point.point_id,
          value: displayValue(point),
          unit: point.unit,
          quality: point.stale ? "stale/0" : point.quality,
        }))}
        headers={[
          { key: "point", header: "Point" },
          { key: "value", header: "Value" },
          { key: "unit", header: "Unit" },
          { key: "quality", header: "Quality" },
        ]}
      >
        {({ rows, headers, getHeaderProps, getRowProps, getTableProps }) => (
          <Table {...getTableProps()} size="sm">
            <TableHead>
              <TableRow>
                {headers.map((header) => (
                  <TableHeader {...getHeaderProps({ header })} key={header.key}>
                    {header.header}
                  </TableHeader>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((row) => (
                <TableRow {...getRowProps({ row })} key={row.id}>
                  {row.cells.map((cell) => (
                    <TableCell key={cell.id}>{cell.value}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DataTable>
    </div>
  );
}

export function CommandOutcomeTable({
  last,
  audit,
}: {
  last: ConfigItem | null;
  audit: ConfigAuditRow[];
}) {
  const rows = [
    ...(last
      ? [
          {
            id: "last",
            when: last.updated_at,
            key: last.key,
            requested: last.requested ?? "—",
            effective: last.effective ?? String(last.value ?? last.value_text ?? ""),
            outcome: last.outcome ?? "applied",
            by: last.updated_by,
          },
        ]
      : []),
    ...audit.map((row) => ({
      id: `${row.key}-${row.changed_at}`,
      when: row.changed_at,
      key: row.key,
      requested: row.new_value,
      effective: row.new_value,
      outcome: "accepted",
      by: row.changed_by,
    })),
  ];
  return (
    <div className="ems-table">
      <h2>Command outcomes</h2>
      <DataTable
        rows={rows}
        headers={[
          { key: "when", header: "When" },
          { key: "key", header: "Setpoint" },
          { key: "requested", header: "Requested" },
          { key: "effective", header: "Effective" },
          { key: "outcome", header: "Outcome" },
          { key: "by", header: "By" },
        ]}
      >
        {({ rows: tableRows, headers, getHeaderProps, getRowProps, getTableProps }) => (
          <Table {...getTableProps()} size="sm">
            <TableHead>
              <TableRow>
                {headers.map((header) => (
                  <TableHeader {...getHeaderProps({ header })} key={header.key}>
                    {header.header}
                  </TableHeader>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {tableRows.map((row) => (
                <TableRow {...getRowProps({ row })} key={row.id}>
                  {row.cells.map((cell) => (
                    <TableCell key={cell.id}>{cell.value}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DataTable>
    </div>
  );
}

function storedValue(item: ConfigItem): string {
  if (item.value_text != null) {
    return item.value_text;
  }
  return item.value == null ? "" : String(item.value);
}

export function ConfigTable({
  items,
  onApply,
  title = "System setpoints",
}: {
  items: ConfigItem[];
  onApply?: (groupId: string, key: string, value: number | string) => Promise<void>;
  title?: string;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  useEffect(() => {
    const next: Record<string, string> = {};
    for (const item of items) {
      next[item.key] = storedValue(item);
    }
    setDrafts(next);
  }, [items]);

  if (!onApply) {
    return (
      <div className="ems-table">
        <h2>{title}</h2>
        <DataTable
          rows={items.map((item) => ({
            id: item.key,
            key: item.key,
            value: storedValue(item),
            unit: item.unit,
            by: `${item.updated_by} @ ${item.updated_at}`,
          }))}
          headers={[
            { key: "key", header: "Key" },
            { key: "value", header: "Value" },
            { key: "unit", header: "Unit" },
            { key: "by", header: "Updated" },
          ]}
        >
          {({ rows, headers, getHeaderProps, getRowProps, getTableProps }) => (
            <Table {...getTableProps()} size="sm">
              <TableHead>
                <TableRow>
                  {headers.map((header) => (
                    <TableHeader {...getHeaderProps({ header })} key={header.key}>
                      {header.header}
                    </TableHeader>
                  ))}
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((row) => (
                  <TableRow {...getRowProps({ row })} key={row.id}>
                    {row.cells.map((cell) => (
                      <TableCell key={cell.id}>{cell.value}</TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </DataTable>
      </div>
    );
  }

  return (
    <div className="ems-table">
      <h2>{title}</h2>
      <p className="ems-setpoint-help">Change any value and apply it. Writes store in the plant database.</p>
      <div className="ems-setpoint-list">
        {items.map((item) => {
          const draft = drafts[item.key] ?? storedValue(item);
          const text = item.value_text != null;
          return (
            <div className="ems-config-row" key={`${item.group_id}.${item.key}`}>
              {text ? (
                <TextInput
                  id={`${item.group_id}-${item.key}`}
                  labelText={`${item.key}${item.unit ? ` (${item.unit})` : ""}`}
                  value={draft}
                  onChange={(event) => {
                    setDrafts((current) => ({ ...current, [item.key]: event.target.value }));
                  }}
                />
              ) : (
                <NumberInput
                  id={`${item.group_id}-${item.key}`}
                  label={`${item.key}${item.unit ? ` (${item.unit})` : ""}`}
                  value={draft === "" ? 0 : Number(draft)}
                  onChange={(_event, { value }) => {
                    setDrafts((current) => ({ ...current, [item.key]: String(value) }));
                  }}
                />
              )}
              <Button
                size="sm"
                onClick={() => {
                  void onApply(item.group_id, item.key, text ? draft : Number(draft));
                }}
              >
                Apply
              </Button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
