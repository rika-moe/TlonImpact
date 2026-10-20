"""Capability artifact schema (spec.py): parse/serialize roundtrip, the
structural rejection classes, purity admission, namespace rejection, and
the env-contract anchor — every word in the capability validation
registries is statically proven to never read ``env``.
"""

import ast
import inspect
import textwrap

import pytest

from tlon.agents.capabilities.spec import (
    CAPABILITY_ACTION_BUILTINS,
    CAPABILITY_FULL_BUILTINS,
    CAPABILITY_PURE_BUILTINS,
    CapabilityError,
    CapabilitySpec,
    evaluate_capability_examples,
    validate_capability,
)
from tlon.agents.vocabulary import MEMBER_ACTION_NAMES
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS

VALID_CAPABILITY = """\
{:version 1
 :name "thread-digest"
 :description "把邮件线程蒸馏成三段式摘要文本"
 :args [{:name "mails" :type :list :doc "按时间升序的邮件列表"}]
 :impl (fn [mails] (str "digest:" (count mails)))
 :pure true}
"""

VALID_IMPURE_CAPABILITY = '{:version 1\n :name "notify-adam"\n :description "给 Adam 发一封通知邮件"\n :args [{:name "content" :type :string :doc "通知正文"}]\n :impl (fn [content] (mail-send {:to ["sink@tlon.internal"] :content content}))\n :pure false}\n'

VALID_CAPABILITY_WITH_EXAMPLES = """\
{:version 1
 :name "shout"
 :description "加感叹号"
 :args [{:name "s" :type :string :doc "原字符串"}]
 :impl (fn [s] (str s "!"))
 :pure true
 :examples [{:args ["hey"] :expect "hey!"}
            {:args ["yo"] :expect "yo!"}]}
"""


def _capability(
    *,
    version: str = "1",
    name: str = '"thread-digest"',
    description: str = '"d"',
    args: str = '[{:name "x" :type :string :doc "the x"}]',
    impl: str = "(fn [x] (str x))",
    pure: str = "true",
    examples: str | None = None,
) -> str:
    text = (
        f"{{:version {version}\n :name {name}\n :description {description}\n"
        f" :args {args}\n :impl {impl}\n :pure {pure}"
    )
    if examples is not None:
        text += f"\n :examples {examples}"
    return text + "}\n"


class TestRoundtrip:
    def test_parse_valid_capability(self):
        spec = CapabilitySpec.from_edn(VALID_CAPABILITY)
        assert spec.version == 1
        assert spec.name == "thread-digest"
        assert spec.description == "把邮件线程蒸馏成三段式摘要文本"
        assert len(spec.args) == 1
        assert spec.args[0].name == "mails"
        assert spec.args[0].type == "list"
        assert spec.args[0].doc == "按时间升序的邮件列表"
        assert spec.pure is True

    def test_roundtrip_identity(self):
        spec = CapabilitySpec.from_edn(VALID_CAPABILITY)
        again = CapabilitySpec.from_edn(spec.to_edn())
        assert again == spec

    def test_roundtrip_zero_args(self):
        spec = CapabilitySpec.from_edn(_capability(args="[]", impl="(fn [] 42)"))
        assert spec.args == ()
        assert CapabilitySpec.from_edn(spec.to_edn()) == spec


class TestStructuralRejection:
    def test_bad_version_rejected(self):
        with pytest.raises(CapabilityError, match=":version must be 1"):
            CapabilitySpec.from_edn(_capability(version="2"))
        with pytest.raises(CapabilityError, match=":version must be 1"):
            CapabilitySpec.from_edn(_capability(version='"1"'))

    def test_bad_name_rejected(self):
        for bad in ('""', '"123abc"', '"a/b"', '"a b"', ":a-keyword", "42"):
            with pytest.raises(CapabilityError, match=":name|invalid capability name"):
                CapabilitySpec.from_edn(_capability(name=bad))

    def test_bad_description_rejected(self):
        with pytest.raises(CapabilityError, match=":description must be a string"):
            CapabilitySpec.from_edn(_capability(description=":not-a-string"))

    def test_bad_args_shape_rejected(self):
        with pytest.raises(CapabilityError, match=":args must be a vector"):
            CapabilitySpec.from_edn(_capability(args='{:name "x"}'))
        with pytest.raises(CapabilityError, match=r":args\[0\] must be a map"):
            CapabilitySpec.from_edn(_capability(args='["x"]'))
        with pytest.raises(CapabilityError, match=r":args\[0\] :type must be one of"):
            CapabilitySpec.from_edn(_capability(args='[{:name "x" :type :frobnicate :doc "d"}]'))
        with pytest.raises(CapabilityError, match=r":args\[0\] :doc must be a string"):
            CapabilitySpec.from_edn(_capability(args='[{:name "x" :type :string}]'))

    def test_impl_must_be_fn_literal(self):
        for bad in ("(defn f [x] x)", "(inc 1)", '"just a string"', "[1 2]"):
            with pytest.raises(CapabilityError, match=r":impl must be a \(fn"):
                CapabilitySpec.from_edn(_capability(impl=bad))

    def test_impl_rest_params_rejected(self):
        with pytest.raises(CapabilityError, match="variadic"):
            CapabilitySpec.from_edn(_capability(args="[]", impl="(fn [& rest] (count rest))"))

    def test_args_fn_arity_mismatch_rejected(self):
        with pytest.raises(CapabilityError, match="declares 1 argument"):
            CapabilitySpec.from_edn(_capability(impl="(fn [x y] (str x y))"))
        with pytest.raises(CapabilityError, match="declares 1 argument"):
            CapabilitySpec.from_edn(_capability(impl="(fn [] 1)"))

    def test_pure_must_be_bool(self):
        with pytest.raises(CapabilityError, match=":pure must be a bool"):
            CapabilitySpec.from_edn(_capability(pure=":yes"))

    def test_non_map_rejected(self):
        with pytest.raises(CapabilityError, match="must be a map"):
            CapabilitySpec.from_edn("[1 2 3]")

    def test_edn_parse_failure_rejected(self):
        with pytest.raises(CapabilityError, match="EDN parse failed"):
            CapabilitySpec.from_edn("{:version 1")


class TestPurityAdmission:
    def test_pure_impl_with_action_word_rejected(self):
        spec = CapabilitySpec.from_edn(
            _capability(impl='(fn [x] (mail-send {:to [x] :content "hi"}))', pure="true")
        )
        with pytest.raises(
            CapabilityError,
            match="failed pure validation: unknown function or special form: mail-send",
        ):
            validate_capability(spec)

    def test_impure_impl_with_action_word_passes(self):
        spec = CapabilitySpec.from_edn(VALID_IMPURE_CAPABILITY)
        validate_capability(spec)

    def test_pure_impl_with_pure_vocabulary_passes(self):
        validate_capability(CapabilitySpec.from_edn(VALID_CAPABILITY))

    def test_env_contract_no_implicit_symbols(self):
        """The impl sees its parameters only: ``msg``/``self``/``event``
        are undefined symbols at admission."""
        spec = CapabilitySpec.from_edn(_capability(impl="(fn [x] (get msg :foo))"))
        with pytest.raises(CapabilityError, match="undefined symbol: msg"):
            validate_capability(spec)

    def test_unknown_builtin_rejected(self):
        spec = CapabilitySpec.from_edn(_capability(impl="(fn [x] (jump x))"))
        with pytest.raises(CapabilityError, match="unknown function or special form: jump"):
            validate_capability(spec)

    def test_host_predicates_excluded(self):
        """Env-reading host vocabulary (trigger predicates, host queries)
        is not capability vocabulary."""
        spec = CapabilitySpec.from_edn(_capability(impl="(fn [x] (sleeping? x))"))
        with pytest.raises(CapabilityError, match="unknown function or special form: sleeping"):
            validate_capability(spec)


class TestNamespaceRejection:
    def test_special_form_collision_rejected(self):
        for word in ("if", "when", "fn", "let"):
            spec = CapabilitySpec.from_edn(_capability(name=f'"{word}"'))
            with pytest.raises(CapabilityError, match="collides with a special form"):
                validate_capability(spec)

    def test_builtin_collision_rejected(self):
        for word in ("get", "str", "count", "inc", "spawn", "mail-send", "mail-forward"):
            spec = CapabilitySpec.from_edn(_capability(name=f'"{word}"'))
            with pytest.raises(CapabilityError, match="collides with a builtin word"):
                validate_capability(spec)

    def test_trusted_capability_collision_rejected(self):
        spec = CapabilitySpec.from_edn(VALID_CAPABILITY)
        with pytest.raises(CapabilityError, match="collides with a trusted capability"):
            validate_capability(spec, taken_names={"thread-digest"})
        # Other names do not block.
        validate_capability(spec, taken_names={"some-other-word"})


class TestRegistryComposition:
    def test_pure_registry_is_core_plus_arithmetic(self):
        assert set(CAPABILITY_PURE_BUILTINS) == set(CORE_BUILTINS) | set(ARITHMETIC_BUILTINS)
        for name, builtin in CAPABILITY_PURE_BUILTINS.items():
            assert builtin.pure is True, name

    def test_action_registry_is_the_member_action_vocabulary(self):
        assert set(CAPABILITY_ACTION_BUILTINS) == MEMBER_ACTION_NAMES
        for name, builtin in CAPABILITY_ACTION_BUILTINS.items():
            assert builtin.pure is False, name

    def test_full_registry_is_pure_plus_actions(self):
        assert set(CAPABILITY_FULL_BUILTINS) == set(CAPABILITY_PURE_BUILTINS) | set(
            CAPABILITY_ACTION_BUILTINS
        )

    def test_no_registry_word_reads_env(self):
        """Anchor the env contract: every word in the capability registries
        is statically proven to never reference its ``env`` parameter.

        The check walks the fn's AST: the first positional parameter
        (``env``) must appear nowhere as a loaded name — not directly,
        not via attribute/item access, not inside a nested function. If a
        future builtin starts reading env (an implicit-context leak into
        capability evaluation), this test fails and the word must leave
        the registry.
        """
        for registry in (CAPABILITY_PURE_BUILTINS, CAPABILITY_ACTION_BUILTINS):
            for name, builtin in registry.items():
                assert not _param_is_used(builtin.fn, 0), (
                    f"capability registry word '{name}' reads its env parameter"
                )


class TestExamplesSchema:
    def test_examples_parsed(self):
        spec = CapabilitySpec.from_edn(VALID_CAPABILITY_WITH_EXAMPLES)
        assert len(spec.examples) == 2
        assert spec.examples[0].args == ("hey",)
        assert spec.examples[0].expect == "hey!"
        assert spec.examples[1].args == ("yo",)

    def test_examples_absent_defaults_empty(self):
        assert CapabilitySpec.from_edn(VALID_CAPABILITY).examples == ()

    def test_examples_roundtrip(self):
        spec = CapabilitySpec.from_edn(VALID_CAPABILITY_WITH_EXAMPLES)
        assert ":examples" in spec.to_edn()
        assert CapabilitySpec.from_edn(spec.to_edn()) == spec

    def test_no_examples_not_serialized(self):
        """Artifacts without :examples roundtrip byte-shape unchanged
        (the key only appears when examples exist)."""
        spec = CapabilitySpec.from_edn(VALID_CAPABILITY)
        assert ":examples" not in spec.to_edn()

    def test_examples_must_be_vector(self):
        with pytest.raises(CapabilityError, match=":examples must be a vector"):
            CapabilitySpec.from_edn(_capability(examples='{:args ["a"] :expect "a"}'))

    def test_example_must_be_map(self):
        with pytest.raises(CapabilityError, match=r":examples\[0\] must be a map"):
            CapabilitySpec.from_edn(_capability(examples='["just-a-string"]'))

    def test_example_args_must_be_vector(self):
        with pytest.raises(CapabilityError, match=r":examples\[0\] :args must be a vector"):
            CapabilitySpec.from_edn(_capability(examples='[{:args "x" :expect "x"}]'))

    def test_example_args_count_must_match_declared(self):
        with pytest.raises(CapabilityError, match=r":examples\[0\] carries 2 arg"):
            CapabilitySpec.from_edn(_capability(examples='[{:args ["a" "b"] :expect "ab"}]'))

    def test_example_expect_required(self):
        with pytest.raises(CapabilityError, match=r":examples\[0\] must declare :expect"):
            CapabilitySpec.from_edn(_capability(examples='[{:args ["a"]}]'))

    def test_example_expect_nil_is_a_legit_value(self):
        spec = CapabilitySpec.from_edn(
            _capability(args="[]", impl="(fn [] nil)", examples="[{:args [] :expect nil}]")
        )
        assert spec.examples[0].expect is None

    def test_impure_artifact_with_examples_rejected(self):
        """A non-pure impl yields ActionRequest host objects, not EDN
        values — assertions cannot compare against them."""
        with pytest.raises(CapabilityError, match="only allowed on :pure true"):
            CapabilitySpec.from_edn(
                _capability(
                    pure="false",
                    impl='(fn [x] (mail-send {:to ["sink@tlon.internal"] :content x}))',
                    examples='[{:args ["a"] :expect "a"}]',
                )
            )


class TestExamplesEvaluation:
    def test_args_are_data_not_programs(self):
        """Example :args bind as-is: a vector arg is the list itself,
        never evaluated as a form."""
        spec = CapabilitySpec.from_edn(
            _capability(
                args='[{:name "xs" :type :list :doc "the xs"}]',
                impl="(fn [xs] (count xs))",
                examples="[{:args [[1 2 3]] :expect 3}]",
            )
        )
        [result] = evaluate_capability_examples(spec)
        assert result.passed is True
        assert result.actual == 3
        assert result.error is None

    def test_last_body_form_is_the_result(self):
        spec = CapabilitySpec.from_edn(
            _capability(
                impl='(fn [x] (str x) (str x "!"))',
                examples='[{:args ["hey"] :expect "hey!"}]',
            )
        )
        [result] = evaluate_capability_examples(spec)
        assert result.passed is True

    def test_mixed_results_recorded_not_raised(self):
        spec = CapabilitySpec.from_edn(
            _capability(
                impl='(fn [x] (str x "!"))',
                examples='[{:args ["a"] :expect "a!"} {:args ["b"] :expect "nope"}]',
            )
        )
        results = evaluate_capability_examples(spec)
        assert [r.passed for r in results] == [True, False]
        assert results[1].actual == "b!"

    def test_eval_error_recorded(self):
        spec = CapabilitySpec.from_edn(
            _capability(impl="(fn [x] (inc x))", examples='[{:args ["not-a-number"] :expect 1}]')
        )
        [result] = evaluate_capability_examples(spec)
        assert result.passed is False
        assert result.error is not None
        assert result.actual is None

    def test_validate_accepts_passing_examples(self):
        validate_capability(CapabilitySpec.from_edn(VALID_CAPABILITY_WITH_EXAMPLES))

    def test_validate_rejects_value_mismatch(self):
        spec = CapabilitySpec.from_edn(
            _capability(impl='(fn [x] (str x "!"))', examples='[{:args ["a"] :expect "wrong"}]')
        )
        with pytest.raises(
            CapabilityError, match=':examples\\[0\\] failed: expected "wrong", got "a!"'
        ):
            validate_capability(spec)

    def test_validate_rejects_runtime_error(self):
        """A runtime failure inside fuel (type error on a pure builtin)
        fails the assertion even though static validation passed."""
        spec = CapabilitySpec.from_edn(
            _capability(impl="(fn [x] (inc x))", examples='[{:args ["s"] :expect 1}]')
        )
        with pytest.raises(CapabilityError, match=r":examples\[0\] failed: evaluation failed"):
            validate_capability(spec)


def _param_is_used(fn, position: int) -> bool:
    """Whether the fn's source references its ``position``-th parameter."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    func = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    param_name = func.args.args[position].arg
    return any(isinstance(node, ast.Name) and node.id == param_name for node in ast.walk(func))
