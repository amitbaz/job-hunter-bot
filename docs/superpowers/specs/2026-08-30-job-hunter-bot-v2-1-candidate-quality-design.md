# Job Hunter Bot v2.1 Candidate Quality Design

**Date:** 2026-08-30

## 1. Purpose

Improve the quality and presentation of candidates after the v2 discovery expansion without reducing the broader source coverage that v2 added.

The v2 run successfully increased discovery breadth, but the first production-like run exposed three quality problems:

1. Telegram results are not ordered by the final Gemini score.
2. Non-engineering roles such as Product Manager, Platform Product Manager, and Product Designer can consume Gemini evaluation budget because the deterministic prefilter allows description keywords to compensate for an unrelated title.
3. Gemini `skip` decisions are currently included in Telegram under the fallback “Needs review / blockers” section.

A fourth concern is budget sizing: the existing `max_jobs_per_run: 25` ceiling was an implementation safety choice, not a user requirement. After v2 broadened discovery, a 25-job ceiling is too restrictive once the deterministic filtering is corrected.

This release tightens candidate gating, raises the Gemini safety ceiling to 75, and makes Telegram output reflect final model decisions and scores.

## 2. Scope

### In scope

- Add a strict engineering-profession gate before deterministic ranking and Gemini evaluation.
- Reject clearly non-engineering titles even when their descriptions contain React, TypeScript, SaaS, product, roadmap, ownership, or other positive keywords.
- Keep the existing broad discovery sources and v2 cross-source aggregation.
- Raise the default `max_jobs_per_run` from 25 to 75.
- Preserve the cap as a configurable safety ceiling rather than a target number of evaluations.
- Sort Telegram results within each section by final Gemini score descending.
- Exclude `skip` decisions from Telegram entirely.
- Map only explicit supported delivery decisions to Telegram sections; unknown decisions must not silently appear as “Needs review / blockers.”
- Add logging that makes profession-gate rejection and budget deferral visible.
- Add regression tests for the production failures observed in the August 30 run.

### Out of scope

- Changing the Gemini model.
- Changing Gemini scoring weights or thresholds.
- Changing discovery sources or adding new job boards.
- Changing cover-letter generation or PDF formatting.
- Employer-form submission.
- Browser automation.
- Removing the evaluation safety ceiling entirely.
- Reworking SQLite schema unless a backward-compatible change is required for diagnostics.

## 3. Production evidence and root causes

The first v2 production-like run reported:

```text
discovery: raw=1208 unique=1194 prefilter_rejected=926 eligible=262 selected=25
selected sources: hackernews=5 weworkremotely=20
```

The bot therefore discarded 237 prefilter-eligible candidates before Gemini because of the 25-job ceiling.

The current deterministic prefilter admits a job when either:

- the title contains one configured target title; or
- any positive keyword appears in either the title or description.

This means a title such as `Senior Product Manager` can pass because its description mentions React, TypeScript, SaaS, ownership, or B2B.

The current ranker also compares target-title word overlap. `Senior Product Manager` overlaps strongly with `Senior Product Engineer`, which can produce a misleading title-fit score even though the professions differ.

Finally, Telegram grouping has mappings for `high_priority`, `package_match`, `possible_match`, and `blocked`, but no mapping for `skip`. The current fallback places unknown decisions in “Needs review / blockers,” causing jobs Gemini explicitly rejected to be delivered to the user.

These are deterministic pipeline problems. They should be corrected before considering a model upgrade.

## 4. Desired pipeline

The relevant v2.1 flow becomes:

```text
all discovery sources
    -> enrich + cross-source dedupe
    -> existing remote / blocked-title checks
    -> engineering-profession title gate
    -> deterministic ranking
    -> top N eligible jobs, N <= max_jobs_per_run (default 75)
    -> Gemini evaluation
    -> decision handling
         high_priority/package_match -> Telegram + PDF
         possible_match              -> Telegram
         blocked                     -> Telegram only when explicitly useful for review
         skip                        -> persist only, never Telegram
    -> sort each Telegram section by final Gemini score descending
```

The broad discovery layer remains unchanged.

## 5. Engineering-profession title gate

### Core rule

A job must have clear evidence in its **title** that the role is an engineering/software-development profession before it may consume a Gemini evaluation call.

Description keywords are supporting evidence only. They must never convert a clearly non-engineering profession into an engineering candidate.

### Accepted engineering title evidence

A title qualifies when at least one of the following is true:

1. It contains an engineering occupation marker:
   - `engineer`
   - `developer`
   - `software architect`
   - `frontend architect`
   - `front-end architect`
   - `web architect`

2. It contains an explicitly accepted engineering-lead phrase:
   - `technical lead`
   - `frontend lead`
   - `front-end lead`
   - `software lead`
   - `engineering lead`

3. It matches or contains a configured target title that itself contains accepted engineering evidence.

Examples that must pass:

- Senior Product Engineer
- Staff Product Engineer
- Senior Frontend Engineer
- Principal Frontend Engineer
- Senior Software Engineer, Product
- Full-Stack Engineer
- Full Stack Developer
- Founding Engineer
- Frontend Developer
- Frontend Technical Lead
- Technical Lead, Web Platform
- Frontend Architect

### Explicit non-engineering professions

The following title families must fail the profession gate even when the description contains strong technical keywords:

- Product Manager
- Platform Product Manager
- Technical Product Manager
- Product Designer
- UX Designer
- UI Designer
- Product Marketing Manager
- Program Manager
- Project Manager
- Customer Success Manager
- Solutions Consultant
- Sales Engineer when the role is primarily sales/pre-sales rather than software engineering
- Data Scientist / ML Researcher when not a software/product-engineering role

The implementation should use explicit configurable blocked profession phrases where ambiguity is common, especially for `manager`, `designer`, `marketing`, `sales`, and `research` titles.

### Precedence

Profession rejection happens before positive description-keyword logic.

For example:

```text
Senior Product Manager
Description: React, TypeScript, SaaS, product ownership, architecture
```

must be rejected before ranking and Gemini evaluation.

Conversely:

```text
Senior Software Engineer, Product Platform
```

must remain eligible despite containing the words `product` and `platform` because the title clearly identifies an engineering profession.

## 6. Configuration

Add explicit configuration for title-profession gating rather than hard-coding all policy in Python.

Recommended `config/search.yml` additions:

```yaml
engineering_title_keywords:
  - engineer
  - developer

engineering_title_phrases:
  - technical lead
  - frontend lead
  - front-end lead
  - software lead
  - engineering lead
  - software architect
  - frontend architect
  - front-end architect
  - web architect

blocked_profession_title_phrases:
  - product manager
  - platform product manager
  - technical product manager
  - product designer
  - ux designer
  - ui designer
  - product marketing manager
  - program manager
  - project manager
  - customer success manager
```

Existing `blocked_title_keywords` remains responsible for role families such as junior, QA, SRE, DevOps, security engineering, and engineering management.

The profession gate is an additional concern: whether the title belongs to the candidate’s profession at all.

## 7. Deterministic ranking interaction

The ranker must operate only on jobs that pass the engineering-profession gate.

The ranker therefore no longer needs to distinguish Product Manager from Product Engineer through word-overlap scoring; the former never reaches ranking.

Existing deterministic ranking signals may remain unchanged in this release unless required to make tests pass.

No ranking score should override a profession-gate rejection.

## 8. Gemini evaluation ceiling

Change the default:

```yaml
max_jobs_per_run: 75
```

The meaning of this value is:

> maximum number of profession-valid, prefilter-valid, globally ranked jobs that may consume Gemini evaluation calls in one run.

It is a safety ceiling for API quota/runtime, not the desired number of evaluations.

Examples:

- 31 valid candidates -> evaluate all 31.
- 62 valid candidates -> evaluate all 62.
- 103 valid candidates -> rank globally and evaluate the best 75.

If candidates are deferred because of the ceiling, log the count explicitly.

The ceiling remains configuration-driven so it can be raised or lowered after observing Gemini quota and runtime behavior.

## 9. Telegram decision policy

Telegram must reflect final Gemini decisions, not every evaluated job.

### Delivered decisions

`high_priority`
- Section: `Ready to apply`
- Include in digest.
- Generate/send PDF according to existing behavior.

`package_match`
- Section: `Ready to apply`
- Include in digest.
- Generate/send PDF according to existing behavior.

`possible_match`
- Section: `Possible matches`
- Include in digest.
- No PDF by default.

`blocked`
- Section: `Needs review / blockers`
- Include only when Gemini explicitly returns `blocked` and the pipeline intentionally treats it as something useful for user review.

`skip`
- Do not include in Telegram.
- Persist evaluation normally for dedupe/history.
- Do not generate material.

### Unknown decisions

An unrecognized decision must fail closed for delivery:

- log a warning containing the decision value and job ID;
- omit it from Telegram;
- do not silently route it to “Needs review / blockers.”

This prevents future model/schema changes from creating notification noise.

## 10. Telegram ordering

Within every Telegram section, sort by:

1. final Gemini `score` descending;
2. company name ascending, case-insensitive;
3. title ascending, case-insensitive;
4. job ID ascending as a deterministic final tie-breaker.

Example:

```text
Ready to apply
- 91 | Company A - Staff Product Engineer
- 86 | Company B - Senior Frontend Engineer
- 78 | Company C - Product Engineer

Possible matches
- 73 | Company D - Senior Software Engineer
- 67 | Company E - Frontend Developer
```

The order in which jobs were discovered, ranked before Gemini, or evaluated must not affect final Telegram ordering.

PDF send order for ready-to-apply jobs should follow the same final Gemini-score order when practical, so the digest and documents appear consistently.

## 11. Run summary and diagnostics

Extend discovery/pipeline logging so the quality funnel is visible.

Recommended log shape:

```text
discovery: raw=1208 unique=1194 prefilter_rejected=926 profession_rejected=137 eligible=125 selected=75 deferred_by_budget=50
```

Exact counts will vary.

Required diagnostics:

- `profession_rejected`: number removed specifically by the engineering-profession gate.
- `eligible`: jobs valid for deterministic ranking/Gemini after all deterministic gates.
- `selected`: jobs chosen for Gemini this run.
- `deferred_by_budget`: eligible jobs not evaluated because the safety ceiling was reached.

Do not log full descriptions, CV content, cover-letter content, API keys, Telegram credentials, or other secrets.

`RunSummary.ready_to_apply`, `possible_matches`, `skipped`, and `errors` retain their existing semantic meaning unless a small adjustment is necessary to count profession rejections consistently with current skipped behavior.

## 12. Persistence and retry compatibility

Preserve existing SQLite compatibility.

Jobs rejected by the profession gate remain persisted as discovered jobs but are not evaluated by Gemini.

Existing evaluation caching remains unchanged.

Existing generated-material persistence remains unchanged.

Existing Telegram retry behavior remains unchanged for previously eligible delivered decisions.

A historical `skip` evaluation that was incorrectly delivered under old behavior does not need a migration. The new delivery rules apply to future digest construction and retries.

Pending delivery retry queries must not newly treat `skip` evaluations as Telegram-pending work.

## 13. Error handling

- Profession gating is deterministic and must not raise on empty/missing titles; such jobs fail the profession gate unless another exact accepted-title rule applies.
- Unknown Gemini decision values are persisted if the existing evaluation parser permits them, but are not delivered.
- A Telegram sorting/filtering failure must be covered by tests; delivery behavior should remain fail-open per existing Telegram semantics.
- Raising the Gemini ceiling must not alter current per-job evaluation exception handling.
- One Gemini evaluation failure must still allow later selected jobs to be evaluated.

## 14. Testing strategy

Use pytest with no live external calls.

Required regression coverage:

### Profession gate

- `Senior Product Engineer` passes.
- `Staff Frontend Engineer` passes.
- `Senior Software Engineer, Product` passes.
- `Founding Engineer` passes.
- `Frontend Developer` passes.
- `Frontend Technical Lead` passes.
- `Senior Product Manager` fails even when description contains React/TypeScript/SaaS/product ownership.
- `Platform Product Manager` fails.
- `Technical Product Manager` fails.
- `Senior Product Designer` fails.
- `Product Designer, AI` fails.
- Existing blocked title rules still win.

### Ranking/pipeline

- Profession-rejected jobs never reach `rank_jobs()` / Gemini selection.
- A batch with 60 valid engineering candidates and default limit 75 evaluates all 60.
- A batch with 90 valid engineering candidates evaluates exactly 75.
- The selected 75 remain the globally highest deterministic-ranked candidates.
- Deferred count is logged as 15 in the 90-candidate case.

### Telegram

- `skip` items never appear in digest text.
- Unknown decisions never appear and produce a warning.
- Ready-to-apply items are sorted by score descending.
- Possible matches are sorted by score descending.
- Blocked review items are sorted by score descending.
- Equal scores use deterministic company/title/job-ID tie-breakers.
- Input ordering does not affect output ordering.
- PDF delivery order for current-run ready items follows final score descending.

### Regression

Existing tests for:

- discovery source failure isolation;
- SQLite evaluation caching;
- pending Telegram delivery retry;
- PDF regeneration on retry;
- dry-run behavior;
- scheduling;
- v2 global pre-Gemini ranking;

must continue to pass.

## 15. Files expected to change

Likely production files:

```text
config/search.yml
src/job_hunter/models.py
src/job_hunter/config.py
src/job_hunter/prefilter.py
src/job_hunter/discovery.py
src/job_hunter/pipeline.py
src/job_hunter/telegram.py
```

Likely tests:

```text
tests/test_config.py
tests/test_prefilter.py
tests/test_discovery.py
tests/test_pipeline.py
tests/test_telegram.py
```

Documentation updates may include `README.md` and `AGENTS.md` if configuration or architecture descriptions need synchronization.

## 16. Acceptance criteria

v2.1 is complete when all of the following are true:

1. Product Manager, Product Designer, and similar non-engineering roles do not consume Gemini calls solely because their descriptions contain positive technical/product keywords.
2. Relevant engineering titles such as Product Engineer, Software Engineer, Frontend Engineer, Developer, Founding Engineer, Technical Lead, and supported Architect variants remain eligible.
3. The default Gemini evaluation ceiling is 75.
4. Runs with fewer than 75 valid engineering candidates evaluate all of them.
5. Runs with more than 75 valid engineering candidates evaluate only the globally highest-ranked 75 and log how many were deferred.
6. Telegram omits every `skip` decision.
7. Unknown decision values are omitted rather than routed to “Needs review / blockers.”
8. Every Telegram section is sorted by final Gemini score descending with deterministic tie-breakers.
9. Existing delivery retry behavior remains functional and does not retry `skip` evaluations.
10. Existing SQLite artifacts remain usable without reset/migration loss.
11. `pytest -q` passes in CI.
12. No changes are made to the Gemini model or v2 discovery-source coverage as part of this release.
