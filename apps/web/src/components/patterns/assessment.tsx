import type { AssignmentGrade, LateData, Rubric } from "@/lib/api/types";
import { formatIst } from "@/lib/ist";

import { StatusBadge } from "./states";

export function LateStatus({ late }: { late?: LateData | null }) {
  if (!late) return null;
  return (
    <div className="flex flex-col gap-2 text-sm" aria-label="Late status">
      <div>
        <StatusBadge kind={late.closed ? "danger" : late.is_late ? "warning" : "success"}>
          {late.closed ? "Closed" : late.is_late ? "Late" : "On time"}
        </StatusBadge>
      </div>
      {late.due_at ? <p>Due {formatIst(late.due_at)}</p> : null}
      {late.is_late ? (
        <p>
          {late.late_days} begun day{late.late_days === 1 ? "" : "s"} late ·{" "}
          {Number(late.penalty_percent)}% penalty on earned marks
        </p>
      ) : null}
    </div>
  );
}

export function GradeBreakdown({
  grade,
  rubric,
}: {
  grade: AssignmentGrade;
  rubric?: Rubric | null;
}) {
  return (
    <div className="flex flex-col gap-3 text-sm" aria-label="Grade breakdown">
      {grade.rubric_breakdown ? (
        <dl className="flex flex-col gap-2">
          {grade.rubric_breakdown.map((entry) => {
            const criterion = rubric?.criteria.find((c) => c.id === entry.criterion_id);
            return (
              <div key={entry.criterion_id} className="flex flex-wrap justify-between gap-2">
                <dt>{criterion?.label ?? "Criterion"}</dt>
                <dd className="tabular-nums">
                  {Number(entry.score)}
                  {criterion ? " / " + Number(criterion.max_marks) : ""}
                </dd>
              </div>
            );
          })}
        </dl>
      ) : null}
      <dl className="flex flex-col gap-2">
        <div className="flex justify-between gap-2">
          <dt>Earned marks</dt>
          <dd>
            {Number(grade.raw_score ?? grade.score)} / {grade.max_marks}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Late penalty ({Number(grade.penalty_percent ?? 0)}%)</dt>
          <dd>−{Number(grade.penalty_marks ?? 0)}</dd>
        </div>
        <div className="flex justify-between gap-2 font-semibold">
          <dt>Final score</dt>
          <dd>
            {Number(grade.score)} / {grade.max_marks}
          </dd>
        </div>
      </dl>
      {grade.feedback ? <p className="whitespace-pre-wrap">{grade.feedback}</p> : null}
      <p className="text-muted-foreground">Graded {formatIst(grade.graded_at)}</p>
    </div>
  );
}
