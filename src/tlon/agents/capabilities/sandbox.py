"""Example execution through the shared compiler/executor, without real services."""

from typing import Any

from tlon.agents.capabilities.spec import ExampleSpec
from tlon.operations.composite import CompositeCatalog, compile_composites
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import InputError


def evaluate_example(name: str, example: ExampleSpec, catalog: CompositeCatalog) -> Any:
    definition = catalog.definitions[name]
    unknown = set(example.fake_results) - set(definition.dependency_ids)
    if unknown:
        raise InputError(f"fake results name non-dependencies: {sorted(unknown)}")
    queues = {key: list(values) for key, values in example.fake_results.items()}

    def fake_handler(operation_name: str) -> Any:
        def invoke(_context: CallContext, _inputs: dict[str, Any]) -> Any:
            queue = queues.get(operation_name)
            if not queue:
                raise InputError(f"explicit fake result required for {operation_name}")
            return queue.pop(0)

        return invoke

    # These are new pure test definitions. The original catalog already checked
    # control/effect admission. The shared executor still checks every leaf's
    # input/output schema and all composite output schemas.
    fake_hosts = tuple(
        Operation(
            operation.name,
            operation.description,
            operation.input_schema,
            operation.output_schema,
            fake_handler(operation.name) if operation.effects else operation.handler,
            control=operation.control,
        )
        for operation in catalog.hosts
    )
    sandbox = compile_composites((item.spec for item in catalog.definitions.values()), fake_hosts)
    executor = OperationExecutor(
        OperationRegistry((*fake_hosts, *sandbox.operations)),
        CallContext(member_id="example", run_id="example", call_id="example"),
    )
    actual = executor.call_pure(name, example.input)
    unused = {key: len(values) for key, values in queues.items() if values}
    if unused:
        raise InputError(f"unused fake results: {unused}")
    return actual
