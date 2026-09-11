# Coding Standards

> **Source**: go/dev-handbook
> **Related files**: [testing-standards.md](testing-standards.md) (testing framework), [development-lifecycle.md](development-lifecycle.md) (SDLC), [service-ownership.md](service-ownership.md) (quality expectations)

This document outlines the coding standards for engineering teams at Rokt. These standards are about risk mitigation and cognitive scalability. We optimize code for reading, not writing. You will write a line once, but you and your teammates will read it ten times during debugging, refactoring, or incidents.

# I. Readability & Cognitive Load

## 1. Line Length & Formatting

* Standard: Soft limit at 100 characters; hard limit at 120.
* The Why: We review code in split-screen diffs (GitHub/GitLab). Horizontal scrolling breaks flow and hides bugs.
* Automation: Do not waste brainpower formatting manually. We enforce Prettier (JS/TS), Ruff (Python), or gofmt (Go) in our CI pipeline. If the linter passes, the formatting is correct.

## 2. Naming Conventions (Intent over Brevity)

* Variables: Must be nouns describing content. Avoid single letters (except i, j in loops).
  * Bad: list, d, val
  * Good: campaign_candidates, bid_price_micros, user_embedding
* Functions: Must be verbs describing action + outcome.
  * Bad: process(), handle_data()
  * Good: calculate_ctr_prediction(), fetch_user_profile()
* Booleans: Should sound like questions (is_active, has_converted, can_retry).

## 3. Comments: The "Why", not the "What"

* The Rule: Code describes how it works. Comments describe why it exists.
* Avoid: i += 1 // Increment i (This is noise).
* Required: Explanations for business logic anomalies, complex algorithmic choices, or "magic numbers."
  * Example: // Using a 1.2x multiplier here to account for the Q4 seasonality drift observed in ticket #402

# II. Structure & Modularity (System Design in the Small)

## 1. File Size & Decomposition

* The Signal: If you cannot describe what a file does in one sentence without using the word "and," it is too large.
* Standard: Break files when they cross boundaries of concern (e.g., Data Access vs. Business Logic).
* The "Scroll Test": If a file exceeds 800 lines, it is a candidate for refactoring. If a function exceeds 100 lines, it is a candidate for decomposition.

## 2. Complexity & Nesting

* Guard Clauses: Avoid deep if/else nesting. Return early.
  * Trade-off: Deep nesting forces the reader to hold multiple states in their head (high cognitive load).
  * Action: Use "Guard Clauses" to handle edge cases at the top of the function, leaving the "happy path" at the bottom with zero indentation.

## 3. Dependency Injection

* Standard: Do not instantiate dependencies (DB connections, API clients) inside your business logic classes. Pass them in (via constructor or arguments).
* The Impact: This decouples our architecture and makes unit testing trivial because we can mock the dependencies.

# III. Reliability & Testing (De-risking Production)

## 1. Testing Strategy: The Pyramid

* Unit Tests (70%): Test logic in isolation. Requirement: Every PR modifying business logic must include unit tests.
* Integration Tests (20%): Test how components talk (e.g., API to DB). Requirement: Focus on the "Happy Path" and one "Failure Path."
* E2E/Smoke Tests (10%): Test the full user journey. Requirement: Critical revenue paths (e.g., "Ad Selection") must have E2E coverage.

## 2. Test Quality

* Naming: Test names must describe the scenario and the expected result.
  * Good: test_calculate_bid_returns_zero_when_budget_exhausted
* No Logic in Tests: Tests should be declarative (Setup -> Act -> Assert). If your test has loops or complex logic, it is a bug waiting to happen.

## 3. Defensive Coding

* Input Validation: Never trust inputs from outside your component boundary (even from other internal services). Validate schema structure and types immediately upon entry.
* Error Handling: Catch specific errors, not generic ones. If you catch an error, you must either handle it (retry/fallback) or log and re-throw it with added context. Never swallow errors silently.

# IV. ML & Data Specifics (Ad Relevancy Context)

## 1. Determinism

* Standard: All random number generators (for sampling, splitting, or initialization) must accept a seed.
* The Why: We cannot debug a ranking anomaly if we cannot reproduce the shuffle order.

## 2. Data Types

* Money & Precision: Never use Floating Point math for currency. Use Integers (micros/cents) or Decimal types.
* Null Safety: Explicitly handle null/None. In ML pipelines, a null feature often defaults to 0, which can silently destroy model accuracy. Explicitly impute defaults.

# V. The Review Standard

## 1. PR Size

* Limit: < 400 lines of code changes.
* The Why: Large PRs get "LGTM" (Looks Good To Me) stamps without reading. Small PRs get actual feedback.

## 2. Deprecation

* Rule: If you replace code, delete the old code immediately. Do not comment it out. That is what Git history is for. Dead code is technical debt.

# VI. Constants & Configuration (The "No Magic" Rule)

## 1. No Magic Numbers or Strings

* The Standard: If a number or string literal appears in your code (other than `0`, `1`, or `-1`), it must be assigned to a named constant.
* Bad: `if (bid > 500000) { ... }` (What is 500000? A cap? A budget? A currency?)
* Good: `const MAX_BID_MICROS = 500000; if (bid > MAX_BID_MICROS) { ... }`
* The Why: "Magic values" hide intent. Naming them centralizes control and explains the business logic.

## 2. Config vs. Constants

* The Distinction:
  * Constants: Values that change only when code logic changes (e.g., `SECONDS_IN_MINUTE`, `PI`). Keep these in code.
  * Configuration: Values that might change based on environment (Dev/Prod) or tuning (e.g., `DB_TIMEOUT_MS`, `ML_MODEL_VERSION`).
* Action: Never hard-code configuration. Use environment variables or a config service. This allows us to tune system performance during an incident without a full code deployment.

# VII. DRY (Don't Repeat Yourself) & Abstraction

## 1. The Rule of Three

* The Standard: Do not abstract immediately.
  * 1st time: Write it.
  * 2nd time: Copy it.
  * 3rd time: Refactor into a shared function/component.
* The Why: Premature abstraction is the root of all evil. It creates complex, generic functions that handle too many use cases poorly. We prefer a little duplication over the wrong abstraction.

## 2. Logic vs. Boilerplate

* Distinction: DRY applies strictly to Business Logic (e.g., how we calculate Ad Rank). If that logic is duplicated and one changes, the other becomes a bug.
* Relaxation: DRY is relaxed for Tests. Tests should be DAMP (Descriptive and Meaningful Phrases). It is okay to repeat setup code in tests if it makes the test case readable in isolation.

# VIII. Object-Oriented Design (Scalable Architecture)

## 1. Composition over Inheritance

* The Standard: Avoid deep class inheritance hierarchies (e.g., `BaseController` -> `AdController` -> `VideoAdController`). Use Composition instead.
* The Why: Inheritance creates tight coupling; changing the parent breaks all children. Composition (injecting a `Logger` or `AuthService` into your class) allows flexible behavior that is easy to test.

## 2. Program to Interfaces, not Implementations

* The Standard: Type hint and depend on Interfaces (Abstract Base Classes in Python, Interfaces in TS/Go), not concrete classes.
* Example: Depend on `IStorageProvider`, not `S3StorageProvider`.
* The Impact: This allows us to swap infrastructure (e.g., moving from S3 to GCS, or swapping a real DB for an in-memory mock during testing) without rewriting a single line of business logic.

## 3. Encapsulation

* The Rule: Keep internal state private. Do not expose public fields unless they are simple Data Transfer Objects (DTOs).
* The Why: If external code can modify your object's state directly, you cannot guarantee data integrity.

# IX. File Headers & Module Documentation

## 1. The "What is this?" Header

* The Standard: Every file (module) must start with a docstring/comment block explaining its Responsibility.
* Avoid: Author names or creation dates (Git handles this).
* Required: A high-level summary of what the file contains and its role in the larger system.
  * Example:

```
"""
AdSelectionService:
Orchestrates the retrieval, filtering, and ranking of ad candidates.
Acts as the facade between the Auction Engine and the ML Inference Service.
"""
```

## 2. Component Boundaries

* The Standard: If a directory represents a specific Component (e.g., `billing/`), it should have a `README.md` or an `index` file that defines the Public API of that component.
* The Why: This tells other engineers: "Only import these things from this folder; everything else is internal implementation detail."
