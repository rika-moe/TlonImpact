"""Unbound host contracts for static admission and review; no service is executed."""

from functools import cache

from tlon.operations.context import member_context_operation
from tlon.operations.core import Operation
from tlon.operations.evolution import evolution_operation_definitions
from tlon.operations.filesystem import filesystem_operation_definitions
from tlon.operations.identity import identity_operation_definitions
from tlon.operations.lifecycle import lifecycle_operation_definitions
from tlon.operations.mail import mail_operation_definitions
from tlon.operations.memory import memory_operation_definitions
from tlon.operations.model import model_operation_definitions
from tlon.operations.observability import observability_operation_definitions
from tlon.operations.shell import shell_operation_definitions
from tlon.operations.state import state_operation_definitions
from tlon.operations.tasks import task_operation_definitions
from tlon.operations.time import time_operation_definitions


@cache
def host_operation_definitions() -> tuple[Operation, ...]:
    return tuple(
        operation
        for factory in (
            lambda: (member_context_operation(),),
            evolution_operation_definitions,
            lifecycle_operation_definitions,
            model_operation_definitions,
            mail_operation_definitions,
            filesystem_operation_definitions,
            shell_operation_definitions,
            time_operation_definitions,
            memory_operation_definitions,
            task_operation_definitions,
            identity_operation_definitions,
            observability_operation_definitions,
            state_operation_definitions,
        )
        for operation in factory()
    )
