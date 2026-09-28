# Keystones: Code Review Gates for the Lines That Matter

Require SME reviews for load-bearing code by pinning a review gate to an AST
node instead of a file path.

<p align="center">
  <a href="https://www.youtube.com/watch?v=QAEkJ54h36Y">
    <img src="https://raw.githubusercontent.com/KyleJamesWalker/keystones/main/docs/keystones-explainer.webp" width="320" alt="Keystones explainer video">
  </a>
  <br>
  <a href="https://www.youtube.com/watch?v=QAEkJ54h36Y">▶ Watch the explainer</a>
</p>

Mark a function with a one-line comment. A CODEOWNERS-guarded sidecar file
records its canonical hash, source, and the reason it matters. Change the
function and the hash stops matching, the only way to get your passing required
checks is to update the sidecar, which puts its owner on the pull request.

CODEOWNERS can only say "someone owns this file", which means protecting one
20-line function also drags its owner into every typo fix in the other 800
lines. That is why those rules get deleted. A keystone protects a key function.

## Install

```bash
pip install keystones          # Python only, zero dependencies
pip install 'keystones[all]'   # adds TypeScript, JavaScript, Go, Terraform, SQL and YAML
pip install 'keystones[yaml]'  # YAML by meaning only
```

As a pre-commit hook:

```yaml
repos:
  - repo: https://github.com/KyleJamesWalker/keystones
    rev: v0.4.0
    hooks:
      - id: keystones          # staged text files, warns on drift
      - id: keystones-all      # whole repo, blocking
        additional_dependencies: ["tree-sitter-language-pack==1.20.0"]
```

**Pin the grammar pack in your own config, not via this package.** The hooks run
in an environment pre-commit builds for them, so `additional_dependencies` fixes
the grammar version for your repo without colliding with anything your project
itself depends on, and without waiting for a keystones release to move it. The
package declares a range; your repo decides the version.

The staged hook runs on every staged text file, whatever the language: a file
with no marker in it costs one read and prints nothing, and a staged sidecar is
checked against its keystone even when the code it covers is not staged. It is
advisory: `--no-verify` skips it. The gate is
`keystones check --all` running in CI, which cannot be skipped. Put
`.pre-commit-config.yaml` in CODEOWNERS, or the gate can be removed by deleting
three lines of YAML.

## Quickstart

```bash
keystones add billing/payout.py::compute_payout \
    --id payout-rounding --category finance \
    -m "GAAP rounding, see the 2026 finance sign-off"
```

That writes the marker into the source and the sidecar entry:

```python
# keystone(finance): payout-rounding
def compute_payout(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

Change the rounding mode and `keystones check --all` fails. Acknowledge it:

```bash
keystones fix -m "switched to banker's rounding per policy review"
```

`fix` refuses to run without `-m` when the change is semantic. The resulting
sidecar diff contains the old and new source, so the owner reviews code rather
than a hash.

### Marking in the editor

Write `keystone add` where the marker belongs and let the CLI ask for the rest:

```python
# keystone add
def compute_payout(amount: Decimal) -> Decimal:
```

```
$ keystones add
billing/payout.py:4: new keystone on billing/payout.py::compute_payout
  id: payout-rounding
  category (default, finance) [default]: finance
  why is it load-bearing: GAAP rounding, see the 2026 finance sign-off
  review every, e.g. 180d (blank for none):
  depends on, path.py::Symbol (blank for none):
keystones: adopted 'payout-rounding' on billing/payout.py::compute_payout
```

The line becomes `# keystone(finance): payout-rounding`. Qualifiers written up
front are kept and not asked for: `keystone(finance) add`,
`keystone(file) add`, and `keystone:start add` for a region. Until it is filled
in, `check` fails on it, so a pending marker cannot be merged looking protected.

### Dependencies and staleness

A keystone can name same-repo symbols it depends on, so a change one call frame
away is still an owner-review event:

```bash
keystones add billing/payout.py::compute_payout --id payout-rounding \
    --category finance -m "GAAP rounding" \
    --depends billing/helpers.py::BASE_RATE \
    --depends billing/helpers.py::quantize
```

In Python a dependency may be a definition, a module constant (`::BASE_RATE`)
or a class attribute (`::Fees.SURCHARGE`). Either can also carry a keystone of
its own, as long as it is bound once in its scope:
`keystones add billing/helpers.py::BASE_RATE`.

In a tree-sitter language a dependency is any definition the language offers,
such as `fees.ts::FEE`. Terraform and HCL also take attributes: a Terragrunt
`inputs` block as `terragrunt.hcl::inputs`, and one inside a block as
`main.tf::locals.rate` or
`main.tf::resource.google_compute_network_peering.prod.export_routes`.

The same predicate often lives in more than one file: one query per ad
platform, or a helper copied into a reporting job. Name the copies as twins and
they are held to the reviewed code, so a copy drifting on its own is [C17]:

```bash
keystones add queries/google/spend.sql::spend --id spend-predicate \
    --twin queries/dv360/spend.sql::spend
```

A twin is hashed with the keystone's own basis, so it must be the same
language and shape. Changing every copy together is one review, through `fix`.

A keystoned test can be switched off without touching it, by a skip on its
class or a `pytestmark`. That is [C16]: the sidecar records what disabled it at
the last review, so switching it off, or back on, needs its owner. pytest and
unittest skips are built in; add your own by dotted name:

```toml
[tool.keystones]
disabling_decorators = ["acme.testing.quarantine"]
```

C16 sees what is in the test's own file: marks with their arguments, a
module-level alias of a mark, a module-level `pytest.skip(...)`, and
`__test__ = False`. It does not see `collect_ignore` in a `conftest.py` or a
`--deselect` in `pytest.ini`, which switch a test off from another file.

Some paths are rewritten by a bot with no pull request, a GitOps mirror or a
monitor backup, and a keystone there can only fail. List them and `add` refuses
the path, an entry targeting it is [C18], and a marker the bot copied there is
noted and ignored rather than gated:

```toml
[tool.keystones]
unreviewed = ["deploy/mirror/**"]
```

`review_every = "180d"` sets a staleness budget. There is deliberately no
`reviewed` field: a stored date would be whatever `fix` last wrote, so the age
comes from `git log` on the sidecar itself. Going stale warns and shows up in
`keystones list --stale`; it never fails the build.

## Configuration

```toml
[tool.keystones]
root = "keystones"
categories = ["default", "finance"]
exclude = ["**/generated/**"]
```

`.git`, `node_modules`, `vendor` and `generated` directories, lockfiles such as
`package-lock.json` and `uv.lock`, and minified assets are never scanned, so a
dependency bump costs the staged hook nothing. `include = ["uv.lock"]` opts a
generated file back in. A file over `max_scan_bytes` (2 MB by default) is never
read either; `add` refuses one, and a keystone whose file grew past the cap is
reported rather than passed unread.

One sidecar file per keystone, inside a per-category directory, so each category
gets its own reviewers and two concurrent changes can never conflict:

```
keystones/
  finance/payout-rounding.md
  INDEX.md                     # generated by `keystones index`
```

An id is unique within its category, so two categories may each have a
`rounding`. Where a bare id names more than one keystone, `--id` takes the
qualified form, `--id finance/rounding`, and says so when it is needed.

```
# CODEOWNERS
/keystones/finance/  @org/finance-eng
```

A repo whose sidecars are guarded by a ruleset's required reviewers instead,
which repo admins cannot edit, opts in:

```toml
[tool.keystones]
codeowners_from_rulesets = true
```

C8 then accepts a `required_reviewers` rule whose file pattern covers a path
that has no CODEOWNERS owner, and prints a notice naming the ruleset. The rule
names a team, never a user; write the pattern as `keystones/<category>/**`. It reads
the rules with `GH_TOKEN`, `GITHUB_TOKEN` or, failing both, `gh auth token`;
without any it warns and checks CODEOWNERS alone. Off by default, and with it
off C8 reads only CODEOWNERS.

`keystones doctor` honours the flag too: required reviewers that cover every
category's sidecars stand in for "Require review from Code Owners", which a
ruleset-guarded repo may have no reason to turn on.

## What changing "the code" means

The hash is taken over a canonical rendering of the AST node, not its text.

| Change | Result |
|---|---|
| edit outside a region, in the same file | passes |
| `ruff format`, line rewrap, quote style | passes |
| edit a `#` comment inside the keystone | needs a note, no owner review |
| change a literal, a call, control flow | needs owner review |
| edit a docstring | needs owner review, docstrings are AST nodes |
| skip a keystoned test from its class or module | needs owner review, [C16] |
| change a symbol listed in `depends` | needs owner review |
| delete the marker | fails until the entry goes too |
| move the marker onto a different definition | fails; the entry records its target |
| define the same name twice in one file | fails; the keystone cannot say which it covers |

Markers are found by lexing, so a marker-shaped string literal is not a marker.
A marker above a decorator attaches to the function it decorates.

## What this is not

A process control, not a security control. Someone who wants around it can
delete the marker and the entry in one pull request. That pull request is
CODEOWNERS-gated and the deletion is legible in the diff. Known gaps:

- **Indirection.** A keystone on `compute_payout` says nothing about a helper it
  calls, unless you name that helper in `depends`. Naming it is opt-in and
  manual, so the hole is narrowed rather than closed.
- **Copy and repoint.** Copying the body to a new unmarked function and
  repointing callers is undetectable.
- **CODEOWNERS is not self-executing.** It requests a reviewer. The block only
  exists when branch protection or a ruleset requires Code Owner review and
  dismisses stale approvals. `keystones doctor` audits both, merges them as
  GitHub does, and names the source of each requirement. It warns when a ruleset
  it relies on has bypass actors, or when the token cannot see them. Rulesets
  are readable with any token that can read the repo; classic branch protection
  needs `admin:repo`. It takes `GH_TOKEN`, `GITHUB_TOKEN`, then `gh auth token`.
  With no token it skips; with a token it cannot use, it fails rather than
  reporting success it cannot vouch for.
- **A keystone protects one definition, not a name.** It records the target it
  covers and fails if the marker moves off it, but nothing stops a caller being
  repointed at different code entirely.

## Any file type

Python gets AST granularity. Everything else gets whole-file or **region**
keystones, with no parser and no dependency:

```hcl
# keystone:start(infra): vpc-peering-cidrs
resource "google_compute_network_peering" "prod" {
  peer_network  = var.peer
  export_routes = true
}
# keystone:end
```

Editing inside the region trips the gate; editing elsewhere in the file does
not. That is the point of regions - a whole-file keystone on a
formatter-managed YAML or Terraform file trips on every unrelated edit, which
gets the tool uninstalled.

`#`, `//`, `--`, `/* */` and `<!-- -->` all work. Region bodies are compared as
normalised text (LF, no trailing whitespace, no runs of blank lines), so a
reformat inside a region does trip it. Only the Python adapter is
reformat-immune.

Whole-line comments inside a region are kept out of the semantic hash where the
file type's comment leader is known, so editing one is [C4] and clears with a
`fix` and no note. A type keystones cannot name a leader for hashes every line.
A trailing comment on a code line is part of that line, so editing it is [C3].

A region is found by its marker, not by the line range the sidecar records. An
edit above it that shifts the range with the body unchanged passes with a
notice, and `fix` records the new range without a note. A new comment line
inside the region shifts the range and is [C4].

### YAML by meaning

`hash=yaml` gates a YAML file, a region, or one mapping key on the parsed
document rather than its layout, so a reformat, a key reorder or a quote change
is invisible and a comment edit is [C4]. It needs `pip install 'keystones[yaml]'`
and is opt-in, because a repo already gating YAML on text keeps that basis until
it says otherwise:

```toml
[[tool.keystones.language]]
extensions = [".yaml", ".yml"]
hash = "yaml"
```

```yaml
spec:
  # keystone: replicas
  replicas: 3
```

That keystone is `values.yaml::spec.replicas` and covers that key's value. A key
may also be named in `depends`. Switching a text keystone to `hash=yaml` is
[C14] until `keystones migrate` proves the stored source still means the same
thing. JSON has no comment syntax to carry a marker, so it is not covered.

A file that documents markers rather than carrying them opts out with a
`keystones: ignore-file` directive anywhere in it. This README has one.

A marker already written into a file is adopted without passing a target:

```bash
keystones add --id vpc-peering-cidrs -m "Peering CIDRs are load bearing"
```

## Languages

| Language | Granularity | Reformat-immune |
|---|---|---|
| Python | function, method, class, test, constant, class attribute, region, file | yes, stdlib `ast` |
| TypeScript, TSX, JavaScript | function, method, class, interface, type alias, region, file | yes, tree-sitter |
| Go | func, method, type, const, region, file | yes, tree-sitter |
| Terraform, HCL | block, region, file | yes, tree-sitter |
| SQL | view, table, function, CTE, region, file | yes, tree-sitter |
| YAML | mapping key at any depth, region, file | `hash=yaml`, PyYAML |
| everything else | region, file | no, normalised text |

Those are the built-in languages. Any other grammar the pack carries, such as
Java, Rust or C#, works at node granularity once a
`[[tool.keystones.language]]` table names it; see
[Adding a language](#adding-a-language). BigQuery SQL ships as `sql_bigquery`,
which claims no extension until a table points one at it.

### Choosing what a keystone is hashed on

Most files have one answer and you never think about it: Python gets its AST,
`.yaml` gets normalised text. A file the parser cannot read is the exception,
and templated SQL is the common case:

```sql
{{ config(materialized='incremental') }}
select
    order_id,
-- keystone:start(finance, hash=text): revenue-recognition
    amount * 0.97 as net_revenue
-- keystone:end
from {{ ref('orders') }}
```

`keystones add` refuses to pick for you when the preferred parser fails:

```
models/revenue.sql:4: error: [kind] 'revenue-recognition' has no basis to be
hashed with. models/revenue.sql does not parse as sql, so say which with a
hash= qualifier: hash=text
```

The choice lands in the marker and in the sidecar, and `check` reads it rather
than re-deriving it from the file extension. That matters: without it, a
grammar bump that starts reading a file it could not read before would silently
move that file's hash basis and report drift on code nobody touched.

```toml
target = "models/revenue.sql#L5-L5"
hash   = "text"
hasher = "keystones-text/2"
```

`hash` is the choice a person made and does not move. `hasher` is the exact
basis, including grammar and spec versions, and is what `migrate` reconciles.
The two must agree with the marker; editing one without the other is [C14].

`hash=text` has no AST, so it only goes with `keystone(file, ...)` or a region.

### Saying it once instead of on every marker

Most repos have one answer for a file type. A repo whose `.sql` is all dbt says
so once, and no marker in it needs a qualifier:

```toml
[[tool.keystones.language]]
extensions = [".sql"]
hash = "text"
```

A repo on one SQL dialect points the extension at a different shipped spec,
without restating its node types:

```toml
[[tool.keystones.language]]
builtin = "sql_bigquery"
extensions = [".sql"]
```

Precedence is **marker qualifier, then config table, then auto-detect**. A table
may take an extension a builtin owns, because a table in a CODEOWNERS-guarded
pyproject.toml is the opposite of a silent rebinding; two tables claiming one
extension is still refused, and `.py` cannot be reassigned at all.

Changing the table re-gates every keystone under it, which is a real change and
is reported as [C14] rather than passing quietly.

### Templated files, via a plugin

A grammar cannot read a templating layer. dbt models are the case: Jinja turns
a model into ERROR nodes, and hashing an error-recovery tree is worse than
refusing one. A preprocessor plugin masks the template so the residue parses:

```toml
[[tool.keystones.language]]
builtin = "sql_bigquery"
extensions = [".sql"]
preprocessor = "keystones_dbt:preprocess"
```

The plugin is an ordinary `pip install`, and the dialect is yours to pick - the
preprocessor never knows which grammar or parser it is feeding. The `hash` kind
then names the plugin rather than the grammar, because a reviewer needs to know
a plugin is in play; `hasher` carries both:

```toml
hash = "dbt"
hasher = "keystones-ts/3+sql_bigquery@1.20.0/a1b2c3d4e5f6+dbt/1"
```

Masked content is hashed verbatim, so a template expression is not a hole in
the gate, and a plugin may refuse a file it cannot handle safely rather than
guess. A refusal is reported and points at `hash=text`; it never silently
downgrades. See `keystones/preprocess.py` for the contract.

SQL that a Python service fills in with `str.format` needs no plugin. One
preprocessor ships with keystones and masks `{name}`-style placeholders:

```toml
[[tool.keystones.language]]
builtin = "sql"
extensions = [".sql"]
preprocessor = "keystones.placeholders:preprocess"
```

A placeholder is hashed as written, so renaming `{rate}` to `{fee}` is a
change. It masks to an identifier, so a placeholder where SQL wants a number,
such as `limit {n}`, still does not parse. A file with `{{` or `}}` is refused,
since those are an escape in `str.format` and Jinja in dbt. BigQuery's `@param`
parameters are SQL already and need no mask.

### A parser the pack does not have, via a plugin

A grammar pack covers common languages, not every dialect. A parser plugin
supplies the tree itself, and keystones does the rest: discovery, resolution,
hashing, the sidecar and C5.

```toml
[[tool.keystones.language]]
extensions = [".sql"]
parser = { plugin = "keystones_dbt.parsers:sqlglot", dialect = "snowflake" }
preprocessor = { plugin = "keystones_dbt:preprocess", control_flow = "first-branch" }
```

`plugin` names a factory; every other key in the table is passed to it, so a
project's choices live in its own `pyproject.toml` and a misspelt option is a
config error naming the table. Options are part of the hasher, so changing one
is a migration rather than drift:

```toml
hash = "dbt"
hasher = "keystones-plugin/1+sqlglot@30.18.0/snowflake/9f1c0b2a7d3e+dbt/1"
```

Both keys also take the plain string form when there is nothing to configure.
See `keystones/parser.py` for the contract a plugin implements.

### Adding a language

Any grammar `tree-sitter-language-pack` carries can be wired up from your own
pyproject.toml, without waiting for a release here:

```toml
[[tool.keystones.language]]
grammar = "sql"          # the pack's name for the grammar
extensions = [".sql"]
definitions = ["create_view", "create_table", "cte"]
name_fields = []         # SQL names are not in a `name` field
label_children = ["identifier", "object_reference"]
line_comment = "--"
```

`grammar`, `extensions` and `definitions` are required; everything else falls
back to the defaults the builtin specs use. An unknown key is an error rather
than a no-op, because a typo would otherwise build a spec that silently matches
nothing. One extension has one parser, so a table cannot take `.ts` from the
builtins or an extension another table already claimed.

The table decides the hash basis, so put pyproject.toml in CODEOWNERS alongside
the sidecars. Editing it reads as a hasher change, not as drift: `migrate`
proves the entries across whatever the edit did not actually move.

tree-sitter languages need the `all` extra, which declares a range rather than
a pin. Each entry's hasher id records the grammar version and a digest of the
language spec that produced the hash:

```
keystones-ts/3+typescript@1.20.0/4957071ba1a6
```

That, not the install requirement, is what makes hashes deterministic. The spec
digest covers only the fields the serialiser reads, so editing a comment leader
or adding a file extension costs nobody a migration.

A version difference is only reported when it actually matters. On a mismatch
the hash is recomputed first: if it still reproduces, the grammar emits the same
thing and nothing is said. Only when the two genuinely disagree does it surface,
and then as a hasher mismatch rather than as code drift, because from there it
is not possible to tell a moved basis from changed code.

`keystones list --unparseable` prints every file a parser claims but cannot
read, with the parser's first error, marker or not. Run it before adopting a
repo to see which spellings to shape, or which files want `hash=text`.

`keystones fix` refuses to write from an environment whose hasher differs from
the one an entry records. Without that, running `fix` with the wrong grammar
pack installed would store a hash CI cannot reproduce, and the next check would
ask for another fix, forever.

An entry this install cannot verify is an error, not a warning: skipping the
hash checks on an unrecognised hasher would make that field a way to switch
them off. `keystones migrate` then moves those entries across, and proves the
move rather
than asserting it: the stored canonical source is re-rendered under the new
hasher, and only when that matches the new hash of the live code does the entry
migrate, with no note and no owner review. Where the code changed too, the entry
is left alone for the normal gate. A hasher version is a wire format; versions
are never removed.

For JavaScript and TypeScript the hash also folds away the things prettier
changes on its own: quote style, number spelling (`1.50` and `1.5`), redundant
parentheses, arrow-parameter parens, and a trailing separator. Operators and
interior separators are kept, so `a + b` and `a - b` differ, and so do `[a,,b]`
and `[a,b]`. The `export` keyword and a `const`/`let`/`var` binding are inside
the hash, so un-exporting a symbol is a change.

### Upgrading

Every release that moves a hasher, and this one moves three, needs one
`keystones migrate` per repo after upgrading. It rewrites each entry whose
recorded hasher differs from the installed one, proving the move from the
stored source, and records the current hasher id. A recorded id that differs
while the hashes still agree passes `check` silently by design; `migrate` is
what refreshes it, so put `keystones migrate --check` next to `check --all` in
CI to see drift in recorded ids before it matters.

### What a hash does not see

- A Python docstring is part of the AST, so editing one is C3.
- Test data held in a module-level name, `@pytest.mark.parametrize("x", CASES)`
  with `CASES` defined above, sits outside the test's hash. Name it in
  `depends` (`tests/test_x.py::CASES`).
- A guard used through a module-level instance (`guard = Guard()`) is keystoned
  on its definition; rebinding the instance passes. Keystone the binding too.
- SQL function-name case is inside the hash (`COALESCE` and `coalesce`
  differ). Quoted identifiers are always exact.
- A twin is compared on the whole semantic hash, docstrings included, and a CTE
  twin must keep the same CTE name because the rendering includes `name AS`.
- A dependent's sidecar stores no dependency source; its diff shows only
  `depends_hash` moving.
- Removing a twin or a dependency from a sidecar is an edit to that sidecar,
  which CODEOWNERS already puts in front of the owner.

One extension has one language table repo-wide, so a repo holding two SQL
dialects side by side picks one grammar for `.sql`; a second extension, such as
`.bqsql`, takes the other.

## Status

Phase 2 in progress.

| Shipped | Not yet |
|---|---|
| C1 orphan marker, C2 orphan entry, C3 semantic drift, C4 comment drift, C5 stored-source integrity, C6 uniqueness, C7 category, C8 CODEOWNERS coverage, C9 removal check, C10 index, C11 dependency drift, C12 staleness, C13 hasher mismatch, C14 hash kind, C16 disabled test, C17 twin drift, C18 unreviewed path | call-closure advisory |
| `check`, `fix`, `add`, `doctor`, `list`, `index`, `migrate` | CI-written `reviewed_by` |

What each check catches, where it runs and how to clear it is in
[docs/checks.md](docs/checks.md).

The hasher is versioned (`keystones-ast/1`) and treated as a wire format. A
pinned-hash test runs on every supported CPython minor, because a hash basis
that moves would fail every keystone at once.

## License

MIT

<!-- keystones: ignore-file -->
