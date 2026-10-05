"""Runtime 公共北向 API（ADR 0019）。

按域拆分的 facade 包：members / mail / prototypes / observability /
config / events / arena / capabilities，外加 bootstrap（``build_runtime()`` 装配）。server
（tlon_server）只许经本包与 tlon.base 触达 runtime，import-linter
固话。facade 是小类：构造器注入 runtime 对象，薄透传、不改逻辑。
"""
