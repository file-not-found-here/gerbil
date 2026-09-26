from __future__ import annotations

import ast
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _REPO_ROOT / "src" / "gerbil" / "analysis" / "schema"
_PRODUCTION_DIR = _REPO_ROOT / "src" / "gerbil"
_ANALYSIS_DIR = _PRODUCTION_DIR / "analysis"

_FORBIDDEN_SCHEMA_IMPORT_PREFIXES: tuple[str, ...] = (
    "gerbil.analysis.shared",
    "gerbil.analysis.http",
    "gerbil.analysis.runtime",
    "gerbil.analysis.properties",
    "gerbil.analysis.test_method",
    "gerbil.analysis.test_class",
    "gerbil.analysis.project",
    "cldk",
)


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _iter_imported_modules(file_path: Path) -> list[str]:
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _is_forbidden_prefix(module_name: str, prefix: str) -> bool:
    return module_name == prefix or module_name.startswith(f"{prefix}.")


def _is_dataclass_decorated(class_def: ast.ClassDef) -> bool:
    for decorator in class_def.decorator_list:
        if isinstance(decorator, ast.Name) and decorator.id == "dataclass":
            return True
        if isinstance(decorator, ast.Attribute) and decorator.attr == "dataclass":
            return True
        if isinstance(decorator, ast.Call):
            func = decorator.func
            if isinstance(func, ast.Name) and func.id == "dataclass":
                return True
            if isinstance(func, ast.Attribute) and func.attr == "dataclass":
                return True
    return False


def test_schema_layer_does_not_import_forbidden_modules() -> None:
    violations: list[str] = []
    for file_path in _python_files(_SCHEMA_DIR):
        for module_name in _iter_imported_modules(file_path):
            for prefix in _FORBIDDEN_SCHEMA_IMPORT_PREFIXES:
                if not _is_forbidden_prefix(module_name, prefix):
                    continue
                rel_path = file_path.relative_to(_REPO_ROOT)
                violations.append(f"{rel_path}: {module_name}")
                break

    assert violations == [], "Forbidden imports in analysis/schema:\n" + "\n".join(
        sorted(violations)
    )


_ALLOWED_DATACLASSES_IN_SCHEMA = {"HttpClassification", "AssertionClassification"}


def test_schema_types_contains_no_dataclass_declarations() -> None:
    types_path = _SCHEMA_DIR / "types.py"
    tree = ast.parse(types_path.read_text(), filename=str(types_path))
    dataclass_classes = [
        class_def.name
        for class_def in tree.body
        if isinstance(class_def, ast.ClassDef)
        and _is_dataclass_decorated(class_def)
        and class_def.name not in _ALLOWED_DATACLASSES_IN_SCHEMA
    ]

    assert dataclass_classes == [], (
        "Dataclass declarations are not allowed in analysis/schema/types.py: "
        + ", ".join(dataclass_classes)
    )


def test_production_code_does_not_import_removed_common_namespace() -> None:
    _COMMON_COMPAT = Path("src/gerbil/analysis/common/__init__.py")
    violations: list[str] = []
    for file_path in _python_files(_PRODUCTION_DIR):
        rel = file_path.relative_to(_REPO_ROOT)
        if rel == _COMMON_COMPAT:
            continue
        for module_name in _iter_imported_modules(file_path):
            if not _is_forbidden_prefix(module_name, "gerbil.analysis.common"):
                continue
            violations.append(f"{rel}: {module_name}")

    assert violations == [], "Removed common namespace still imported:\n" + "\n".join(
        sorted(violations)
    )


def test_production_code_does_not_import_removed_model_namespace() -> None:
    _MODEL_COMPAT = Path("src/gerbil/analysis/model/__init__.py")
    violations: list[str] = []
    for file_path in _python_files(_PRODUCTION_DIR):
        rel = file_path.relative_to(_REPO_ROOT)
        if rel == _MODEL_COMPAT:
            continue
        for module_name in _iter_imported_modules(file_path):
            if not _is_forbidden_prefix(module_name, "gerbil.analysis.model"):
                continue
            violations.append(f"{rel}: {module_name}")

    assert violations == [], "Removed model namespace still imported:\n" + "\n".join(
        sorted(violations)
    )


def test_production_code_does_not_import_removed_lifecycle_namespace() -> None:
    _LIFECYCLE_COMPAT = Path("src/gerbil/analysis/lifecycle/__init__.py")
    violations: list[str] = []
    for file_path in _python_files(_PRODUCTION_DIR):
        rel = file_path.relative_to(_REPO_ROOT)
        if rel == _LIFECYCLE_COMPAT:
            continue
        for module_name in _iter_imported_modules(file_path):
            if not _is_forbidden_prefix(module_name, "gerbil.analysis.lifecycle"):
                continue
            violations.append(f"{rel}: {module_name}")

    assert (
        violations == []
    ), "Removed lifecycle namespace still imported:\n" + "\n".join(sorted(violations))


def test_non_analysis_modules_do_not_import_internal_namespace() -> None:
    violations: list[str] = []
    non_analysis_files = [
        file_path
        for file_path in _python_files(_PRODUCTION_DIR)
        if _ANALYSIS_DIR not in file_path.parents
    ]
    for file_path in non_analysis_files:
        for module_name in _iter_imported_modules(file_path):
            if not _is_forbidden_prefix(module_name, "gerbil.analysis.runtime"):
                continue
            rel_path = file_path.relative_to(_REPO_ROOT)
            violations.append(f"{rel_path}: {module_name}")

    assert (
        violations == []
    ), "Non-analysis modules must not import analysis.internal:\n" + "\n".join(
        sorted(violations)
    )
