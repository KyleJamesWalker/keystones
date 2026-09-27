# Design note: `binds`, gating the pin that deploys a keystone

<!-- keystones: ignore-file -->

**Status:** proposal, not implemented.

A keystone on `main` guards code that often is not what runs. What runs is
chosen by a pin elsewhere: an image tag or package version in a deploy repo, or
a query copied into an external system. This note proposes an optional `binds`
field that names that pin, and a check that fails when the pin and the keystone
disagree about which reviewed revision is live.

## Problem

Today a reviewed change to a keystone is merged, and nothing connects it to the
thing that ships it. Two failures follow:

- The deploy pin moves to a revision whose keystone was never reviewed, for
  example a hotfix branch built and tagged outside `main`.
- The keystone changes and is reviewed, but the pin stays on an old revision,
  so the review protects code that is not running.

Neither is visible from the repo that holds the keystone.

## Proposal

A sidecar may declare where its code is pinned:

```toml
[[binds]]
path = "deploy/values.yaml"             # a file, in this repo or another
repo = "acme/deploy"                    # optional; omitted means this repo
pattern = 'image: example.com/orders:(?P<ref>[\w.-]+)'
```

`pattern` is a regular expression with one named group, `ref`, which captures
the pinned revision: a tag, a version or a commit.

A new check, C15, resolves each bind and asks one question: does the pinned
`ref` contain the last reviewed revision of this keystone? The last reviewed
revision is the commit that last touched the sidecar, which is how C12 already
ages keystones, so nothing new is stored.

| Outcome | Result |
|---|---|
| the pin contains the reviewed revision | pass |
| the pin predates the reviewed revision | warning: the review is not live yet |
| the pin names a revision the keystone's history does not contain | error |
| the bind cannot be read | warning, naming why, never a silent pass |

## Scope

- **Read only.** C15 reads pins and never writes them.
- **Opt-in per keystone.** No `binds`, no C15.
- **Same-repo first.** A bind with no `repo` needs nothing but git. A
  cross-repo bind needs a token and the GitHub API, so it runs where `doctor`
  runs, not in the staged hook.
- **External systems are out of scope.** A query stored in a database or a SaaS
  tool has no file to point at. Exporting it to a file in some repo, and binding
  that file, is the supported route.

## Open questions

1. Should a pin that predates the reviewed revision warn or fail? Warning
   matches C12; failing is stricter but blocks a keystone review until the
   deploy catches up.
2. Is "contains" the right relation for a tag, or should a bind be able to say
   "equals" for pins that must track a release exactly?
3. Does the pattern live in the sidecar, which the keystone's owner reviews, or
   in `pyproject.toml`, so that one pattern can serve every keystone deployed by
   the same pin?
4. Cross-repo binds need read access to the other repo. Is a missing token a
   warning, as in `doctor`, or a reason to skip C15 entirely?
