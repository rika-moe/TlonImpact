"""Artifact contract and example isolation over the shared operation compiler."""

import pytest

from tlon.agents.capabilities.spec import (
    CapabilityArtifact,
    CapabilityError,
    evaluate_capability_examples,
    validate_examples,
)
from tlon.operations.composite import compile_composites
from tlon.operations.definitions import host_operation_definitions

PURE = """{:version 2 :name "shout" :description "append punctuation"
 :input-schema {:type "object" :properties {:text {:type "string"}} :required ["text"] :additionalProperties false}
 :output-schema {:type "string"}
 :impl (fn [input] (str (get input :text) "!"))
 :examples [{:input {:text "hey"} :expect "hey!"}]}"""


def compiled(text: str):
    artifact = CapabilityArtifact.from_edn(text)
    return artifact, compile_composites((artifact.spec,), host_operation_definitions())


def test_artifact_roundtrip_and_pure_example() -> None:
    artifact, catalog = compiled(PURE)
    assert CapabilityArtifact.from_edn(artifact.to_edn()) == artifact
    assert catalog.definitions["shout"].effects == frozenset()
    (result,) = evaluate_capability_examples(artifact, catalog)
    assert result.passed and result.actual == "hey!"


@pytest.mark.parametrize(
    "changed",
    [
        PURE.replace(":version 2", ":version 1"),
        PURE.replace(":version 2", ':version "2"'),
        PURE.replace(":version 2", ":version true"),
        PURE.replace(":version 2", ":version 2 :pure true"),
        PURE.replace(":version 2", ":version 2 :args []"),
        PURE.replace(':name "shout"', ":name :shout"),
        PURE.replace(':description "append punctuation"', ":description 1"),
        PURE.replace(':examples [{:input {:text "hey"} :expect "hey!"}]', ":examples {}"),
        PURE.replace(':input {:text "hey"}', ":input []"),
        PURE.replace(':expect "hey!"', ":unexpected 1"),
        PURE.replace(':expect "hey!"', ':expect "hey!" :fake-results {"mail-send" {}}'),
        "[]",
        "{:version",
    ],
)
def test_reject_invalid_artifacts(changed: str) -> None:
    with pytest.raises(CapabilityError):
        CapabilityArtifact.from_edn(changed)


def test_example_wrong_result_rejects() -> None:
    artifact, catalog = compiled(PURE.replace(':expect "hey!"', ':expect "wrong"'))
    with pytest.raises(CapabilityError, match="examples\\[0\\]"):
        validate_examples(artifact, catalog)


def test_example_schema_output_violation_is_recorded() -> None:
    artifact, catalog = compiled(
        PURE.replace(':output-schema {:type "string"}', ':output-schema {:type "integer"}')
    )
    (result,) = evaluate_capability_examples(artifact, catalog)
    assert not result.passed and result.error


def test_effect_example_requires_explicit_fake_and_uses_no_service() -> None:
    artifact, catalog = compiled(
        PURE.replace('(str (get input :text) "!")', '(state-get {:key "x"})').replace(
            ':output-schema {:type "string"}', ":output-schema true"
        )
    )
    (result,) = evaluate_capability_examples(artifact, catalog)
    assert not result.passed and "explicit fake" in result.error
    text = artifact.to_edn().replace(
        ':expect "hey!"', ':expect {"value" "fake"} :fake-results {"state-get" [{"value" "fake"}]}'
    )
    fake_artifact, fake_catalog = compiled(text)
    (result,) = evaluate_capability_examples(fake_artifact, fake_catalog)
    assert result.passed and result.actual == {"value": "fake"}


def test_fake_leaf_output_still_validates_shared_contract() -> None:
    text = (
        PURE.replace('(str (get input :text) "!")', "(time-now {})")
        .replace(':output-schema {:type "string"}', ":output-schema true")
        .replace(':expect "hey!"', ':expect 7 :fake-results {"time-now" [7]}')
    )
    artifact, catalog = compiled(text)
    (result,) = evaluate_capability_examples(artifact, catalog)
    assert not result.passed and result.error
