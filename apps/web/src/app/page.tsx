import { RoleHome } from "@/features/auth/role-home";
import { HealthStatus } from "@/features/health/health-status";

export default function HomePage() {
  return (
    <RoleHome>
      <div className="flex flex-col gap-6">
        <section className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">SkillifyMe Portal</h1>
          <p className="max-w-prose text-sm text-muted-foreground sm:text-base">
            Practical learning, coding labs and assessments for colleges and placement training.
          </p>
        </section>
        <HealthStatus />
      </div>
    </RoleHome>
  );
}
