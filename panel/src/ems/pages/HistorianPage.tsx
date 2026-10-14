import { Button } from "@carbon/react";
import { useState } from "react";

import type { ApiClient } from "../../api/client";
import type { TelemetryPoint } from "../api";
import { displayValue } from "./shared";

interface HistorianPageProps {
  byId: Map<string, TelemetryPoint>;
  client: ApiClient;
  token: string;
}

export function HistorianPage({ byId, client, token }: HistorianPageProps) {
  const [pointId, setPointId] = useState("bess.cluster.soc");
  const [csv, setCsv] = useState<string | null>(null);

  return (
    <section className="ems-page" aria-labelledby="historian-title">
      <div className="page-title">
        <h1 id="historian-title">Historian</h1>
        <p>Timescale-backed trends via CSV export for any live point.</p>
      </div>
      <label className="ems-select">
        Point
        <select
          value={pointId}
          onChange={(event) => {
            setPointId(event.target.value);
          }}
        >
          {[...byId.keys()].sort().map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </label>
      <p>
        Latest: <strong>{displayValue(byId.get(pointId))}</strong>
      </p>
      <Button
        onClick={() => {
          void client
            .get(`/v1/ems/historian/${encodeURIComponent(pointId)}/csv`, {
              headers: { Authorization: `Bearer ${token}` },
            })
            .then(async (response) => {
              if (!response.ok) {
                throw new Error("CSV export failed");
              }
              const body = (await response.json()) as { csv: string; filename: string };
              setCsv(body.csv);
              const blob = new Blob([body.csv], { type: "text/csv" });
              const url = URL.createObjectURL(blob);
              const anchor = document.createElement("a");
              anchor.href = url;
              anchor.download = body.filename;
              anchor.click();
              URL.revokeObjectURL(url);
            });
        }}
      >
        Download CSV
      </Button>
      {csv ? (
        <pre className="ems-csv-preview">{csv.split("\n").slice(0, 12).join("\n")}</pre>
      ) : null}
    </section>
  );
}
