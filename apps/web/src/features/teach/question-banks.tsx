"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "@/lib/toast";
import { z } from "zod";

import { NativeSelect } from "@/components/native-select";
import {
  ConfirmButton,
  EmptyState,
  ErrorAlert,
  LoadMore,
  PageTitle,
  errorMessage,
} from "@/components/patterns/page";
import { FormField, PageSkeleton } from "@/components/patterns/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useDebounced } from "@/features/admin/hooks";
import type { AuthorQuestion, QuestionBank, Skill } from "@/lib/api/types";

import { allSkillsQuery } from "./api";
import {
  bankQuery,
  banksQuery,
  bankQuestionsQuery,
  useArchiveBank,
  useArchiveQuestion,
  useCreateBank,
  useQuestionSkills,
  useSaveQuestion,
  useUpdateBank,
} from "./assessment-api";
import { QuestionForm, QUESTION_TYPES } from "./question-form";
import { SkillsPicker } from "./skills-picker";

const bankSchema = z.object({
  name: z.string().trim().min(1, "Name is required.").max(200),
  description: z.string().max(20000),
});
function BankForm({
  bank,
  save,
}: {
  bank?: QuestionBank;
  save: (body: z.infer<typeof bankSchema>) => Promise<unknown>;
}) {
  const form = useForm<z.infer<typeof bankSchema>>({
    resolver: zodResolver(bankSchema),
    defaultValues: { name: bank?.name ?? "", description: bank?.description ?? "" },
  });
  const busy = form.formState.isSubmitting;
  return (
    <form
      aria-label="Bank details"
      noValidate
      onSubmit={form.handleSubmit(async (body) => {
        try {
          await save(body);
          form.reset(body);
        } catch (error) {
          form.setError("root", { message: errorMessage(error) + " Your edits are kept." });
        }
      })}
      className="flex max-w-xl flex-col gap-4"
    >
      <FormField label="Bank name" error={form.formState.errors.name?.message} saving={busy}>
        {(props) => <Input {...props} {...form.register("name")} />}
      </FormField>
      <FormField
        label="Description (optional)"
        error={form.formState.errors.description?.message}
        saving={busy}
      >
        {(props) => <Textarea {...props} rows={3} {...form.register("description")} />}
      </FormField>
      {form.formState.errors.root ? (
        <p role="alert" className="text-sm text-danger">
          {form.formState.errors.root.message}
        </p>
      ) : null}
      <div>
        <Button type="submit" disabled={busy}>
          {busy ? "Saving…" : bank ? "Save bank" : "Create bank"}
        </Button>
      </div>
    </form>
  );
}

export function QuestionBanksPage() {
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const q = useDebounced(search.trim(), 250);
  const query = useInfiniteQuery(banksQuery(q));
  const create = useCreateBank();
  const router = useRouter();
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="flex flex-col gap-5">
      <PageTitle
        title="Question banks"
        actions={<Button onClick={() => setCreating(true)}>Create bank</Button>}
      />
      <p className="text-sm text-muted-foreground">
        Reusable questions for your organization&apos;s quizzes. Answer keys stay within your
        authoring team.
      </p>
      <FormField label="Search banks">
        {(props) => (
          <Input
            {...props}
            type="search"
            maxLength={200}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        )}
      </FormField>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && !items.length ? (
        <EmptyState action={<Button onClick={() => setCreating(true)}>Create bank</Button>}>
          {q ? "No banks match." : "Create your first bank to add questions."}
        </EmptyState>
      ) : null}
      <ul aria-label="Question banks" className="flex flex-col gap-3">
        {items.map((bank) => (
          <li key={bank.id}>
            <Link
              href={"/teach/question-banks/" + bank.id}
              className="state-card flex flex-col gap-2 hover:bg-muted/50"
            >
              <span className="font-semibold break-words">{bank.name}</span>
              {bank.description ? (
                <span className="line-clamp-2 text-sm text-muted-foreground">
                  {bank.description}
                </span>
              ) : null}
            </Link>
          </li>
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
      <Dialog open={creating} onOpenChange={setCreating}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create question bank</DialogTitle>
            <DialogDescription>Name a reusable collection of questions.</DialogDescription>
          </DialogHeader>
          <BankForm
            save={async (body) => {
              const bank = await create.mutateAsync(body);
              setCreating(false);
              router.push("/teach/question-banks/" + bank.id);
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function QuestionBankPage({ bankId }: { bankId: string }) {
  const bank = useQuery(bankQuery(bankId));
  const update = useUpdateBank(bankId);
  const archive = useArchiveBank(bankId);
  const router = useRouter();
  if (bank.isPending) return <PageSkeleton />;
  if (bank.error) return <ErrorAlert error={bank.error} onRetry={() => void bank.refetch()} />;
  return (
    <div className="flex flex-col gap-6">
      <Link href="/teach/question-banks" className="text-sm text-muted-foreground">
        ← Question banks
      </Link>
      <PageTitle
        title={bank.data.name}
        actions={
          <ConfirmButton
            label="Archive bank"
            title={"Archive " + bank.data.name + "?"}
            description="Published quizzes retain their questions. Drafts referencing this bank must be updated before publishing."
            confirmLabel="Archive bank"
            onConfirm={async () => {
              await archive.mutateAsync();
              router.push("/teach/question-banks");
            }}
          />
        }
      />
      <details className="rounded-lg border p-4">
        <summary className="min-h-11 cursor-pointer font-medium">Bank settings</summary>
        <div className="mt-3">
          <BankForm
            bank={bank.data}
            save={async (body) => {
              await update.mutateAsync(body);
              toast.success("Bank saved");
            }}
          />
        </div>
      </details>
      <QuestionList bankId={bankId} />
    </div>
  );
}

function QuestionList({ bankId }: { bankId: string }) {
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<"" | AuthorQuestion["question_type"]>("");
  const [filterSkills, setFilterSkills] = useState<string[]>([]);
  const [names, setNames] = useState(new Map<string, Skill>());
  const taxonomy = useQuery(allSkillsQuery());
  const q = useDebounced(search.trim(), 250);
  const query = useInfiniteQuery(
    bankQuestionsQuery(bankId, { q, kind: kind || undefined, skills: filterSkills }),
  );
  const archive = useArchiveQuestion(bankId);
  const [editing, setEditing] = useState<AuthorQuestion | "new" | null>(null);
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <section aria-label="Questions" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2>Questions</h2>
        <Button onClick={() => setEditing("new")}>Add question</Button>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Search questions">
          {(props) => (
            <Input
              {...props}
              type="search"
              maxLength={200}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          )}
        </FormField>
        <FormField label="Filter question type">
          {(props) => (
            <NativeSelect
              {...props}
              value={kind}
              onChange={(e) => setKind(e.target.value as typeof kind)}
            >
              <option value="">All types</option>
              {Object.entries(QUESTION_TYPES).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </NativeSelect>
          )}
        </FormField>
      </div>
      <details className="rounded-lg border p-3">
        <summary className="min-h-11 cursor-pointer">Filter by skills</summary>
        <SkillsPicker
          selected={filterSkills}
          names={new Map([...(taxonomy.data ?? []), ...names])}
          disabled={false}
          onChange={(ids, picked) => {
            setFilterSkills(ids);
            if (picked) setNames((v) => new Map(v).set(picked.id, picked));
          }}
        />
      </details>
      {query.isPending ? <PageSkeleton /> : null}
      {query.error ? <ErrorAlert error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.isSuccess && !items.length ? (
        <EmptyState action={<Button onClick={() => setEditing("new")}>Add question</Button>}>
          No questions{q || kind || filterSkills.length ? " match these filters" : " yet"}.
        </EmptyState>
      ) : null}
      <ul aria-label="Bank questions" className="flex flex-col gap-3">
        {items.map((question) => (
          <li key={question.id} className="state-card flex flex-col gap-3">
            <h3 className="text-base font-medium break-words">{question.prompt}</h3>
            <p className="text-sm text-muted-foreground">
              {QUESTION_TYPES[question.question_type]}
              {question.skill_ids.length
                ? " · " +
                  question.skill_ids.map((id) => taxonomy.data?.get(id)?.name ?? "Skill").join(", ")
                : ""}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => setEditing(question)}>
                Edit question
              </Button>
              <ConfirmButton
                label="Archive question"
                title="Archive this question?"
                description="Published copies remain. Draft quizzes using it must choose another active question."
                confirmLabel="Archive question"
                onConfirm={() => archive.mutateAsync(question.id)}
              />
            </div>
          </li>
        ))}
      </ul>
      <LoadMore
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
      {editing ? (
        <QuestionDialog
          bankId={bankId}
          question={editing === "new" ? undefined : editing}
          onSaved={setEditing}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </section>
  );
}

function QuestionDialog({
  bankId,
  question,
  onSaved,
  onClose,
}: {
  bankId: string;
  question?: AuthorQuestion;
  onSaved: (q: AuthorQuestion) => void;
  onClose: () => void;
}) {
  const save = useSaveQuestion(bankId);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{question ? "Edit question" : "Add question"}</DialogTitle>
          <DialogDescription>
            Keep options identifiable across corrections. Grading-key changes require a major course
            release.
          </DialogDescription>
        </DialogHeader>
        <QuestionForm
          key={question?.id ?? "new"}
          question={question}
          save={async (body) => {
            const saved = await save.mutateAsync({ id: question?.id, body });
            onSaved(saved);
            toast.success("Question saved");
            return saved;
          }}
        />
        {question ? (
          <QuestionSkillsPanel bankId={bankId} question={question} onSaved={onSaved} />
        ) : (
          <p className="text-sm text-muted-foreground">
            Save the question first to tag its skills.
          </p>
        )}
        <div>
          <Button variant="outline" onClick={onClose}>
            Done
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function QuestionSkillsPanel({
  bankId,
  question,
  onSaved,
}: {
  bankId: string;
  question: AuthorQuestion;
  onSaved: (q: AuthorQuestion) => void;
}) {
  const taxonomy = useQuery(allSkillsQuery());
  const [names, setNames] = useState(new Map<string, Skill>());
  const [error, setError] = useState<unknown>(null);
  const [pendingSkills, setPendingSkills] = useState<string[]>(question.skill_ids);
  const save = useQuestionSkills(bankId);
  function persist(ids: string[]) {
    setPendingSkills(ids);
    setError(null);
    void save.mutateAsync({ id: question.id, skills: ids }).then(onSaved).catch(setError);
  }
  return (
    <section aria-label="Question skills" className="flex flex-col gap-2">
      <h3 className="font-semibold">Skills</h3>
      <SkillsPicker
        selected={question.skill_ids}
        names={new Map([...(taxonomy.data ?? []), ...names])}
        disabled={save.isPending}
        onChange={(ids, picked) => {
          if (picked) setNames((v) => new Map(v).set(picked.id, picked));
          persist(ids);
        }}
      />
      {error ? <ErrorAlert error={error} onRetry={() => persist(pendingSkills)} /> : null}
    </section>
  );
}
