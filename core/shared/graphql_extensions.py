"""Custom Strawberry validation extensions used to harden the public GraphQL surface."""

from collections.abc import Mapping

from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    InlineFragmentNode,
    OperationDefinitionNode,
    ValidationContext,
    ValidationRule,
)
from strawberry.extensions.add_validation_rules import AddValidationRules


class MaxQueryComplexityLimiter(AddValidationRules):
    """Reject overly broad GraphQL documents using a simple field-count budget."""

    def __init__(self, max_complexity: int) -> None:
        super().__init__([create_validator(max_complexity)])


def create_validator(max_complexity: int) -> type[ValidationRule]:
    """Create a GraphQL validation rule that caps total selected field count."""

    class MaxQueryComplexityValidator(ValidationRule):
        def __init__(self, validation_context: ValidationContext) -> None:
            document = validation_context.document
            fragments = {
                definition.name.value: definition
                for definition in document.definitions
                if isinstance(definition, FragmentDefinitionNode)
            }
            operations = (
                definition
                for definition in document.definitions
                if isinstance(definition, OperationDefinitionNode)
            )
            total_complexity = sum(
                count_selected_fields(operation, fragments) for operation in operations
            )
            if total_complexity > max_complexity:
                validation_context.report_error(
                    GraphQLError(
                        f"Query complexity {total_complexity} exceeds the allowed maximum of {max_complexity}."
                    )
                )
            super().__init__(validation_context)

    return MaxQueryComplexityValidator


def count_selected_fields(
    selection_set_owner: (
        OperationDefinitionNode | FragmentDefinitionNode | FieldNode | InlineFragmentNode
    ),
    fragments: Mapping[str, FragmentDefinitionNode] | None = None,
    visited_fragments: frozenset[str] | None = None,
) -> int:
    """Count selected fields in an operation, expanding fragments safely."""
    if selection_set_owner.selection_set is None:
        return 0

    if visited_fragments is None:
        visited_fragments = frozenset()

    result = 0

    for selection in selection_set_owner.selection_set.selections:
        if isinstance(selection, FieldNode):
            if not selection.name.value.startswith("__"):
                result += 1
            if selection.selection_set:
                result += count_selected_fields(selection, fragments, visited_fragments)
            continue

        if isinstance(selection, InlineFragmentNode) and selection.selection_set:
            result += count_selected_fields(selection, fragments, visited_fragments)
            continue

        if isinstance(selection, FragmentSpreadNode) and fragments:
            fragment_name = selection.name.value
            fragment = fragments.get(fragment_name)
            if fragment is None or fragment_name in visited_fragments:
                continue
            result += count_selected_fields(
                fragment,
                fragments,
                visited_fragments | {fragment_name},
            )

    return result


__all__ = ["MaxQueryComplexityLimiter", "count_selected_fields", "create_validator"]
