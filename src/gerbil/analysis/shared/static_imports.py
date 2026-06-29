from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from cldk.models.java import JImport

from gerbil.analysis.http.framework_registry import (
    HTTP_OWNER_FAMILY_RULES,
    matches_receiver_prefix,
    normalize_method_names,
)
from gerbil.analysis.shared.constants import (
    AMBIGUOUS_PROPERTY_METHODS,
    AUTH_STATIC_IMPORT_METHODS_BY_RECEIVER,
    MOCKED_CALL_NAMES,
    OBSERVATION_MEDIUM_DB_QUERY_RECEIVER_METHODS,
    OBSERVATION_MEDIUM_FS_RECEIVER_METHODS,
    OBSERVATION_MEDIUM_MQ_RECEIVER_METHODS,
    PROPERTY_RECEIVER_PREFIXES,
    STATIC_IMPORT_RECEIVER_HINTS,
    STRONG_PROPERTY_METHODS,
    VIRTUALIZED_STATIC_METHODS_BY_RECEIVER,
    WAIT_SIGNAL_RECEIVER_METHODS,
)


def _looks_like_method_token(token: str) -> bool:
    return bool(token) and token[0].isalpha() and token[0].islower()


_FRAMEWORK_WILDCARD_RECEIVER_PREFIXES: tuple[str, ...] | None = None
_FRAMEWORK_WILDCARD_RULES: tuple["_WildcardImportRule", ...] | None = None
_HTTP_STATIC_IMPORT_OWNER_METHOD_NAMES: dict[str, frozenset[str]] | None = None
_UTILITY_WILDCARD_OWNER_METHODS: dict[str, frozenset[str]] | None = None

# Assertion-framework holder classes that expose a static ``fail`` method through
# wildcard imports. Kept curated and class-level to avoid over-resolving domain
# ``fail`` helpers.
_ASSERTION_FRAMEWORK_STATIC_METHODS: dict[str, set[str]] = {
    "org.junit.Assert": {"fail"},
    "org.junit.jupiter.api.Assertions": {"fail"},
    "org.testng.Assert": {"fail"},
    "org.testng.AssertJUnit": {"fail"},
    "org.assertj.core.api.Assertions": {"fail"},
    "junit.framework.Assert": {"fail"},
    "junit.framework.TestCase": {"fail"},
}


@dataclass(frozen=True)
class _WildcardImportRule:
    owner_prefix: str
    method_names: frozenset[str]


def _normalize_owner_prefix(owner_prefix: str) -> str:
    return owner_prefix.strip().lower()


def _add_wildcard_methods(
    entries: dict[str, set[str]],
    *,
    owner_prefix: str,
    method_names: Iterable[str],
) -> None:
    normalized_owner_prefix = _normalize_owner_prefix(owner_prefix)
    if not normalized_owner_prefix:
        return
    entries.setdefault(normalized_owner_prefix, set()).update(
        normalize_method_names(method_names)
    )


def _get_framework_wildcard_receiver_prefixes() -> tuple[str, ...]:
    global _FRAMEWORK_WILDCARD_RECEIVER_PREFIXES
    if _FRAMEWORK_WILDCARD_RECEIVER_PREFIXES is not None:
        return _FRAMEWORK_WILDCARD_RECEIVER_PREFIXES

    receiver_prefixes: set[str] = set()
    receiver_prefixes.update(
        _normalize_owner_prefix(owner_class_name)
        for owner_class_name in _get_http_static_import_owner_method_names()
    )

    receiver_prefixes.update(
        _normalize_owner_prefix(receiver_prefix)
        for receiver_prefix in STATIC_IMPORT_RECEIVER_HINTS
    )
    receiver_prefixes.update(
        _normalize_owner_prefix(receiver_prefix)
        for receiver_prefix in PROPERTY_RECEIVER_PREFIXES
    )
    receiver_prefixes.update(
        _normalize_owner_prefix(receiver_prefix)
        for receiver_prefix in VIRTUALIZED_STATIC_METHODS_BY_RECEIVER
    )
    receiver_prefixes.update(
        _normalize_owner_prefix(receiver_prefix)
        for receiver_prefix in AUTH_STATIC_IMPORT_METHODS_BY_RECEIVER
    )
    receiver_prefixes.update(
        _normalize_owner_prefix(receiver_prefix)
        for receiver_prefix in _ASSERTION_FRAMEWORK_STATIC_METHODS
    )
    receiver_prefixes.add("org.awaitility.")

    _FRAMEWORK_WILDCARD_RECEIVER_PREFIXES = tuple(
        sorted(receiver_prefixes, key=len, reverse=True)
    )
    return _FRAMEWORK_WILDCARD_RECEIVER_PREFIXES


def _get_framework_wildcard_rules() -> tuple[_WildcardImportRule, ...]:
    global _FRAMEWORK_WILDCARD_RULES
    if _FRAMEWORK_WILDCARD_RULES is not None:
        return _FRAMEWORK_WILDCARD_RULES

    entries: dict[str, set[str]] = {}

    for (
        owner_class_name,
        owner_method_names,
    ) in _get_http_static_import_owner_method_names().items():
        _add_wildcard_methods(
            entries,
            owner_prefix=owner_class_name,
            method_names=owner_method_names,
        )

    for receiver_prefix in STATIC_IMPORT_RECEIVER_HINTS:
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=MOCKED_CALL_NAMES,
        )

    for receiver_prefix in PROPERTY_RECEIVER_PREFIXES:
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=STRONG_PROPERTY_METHODS | AMBIGUOUS_PROPERTY_METHODS,
        )

    for receiver_prefix, wait_method_names in WAIT_SIGNAL_RECEIVER_METHODS.items():
        if receiver_prefix == "java.lang.Thread":
            continue
        if receiver_prefix == "org.awaitility.":
            _add_wildcard_methods(
                entries,
                owner_prefix=receiver_prefix,
                method_names=wait_method_names,
            )
            continue
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=wait_method_names,
        )

    for (
        receiver_prefix,
        db_method_names,
    ) in OBSERVATION_MEDIUM_DB_QUERY_RECEIVER_METHODS.items():
        if receiver_prefix == "org.springframework.test.jdbc.JdbcTestUtils":
            continue
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=db_method_names,
        )

    for (
        receiver_prefix,
        fs_method_names,
    ) in OBSERVATION_MEDIUM_FS_RECEIVER_METHODS.items():
        if receiver_prefix == "java.nio.file.Files":
            continue
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=fs_method_names,
        )

    for (
        receiver_prefix,
        mq_method_names,
    ) in OBSERVATION_MEDIUM_MQ_RECEIVER_METHODS.items():
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=mq_method_names,
        )

    for (
        receiver_prefix,
        virtualized_method_names,
    ) in VIRTUALIZED_STATIC_METHODS_BY_RECEIVER.items():
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=virtualized_method_names,
        )

    for (
        receiver_prefix,
        auth_method_names,
    ) in AUTH_STATIC_IMPORT_METHODS_BY_RECEIVER.items():
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=auth_method_names,
        )

    for (
        receiver_prefix,
        assertion_method_names,
    ) in _ASSERTION_FRAMEWORK_STATIC_METHODS.items():
        _add_wildcard_methods(
            entries,
            owner_prefix=receiver_prefix,
            method_names=assertion_method_names,
        )

    _FRAMEWORK_WILDCARD_RULES = tuple(
        sorted(
            [
                _WildcardImportRule(
                    owner_prefix=owner_prefix,
                    method_names=frozenset(method_names),
                )
                for owner_prefix, method_names in entries.items()
            ],
            key=lambda rule: len(rule.owner_prefix),
            reverse=True,
        )
    )
    return _FRAMEWORK_WILDCARD_RULES


def _get_utility_wildcard_owner_methods() -> dict[str, frozenset[str]]:
    global _UTILITY_WILDCARD_OWNER_METHODS
    if _UTILITY_WILDCARD_OWNER_METHODS is not None:
        return _UTILITY_WILDCARD_OWNER_METHODS

    entries: dict[str, frozenset[str]] = {}
    for owner_class_name in (
        "java.nio.file.Files",
        "org.springframework.test.jdbc.JdbcTestUtils",
    ):
        method_names = OBSERVATION_MEDIUM_FS_RECEIVER_METHODS.get(
            owner_class_name, set()
        )
        if not method_names:
            method_names = OBSERVATION_MEDIUM_DB_QUERY_RECEIVER_METHODS.get(
                owner_class_name, set()
            )
        entries[owner_class_name.lower()] = normalize_method_names(method_names)

    entries["java.lang.thread"] = normalize_method_names(
        WAIT_SIGNAL_RECEIVER_METHODS.get("java.lang.Thread", set())
    )
    _UTILITY_WILDCARD_OWNER_METHODS = entries
    return entries


def _get_http_static_import_owner_method_names() -> dict[str, frozenset[str]]:
    global _HTTP_STATIC_IMPORT_OWNER_METHOD_NAMES
    if _HTTP_STATIC_IMPORT_OWNER_METHOD_NAMES is not None:
        return _HTTP_STATIC_IMPORT_OWNER_METHOD_NAMES

    entries: dict[str, set[str]] = {}
    for owner_family_rule in HTTP_OWNER_FAMILY_RULES:
        for owner_class_name in owner_family_rule.static_import_owners:
            entries.setdefault(owner_class_name.lower(), set()).update(
                method_name.lower()
                for method_name in owner_family_rule.static_import_methods
            )

    _HTTP_STATIC_IMPORT_OWNER_METHOD_NAMES = {
        owner: frozenset(methods) for owner, methods in entries.items()
    }
    return _HTTP_STATIC_IMPORT_OWNER_METHOD_NAMES


def _iter_wildcard_method_candidates(import_path: str) -> Iterable[str]:
    normalized_import_path = import_path.lower()
    for rule in _get_framework_wildcard_rules():
        if not matches_receiver_prefix(normalized_import_path, rule.owner_prefix):
            continue
        for method_name in rule.method_names:
            yield method_name

    method_names = _get_utility_wildcard_owner_methods().get(normalized_import_path)
    if method_names is None:
        return
    for method_name in method_names:
        yield method_name


def _is_framework_wildcard_receiver(import_path: str) -> bool:
    normalized_import_path = import_path.lower()
    return any(
        matches_receiver_prefix(normalized_import_path, receiver_prefix)
        for receiver_prefix in _get_framework_wildcard_receiver_prefixes()
    )


@dataclass(frozen=True)
class StaticImportIndex:
    _exact_method_to_receiver: dict[str, str] = field(default_factory=dict)
    _framework_method_to_receiver: dict[str, str] = field(default_factory=dict)
    _utility_method_to_receiver: dict[str, str] = field(default_factory=dict)
    _static_method_evidence: frozenset[str] = field(default_factory=frozenset)

    EMPTY: ClassVar[StaticImportIndex]

    @classmethod
    def from_import_entries(
        cls,
        imports: Sequence[JImport],
        *,
        extra_wildcard_candidates: dict[str, set[str]] | None = None,
    ) -> StaticImportIndex:
        """Build an index from structured import entries.

        Args:
            imports: Structured imports for a class.
            extra_wildcard_candidates: Additional method-name -> receiver-type-set
                candidates discovered from project-local wildcard static imports.
                These are merged into the framework wildcard tier so that a name
                provided by both a framework wildcard and a project-local wildcard
                remains ambiguous (fail closed), and named static imports still
                outrank wildcard candidates.

        Returns:
            A static import index containing unambiguous method-to-receiver
            mappings and method-level static import evidence.
        """
        exact_candidates_by_method: dict[str, set[str]] = {}
        framework_candidates_by_method: dict[str, set[str]] = {}
        utility_candidates_by_method: dict[str, set[str]] = {}
        method_evidence: set[str] = set()

        if extra_wildcard_candidates:
            for method_name, receiver_types in extra_wildcard_candidates.items():
                for receiver_type in receiver_types:
                    cls._register_method_candidate(
                        method_name=method_name,
                        receiver_type=receiver_type,
                        candidates_by_method=framework_candidates_by_method,
                        method_evidence=method_evidence,
                    )

        for import_entry in imports:
            import_path = import_entry.path.strip()
            if not import_path or not import_entry.is_static:
                continue

            if import_entry.is_wildcard:
                if _is_framework_wildcard_receiver(import_path):
                    target_candidates = framework_candidates_by_method
                else:
                    target_candidates = utility_candidates_by_method
                for method_name in _iter_wildcard_method_candidates(import_path):
                    cls._register_method_candidate(
                        method_name=method_name,
                        receiver_type=import_path,
                        candidates_by_method=target_candidates,
                        method_evidence=method_evidence,
                    )
                continue

            receiver_type, separator, imported_member = import_path.rpartition(".")
            if not separator or not receiver_type:
                continue
            if not _looks_like_method_token(imported_member):
                continue

            imported_method = imported_member.lower()
            cls._register_method_candidate(
                method_name=imported_method,
                receiver_type=receiver_type,
                candidates_by_method=exact_candidates_by_method,
                method_evidence=method_evidence,
            )

        if (
            not exact_candidates_by_method
            and not framework_candidates_by_method
            and not utility_candidates_by_method
            and not method_evidence
        ):
            return cls.EMPTY

        return cls(
            _exact_method_to_receiver=cls._resolve_unique_candidates(
                exact_candidates_by_method
            ),
            _framework_method_to_receiver=cls._resolve_unique_candidates(
                framework_candidates_by_method
            ),
            _utility_method_to_receiver=cls._resolve_unique_candidates(
                utility_candidates_by_method
            ),
            _static_method_evidence=frozenset(method_evidence),
        )

    @staticmethod
    def _register_method_candidate(
        *,
        method_name: str,
        receiver_type: str,
        candidates_by_method: dict[str, set[str]],
        method_evidence: set[str],
    ) -> None:
        normalized_method_name = method_name.lower()
        normalized_receiver_type = receiver_type.strip()
        if not normalized_method_name or not normalized_receiver_type:
            return
        method_evidence.add(normalized_method_name)
        candidates_by_method.setdefault(normalized_method_name, set()).add(
            normalized_receiver_type
        )

    @staticmethod
    def _resolve_unique_candidates(
        candidates_by_method: dict[str, set[str]],
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for method_name, candidate_receivers in candidates_by_method.items():
            if len(candidate_receivers) != 1:
                continue
            resolved[method_name] = next(iter(candidate_receivers))
        return resolved

    def resolve(self, method_name: str) -> str | None:
        """Look up a bare method name and return the inferred receiver type."""
        if not method_name:
            return None
        normalized_method_name = method_name.lower()

        exact_receiver = self._exact_method_to_receiver.get(normalized_method_name)
        if exact_receiver is not None:
            return exact_receiver

        framework_receiver = self._framework_method_to_receiver.get(
            normalized_method_name
        )
        if framework_receiver is not None:
            return framework_receiver

        return self._utility_method_to_receiver.get(normalized_method_name)

    def has_method(self, method_name: str) -> bool:
        """Return True when static import evidence exists for a method name."""
        if not method_name:
            return False
        return method_name.lower() in self._static_method_evidence


StaticImportIndex.EMPTY = StaticImportIndex(
    _exact_method_to_receiver={},
    _framework_method_to_receiver={},
    _utility_method_to_receiver={},
    _static_method_evidence=frozenset(),
)


__all__ = [
    "StaticImportIndex",
    "matches_receiver_prefix",
]
