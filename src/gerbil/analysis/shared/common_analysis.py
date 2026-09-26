from __future__ import annotations

from collections.abc import Sequence

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JCallable, JImport

from gerbil.analysis.shared.annotations import (
    RUNTIME_INHERITED_ANNOTATION_IMPORT_ROOTS,
    annotation_matches_expected,
    annotation_short_name as _annotation_short_name,
    annotation_token as _annotation_token,
)
from gerbil.analysis.shared.caching import cache_put_bounded
from gerbil.analysis.shared.class_utils import (
    ClassAnnotationResolutionConfig,
    ResolvedAnnotation,
    categorize_classes as _categorize_classes,
    is_test_method as _is_test_method,
    resolve_effective_class_annotations,
)
from gerbil.analysis.shared.constant_resolution import ConstantResolver
from gerbil.analysis.shared.constants import (
    SETUP_ANNOTATIONS,
    TEARDOWN_ANNOTATIONS,
    TEST_DIRS,
)
from gerbil.analysis.shared.fixture_discovery import (
    SETUP_CLASS_SCOPE_ANNOTATIONS,
    TEARDOWN_CLASS_SCOPE_ANNOTATIONS,
    find_fixture_methods as _find_fixture_methods,
    get_effective_fixture_methods as _get_effective_fixture_methods,
)
from gerbil.analysis.shared.framework_inference import (
    infer_testing_frameworks,
)
from gerbil.analysis.shared.imports import (
    clone_imports,
    deduplicate_imports,
    get_class_import_declarations,
)
from gerbil.analysis.shared.metrics_helpers import (
    count_objects_created as _count_objects_created,
    get_application_method_metrics as _get_application_method_metrics,
    get_ncloc as _get_ncloc,
    get_test_utility_method_count as _get_test_utility_method_count,
)
from gerbil.analysis.shared.reachability import Reachability
from gerbil.analysis.shared.static_imports import StaticImportIndex
from gerbil.analysis.runtime.fixtures import FixtureMethod
from gerbil.analysis.schema import TestingFramework


_INHERITED_MARKER_ANNOTATIONS: set[str] = {
    "@Inherited",
    "@java.lang.annotation.Inherited",
}
_CLASS_CACHE_MAX_ENTRIES: int = 2_048
_ANNOTATION_CACHE_MAX_ENTRIES: int = 8_192


class CommonAnalysis:
    def __init__(
        self,
        analysis: JavaAnalysis,
        test_dirs: Sequence[str] | None = None,
    ):
        self.analysis: JavaAnalysis = analysis
        resolved_test_dirs = tuple(test_dirs or TEST_DIRS)
        if not resolved_test_dirs:
            raise ValueError("test_dirs must contain at least one path pattern")
        self.test_dirs: tuple[str, ...] = resolved_test_dirs
        self._reachability: Reachability = Reachability(analysis)
        self._superclass_chain_cache: dict[str, tuple[str, ...]] = {}
        self._class_imports_cache: dict[str, tuple[JImport, ...]] = {}
        self._static_import_index_cache: dict[str, StaticImportIndex] = {}
        self._effective_class_imports_cache: dict[str, tuple[JImport, ...]] = {}
        self._effective_class_annotations_cache: dict[
            str, tuple[ResolvedAnnotation, ...]
        ] = {}
        self._annotation_inherited_cache: dict[tuple[str, str], bool] = {}
        self._visible_class_methods_cache: dict[
            str, tuple[tuple[str, tuple[str, ...]], ...]
        ] = {}
        self._constant_resolver: ConstantResolver | None = None

    def get_reachability(self) -> Reachability:
        return self._reachability

    def get_constant_resolver(self) -> ConstantResolver:
        if self._constant_resolver is None:
            self._constant_resolver = ConstantResolver(
                analysis=self.analysis,
                get_class_imports_for_class=self.get_class_imports,
                get_class_resolution_order=(
                    lambda class_name, include_interfaces: (
                        self._reachability.get_class_resolution_order(
                            class_name,
                            include_superclasses=True,
                            include_interfaces=include_interfaces,
                        )
                    )
                ),
            )
        return self._constant_resolver

    def get_visible_class_methods(
        self,
        qualified_class_name: str,
    ) -> dict[str, list[str]]:
        cached_visible_methods = self._visible_class_methods_cache.get(
            qualified_class_name
        )
        if cached_visible_methods is not None:
            return {
                class_name: list(method_signatures)
                for class_name, method_signatures in cached_visible_methods
            }

        visible_methods = self._reachability.get_visible_class_methods(
            qualified_class_name
        )
        resolved_visible_methods: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
            (class_name, tuple(method_signatures))
            for class_name, method_signatures in visible_methods.items()
        )
        cache_put_bounded(
            cache=self._visible_class_methods_cache,
            key=qualified_class_name,
            value=resolved_visible_methods,
            max_entries=_CLASS_CACHE_MAX_ENTRIES,
        )
        return {
            class_name: list(method_signatures)
            for class_name, method_signatures in resolved_visible_methods
        }

    def get_ncloc(self, declaration: str, body: str) -> int:
        return _get_ncloc(declaration, body)

    def get_class_imports(self, qualified_class_name: str) -> list[JImport]:
        cached_imports = self._class_imports_cache.get(qualified_class_name)
        if cached_imports is not None:
            return clone_imports(cached_imports)

        class_imports = tuple(
            get_class_import_declarations(self.analysis, qualified_class_name)
        )
        cache_put_bounded(
            cache=self._class_imports_cache,
            key=qualified_class_name,
            value=class_imports,
            max_entries=_CLASS_CACHE_MAX_ENTRIES,
        )
        return clone_imports(class_imports)

    def _project_local_wildcard_candidates(
        self, imports: list[JImport]
    ) -> dict[str, set[str]]:
        """Discover static-method candidates from project-local wildcard imports.

        A static wildcard import of an analyzed class (e.g.
        ``import static com.acme.TestFixtures.*;``) contributes every static
        method of that class as a wildcard-tier candidate. These candidates are
        returned separately so they can be merged with framework wildcard
        candidates, preserving ambiguity when the same simple name is also
        available from a framework wildcard import.
        """
        analyzed_classes = self.analysis.get_classes()
        candidates_by_method: dict[str, set[str]] = {}
        for import_entry in imports:
            if not import_entry.is_static or not import_entry.is_wildcard:
                continue
            owner_path = import_entry.path.strip()
            if owner_path not in analyzed_classes:
                continue
            for method_signature, method_details in self.analysis.get_methods_in_class(
                owner_path
            ).items():
                if "static" not in (method_details.modifiers or []):
                    continue
                method_name = method_signature.split("(", 1)[0].strip().lower()
                if not method_name:
                    continue
                candidates_by_method.setdefault(method_name, set()).add(owner_path)
        return candidates_by_method

    def get_static_import_index(self, qualified_class_name: str) -> StaticImportIndex:
        cached_index = self._static_import_index_cache.get(qualified_class_name)
        if cached_index is not None:
            return cached_index

        class_imports = self.get_class_imports(qualified_class_name)
        extra_wildcard_candidates = self._project_local_wildcard_candidates(
            class_imports
        )
        static_import_index = StaticImportIndex.from_import_entries(
            class_imports,
            extra_wildcard_candidates=extra_wildcard_candidates,
        )
        cache_put_bounded(
            cache=self._static_import_index_cache,
            key=qualified_class_name,
            value=static_import_index,
            max_entries=_CLASS_CACHE_MAX_ENTRIES,
        )
        return static_import_index

    def get_superclass_chain(self, qualified_class_name: str) -> list[str]:
        cached_superclasses = self._superclass_chain_cache.get(qualified_class_name)
        if cached_superclasses is not None:
            return list(cached_superclasses)

        resolution_order = self._reachability.get_class_resolution_order(
            qualified_class_name,
            include_superclasses=True,
            include_interfaces=False,
            include_enclosing_classes=False,
        )
        superclasses = resolution_order[1:] if len(resolution_order) > 1 else []

        resolved_superclasses: tuple[str, ...] = tuple(superclasses)
        cache_put_bounded(
            cache=self._superclass_chain_cache,
            key=qualified_class_name,
            value=resolved_superclasses,
            max_entries=_CLASS_CACHE_MAX_ENTRIES,
        )
        return list(resolved_superclasses)

    def get_effective_class_imports(self, qualified_class_name: str) -> list[JImport]:
        cached_imports = self._effective_class_imports_cache.get(qualified_class_name)
        if cached_imports is not None:
            return clone_imports(cached_imports)

        effective_imports: list[JImport] = []
        for class_name in self._reachability.get_class_resolution_order(
            qualified_class_name,
            include_superclasses=True,
            include_interfaces=False,
        ):
            effective_imports.extend(self.get_class_imports(class_name))
        resolved_imports: tuple[JImport, ...] = tuple(
            deduplicate_imports(effective_imports)
        )
        cache_put_bounded(
            cache=self._effective_class_imports_cache,
            key=qualified_class_name,
            value=resolved_imports,
            max_entries=_CLASS_CACHE_MAX_ENTRIES,
        )
        return clone_imports(resolved_imports)

    def _build_import_lookup(
        self,
        class_names: set[str],
    ) -> dict[str, list[JImport]]:
        return {
            class_name: self.get_class_imports(class_name)
            for class_name in sorted(class_names)
        }

    def _annotation_type_candidates(
        self,
        annotation_name: str,
        imports: list[JImport],
        package_name: str,
    ) -> list[str]:
        annotation_token = _annotation_token(annotation_name)
        qualified_name: str = annotation_token.removeprefix("@").strip()
        short_name: str = _annotation_short_name(annotation_token).removeprefix("@")
        short_name = short_name.strip()
        if not short_name:
            return []

        candidates: list[str] = []
        seen_candidates: set[str] = set()

        def add_candidate(candidate: str) -> None:
            if not candidate or candidate in seen_candidates:
                return
            seen_candidates.add(candidate)
            candidates.append(candidate)

        if "." in qualified_name:
            add_candidate(qualified_name)

        for import_entry in imports:
            if import_entry.is_static:
                continue
            import_name = import_entry.path
            if import_name.endswith(f".{short_name}") and not import_entry.is_wildcard:
                add_candidate(import_name)
                continue
            if import_entry.is_wildcard:
                add_candidate(f"{import_name}.{short_name}")

        if package_name:
            add_candidate(f"{package_name}.{short_name}")

        add_candidate(short_name)
        return candidates

    def _is_annotation_inherited_for_declaring_class(
        self,
        annotation_name: str,
        declaring_class_name: str,
    ) -> bool:
        cache_key = (declaring_class_name, annotation_name)
        cached_value = self._annotation_inherited_cache.get(cache_key)
        if cached_value is not None:
            return cached_value

        declaring_imports: list[JImport] = self.get_class_imports(declaring_class_name)
        annotation_short = _annotation_short_name(annotation_name)
        is_inherited = (
            annotation_short in RUNTIME_INHERITED_ANNOTATION_IMPORT_ROOTS
            and annotation_matches_expected(
                annotation=annotation_name,
                expected_annotation=annotation_short,
                class_imports=declaring_imports,
                import_roots_by_annotation=RUNTIME_INHERITED_ANNOTATION_IMPORT_ROOTS,
            )
        )
        if not is_inherited:
            package_name: str = declaring_class_name.rpartition(".")[0]
            for candidate in self._annotation_type_candidates(
                annotation_name=annotation_name,
                imports=declaring_imports,
                package_name=package_name,
            ):
                annotation_details = self.analysis.get_class(candidate)
                if not annotation_details:
                    continue

                annotation_markers = {
                    _annotation_token(marker)
                    for marker in (annotation_details.annotations or [])
                }
                if annotation_markers & _INHERITED_MARKER_ANNOTATIONS:
                    is_inherited = True
                    break

        cache_put_bounded(
            cache=self._annotation_inherited_cache,
            key=cache_key,
            value=is_inherited,
            max_entries=_ANNOTATION_CACHE_MAX_ENTRIES,
        )
        return is_inherited

    def resolve_effective_class_annotations(
        self,
        qualified_class_name: str,
    ) -> list[ResolvedAnnotation]:
        cached_annotations = self._effective_class_annotations_cache.get(
            qualified_class_name
        )
        if cached_annotations is not None:
            return list(cached_annotations)

        effective_annotations = resolve_effective_class_annotations(
            analysis=self.analysis,
            qualified_class_name=qualified_class_name,
            config=ClassAnnotationResolutionConfig(
                include_superclasses=True,
                include_interfaces=False,
                require_inherited_annotations_from_parents=True,
            ),
            inherited_annotation_filter=self._is_annotation_inherited_for_declaring_class,
        )

        resolved_annotations: tuple[ResolvedAnnotation, ...] = tuple(
            effective_annotations
        )
        cache_put_bounded(
            cache=self._effective_class_annotations_cache,
            key=qualified_class_name,
            value=resolved_annotations,
            max_entries=_CLASS_CACHE_MAX_ENTRIES,
        )
        return list(resolved_annotations)

    def get_testing_frameworks_for_class(
        self, qualified_class_name: str
    ) -> list[TestingFramework]:
        class_imports: list[JImport] = self.get_effective_class_imports(
            qualified_class_name
        )
        class_annotations: list[ResolvedAnnotation] = (
            self.resolve_effective_class_annotations(qualified_class_name)
        )
        class_imports_by_name = self._build_import_lookup(
            {annotation.declaring_class_name for annotation in class_annotations}
        )
        return infer_testing_frameworks(
            class_imports=class_imports,
            class_annotations=class_annotations,
            class_annotation_imports_by_class=class_imports_by_name,
        )

    def is_test_method(
        self,
        method_signature: str,
        qualified_class_name: str,
        testing_frameworks: list[TestingFramework],
    ) -> bool:
        method_details = self.analysis.get_method(
            qualified_class_name, method_signature
        )
        if not method_details:
            return False

        class_details = self.analysis.get_class(qualified_class_name)
        if not class_details:
            return False

        class_annotations: list[ResolvedAnnotation] = (
            self.resolve_effective_class_annotations(qualified_class_name)
        )
        class_annotation_imports_by_class = self._build_import_lookup(
            {annotation.declaring_class_name for annotation in class_annotations}
        )
        direct_class_imports = self.get_class_imports(qualified_class_name)
        return _is_test_method(
            method_signature=method_signature,
            method_details=method_details,
            class_extends_list=list(class_details.extends_list or []),
            class_annotations=class_annotations,
            testing_frameworks=testing_frameworks,
            method_imports=direct_class_imports,
            class_annotation_imports_by_class=class_annotation_imports_by_class,
        )

    def categorize_classes(self) -> tuple[dict[str, list[str]], list[str], list[str]]:
        return _categorize_classes(
            analysis=self.analysis,
            qualified_class_names=list(self.analysis.get_classes().keys()),
            test_dirs=self.test_dirs,
            get_testing_frameworks_for_class=self.get_testing_frameworks_for_class,
            is_test_method_for_class=self.is_test_method,
        )

    def get_setup_methods(self, qualified_class_name: str) -> list[FixtureMethod]:
        return _find_fixture_methods(
            analysis=self.analysis,
            reachable_methods=self.get_visible_class_methods(qualified_class_name),
            fixture_annotations=SETUP_ANNOTATIONS,
        )

    def get_teardown_methods(self, qualified_class_name: str) -> list[FixtureMethod]:
        return _find_fixture_methods(
            analysis=self.analysis,
            reachable_methods=self.get_visible_class_methods(qualified_class_name),
            fixture_annotations=TEARDOWN_ANNOTATIONS,
        )

    def get_effective_setup_methods(
        self,
        qualified_class_name: str,
        test_method_signature: str,
        setup_methods: list[FixtureMethod],
    ) -> list[FixtureMethod]:
        return _get_effective_fixture_methods(
            analysis=self.analysis,
            qualified_class_name=qualified_class_name,
            test_method_signature=test_method_signature,
            fixture_methods=setup_methods,
            class_annotations=self.resolve_effective_class_annotations(
                qualified_class_name
            ),
            fixture_annotations=SETUP_ANNOTATIONS,
            class_scope_annotations=SETUP_CLASS_SCOPE_ANNOTATIONS,
        )

    def get_effective_teardown_methods(
        self,
        qualified_class_name: str,
        test_method_signature: str,
        teardown_methods: list[FixtureMethod],
    ) -> list[FixtureMethod]:
        return _get_effective_fixture_methods(
            analysis=self.analysis,
            qualified_class_name=qualified_class_name,
            test_method_signature=test_method_signature,
            fixture_methods=teardown_methods,
            class_annotations=self.resolve_effective_class_annotations(
                qualified_class_name
            ),
            fixture_annotations=TEARDOWN_ANNOTATIONS,
            class_scope_annotations=TEARDOWN_CLASS_SCOPE_ANNOTATIONS,
        )

    @staticmethod
    def count_objects_created(method_details: JCallable | None) -> int:
        return _count_objects_created(method_details)

    def get_application_method_metrics(
        self, application_classes: list[str]
    ) -> tuple[int, int]:
        return _get_application_method_metrics(self.analysis, application_classes)

    def get_test_utility_method_count(self, test_utility_classes: list[str]) -> int:
        return _get_test_utility_method_count(self.analysis, test_utility_classes)
