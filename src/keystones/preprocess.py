"""The contract a preprocessor plugin implements.

A plugin masks what a grammar cannot read - dbt's Jinja being the case this
exists for - so the residue parses and the masked content still reaches the
hash. It is deliberately the only thing a plugin imports from keystones: a
`LanguageSpec` gains fields between releases, this does not.

A module named by `preprocessor = "package:attribute"` provides:

    KEYSTONES_PREPROCESSOR_NAME = "dbt"      # becomes the `hash` kind
    KEYSTONES_PREPROCESSOR_VERSION = "1"     # part of the hash identity

    def preprocess(src: str) -> tuple[str, str]:
        '''Return (text_to_parse, extra_hash_payload).'''

`text_to_parse` must have the same number of lines as `src`, with each line
corresponding to the same original line. Targets are line numbers and markers
are found by line, so a plugin that shifts them corrupts every keystone in the
file; keystones checks this rather than trusting it.

`preprocess` must be a pure function of the text it is given, and must give the
same answer for a slice of a file as for that slice in context. Keystones hashes
a target's own lines, and C5 re-hashes the stored slice on its own to prove the
sidecar was not hand-edited. So derive a placeholder from the span's content -
`__ks_<digest>` - never from its index in the file, or the same span renders
differently depending on what precedes it.

`extra_hash_payload` is folded into the hash, so content the mask removed is
still gated. Normalise it - strip the whitespace a formatter owns - or every
reformat inside a masked span reads as a change.

Options from the table are passed as keyword arguments. Keystones calls the
bound function once with empty text when it loads the config, so raise
`ValueError` for an option value you cannot take and it is reported as a
config error before any file is touched.

Raise `Refused` for a file the plugin cannot handle safely. Refusing is the
right answer where masking would produce a tree that parses but means something
else; keystones reports it and points at `hash=text`, and never quietly
downgrades on the plugin's behalf.
"""

from __future__ import annotations


class Refused(Exception):
    """This preprocessor cannot safely handle this file."""
