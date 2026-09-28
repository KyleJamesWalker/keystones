# Check reference

<!-- keystones: ignore-file -->

Every finding keystones reports, what raises it and how to clear it. Findings
print as `path:line: <severity>: [<code>] <message>`, or as GitHub annotations
in Actions.

**Where each check runs.** *Staged* is `keystones check <paths>`, which the
staged pre-commit hook runs on the files being committed. *Whole repo* is
`keystones check --all`, run by the `keystones-all` hook on pre-push and in CI.
Checks that need the whole tree only run in whole-repo mode, except that a
staged sidecar also gets C2 and C5, and a staged keystone's own `depends` and
`twins` get C11 and C17. A staged file that is another keystone's dependency
or twin is only checked in whole-repo mode, and the staged summary says so.

**Severity.** An error fails the run. A warning is reported and passes. A
notice reports a pass that went a non-default route. `--warn-only`, which the
staged hook uses, turns C3, C4, C13, C14, C16 and a target that cannot be hashed into
warnings.

## Numbered checks

| Code | Name | Fires when | Severity | Runs in | To clear it |
|---|---|---|---|---|---|
| C1 | Orphan marker | A marker has no sidecar entry in its category. | error | staged, whole repo | `keystones add --id <id> -m "<why>"` adopts it. |
| C2 | Orphan entry | A sidecar entry has no marker in the tree: the marker was deleted or its file is now excluded. Skipped for a file whose parser is not installed. | error | whole repo; staged for a staged sidecar | Restore the marker, or delete the entry, which is then C9. |
| C3 | Semantic drift | The keystoned code changed, or its marker now sits on a different target from the one the entry records. A region whose line range shifted under an edit above it, with its body unchanged, is a notice rather than an error. | error | staged, whole repo | `keystones fix --id <id> -m "<why>"`. The owner reviews the sidecar diff. |
| C4 | Comment drift | Only comments inside the keystone changed. In a region or a whole-file text keystone, that is a whole-line comment in the file type's comment syntax. | error | staged, whole repo | `keystones fix --id <id>`; no `-m` needed. |
| C5 | Stored-source integrity | The sidecar stores no canonical source, the stored source does not parse, or it no longer hashes to the stored hash, which means the sidecar was edited by hand. Skipped for an entry on a different hasher, which is C13's job. | error | whole repo; staged for a staged sidecar | Regenerate with `keystones fix` instead of editing the sidecar. |
| C6 | Uniqueness | An id is used twice in one category, two markers resolve to one target, or a file defines one name twice so a decoy could sit under the marker. | error | whole repo | Rename one id, remove the duplicate marker, or rename the duplicate definition. |
| C7 | Category | A marker names a category missing from `[tool.keystones] categories`, or names a different category from the directory its sidecar sits in. | error | staged, whole repo | Add the category, or make the marker and the sidecar directory agree. |
| C8 | Ownership | There is no CODEOWNERS file, a gate file (CODEOWNERS, `pyproject.toml`, the pre-commit configs, workflows) has no owner, or a category directory is unowned or owned by a rule outside the sidecar root. The implicit `default` category, when config does not list it, needs an owner only once a keystone uses it. | error; warning or notice with `codeowners_from_rulesets`, see below | whole repo | Add or fix the CODEOWNERS lines, or cover the path with a ruleset's required reviewers and opt in. |
| C9 | Removal | Compared with the base ref, a marker was deleted, a marker and its entry were both deleted, a file became excluded, an entry was deleted, a keystone moved to another category, or a category was dropped from config. | error | whole repo, when a base ref is available | Not fixable locally: it is the review event. The losing category's owner approves the pull request. |
| C10 | Index | `INDEX.md` does not match the entries. | error | whole repo | `keystones index`. |
| C11 | Dependency drift | A symbol named in `depends` changed or no longer resolves. | error | whole repo | `keystones fix -m "<why>"`, or correct the `depends` entry. |
| C12 | Staleness | The sidecar was last committed longer ago than its `review_every`, or `review_every` is malformed. | warning; error if malformed | whole repo | Review the keystone and commit a change to its sidecar, such as a History line. The age comes from `git log`, not a stored date. |
| C13 | Hasher mismatch | The entry was hashed by a different hasher (serializer, grammar or spec version) and the hashes disagree, so whether the code changed cannot be told. The message names what moved: a grammar pack or parsing library version, a spec edit, a preprocessor version, or the serializer. | error | staged, whole repo | Install the grammar pack the repo pins, or `keystones migrate`. |
| C14 | Hash kind | The sidecar's `hash` differs from the basis the marker or `[[tool.keystones.language]]` gates on. | error | staged, whole repo | `keystones migrate` if the config changed on purpose; otherwise make the marker and the sidecar agree. |
| C16 | Disabled test | A skip, skipif or xfail mark, or a unittest skip, now switches the keystoned definition off from outside its own hash: on an enclosing class, in a class or module `pytestmark`, through a module-level alias such as `skip = pytest.mark.skip`, a module-level `pytest.skip(...)`, or `__test__ = False` on the class or module. Mark arguments are recorded, so flipping `skipif(False)` to `skipif(True)` counts. Not seen: `collect_ignore` in a `conftest.py`, and `--deselect` or `addopts` in `pytest.ini` or `pyproject.toml`, which live outside the test's file. A mark on the definition itself is inside its hash, so that is C3. Also fires when one reviewed earlier is removed. `disabling_decorators` adds names to the built-in list. | error | staged, whole repo | `keystones fix --id <id> -m "<why>"` records the new set in `disabled_by`, so the owner reviews it. |
| C17 | Twin drift | A target named in the sidecar's `twins` no longer hashes like the keystone, or cannot be found. A twin is a same-repo copy held to the reviewed code with the keystone's own basis. | error | whole repo | Bring the copy back in line, or change both and `keystones fix -m "<why>"`. A twin that is gone for good comes off the `twins` list, which its owner reviews. |
| C18 | Unreviewed path | A marker, or an entry's target, sits under a pattern in `[tool.keystones] unreviewed`: a path a bot rewrites with no pull request, where no gate can hold. `add` refuses such a path up front. | error | staged (markers), whole repo | Remove the marker and its entry, or take the path off the list. |

A grammar version difference that still produces the same hash says nothing.
Only a disagreement surfaces, and then as C13, never as C3.

### C8 with `codeowners_from_rulesets`

With the flag on, C8 also reads the default branch's ruleset rules through the
GitHub API.

| Situation | Result |
|---|---|
| CODEOWNERS covers the path | pass, as with the flag off |
| CODEOWNERS does not, and a `required_reviewers` rule with at least one approval covers it | notice naming the ruleset |
| Neither covers it | the usual C8 error |
| The rules cannot be read, for example with no token from the environment or `gh auth token` | warning, then CODEOWNERS alone decides |

A `required_reviewers` rule names a team, never a user, so a path a ruleset
covers is owned by that team. Its `file_patterns` are matched as fnmatch with
`*` staying inside one path segment and `**` crossing segments; GitHub does not
document the exact flags, and no live ruleset with the rule was available to
confirm them, so a pattern such as `keystones/**` is the safe shape.

## Other findings

These are not numbered checks. They mean keystones could not get far enough to
run one.

| Code | Fires when | To clear it |
|---|---|---|
| `pending` | A `keystone add` marker has not been filled in. | `keystones add`, which asks for the details. |
| `extra` | The file needs a tree-sitter parser that is not installed. | `pip install 'keystones[all]'`, or add the grammar pack to the hook. |
| `grammar` | A configured grammar is not in the installed pack. | Check the name in `[[tool.keystones.language]]`. |
| `marker` | A marker is malformed, for example two `hash=` qualifiers or an empty one. | Fix the marker text. |
| `region` | A region is unbalanced: a start with no end, an end with no start, or one region opened inside another. | Balance the `keystone:start` / `keystone:end` pair. |
| `kind` | The marker asks for a basis this file does not offer, puts `hash=text` on a node, or the file does not parse and no basis was chosen. | Pick a basis with `hash=`, or use a region or a whole-file marker. |
| `resolve` | The marker attaches to nothing, or its target cannot be hashed. | Move the marker above a definition, or use a region. |
| `plugin` | A preprocessor or parser plugin broke its contract, for example by changing the line count. | A plugin bug; report it to the plugin. |
| `parse` | A Python file with a keystone in it has a syntax error, so nothing in it can be checked. Named with the line. | Fix the syntax error. |
| `sidecar` | A staged sidecar cannot be parsed. | Restore the sidecar's `toml` block. |
| `size` | A keystone's file is over `max_scan_bytes`, so it was not read. | Raise `[tool.keystones] max_scan_bytes`, or keystone a smaller file. |
| `doctor` | `keystones doctor` found protection that does not require owner review. Only that command raises it. | Change branch protection or the ruleset; the message says which setting. |
