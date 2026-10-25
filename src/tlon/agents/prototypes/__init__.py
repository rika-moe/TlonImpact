"""Behavior prototype library (stage 5 of docs/design/dsl-agents.md).

A prototype is a directory of pure data — the behavior genome (unified
member model, ADR 0006: prototypes carry **no persona**; a member's
persona files are seeded at creation and member-owned afterwards
(self-editable via ``persona-update``, ADR 0024), never part of the
genome):

- ``prototype.tlon`` — metadata ``{:name :display-name :description
  :trusted :author :parent :generation :proposal-note}``
- ``behavior.tlon`` — a v2 event-handler declaration with a complete
  ``modules/`` source package
- ``LESSONS.md`` — optional (see docs/design/prototypes.md): lessons
  explicitly authored and packaged with the prototype
  and copied to the member's ``config/`` at instantiation; the DSL
  ``member-context`` read makes it available to ordinary programs
- ``REVIEW.md`` — optional (explicit advisory review, see
  docs/design/prototypes.md): peer-review
  records submitted by ``prototype-review-submit`` while the prototype is staged.
  Host identity, run and material digest establish provenance; no hidden model call.
  ``prototype-read`` exposes a manifest and bounded file pages.
  Metadata reading never touches it, so listing stays free; approval's
  whole-directory move carries it into the trusted library.

A directory qualifies as a prototype when it contains ``behavior.tlon``.
Members execute ordinary module entry functions. Prototypes declare reusable
programs and their complete dependencies.

Built-in prototypes ship inside the package (``tlon/prototypes/``) and are
seeded to ``~/.tlon/prototypes/`` on startup; seeding never overwrites
existing files, so users may edit or add prototypes. ``list_prototypes``
reads the seeded directory (user modifications stay visible).

Trust model (stage 4a, the "code signing" analogy): package-seeded and
user-installed prototypes are trusted (``:trusted true``, directly
spawnable). LLM-authored prototypes are written to the staging area
(``~/.tlon/prototypes-staging/``) with ``:trusted false`` and can only be
spawned after a human approves them (ops/WebUI), which moves them into
the trusted library.

A ``behavior.tlon`` may declare a top-level ``:parameters`` map (a
write-side template, e.g. relay's ``{:to {:type :string-list}}``) and
reference the variables as ``${to}`` inside behavior or module source. Instantiation
renders the template with the caller's values before writing the instance
file; the runtime never sees placeholders.

Instantiation **copies** rather than references: the behavior file is
copied verbatim, so an agent's behavior is frozen at creation time. There
are no overrides — a spec is total, and the prototype's file is the whole
behavior.
"""
