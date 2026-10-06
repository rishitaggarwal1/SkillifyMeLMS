"use client";

import type { ReactNode } from "react";

export type TableColumn<T> = {
  key: string;
  title: string;
  headingTitle?: string;
  rowHeader?: boolean;
  render: (row: T) => ReactNode;
};
/** One source of row/cell data; readable cards on phones and a sticky header on desktop. */
export function DataTable<T>({
  label,
  rows,
  columns,
  rowKey,
}: {
  label: string;
  rows: T[];
  columns: TableColumn<T>[];
  rowKey: (row: T) => string;
}) {
  return (
    <>
      <div
        role="region"
        aria-label={label}
        tabIndex={0}
        className="hidden max-h-[36rem] overflow-auto rounded-lg border bg-card md:block"
      >
        <table className="data-table">
          <caption className="sr-only">{label}</caption>
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  title={c.headingTitle}
                  className={c.rowHeader ? "sticky left-0 z-20 bg-card" : undefined}
                >
                  {c.title}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={rowKey(row)}>
                {columns.map((c) =>
                  c.rowHeader ? (
                    <th
                      key={c.key}
                      scope="row"
                      className="sticky left-0 z-10 border-t bg-card px-3 py-3 font-normal"
                    >
                      {c.render(row)}
                    </th>
                  ) : (
                    <td key={c.key}>{c.render(row)}</td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul aria-label={label} className="data-table-cards md:hidden">
        {rows.map((row) => (
          <li key={rowKey(row)} className="state-card">
            <dl className="flex flex-col gap-3">
              {columns.map((c) => (
                <div key={c.key}>
                  <dt className="text-xs text-muted-foreground">{c.title}</dt>
                  <dd>{c.render(row)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}
