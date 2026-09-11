# A Pragmatic Guide to Testing Standards

> **Source**: go/dev-handbook
> **Related files**: [coding-standards.md](coding-standards.md) (code quality), [development-lifecycle.md](development-lifecycle.md) (SDLC), [approved-tooling.md](approved-tooling.md) (test frameworks)

This document synthesizes industry best practices (including Google's testing philosophy) into a unified framework for writing effective, maintainable tests at Rokt.

# Part 1: Foundational Principles of Test Effectiveness

## The Three Pillars of Effective Tests: Fidelity, Resilience, and Precision

All testing decisions must be evaluated against the competing trade-offs of the Three Pillars. Every test should try to maximize these three qualities:

1. **Fidelity**: A high-fidelity test is "very sensitive to defects". Its guiding principle is: "When the code under test is broken, the test fails". Fidelity is maximized by ensuring tests "cover all the paths" and "include all relevant assertions". A test's fidelity is highest when it uses real implementations of its dependencies and is "reduced" when it uses mocks.
2. **Resilience**: A resilient test "shouldn't fail if the code under test isn't defective". It "only fails when a breaking change is made". This quality is crucial for reducing maintenance costs, as non-breaking changes (like refactoring) can be made without needing to modify the test. A flaky test, by definition, "has very low resilience". Resilience is maximized by "only testing the exposed API... avoid reaching into internals" and by favoring "stubs and fakes over mocks".
3. **Precision**: A high-precision test "tells you exactly where the defect lies" when it fails. A well-written unit test can pinpoint the exact line of code at fault, whereas poorly written large tests (especially end-to-end) often exhibit "very low precision". Precision is maximized by keeping tests "small and tightly focused" and by choosing "descriptive method names".

Crucially, these three qualities are often in tension with each other. This tension represents the central trade-off in all testing. For example, a large end-to-end test that uses the real, live system maximizes Fidelity. However, it does so at the expense of Resilience (it is more likely to be flaky) and Precision (when it fails, it's hard to know where the problem is). Conversely, a small unit test that mocks all dependencies maximizes Resilience and Precision (it's fast, focused, and isolated), but it does so at the expense of Fidelity (its behavior may diverge from the real implementation).

All subsequent recommendations in this document can be understood as strategies for navigating and finding the optimal balance between these three competing pillars.

| Pillar | Guiding Question | How to Maximize |
| :---- | :---- | :---- |
| Fidelity | Does the test find real bugs? | Ensure tests cover all code paths and include all relevant assertions. Prefer using real implementations or high-fidelity fakes. |
| Resilience | Does the test fail only for real bugs? | Only test the exposed public API; avoid testing implementation details. Favor stubs and fakes over mocks. Avoid flakiness. |
| Precision | When the test fails, do I know why? | Keep tests small and tightly focused on a single behavior. Use descriptive method names that state the scenario and expected outcome. |

## Beyond the Pyramid: The "SMURF" Heuristic for a Scalable Test Suite

The "test pyramid" (many unit tests, fewer integration tests, fewest end-to-end tests) is the canonical heuristic. However, this simple model lacks the details you need as a test suite grows to scale.

To manage the trade-offs of a massive test suite, use the **SMURF** mnemonic. SMURF is a heuristic for balancing the test suite based on these five attributes:

* **S**peed: Faster tests, like unit tests, "can be run more often" and thus "catch problems sooner".
* **M**aintainability: This is the "aggregated cost of debugging and maintaining tests," which "adds up quickly." A larger system under test has greater exposure to "dependency churn and requirement drift," which creates more maintenance work.
* **U**tilization: Tests that "use fewer resources (memory, disk, CPU) cost less to run." A good test suite optimizes utilization so its cost does not grow "super-linearly with the number of tests".
* **R**eliability: "Reliable tests only fail when an actual problem has been discovered." "Sorting through flaky tests for problems wastes developer time".
* (**F**)idelity: (While not explicitly in the SMURF acronym, Fidelity is the fundamental purpose of the test.)

The SMURF heuristic can be understood as the Three Pillars (Fidelity, Resilience, Precision) applied at the system level, with the critical addition of economics.

* Reliability (SMURF) is the system-wide outcome of Resilience (Pillar). An unreliable suite is composed of non-resilient, flaky tests.
* Speed (SMURF) is the system-wide outcome of Precision (Pillar). A suite of "small and tightly focused" tests is inherently "faster."
* Maintainability & Utilization (SMURF) are the economic factors. They introduce the cost (of developer time and compute resources) as a first-class citizen in the trade-off.

This framework reframes testing from a simple correctness problem into a complex systems engineering and economics problem. It forces an engineering leader to analyze the total cost and ROI of the test suite, not just its bug-finding ability.

# Part 2: Recommendations for Test Scoping and Structure

## Unit Tests: The Foundation

A "solid base of unit tests" is the required foundation for a healthy testing strategy. These tests should be "small and tightly focused".

A central principle for unit testing is to **Prefer Testing Public APIs Over Implementation-Detail Classes**.

* A **public API** is a class that "can be called by any number of users" or is "used in a different part of a codebase".
* An **implementation-detail class** "exists only to support public APIs" and is often "called by a very limited number of users (often only one)".

The recommendation is that an implementation-detail class "probably doesn't need tests" of its own, if "all paths can be tested through the public API that use[s] it".

The rationale for this principle is that over-testing implementation details makes code "harder to maintain" and "refactoring... more difficult". Testing implementation-detail classes is still "useful in many cases" (e.g., if the class is particularly "complex"), but they "often don't need to be tested in as much depth".

This rule is a direct, practical application of the **Resilience** pillar. A test that is coupled to an implementation detail is brittle—it will break during a refactor even if the system's behavior is still correct. A test coupled only to the public API is resilient to such non-breaking changes, drastically reducing the cost of test maintenance.

## Integration Tests: Bridging the Gaps and Fixing the "Hourglass"

The recommendation is to "Don't skimp on integration testing". Integration tests are presented as the primary solution to the "test hourglass" anti-pattern. The "hourglass" describes a common failure mode where a team has a "solid base of unit tests" (the bottom) and a growing number of "end-to-end" tests (the top), but a "narrow waist" with few, if any, medium-scoped integration tests.

This anti-pattern is a problem because, as established, end-to-end tests are "slower, more flaky, and more expensive to maintain". A healthy test suite should "expand the medium size tests... allowing us to have fewer end-to-end tests with real backends".

Integration tests are therefore the primary economic tool for managing the test suite. They allow a team to "shift left," verifying complex interactions without paying the full price (in Speed, Reliability, and Maintainability) of a full end-to-end test. By strategically adding integration tests, teams can reduce their reliance on E2E tests, thus improving the overall economic (SMURF) profile of the entire test suite.

## End-to-End Tests: A Guide to Cost-Effective Use

End-to-end (E2E) tests are a "critical part of a balanced testing diet" because they "can catch bugs that manifest across your entire system". However, they are also "slower, more flaky, and more expensive to maintain". Therefore, they must be used sparingly and managed as high-cost assets.

Key recommendations for E2E tests include:

* **Limit Scope**: "Keep your total end-to-end count low."
* **Focus**: Focus only on aspects "that cannot be reliably evaluated with smaller tests," such as "resource allocation, concurrency issues and API compatibility."
* **Define Use Case**: Have "one corresponding end-to-end test" for "each important use case" and "each important class of error."
* **Improve Resilience**: "Focus your efforts on verifying overall system behavior instead of specific implementation details." For example, "verify that the process succeeds independent of the exact messages or visual layouts," which change frequently.
* **Budget for Maintenance**: "Be prepared to allocate at least one week a quarter per test to keep your end-to-end tests stable."
* **Improve Precision**: Make tests "easy to debug" by "preserving all relevant system state information (e.g.: screenshots, database snapshots, etc.)."

This advice reframes E2E tests not as code, but as a high-cost service that the team must operate and pay for in perpetuity. The "one week a quarter" data point is a budget that acknowledges the high Total Cost of Ownership (TCO) of these tests, aligning perfectly with the "Maintainability" arm of the SMURF heuristic.

These recommendations are, in effect, a mitigation strategy for the poor trade-offs E2E tests force within the Three Pillars framework. E2E tests have low **Resilience** (they're "flaky"); the mitigation is to test behaviors, not brittle UI layouts. They have low **Precision**; the mitigation is to add debugging aids ("screenshots, database snapshots") to artificially improve precision for the human developer.

# Part 3: Actionable Recommendations for Test Authoring

## Writing Focused and Maintainable Tests

When writing an individual test function, the primary goals are focus and maintainability.

**Test Behaviors, Not Methods**

It is "harmful to think that tests and public methods should have a 1:1 relationship". A "single method can exhibit many behaviors," and "a single behavior sometimes spans across multiple methods". The recommendation is to "use separate tests to verify separate behaviors". A test name that "is a direct mirror of the method's name is a bad sign".

**Keep Tests Focused: One Scenario Per Test**

This is the tactical implementation of the first principle. A test should not test multiple scenarios at once. A "bad" test might check a deposit, then a successful withdrawal, then a rejected overdraft, then a successful overdraft, all in one function. This is "testing three scenarios, not one," and is "hard to track".

The "better approach" is to refactor this into three separate, focused tests:

1. `TEST_F(BankAccountTest, CanWithdrawWithinBalance)`
2. `TEST_F(BankAccountTest, CannotOverdraw)`
3. `TEST_F(BankAccountTest, CanOverdrawUpToOverdraftLimit)`

The benefits of this **"One Behavior, One Scenario, One Test"** approach are numerous: the logic is "easier to understand," setup code is "simpler," side effects are isolated, and "test names clearly describe each scenario". This approach directly maximizes the **Precision** pillar, as a failing test now points to a single, specific, broken behavior.

## Writing Readable and Descriptive Tests

A test must be easily understood by a human inspector. The golden rule: **"Since tests don't have tests, it should be easy for humans to manually inspect them for correctness."** This principle informs several specific rules for test readability.

**Write Descriptive Test Names**

Test names must be "clear, useful, even verbose". A name like `isUserLockedOut_invalidLogin` is considered poor because it requires reading the code to understand the outcome. A good test name "contain[s] both the scenario being tested and the expected outcome".

An example is `shouldLockOutUserAfterThreeInvalidLoginAttempts`. This provides "immediate failure diagnosis" and makes it easier to "learn which scenarios exist".

**Favor DAMP over DRY**

While "Don't Repeat Yourself" (DRY) is a best practice for production code, tests should favor the DAMP principle: "Descriptive and Meaningful Phrases". DAMP "emphasizes readability over uniqueness," even at the "expense of greater code duplication". A "DRY" test that uses loops and helper methods may be concise, but it requires "mental computation" to understand. A "DAMP" test that explicitly repeats similar code (e.g., `self.forum.Register(user1)` and `self.forum.Register(user2)`) is "more obviously correct" to a human inspector.

**Don't Put Logic in Tests**

"Simplicity is more important than flexibility in tests". Tests should avoid complexity by stating their "inputs and outputs directly rather than computing them". The use of "operators, loops, or conditionals" in a test makes it harder to be confident in the test's own correctness. If logic is truly required, it should be moved into a "nontrivial test utility" which "should have its own tests".

**Use Helpers Intelligently**

There is a nuanced trade-off in the use of helper methods. The unified rule is one of intent: **A test body should contain everything a human needs to understand the specific behavior being tested, and nothing more.**

* A helper method that hides irrelevant boilerplate (e.g., `newCalculator()`) is **GOOD**, as it makes the test more concise and focused.
* A helper method that hides behavior-critical logic (e.g., `_RegisterAllUsers()`) is **BAD**, as it makes the test harder to inspect.

# Part 4: A Comprehensive Guide to Managing Dependencies and Test Doubles

## The Fidelity Hierarchy: Prefer Reals, Fakes, then Mocks

When a test has dependencies, we recommend a clear hierarchy of preference, ordered from highest to lowest fidelity. Fidelity refers to "how closely the behavior of the test resembles the behavior of the production code".

1. **Use the Real Implementation:** This is the default preference as it "provides the most fidelity". The trade-offs are that real implementations "can be slow, non-deterministic, or difficult to instantiate (e.g., it connects to an external server)".
2. **Use a Fake Implementation:** If a real implementation is not feasible, a fake is the next best choice. A fake is a "lightweight implementation of an API that behaves similarly to the real implementation" (e.g., "an in-memory database"). A fake ensures "high fidelity" but "takes effort to write and maintain".
3. **Use a Mock:** This is the last resort. A mock "reduces fidelity" because "it doesn't execute any of the actual implementation". Its behavior is "specified inline in a test" (a technique known as stubbing) and "may diverge from the behavior of the real implementation," leading to false confidence.

This hierarchy is summarized as: "Prefer realism or isolation (mock only when needed)".

| Preference | Type | Fidelity | Key Trade-offs |
| :---- | :---- | :---- | :---- |
| **1. Best** | Real Implementation | **Highest** | Provides the most confidence. Can be slow, non-deterministic, or hard to set up. |
| **2. Good** | Fake Implementation | **High** | Behaves like the real thing. Fast and reliable. Takes effort to write and maintain. |
| **3. Last Resort** | Mock | **Reduced** | Fast and easy to specify inline. Does not execute real code; can diverge from real behavior. |

## "Don't Overuse Mocks": A Core Tenet

A recurring and central recommendation is "Don't Overuse Mocks". The overuse of mocks is an anti-pattern that introduces severe problems:

* **Harder to Understand:** Mocks require "extra code to tell the mocks how to behave," which "detracts from the actual intent of what you're trying to test".
* **Harder to Maintain:** Mocks "leak implementation details... into your test". This means when the implementation of the production code changes (a refactor), the test breaks, even if the user-facing behavior is unchanged.
* **Less Assurance (Lower Fidelity):** A test with mocks only guarantees "that your code will work if your mocks behave exactly like your real implementations," which is "very hard to guarantee". Mocks and real implementations "get out of sync," leading to tests that pass even when the code is broken.

This overuse of mocks is a systematic violation of all three foundational pillars. It violates **Fidelity** ("less assurance"), **Resilience** ("harder to maintain," breaks on refactors), and **Precision** ("harder to understand").

## Know Your Test Doubles: Stubs, Mocks, and Fakes

To clarify the "Fidelity Hierarchy," we provide a precise taxonomy for different types of test doubles, distinguishing them by their intent.

* **Stub:** A stub has "no logic and only returns what you tell it to return". It is used to provide state for a test. For example, `when(stubAuthenticationService.isAuthenticated(USER_ID)).thenReturn(true);`.
* **Mock:** A mock "has expectations about how it should be called," and the test will fail if it is not. It is used to test interactions between objects, especially when there is no other visible state change to assert (e.g., verifying a "save" method was called).
* **Fake:** A fake is a "lightweight implementation of an API that behaves like the real implementation" but is not production-suitable (e.g., FakeAuthenticationService). It is used to test stateful behavior. For example, `fakeAuthenticationService.addAuthenticatedUser(USER_ID);` `assertTrue(accessManager.userHasAccess(USER_ID))`;.

The critical difference is that the Fake has its own internal logic and state, thus providing a much higher-fidelity test of the code's behavior than a simple, stateless Stub.

## The "Fake" Implementation: A High-Fidelity Shared Asset

Fakes are the preferred high-fidelity alternative when real implementations are not practical. Because they are so critical, there are specific recommendations for their management:

* **Ownership:** "Each fake should ideally be created and maintained by the person or team that owns the real implementation".
* **Testing the Fake:** "Fakes should have their own tests" to ensure they "behave like the real implementation".
* **Testing Method:** The best way to test a fake is to "write tests against the API's public interface, and run those tests against both the real and fake implementations".

This elevates a "Fake" from a simple test object into a first-class, shared engineering asset. It is a reusable component (e.g., FakeDatabase) with its own test suite and a formal maintenance contract, provided by the API owner to all of that API's consumers. This is a high-investment (the team must maintain two implementations) but high-return (all consumers get high-fidelity, high-speed, high-resilience tests) engineering strategy.

## "Don't Mock Types You Don't Own"

This is a critical, specific rule for maintaining resilience at API boundaries. "Types you don't own" refers to third-party libraries.

Mocking an external library is dangerous for two reasons:

1. **Upgrades:** It "can make it harder to upgrade the library" because the "expectations of an API hardcoded in a mock" will be out of date, requiring manual test fixes.
2. **False Confidence:** "The assumptions built into mocks may get out of date... resulting in tests that pass even when the code under test has a bug".

The correct alternatives are:

1. Use the "real implementation" or a "fake implementation" that is "provided by the library owner".
2. If neither is possible, **"create a wrapper class"** that calls the third-party type, and then **"mock this wrapper class instead"**.

This "wrapper" (an Adapter or Facade pattern) is an application of "Separation of Concerns". It isolates the external dependency to a single class that you own and control. This allows you to mock your simple adapter, not their complex, unowned library, thus preserving the resilience of your test suite against external churn.

# Part 5: Addressing Test Flakiness and Long-Term Reliability

## Diagnosing the Causes of Flakiness

Test flakiness is identified as "one of the main challenges of automated testing". A flaky test has "very low resilience" and "wastes developer time," eroding trust in the entire test suite.

A systematic cheat sheet for triaging flaky tests divides the causes into four general areas:

1. **The tests themselves:** Improper initialization or cleanup, "invalid assumptions" about test data or execution order, and "Dependencies on the timing" of the application (race conditions).
2. **The test-running framework:** "Failure to allocate enough resources" for the test, or "Improper scheduling" that causes tests to "collide" and interfere with one another.
3. **The application or system under test (SUT):** Internal "race conditions," "uninitialized variables," "memory leaks," or being slow to respond.
4. **The OS and hardware (Infrastructure):** "networking failures or instability," "disk errors," or resource consumption by unrelated processes.

The existence of this formal, multi-category taxonomy demonstrates that flakiness is treated as a systemic, high-priority bug in the engineering process, not as a minor annoyance.

## Strategies for Reliability and Hermeticity

The primary architectural defense against flakiness is **hermeticity**. "Hermetic environments are generally less likely to be flaky". A hermetic test is one that has no external dependencies, such as network services.

This connects all the previous concepts. A test that depends on a real, external network service is non-hermetic and prone to flakiness (low **Resilience**). The recommended solution is to use a **"fake implementation"** (like an "in-memory credit card server") or a **"hermetic local server"**.

Therefore, the "Fidelity Hierarchy" (Real > Fake > Mock) is also a Flakiness Reduction framework. When a "Real Implementation" is non-hermetic and flaky, the recommendation is to choose a "Fake Implementation". This is a conscious strategic trade-off: the team accepts a minor decrease in Fidelity to gain a massive increase in **Resilience** (by eliminating flakiness) and **Speed**.

# Part 6: Synthesized Recommendations and Core Principles

## The Unified Framework: From Culture to Code

The collection of testing recommendations forms a single, unified framework that connects high-level culture to low-level code.

1. **Strategy (The "Physics"):** All testing decisions must be evaluated against the competing trade-offs of the **Three Pillars (Fidelity, Resilience, Precision)** and the system-level economics of the **SMURF Heuristic (Speed, Maintainability, Utilization, Reliability)**
2. **Tactics (The "How"):** Every specific rule is a tactic to find the optimal strategic balance. (e.g., "Be DAMP" optimizes for **Precision**; "Use Fakes" optimizes for **Resilience** and **Speed**; "Test Public APIs" optimizes for **Resilience**).

## Actionable Checklist for Test Authoring

* **One Behavior, One Test:** Test behaviors, not methods. Keep tests focused on a single scenario.
* **Test Public APIs:** Prefer testing public APIs over internal implementation details.
* **Name Descriptively:** Test names must include the "scenario... and the expected outcome"
* **Optimize for Readability:** Favor DAMP (Descriptive and Meaningful Phrases) over DRY (Don't Repeat Yourself)
* **Keep Tests Simple:** "Don't Put Logic in Tests." State inputs and outputs directly rather than computing them

## Actionable Checklist for Dependency Management

* **Follow the Hierarchy:** In order of preference, use the **Real Implementation**, a **Fake Implementation**, or (as a last resort) a **Mock**.
* **Mocks are a Last Resort:** "Don't overuse mocks". They are low-fidelity, brittle, and hard to read, violating all three pillars of effective testing.
* **Isolate External Code:** "Don't Mock Types You Don't Own". Instead, use a wrapper (Adapter) and mock your own wrapper.
* **Invest in Fakes:** Treat Fakes as shared, first-class engineering assets that are tested and maintained by the owning team.
* **Strive for Hermeticity:** Use fakes and hermetic servers to eliminate non-deterministic, flaky dependencies and improve test reliability.
