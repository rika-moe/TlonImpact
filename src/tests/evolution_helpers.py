"""Construct real operation projections for evolution domain tests."""

from typing import Any

from tlon.evolution.get_lineage import PrototypeLineage
from tlon.evolution.list_prototypes import PrototypeCatalog
from tlon.evolution.list_staging import StagingCatalog
from tlon.evolution.write_prototype import PrototypeWriter
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.evolution import EvolutionService, make_evolution_operations


def project(
    name: str, service: EvolutionService, slot: str, member_id: str = "adam"
) -> OperationTool:
    operations = make_evolution_operations(member_id, **{slot: service})
    executor = OperationExecutor(
        OperationRegistry(operations), CallContext(member_id, "run", "call")
    )
    return OperationTool(executor, name)


def make_write_tool(**kwargs: Any) -> OperationTool:
    return project("prototype-write", PrototypeWriter(**kwargs), "writer", kwargs["author_id"])


def make_lineage_tool(**kwargs: Any) -> OperationTool:
    return project("prototype-lineage", PrototypeLineage(**kwargs), "lineage")


def make_staging_tool(**kwargs: Any) -> OperationTool:
    return project("prototype-staging-list", StagingCatalog(**kwargs), "staging")


def make_catalog_tool(**kwargs: Any) -> OperationTool:
    return project("prototype-list", PrototypeCatalog(**kwargs), "catalog")
