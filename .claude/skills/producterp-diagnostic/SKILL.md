# productERP Diagnostic Skill

## Purpose

This skill is invoked when the user asks for a diagnostic, investigation, or "DO NOT make changes — diagnosis only" task. It encodes the diagnostic methodology used throughout productERP development.

## When to apply this skill

Apply when the prompt contains any of:
- "DO NOT make code changes"
- "diagnosis only"
- "investigate"
- "report back on"
- A list of files to read followed by numbered sections asking about findings

The diagnostic phase happens BEFORE any implementation work. The user reviews findings before authorizing changes.

## Core principle

Never write code during a diagnostic. Never run migrations. Never modify files. The output is a report, not a change set.

## Required workflow

1. Read every file in the "Read these files" section of the prompt, in order. Do not skip.
2. PRODUCT_DECISIONS.md is a reference document, not required reading. Consult it only when you need to verify a specific established convention or decision. Do not read it cover-to-cover for every diagnostic.
3. Verify files exist before referencing them. If a file is not where the prompt says it is, find the correct path and note the discrepancy.
4. Read the model files in full, not just summaries. Look at the field definitions, related_name, on_delete behavior, unique_together, indexes.
5. Read view functions in full. Look at the permission guards, query patterns, context shapes.
6. Read templates carefully. Look at how JS interacts with the DOM, how data flows from view to template.
7. For each numbered section in the prompt, produce a substantive answer based on actual file contents.

## Output structure

The diagnostic report should follow the exact structure requested by the prompt's numbered sections. Each section should:

- Start with a clear heading matching the prompt's numbering
- State concrete findings (file paths, line numbers, function names, exact code snippets when helpful)
- Identify any discrepancies between the prompt's assumptions and the actual code
- Flag anything unexpected or risky
- Provide a recommendation when the prompt asks for one

End every diagnostic with an "Unexpected findings" or "Anything else" section that surfaces things the prompt didn't ask about but that matter for implementation.

> After the unexpected findings section, append a one-line footer confirming the skill was applied:
> `— Diagnostic generated using the producterp-diagnostic skill —`

## What to look for proactively

Every diagnostic should consider these even if not asked:

1. **Naming collisions** — Will any new model, function, URL name, or template name clash with existing ones?
2. **Migration ordering** — What's the latest migration number? Are there dependencies between apps?
3. **Permission gating** — What permissions guard the affected views? Do new endpoints need new permission entries?
4. **Multi-tenancy** — Is every query scoped to the user's company? Could the change leak data across tenants?
5. **Existing tests** — Will the change break any existing tests? Which test files cover the affected code?
6. **Template patterns** — Does the area use IIFE for JS, per-template inline JS, eager-load context?
7. **Database constraints** — Are there unique_together, PROTECT, CASCADE, or SET_NULL behaviors that affect the change?
8. **Cross-app dependencies** — Does the change span multiple apps? Are there circular import risks?
9. **Reusable patterns** — Is there existing code solving a similar problem? Should new logic be extracted into a shared helper that both new and existing code can use? Look especially at: shared algorithms (forecast walking, aggregation patterns), shared form/view patterns (admin CRUD, modal data endpoints), shared template includes (forecast grids, filter blocks). When ARCHITECTURE.md exists, consult it as the catalog of available reusable building blocks.
10. **Database layer review** — For changes touching models: examine existing constraints (unique, unique_together, PROTECT/CASCADE/SET_NULL), index usage, related_name patterns. Consider whether the change affects existing queries (could it cause N+1?), whether new fields need indexes for filter performance, and whether existing data migrations would still produce correct results.

## What to refuse

- Writing code or pseudocode beyond what's needed to illustrate a finding
- Running migrations or test commands
- Modifying any file
- Pushing to remote

If the user's prompt drifts toward asking for implementation work during a diagnostic, stop and ask them to confirm they want to skip diagnosis and proceed to implementation.

## Tone

Direct, concrete, evidence-based. Cite file paths and line numbers. Avoid vague statements like "this might be complex" — instead say "this requires changes to N files across M apps" or "this conflicts with the existing X pattern at file:line".

## Recommendations vs. decisions

The diagnostic surfaces options. It does not decide. When a question requires a product decision (e.g., "should the modal be read-only or editable"), present the options with tradeoffs and let the user decide. Avoid burying decisions in implementation suggestions.
