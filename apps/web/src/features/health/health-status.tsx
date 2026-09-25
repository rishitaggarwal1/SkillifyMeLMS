"use client";

import { useQuery } from "@tanstack/react-query";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { ApiClient } from "@/lib/api/client";

import { readinessQuery } from "./queries";

const CHECK_LABELS: Record<string, string> = {
  database: "PostgreSQL",
  redis: "Redis",
};

export function HealthStatus({ client }: { client?: ApiClient }) {
  const { data, error, isPending, isFetching, refetch } = useQuery(readinessQuery(client));

  const overall = isPending
    ? { label: "Checking…", variant: "secondary" as const }
    : error
      ? { label: "API unreachable", variant: "destructive" as const }
      : data.status === "ok"
        ? { label: "All systems operational", variant: "default" as const }
        : { label: "Degraded", variant: "destructive" as const };

  return (
    <Card aria-busy={isFetching}>
      <CardHeader>
        <CardTitle>Platform status</CardTitle>
        <CardDescription>Live readiness of the API and its dependencies.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Badge variant={overall.variant} data-testid="overall-status">
            {overall.label}
          </Badge>
          <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
            {isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        </div>

        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error.message}
          </p>
        ) : null}

        {data ? (
          <ul className="divide-y rounded-lg border" aria-label="Dependency checks">
            {Object.entries(data.checks).map(([name, check]) => (
              <li
                key={name}
                className="flex items-center justify-between gap-3 px-3 py-2.5 text-sm"
                data-testid={`check-${name}`}
              >
                <span className="font-medium">{CHECK_LABELS[name] ?? name}</span>
                <span className="flex items-center gap-2 text-muted-foreground">
                  <span className="tabular-nums">{check.latency_ms.toFixed(0)} ms</span>
                  <Badge variant={check.status === "ok" ? "secondary" : "destructive"}>
                    {check.status === "ok" ? "OK" : (check.error ?? "Error")}
                  </Badge>
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}
