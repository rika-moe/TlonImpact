"""Capability library (docs/design/capabilities.md, issue #45 slice 1/3).

A capability is a data artifact — one tlosure function per word — not
host code. A capability directory is pure data:

- ``capability.tlon`` — the artifact itself (``{:version 1 :name
  :description :args :impl (fn [args] ...) :pure bool}``): the whole
  behavior, statically validated at load, fuel-bounded at evaluation
- ``capability.edn`` — metadata ``{:name :trusted :author
  :proposal-note :deprecated}`` (prototype-channel key conventions)
- ``REVIEW.md`` — optional heterogeneous peer-review records, advisory;
  the approval move carries the file into the trusted library

A directory qualifies as a capability when it contains
``capability.tlon``.

Trust model (isomorphic with the prototype channel): trusted is the
default — a capability handwritten into ``~/.tlon/capabilities/`` is
trusted. LLM-authored artifacts land in the staging area
(``~/.tlon/capabilities-staging/``) with ``:trusted false`` and can only
enter the trusted library through human approval
(``approve_capability`` moves the directory and flips the flag;
``reject_capability`` deletes it).

Env contract (2026-10-05 拍板): a capability's evaluation env is its
positional arguments, zero injection — no ``msg``, no memory, no
``self``. ``agents/capabilities/spec.py`` enforces this structurally at
the admission gate: the validation registries contain only words
verified to never read ``env``.

Not in this package (later slices of issue #45): the vocabulary bridge
(trusted library → member registries, loader injection), admin
endpoints / WebUI presentation, capability-referencing-capability.
"""
