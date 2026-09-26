from __future__ import annotations

from collections import deque
from collections.abc import Callable

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JImport
from cldk.models.java.models import JCallSite

from gerbil.analysis.runtime.call_sites import (
    LoadCallSites,
    MethodRef,
    ResolveHelper,
)
from gerbil.analysis.shared.class_utils import (
    get_nested_enclosing_chain,
    normalize_type_reference,
)
from gerbil.analysis.shared.imports import get_class_import_declarations
from gerbil.analysis.shared.receiver_resolution import resolve_receiver
from gerbil.analysis.shared.static_imports import (
    StaticImportIndex,
)


class Reachability:
    def __init__(self, analysis: JavaAnalysis):
        self.analysis: JavaAnalysis = analysis

    def _get_class_imports_for_class(self, qualified_class_name: str) -> list[JImport]:
        return get_class_import_declarations(self.analysis, qualified_class_name)

    def _get_superclass_chain_for_class(self, qualified_class_name: str) -> list[str]:
        resolution_order = self.get_class_resolution_order(
            qualified_class_name,
            include_superclasses=True,
            include_interfaces=False,
        )
        if len(resolution_order) <= 1:
            return []
        return resolution_order[1:]

    def _resolve_target_class_for_call(
        self,
        current_class: str,
        callee_signature: str,
        receiver_type: str,
        receiver_expr: str,
        valid_classes: set[str],
    ) -> str | None:
        if receiver_type:
            if receiver_type not in valid_classes:
                return None
            method = self.analysis.get_method(receiver_type, callee_signature)
            if method and method.code:
                return receiver_type
            for ancestor in self.get_class_resolution_order(receiver_type)[1:]:
                if ancestor not in valid_classes:
                    continue
                method = self.analysis.get_method(ancestor, callee_signature)
                if method and method.code:
                    return ancestor
            return None

        resolution_order = self.get_class_resolution_order(current_class)
        if not resolution_order:
            resolution_order = [current_class]

        candidate_classes: list[str]
        if receiver_expr == "super":
            candidate_classes = (
                resolution_order[1:] if len(resolution_order) > 1 else []
            )
        else:
            candidate_classes = resolution_order

        for candidate_class in candidate_classes:
            if candidate_class not in valid_classes:
                continue
            method = self.analysis.get_method(candidate_class, callee_signature)
            if method and method.code:
                return candidate_class

        return None

    def build_helper_resolver(
        self,
        qualified_class_name: str,
        add_extended_class: bool = True,
        test_utility_classes: list[str] | None = None,
        get_static_import_index_for_class: (
            Callable[[str], StaticImportIndex] | None
        ) = None,
    ) -> tuple[ResolveHelper, LoadCallSites]:
        """Build resolver callbacks for call-site helper expansion."""

        valid_classes: set[str] = {qualified_class_name}
        if add_extended_class:
            hierarchy = self.get_class_resolution_order(qualified_class_name)
            valid_classes.update(hierarchy)
        valid_classes.update(set(test_utility_classes or []))

        def resolve_helper(owner: MethodRef, call_site: JCallSite) -> MethodRef | None:
            callee_signature: str = call_site.callee_signature or ""
            if not callee_signature:
                return None

            owner_class_name = owner.defining_class_name or qualified_class_name
            receiver_expr = (call_site.receiver_expr or "").strip()
            explicit_receiver_type = (call_site.receiver_type or "").strip()

            # If no receiver, check if inherited helper first, before trying static import resolution
            if not receiver_expr and not explicit_receiver_type:
                local_target = self._resolve_target_class_for_call(
                    current_class=owner_class_name,
                    callee_signature=callee_signature,
                    receiver_type="",
                    receiver_expr=receiver_expr,
                    valid_classes=valid_classes,
                )
                if local_target is not None:
                    return MethodRef(
                        defining_class_name=local_target,
                        method_signature=callee_signature,
                    )

            owner_method_details = self.analysis.get_method(
                owner_class_name,
                owner.method_signature,
            )
            static_import_index = (
                get_static_import_index_for_class(owner_class_name)
                if get_static_import_index_for_class is not None
                else StaticImportIndex.EMPTY
            )

            resolved_receiver = resolve_receiver(
                call_site=call_site,
                static_import_index=static_import_index,
                owner_class_name=owner_class_name,
                owner_method_details=owner_method_details,
                analysis=self.analysis,
                get_class_imports_for_class=self._get_class_imports_for_class,
                get_superclass_chain_for_class=self._get_superclass_chain_for_class,
            )
            receiver_type = resolved_receiver.receiver_type

            # Terminate if we cannot resolve owner for method at all
            if receiver_expr and not receiver_type:
                return None

            target_class = self._resolve_target_class_for_call(
                current_class=owner_class_name,
                callee_signature=callee_signature,
                receiver_type=receiver_type,
                receiver_expr=receiver_expr,
                valid_classes=valid_classes,
            )

            if target_class is None:
                return None

            return MethodRef(
                defining_class_name=target_class,
                method_signature=callee_signature,
            )

        def load_call_sites(method_ref: MethodRef) -> list[JCallSite] | None:
            method_details = self.analysis.get_method(
                method_ref.defining_class_name, method_ref.method_signature
            )
            if method_details is None:
                return None
            return list(method_details.call_sites)

        return resolve_helper, load_call_sites

    def _resolve_hierarchy_candidate(
        self,
        type_reference: str,
        declaring_class_name: str,
    ) -> str | None:
        normalized = normalize_type_reference(type_reference)
        if not normalized or normalized == "java.lang.Object":
            return None

        if self.analysis.get_class(normalized) is not None:
            return normalized

        if "." in normalized:
            return normalized

        simple_name = normalized
        wildcard_candidates: list[str] = []
        for import_entry in self._get_class_imports_for_class(declaring_class_name):
            if import_entry.is_static:
                continue
            path = import_entry.path.strip()
            if not path:
                continue
            if import_entry.is_wildcard:
                wildcard_candidates.append(f"{path}.{simple_name}")
                continue
            if path.rsplit(".", 1)[-1] == simple_name:
                return path

        # A same-package supertype carries no import (a bare `extends BaseTest`),
        # so resolve it against the declaring class's package, mirroring the
        # receiver/type resolvers' same-package handling.
        package_name = declaring_class_name.rpartition(".")[0]
        if package_name:
            same_package_candidate = f"{package_name}.{simple_name}"
            if self.analysis.get_class(same_package_candidate) is not None:
                return same_package_candidate

        if len(wildcard_candidates) == 1:
            return wildcard_candidates[0]

        return None

    def get_class_resolution_order(
        self,
        qualified_class_name: str,
        include_superclasses: bool = True,
        include_interfaces: bool = True,
        include_enclosing_classes: bool = True,
    ) -> list[str]:
        class_details = self.analysis.get_class(qualified_class_name)
        if not class_details:
            return []

        class_order: list[str] = [qualified_class_name]
        seen: set[str] = {qualified_class_name}
        super_bfs_order: list[str] = []

        def _append_unique(candidate: str | None) -> None:
            if candidate and candidate not in seen:
                seen.add(candidate)
                class_order.append(candidate)

        if include_superclasses:
            super_queue: deque[str] = deque()
            for super_ref in class_details.extends_list or []:
                candidate = self._resolve_hierarchy_candidate(
                    super_ref, qualified_class_name
                )
                if candidate is not None and candidate not in seen:
                    super_queue.append(candidate)

            while super_queue:
                super_class = super_queue.popleft()
                super_details = self.analysis.get_class(super_class)
                if super_details is None:
                    _append_unique(super_class)
                    continue

                _append_unique(super_class)
                super_bfs_order.append(super_class)

                for next_super_ref in super_details.extends_list or []:
                    next_super = self._resolve_hierarchy_candidate(
                        next_super_ref, super_class
                    )
                    if next_super is None or next_super in seen:
                        continue
                    super_queue.append(next_super)

        if include_enclosing_classes:
            for enclosing_class in get_nested_enclosing_chain(
                self.analysis, qualified_class_name
            ):
                if enclosing_class in seen:
                    continue
                _append_unique(enclosing_class)
                super_bfs_order.append(enclosing_class)

                if include_superclasses:
                    enclosing_details = self.analysis.get_class(enclosing_class)
                    if enclosing_details is None:
                        continue

                    enclosing_super_queue: deque[str] = deque()
                    for super_ref in enclosing_details.extends_list or []:
                        candidate = self._resolve_hierarchy_candidate(
                            super_ref, enclosing_class
                        )
                        if candidate is not None and candidate not in seen:
                            enclosing_super_queue.append(candidate)

                    while enclosing_super_queue:
                        super_class = enclosing_super_queue.popleft()
                        super_details = self.analysis.get_class(super_class)
                        if super_details is None:
                            _append_unique(super_class)
                            continue

                        _append_unique(super_class)
                        super_bfs_order.append(super_class)

                        for next_super_ref in super_details.extends_list or []:
                            next_super = self._resolve_hierarchy_candidate(
                                next_super_ref, super_class
                            )
                            if next_super is None or next_super in seen:
                                continue
                            enclosing_super_queue.append(next_super)

        if include_interfaces:
            interface_queue: deque[tuple[str, str]] = deque()
            visited_interfaces: set[str] = set()

            def enqueue_interfaces(owner_class: str) -> None:
                owner_details = self.analysis.get_class(owner_class)
                if owner_details is None:
                    return

                for interface_ref in owner_details.implements_list or []:
                    candidate = self._resolve_hierarchy_candidate(
                        interface_ref, owner_class
                    )
                    if candidate is None or candidate in visited_interfaces:
                        continue
                    visited_interfaces.add(candidate)
                    interface_queue.append((candidate, owner_class))

            enqueue_interfaces(qualified_class_name)
            for super_class in super_bfs_order:
                enqueue_interfaces(super_class)

            while interface_queue:
                interface_name, owner_class = interface_queue.popleft()
                interface_details = self.analysis.get_class(interface_name)
                if interface_details is None:
                    _append_unique(interface_name)
                    continue

                _append_unique(interface_name)

                for parent_interface_ref in interface_details.extends_list or []:
                    parent_interface = self._resolve_hierarchy_candidate(
                        parent_interface_ref, interface_name
                    )
                    if (
                        parent_interface is None
                        or parent_interface in visited_interfaces
                    ):
                        continue
                    visited_interfaces.add(parent_interface)
                    interface_queue.append((parent_interface, interface_name))

        return class_order

    def get_visible_class_methods(
        self,
        qualified_class_name: str,
    ) -> dict[str, list[str]]:
        visible_methods: dict[str, list[str]] = {}
        processed_classes: set[str] = set()

        own_order = self.get_class_resolution_order(
            qualified_class_name,
            include_superclasses=True,
            include_interfaces=True,
            include_enclosing_classes=False,
        )
        groups: list[list[str]] = [own_order]

        for enclosing_class in get_nested_enclosing_chain(
            self.analysis, qualified_class_name
        ):
            enclosing_order = self.get_class_resolution_order(
                enclosing_class,
                include_superclasses=True,
                include_interfaces=True,
                include_enclosing_classes=False,
            )
            groups.append(enclosing_order)

        for group in groups:
            seen_signatures: set[str] = set()
            for class_name in group:
                if class_name in processed_classes:
                    continue
                processed_classes.add(class_name)

                methods = self.analysis.get_methods_in_class(class_name)
                added_signatures: list[str] = []
                for method_signature in methods.keys():
                    if method_signature in seen_signatures:
                        continue
                    seen_signatures.add(method_signature)
                    added_signatures.append(method_signature)

                if added_signatures:
                    visible_methods[class_name] = added_signatures

        return visible_methods
