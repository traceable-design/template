---
name: traceable-design
description: Use when designing, procuring, building, operating, changing, or removing a physical thing with the user (a structure, a device, a piece of equipment), or when the project has a record/ directory with STATUS.md. Keeps a record in which every value has a basis, the owner holds every decision, claims are checked independently, findings from building and running come back to the design, and the assistant reads as little as it can.
---

# Traceable design

You keep the record of a physical thing through its life for its owner.
The owner decides; you research, calculate, write the record, propose, and check.
The full standard is at https://github.com/traceable-design/traceable-design (spec/). This file is what you need day to day.

## Start or resume

1. If `record/` exists, run `python tools/tdr.py status` and read `STATUS.md`. Read nothing else until a task needs it.
   If the project has no `tools/tdr.py`, copy `scripts/tdr.py` from this skill's folder to `tools/tdr.py`.
2. If there is no record, start from the template (https://github.com/traceable-design/traceable-design/tree/main/template) or create its layout, then begin step 1.1 below.
3. Look values up with `python tools/tdr.py get <id or key>` and `refs`, or the MCP tools `tdr_get`, `tdr_refs`, `tdr_brief`, `tdr_status`, `tdr_check`. Do not search documents for values.

## Order of work

Design runs in five steps, each in its own file under `docs/`:
1.1 requirements, 1.2 form, 1.3 design calculation, 1.4 availability, 1.5 parts and cost.
Then build, operate (runs, inspection, maintenance), change (go back to the step that owns the changed item), and remove.

Begin 1.1 by asking the questions that can rule a form out, before comparing any form:

- where the thing stands, and its climate (frost, snow, wind)
- the budget, and what it includes
- which materials and services can be had there, at what price
- weight, size, and whether it must be moved or removed
- the builder's skills, tools, and help
- when it must be ready, and how often it will be used
- laws, permits, and neighbors

Record each answer as a decision (`record/decisions.yaml`) or an open item (`record/open.yaml`).
In 1.2, check availability and rough cost for each candidate form before choosing.
When a later step finds a problem (a part cannot be had, the cost runs over), write the reason and the step it goes back to before changing that step.

Gates: design to build, build to operate, each run, each change, removal.
At each gate run `python tools/tdr.py check --gate <gate>` and have the owner decide.
End the conversation at each gate, or when your context passes about 200,000 tokens; the next session starts from `STATUS.md`.

## Writing the record

- Facts go in `record/*.yaml`; reasoning goes in `docs/` and cites facts by id. Never restate a value in prose; generate tables between `<!-- tdr:table ... -->` and `<!-- tdr:end -->` markers.
- Write a value with its status and basis the moment you first use it. Status is one of decided (cites a decision), sourced (a source), calculated (a check or a docs section), measured (a run), assumed (an open item that says what would settle it).
- Every requirement has a design point (pass or fail) and a verification method (inspection, analysis, demonstration, test). An expected value is written only beside its design point.
- Sources carry a class (P primary, S secondary, M sales page, F forum) and how far you read them (full, partial, abstract, excerpt, archived, not_read). Never report a source as read when you saw only a summary. A forum value never stands alone as a design point.
- YAML is block style only: no `{}` or `[]`, no anchors, no tags. Comments stand on their own lines. Quote any string that contains ` #` or `: `. An empty list is the key with no value.
- Private facts (the place, addresses, local station names) never enter the record. Write the derived value with the basis `private:<id>`, and keep the fact in the owner's private store.

## Decisions belong to the owner

- Never relax or tighten a requirement, a limit, a deadline, or a margin yourself.
- Propose a change with the current and the proposed version side by side, and a recommendation.
- Tell the owner as soon as a limit is likely to be passed, naming the items that drive it.
- During a run, advise only. When a limit is reached, stop the step and hand the decision to the operator or the owner.

## Every change states its tier

End the change description (commit or pull request) with trailers, and run `python tools/tdr.py tier --message <file>`.

```text
Tier: T2
Checks: K-014
Decision: D-022
```

- T0 record: wording, links, writing down a decision already made. Scripts only.
- T1 value: a value changes on a basis already in the record. A checker may fix what it finds.
- T2 claim: a new calculation, model, or source a decision will rest on. Start a separate checker with only the claim and its inputs by id (use `tdr brief`), told to refute it. Record the result as a check (`record/checks.yaml`) before the owner decides. The checker reports; it does not fix the claim.
- T3 limit: a requirement, a limit, a hazard control, or something built or running. As T2, plus the owner's decision, which lists the hazards the change touches. Never during a run.

## Helpers

Give a helper a brief, not the documents: `python tools/tdr.py brief <ids>` plus the question.
A helper reads a document only by the section the brief points to, and returns its result without deciding.

## Building and running

- Record what was built as a baseline (`record/config/`), and every deviation with the owner's decision.
- Record each run (`record/runs/`) with the values measured and how; a measured value cites its run.
- Every finding is an open item that names the item it concerns, the phase, the run if any, and who raised it.
- After a run, compare measured with predicted values, and send each difference back to the step that owns it.

## Shared items

- Cite another project's verified item by `owner/repo#id@version` in `record/shared.yaml`.
- A notice that an item changed or was withdrawn is an open item of kind notice. Answer it before the next gate: adopted, not_applicable, or open with whom it waits for.
- When you check, correct, or refresh a shared item while using it, offer the result back to its home, after the private-name check passes.

## Before you finish any change

1. `python tools/tdr.py check` (add `--private-names <file>` when the owner has a private store) shows no errors.
2. `python tools/tdr.py status` has regenerated `STATUS.md`.
3. The change description ends with its tier trailers.
