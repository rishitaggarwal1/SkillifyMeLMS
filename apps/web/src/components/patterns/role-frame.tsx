"use client";

import {
  BookOpen,
  ClipboardCheck,
  FileQuestion,
  Film,
  GraduationCap,
  Home,
  Layers,
  MoreHorizontal,
  ScrollText,
  Users,
  Building2,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Area } from "@/features/auth/roles";

const NAV = {
  platform: [
    { href: "/platform", label: "Dashboard", icon: Home, exact: true },
    { href: "/platform/organizations", label: "Organizations", icon: Building2 },
    { href: "/platform/users", label: "Users", icon: Users },
    { href: "/platform/courses", label: "Courses", icon: BookOpen },
    { href: "/platform/audit", label: "Audit log", icon: ScrollText },
  ],
  admin: [
    { href: "/admin/batches", label: "Batches", icon: Layers },
    { href: "/admin/courses", label: "Courses", icon: BookOpen },
    { href: "/admin/members", label: "Members", icon: Users },
    { href: "/admin/imports", label: "Import", icon: ScrollText },
  ],
  teach: [
    { href: "/teach/courses", label: "Courses", icon: BookOpen },
    { href: "/teach/grading", label: "Grading", icon: ClipboardCheck },
    { href: "/teach/question-banks", label: "Question banks", icon: FileQuestion },
    { href: "/teach/videos", label: "Videos", icon: Film },
  ],
  learn: [
    { href: "/learn", label: "My learning", icon: GraduationCap, exact: true },
    { href: "/catalog", label: "Catalog", icon: BookOpen },
  ],
} satisfies Record<Area, { href: string; label: string; icon: typeof Home; exact?: boolean }[]>;

const LABEL = { platform: "Platform", admin: "Admin", teach: "Teaching", learn: "Learning" };
const PATH_LABELS: Record<string, string> = {
  organizations: "Organizations",
  users: "Users",
  courses: "Courses",
  audit: "Audit log",
  batches: "Batches",
  members: "Members",
  imports: "Import",
  grading: "Grading",
  "question-banks": "Question banks",
  videos: "Videos",
  lessons: "Lessons",
  assignments: "Assignments",
  submissions: "Submissions",
  progress: "Progress",
  enrollments: "Enrollment",
  video: "Video",
};

export function Breadcrumbs({ area }: { area: Area }) {
  const parts = usePathname().split("/").filter(Boolean);
  return (
    <nav aria-label="Breadcrumb" className="mb-4 text-sm text-muted-foreground">
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <li>
          <Link className="inline-flex min-h-11 items-center" href={`/${area}`}>
            {LABEL[area]}
          </Link>
        </li>
        {parts.slice(1).map((part, i) => {
          const last = i === parts.length - 2;
          const label =
            PATH_LABELS[part] ??
            (parts[i] === "lessons"
              ? "Lesson"
              : parts[i] === "submissions"
                ? "Submission"
                : "Details");
          const href = "/" + parts.slice(0, i + 2).join("/");
          // Dynamic ids name the resource through its page heading. They are never labels or links to invalid intermediate paths.
          return (
            <li key={href} className="flex items-center gap-2">
              <span aria-hidden="true">/</span>
              {last ||
              !PATH_LABELS[part] ||
              part === "enrollments" ||
              part === "submissions" ||
              part === "lessons" ||
              part === "assignments" ? (
                <span aria-current={last ? "page" : undefined}>{label}</span>
              ) : (
                <Link className="inline-flex min-h-11 items-center" href={href}>
                  {label}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function RoleFrame({ area, children }: { area: Area; children: ReactNode }) {
  const pathname = usePathname();
  const [more, setMore] = useState(false);
  const nav = NAV[area];
  const current = (item: { href: string; exact?: boolean }) =>
    item.exact ? pathname === item.href : pathname.startsWith(item.href);
  const mobile = area === "platform" ? nav.slice(0, 3) : nav;
  return (
    <div className="role-shell" data-testid={`shell-${area}`}>
      <aside className="hidden lg:block" data-testid="desktop-sidebar">
        <div className="sticky top-32 rounded-lg border bg-card p-3">
          <Link href={`/${area}`} className="shell-nav-link font-semibold text-foreground">
            {LABEL[area]} home
          </Link>
          <nav aria-label={LABEL[area]} className="mt-2 flex flex-col gap-1">
            {nav.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={current(item) ? "page" : undefined}
                className="shell-nav-link"
              >
                <item.icon size={20} aria-hidden="true" />
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      </aside>
      <div className="min-w-0">
        <Breadcrumbs area={area} />
        {children}
      </div>
      <nav
        aria-label={LABEL[area]}
        data-testid="mobile-navigation"
        className="fixed inset-x-0 bottom-0 z-30 flex border-t bg-card px-2 pb-[env(safe-area-inset-bottom)] lg:hidden"
      >
        {mobile.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={current(item) ? "page" : undefined}
            className="mobile-nav-link"
          >
            <item.icon size={20} aria-hidden="true" />
            <span className="text-center">{item.label}</span>
          </Link>
        ))}
        {area === "platform" ? (
          <Button variant="ghost" className="mobile-nav-link h-auto" onClick={() => setMore(true)}>
            <MoreHorizontal size={20} aria-hidden="true" />
            More
          </Button>
        ) : null}
      </nav>
      <Dialog open={more} onOpenChange={setMore}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Platform navigation</DialogTitle>
            <DialogDescription>More platform tools</DialogDescription>
          </DialogHeader>
          {nav.slice(3).map((item) => (
            <Link
              key={item.href}
              className="shell-nav-link"
              href={item.href}
              onClick={() => setMore(false)}
            >
              <item.icon size={20} aria-hidden="true" />
              {item.label}
            </Link>
          ))}
        </DialogContent>
      </Dialog>
    </div>
  );
}
