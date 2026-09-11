# Your First Week at Rokt

> **Source**: go/dev-handbook
> **Related files**: [quick-reference.md](quick-reference.md) (tools and links), [onboarding-resources.md](onboarding-resources.md) (team-specific guides)

Whether you're new to Rokt or transferring to a new team, your goal for week one is the same: **ship a change to production**. This section provides the practical steps to make that happen.

## Day 1-2 Checklist

**For new Rokt hires:**

- [ ] Complete IT onboarding (laptop, accounts via IT ticket)
- [ ] Set up AppGate SDP for internal network access (go/appgate)
- [ ] Request GitHub access via #technology-github
- [ ] Install your IDE (Cursor, VS Code, or JetBrains—see go/jetbrains for licenses)
- [ ] Set up 1Password for credential management (go/1password)
- [ ] Join key GChat channels (see [quick-reference.md](quick-reference.md))

**For everyone (including team transfers):**

- [ ] Meet your onboarding buddy (your manager will introduce you)
- [ ] Find your team in go/teams and review team ownership
- [ ] Locate your team's primary repos in Cortex (go/cortex)
- [ ] Join your team's GChat channels
- [ ] Get added to your team's on-call rotation (if applicable)
- [ ] Review your team's onboarding guide (see [onboarding-resources.md](onboarding-resources.md))

## Finding Your Work

The path from "what should I work on?" to "where's the code?" follows this workflow:

1. **Jira** (go/jira) → Find tickets assigned to you, or browse your team's backlog. Your manager or buddy will point you to a good first ticket.
2. **Cortex** (go/cortex) → Look up the service mentioned in the ticket to find the owning repository, runbook, and team contacts.
3. **GitHub** (github.com/rokt) → Clone the repository and create a feature branch.

**Tip:** If you're unsure which repo to work in, check the ticket for service names, then search Cortex. Your buddy can also point you in the right direction.

## Your First Change

Here's the end-to-end flow from ticket to production:

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ Jira Ticket │───▶│  Find Repo  │───▶│Clone & Branch│───▶│Write Code & │
│             │    │  (Cortex)   │    │             │    │   Tests     │
└─────────────┘    └─────────────┘    └─────────────┘    └──────┬──────┘
                                                                │
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌──────▼──────┐
│  Verify &   │◀───│ Production  │◀───│  DeployKit  │◀───│  Create PR  │
│   Monitor   │    │             │    │   Builds    │    │  & Review   │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

**Step-by-step:**

1. **Clone and branch**: `git clone <repo-url>` then `git checkout -b your-branch-name`
2. **Write code and tests**: Check the repo's README for local development setup. Most repos use `make test` or language-specific test commands (see [approved-tooling.md](approved-tooling.md) for testing frameworks).
3. **Create a PR**: Push your branch and open a Pull Request in GitHub. Link the Jira ticket in the PR description.
4. **Code review**: PRs require approval from a code owner (check the CODEOWNERS file). Typically 1 approval is needed, but some repos require more. All CI checks must pass.
5. **Merge to main**: Once approved and green, merge your PR. Code merged to main deploys automatically via DeployKit.
6. **Watch deployment**: Monitor deployment progress in Buildkite (go/buildkite). Approve any blocking steps there. Check for build/deploy failures.
7. **Verify in production**: Use Datadog (go/datadog) to confirm your change is working. Check for errors in logs (go/observe) and verify key metrics.

## Common Questions

| Question | Answer |
| :--- | :--- |
| How do I run tests locally? | Check the repo README. Common patterns: `make test`, `npm test`, `pytest`, or `./gradlew test`. |
| Who can approve my PR? | Check the CODEOWNERS file in the repo. Generally, any team member can approve. |
| How do I get access to X? | IT requests go to #hitech. For specific systems, look for go/request-* links. |
| How do I connect to staging? | Use AppGate SDP (go/appgate) + your role-specific access. Ask your buddy for team-specific details. |
| What if my deploy fails? | Check DeployKit logs in GitHub Actions, consult the service runbook, and ping your team channel. |
| What if my tests fail in CI? | Read the CI logs carefully. Common issues: flaky tests, missing dependencies, or environment differences. |
| Who do I ask when I'm stuck? | Your buddy first → Team channel → Domain-specific channels (e.g., #eng-architecture, #technology-github). |
| How do I find the runbook? | Look in Cortex for the service, or check the repo's docs/ or runbook/ folder. |
