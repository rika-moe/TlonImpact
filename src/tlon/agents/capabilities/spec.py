"""Capability file syntax and review examples over shared composite definitions.

Artifacts supply JSON contracts and one explicit input. Effects and dependencies
are compiler facts, never author claims. Example execution never binds a member
service; effectful examples require explicit fake results.
"""

from dataclasses import dataclass, field
from typing import Any

from tlon.evolution.ports import CapabilityProposalError
from tlon.operations.composite import CompositeCatalog, CompositeSpec
from tlon.operations.schema import DefinitionError, InputError, json_value
from tlosure.builtins import EvalError
from tlosure.edn import EDNParseError, Keyword, dumps, loads


class CapabilityError(CapabilityProposalError):
    """Invalid capability artifact or trust publication."""


@dataclass(frozen=True)
class ExampleSpec:
    input: dict[str, Any]
    expect: Any
    fake_results: dict[str, tuple[Any, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ExampleResult:
    input: dict[str, Any]
    expect: Any
    passed: bool
    actual: Any = None
    error: str | None = None


@dataclass(frozen=True)
class CapabilityArtifact:
    spec: CompositeSpec
    examples: tuple[ExampleSpec, ...] = ()

    @property
    def name(self) -> str:
        return self.spec.name

    @staticmethod
    def from_edn(text: str) -> "CapabilityArtifact":
        try:
            data = loads(text)
            if type(data) is not dict:
                raise CapabilityError("capability artifact must be a map")
            allowed = {
                "version",
                "name",
                "description",
                "input-schema",
                "output-schema",
                "impl",
                "examples",
            }
            unknown = set(data) - allowed
            if unknown:
                raise CapabilityError(f"unknown capability fields: {sorted(unknown)}")
            if type(data.get("version")) is not int or data["version"] != 2:
                raise CapabilityError("capability :version must be 2")
            for key in ("name", "description"):
                if type(data.get(key)) is not str:
                    raise CapabilityError(f"capability :{key} must be a string")
            spec = CompositeSpec.from_edn(
                dumps({key: value for key, value in data.items() if str(key) != "examples"})
            )
            raw_examples = data.get("examples", [])
            if type(raw_examples) is not list:
                raise CapabilityError("capability :examples must be a vector")
            examples: list[ExampleSpec] = []
            for index, raw in enumerate(raw_examples):
                if type(raw) is not dict or set(raw) - {"input", "expect", "fake-results"}:
                    raise CapabilityError(f"capability :examples[{index}] has invalid fields")
                if "input" not in raw or "expect" not in raw:
                    raise CapabilityError(f"capability :examples[{index}] needs :input and :expect")
                inputs = json_value(raw["input"], edn_keys=True)
                if type(inputs) is not dict:
                    raise CapabilityError("example :input must be an object")
                fakes = json_value(raw.get("fake-results", {}), edn_keys=True)
                if type(fakes) is not dict or any(type(v) is not list for v in fakes.values()):
                    raise CapabilityError(
                        "example :fake-results must map operation names to result vectors"
                    )
                examples.append(
                    ExampleSpec(
                        inputs,
                        json_value(raw["expect"], edn_keys=True),
                        {k: tuple(v) for k, v in fakes.items()},
                    )
                )
            return CapabilityArtifact(spec, tuple(examples))
        except (EDNParseError, DefinitionError, InputError) as error:
            raise CapabilityError(str(error)) from error

    def to_edn(self) -> str:
        data: dict[Any, Any] = {
            Keyword("version"): 2,
            Keyword("name"): self.spec.name,
            Keyword("description"): self.spec.description,
            Keyword("input-schema"): self.spec.input_schema,
            Keyword("output-schema"): self.spec.output_schema,
            Keyword("impl"): self.spec.impl,
        }
        if self.examples:
            data[Keyword("examples")] = [
                {
                    Keyword("input"): e.input,
                    Keyword("expect"): e.expect,
                    **(
                        {Keyword("fake-results"): {k: list(v) for k, v in e.fake_results.items()}}
                        if e.fake_results
                        else {}
                    ),
                }
                for e in self.examples
            ]
        return dumps(data) + "\n"


def evaluate_capability_examples(
    artifact: CapabilityArtifact, catalog: CompositeCatalog
) -> list[ExampleResult]:
    """Run data assertions in a service-free, fuel-bounded sandbox."""
    from tlon.agents.capabilities.sandbox import evaluate_example

    results: list[ExampleResult] = []
    for example in artifact.examples:
        try:
            actual = evaluate_example(artifact.name, example, catalog)
            results.append(
                ExampleResult(example.input, example.expect, actual == example.expect, actual)
            )
        except (EvalError, ValueError, RuntimeError) as error:
            results.append(ExampleResult(example.input, example.expect, False, error=str(error)))
    return results


def validate_examples(artifact: CapabilityArtifact, catalog: CompositeCatalog) -> None:
    for index, result in enumerate(evaluate_capability_examples(artifact, catalog)):
        if not result.passed:
            detail = result.error or f"expected {dumps(result.expect)}, got {dumps(result.actual)}"
            raise CapabilityError(f"capability :examples[{index}] failed: {detail}")
