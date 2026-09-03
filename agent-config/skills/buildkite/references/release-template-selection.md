---
name: buildkite-release-template-selection
description: >-
  Resolve the Buildkite release pipeline template UUID by listing org pipeline
  templates and filtering the active stable template name.
---

# Release Template Selection

Use this reference when executing a template-backed release pipeline handoff
that omits `pipeline_template_uuid`.

## Stable template rule

- filter template names with the substring `Release Pipeline [STABLE]`
- expect a single active stable template at a time
- stable and standby names should look like
  `Release Pipeline [STABLE] [A]` and `Release Pipeline [B]`, with `[A]` and
  `[B]` swapping during migrations
- if zero or multiple templates match the stable filter, stop and surface the
  candidate names instead of guessing

## Lookup path

- determine the Buildkite org slug from the handoff or ask for it
- list org pipeline templates with
  `GET /v2/organizations/{org.slug}/pipeline-templates`
- filter client-side on the template `name` field because the API exposes list
  and get-by-UUID, not direct lookup by name
- select the unique match whose name contains `Release Pipeline [STABLE]` and
  use its `uuid` as `pipeline_template_uuid`
- carry the matched template name in the response summary so the user can see
  which stable slot was selected

## Migration note

- mark the active template in the template name with `[STABLE]`; do not use
  comments as the stability marker
- end migrations with both template variants carrying the same content
