# Grounding Brief: Hermes OAuth Login

## Objective

- Workflow: plan-to-launch, default mode escalated for credential security.
- Decision: let operator log Hermes into Claude or OpenAI account without putting OAuth tokens in Git or `.env`.
- Scope: interactive login, persistent credential state, provider-scoped runtime egress, status and logout.
- Non-goals: automatic `doc-write` routing, unattended browser automation, copying host Claude/Codex credentials, M2b launch.

## Sources

| Source | Evidence |
| --- | --- |
| User request, 2026-09-15 | OAuth account login required; every code change must use a PR. |
| `docker-compose.yml` | `hermes-doc` has isolated state, internal network, OpenAI-only proxy, and read-only config. |
| `docker/hermes-doc-egress.conf` | Runtime currently permits only `api.openai.com:443`. |
| Pinned Hermes `hermes auth --help` | OAuth providers include `anthropic` and `openai-codex`; `--no-browser` is supported. |
| Hermes tag `v2026.9.14` source matching pinned-image date | Anthropic uses PKCE S256, state check, `platform.claude.com` token exchange, and scopes `org:create_api_key user:profile user:inference`; OpenAI Codex uses device code and `auth.openai.com`, then `chatgpt.com/backend-api/codex`. |
| PR [#129](https://github.com/Zhachory1/ai-pr-automation/pull/129) | M2a gate changes are separate and still open. |

Memory and ZBrain MCP tools were unavailable in this session. Local repository and pinned-image CLI are primary evidence.

## Facts

- OpenAI API-key inference already passed one manual smoke run.
- Account OAuth differs from API-key authentication.
- Current runtime cannot complete OAuth because its network only reaches the provider API proxy.
- Anthropic OAuth requests permission to create API keys plus profile and inference access; human must accept or reject this scope.
- OAuth state can persist in `hermes_doc_state`; token values must never enter Compose output, logs, Git, or command arguments.
- Interactive browser/code completion requires operator participation.

## Constraints

- Human performs browser login and approves provider terms.
- Stop `hermes-doc` before mutating shared auth state.
- Login helper gets temporary outbound network access. Runtime keeps provider-domain proxy only.
- Existing zero-tool and publication boundaries stay unchanged.
- Claude/OpenAI OAuth does not authorize automatic document routing.

## Risks And Unknowns

| Risk | Next check |
| --- | --- |
| OAuth endpoint list changes | Exercise login against pinned image; fail closed on proxy denial. |
| Provider subscription disallows this usage | Operator confirms provider terms before login. |
| Token refresh needs another domain | Capture denied proxy destination; add only verified provider domain in follow-up PR. |
| Shared volume changes while gateway runs | Login procedure stops gateway first. |
| Login succeeds but Runs API provider override differs | Verify auth status first; make one manual zero-tool smoke only after operator approval. |

## Proceed Gate

- Status: council-first.
- Reason: credential storage and expanded provider egress are security-sensitive.
- Next: lightweight PRD/DD, one minimal council, task plan, then implementation PR.
