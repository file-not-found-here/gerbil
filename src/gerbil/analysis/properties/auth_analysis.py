from __future__ import annotations

import re
from dataclasses import dataclass

from cldk.models.java import JImport

from gerbil.analysis.http.framework_registry import matches_receiver_prefix
from gerbil.analysis.shared.annotations import (
    annotation_matches_expected,
)
from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.shared.constants import (
    AUTH_BYPASSED_STATIC_METHODS_BY_RECEIVER,
    AUTH_MOCKED_STATIC_METHODS_BY_RECEIVER,
    AUTH_TEST_TOKEN_STATIC_METHODS_BY_RECEIVER,
    REST_ASSURED_ROOT_PACKAGES,
)
from gerbil.analysis.schema import (
    AuthHandling,
    AuthHandlingDecision,
    HttpClassification,
    HttpRequestRole,
)
from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver


@dataclass(frozen=True)
class _AuthCallSiteRule:
    receiver_prefixes: tuple[str, ...]
    method_names: frozenset[str]


_MOCKED_AUTH_ANNOTATIONS: set[str] = {
    "@WithMockUser",
    "@WithUserDetails",
}

_BYPASS_AUTH_ANNOTATIONS: set[str] = {
    "@WithAnonymousUser",
    "@PermitAll",
}

_MOCKED_CALL_SITE_RULES: tuple[_AuthCallSiteRule, ...] = (
    _AuthCallSiteRule(
        receiver_prefixes=("org.springframework.security.test.",),
        method_names=frozenset(
            {
                "withMockUser",
                "oauth2Login",
                "jwt",
                "oidcLogin",
                "oauth2Client",
                "mockUser",
                "mockJwt",
                "mockOAuth2Login",
                "mockOidcLogin",
                "mockOAuth2Client",
            }
        ),
    ),
)

_HIGH_CONFIDENCE_MOCKED_METHODS: frozenset[str] = frozenset(
    {
        "withMockUser",
        "oauth2Login",
        "oidcLogin",
        "oauth2Client",
        "mockOAuth2Login",
        "mockOidcLogin",
        "mockOAuth2Client",
    }
)

_TEST_TOKEN_CALL_SITE_RULES: tuple[_AuthCallSiteRule, ...] = (
    # RestAssured `.auth()` chain: AuthenticationSpecification/PreemptiveAuthSpec
    # credential methods all carry static test credentials.
    _AuthCallSiteRule(
        receiver_prefixes=REST_ASSURED_ROOT_PACKAGES,
        method_names=frozenset(
            {
                "bearerToken",
                "oauth2",
                "oauth",
                "basic",
                "digest",
                "ntlm",
                "form",
                "certificate",
            }
        ),
    ),
    _AuthCallSiteRule(
        receiver_prefixes=("org.springframework.boot.test.web.client.",),
        method_names=frozenset({"withBasicAuth"}),
    ),
    _AuthCallSiteRule(
        receiver_prefixes=("org.springframework.http.",),
        method_names=frozenset({"setBearerAuth", "setBasicAuth"}),
    ),
)

_REAL_FLOW_CALL_SITE_RULES: tuple[_AuthCallSiteRule, ...] = (
    _AuthCallSiteRule(
        receiver_prefixes=(
            "org.springframework.security.authentication.",
            "org.springframework.security.oauth2.",
            "org.keycloak.",
            "com.okta.",
            "com.auth0.",
        ),
        method_names=frozenset({"authenticate", "login", "authorize"}),
    ),
)

_AUTH_ROUTE_HINT_WORDS: frozenset[str] = frozenset({"oauth", "login", "token", "auth"})
_ROUTE_WORD_PATTERN = re.compile(r"[a-z]+")

# Credential scheme words recognized inside raw header expressions. The canonical
# AUTH_HEADER_HINTS tokens (Authorization/Bearer/Basic/X-API-Key) are all
# credential-bearing, so any extracted auth hint already implies test-token.
_AUTH_CREDENTIAL_HEADER_WORDS: frozenset[str] = frozenset(
    {"authorization", "bearer", "basic", "x-api-key"}
)

# Receiver namespaces that exist only for authentication. A call into one of these
# whose method we do not recognize is auth machinery we cannot categorize (weak
# evidence for `unknown`). Deliberately excludes the broader spring-security test
# post-processors namespace, which also hosts non-auth concerns such as csrf.
_AUTH_SPECIFIC_RECEIVER_PREFIXES: tuple[str, ...] = (
    "org.springframework.security.authentication.",
    "org.springframework.security.oauth2.",
    "org.keycloak.",
    "com.okta.",
    "com.auth0.",
)

# Simple names of @With* annotations already classified by the strong tier; the
# custom-annotation weak signal skips them to avoid double-recording.
_STRONG_WITH_ANNOTATION_SIMPLE_NAMES: frozenset[str] = frozenset(
    {"withmockuser", "withuserdetails", "withanonymoususer"}
)

# Security-context tokens that distinguish a custom @With* security annotation
# (Spring's @WithSecurityContext convention) from unrelated @With* annotations
# such as JUnit Pioneer's @WithEnvironmentVariable.
_SECURITY_CONTEXT_ANNOTATION_TOKENS: frozenset[str] = frozenset(
    {
        "user",
        "securitycontext",
        "mock",
        "oidc",
        "oauth",
        "jwt",
        "principal",
        "authentication",
        "login",
        "role",
        "anonymous",
        "credential",
        "identity",
    }
)

_AUTH_PRECEDENCE: list[AuthHandling] = [
    AuthHandling.REAL_FLOW,
    AuthHandling.TEST_TOKEN,
    AuthHandling.MOCKED,
    AuthHandling.BYPASSED,
    AuthHandling.UNKNOWN,
]


def _has_auth_route(path: str) -> bool:
    route_words = _ROUTE_WORD_PATTERN.findall(path.lower())
    return bool(_AUTH_ROUTE_HINT_WORDS.intersection(route_words))


def _has_auth_credentials(classification: HttpClassification) -> bool:
    # Any extracted auth hint is a request credential (Authorization/Bearer/Basic/
    # X-API-Key); fall back to scanning raw header expressions for the schemes.
    if classification.auth_hints:
        return True
    combined = " ".join(classification.headers).lower()
    return any(word in combined for word in _AUTH_CREDENTIAL_HEADER_WORDS)


def _is_custom_security_context_annotation(annotation: str) -> bool:
    simple_name = annotation.lstrip("@").split("(", 1)[0].strip().rsplit(".", 1)[-1]
    lowered = simple_name.lower()
    if not lowered.startswith("with") or len(lowered) <= 4:
        return False
    if lowered in _STRONG_WITH_ANNOTATION_SIMPLE_NAMES:
        return False
    return any(token in lowered for token in _SECURITY_CONTEXT_ANNOTATION_TOKENS)


def _matches_auth_specific_receiver(receiver_type: str) -> bool:
    return any(
        matches_receiver_prefix(receiver_type, prefix)
        for prefix in _AUTH_SPECIFIC_RECEIVER_PREFIXES
    )


def _matches_any_rule(
    receiver_type: str,
    method_name: str,
    rules: tuple[_AuthCallSiteRule, ...],
) -> bool:
    for rule in rules:
        if method_name not in rule.method_names:
            continue
        for prefix in rule.receiver_prefixes:
            if matches_receiver_prefix(receiver_type, prefix):
                return True
    return False


def _rules_from_method_map(
    method_map: dict[str, set[str]],
) -> tuple[_AuthCallSiteRule, ...]:
    return tuple(
        _AuthCallSiteRule(
            receiver_prefixes=(receiver_prefix,),
            method_names=frozenset(method_names),
        )
        for receiver_prefix, method_names in method_map.items()
    )


def _add_signal(signals: dict[str, list[str]], key: str, signal: str) -> None:
    signals.setdefault(key, []).append(signal)


_STRONG_AUTH_LABELS: tuple[AuthHandling, ...] = (
    AuthHandling.REAL_FLOW,
    AuthHandling.TEST_TOKEN,
    AuthHandling.MOCKED,
    AuthHandling.BYPASSED,
)


def _count_strong_signals(signals: dict[str, list[str]]) -> int:
    return sum(len(signals.get(label.value, [])) for label in _STRONG_AUTH_LABELS)


_ANNOTATION_CHECKS: list[tuple[set[str], AuthHandling]] = [
    (_MOCKED_AUTH_ANNOTATIONS, AuthHandling.MOCKED),
    (_BYPASS_AUTH_ANNOTATIONS, AuthHandling.BYPASSED),
]

_BYPASSED_CALL_SITE_RULES: tuple[_AuthCallSiteRule, ...] = _rules_from_method_map(
    AUTH_BYPASSED_STATIC_METHODS_BY_RECEIVER
)

_STATIC_MOCKED_CALL_SITE_RULES: tuple[_AuthCallSiteRule, ...] = _rules_from_method_map(
    AUTH_MOCKED_STATIC_METHODS_BY_RECEIVER
)

_STATIC_TEST_TOKEN_CALL_SITE_RULES: tuple[_AuthCallSiteRule, ...] = (
    _rules_from_method_map(AUTH_TEST_TOKEN_STATIC_METHODS_BY_RECEIVER)
)


def classify_auth_handling(
    class_annotations: list[ResolvedAnnotation],
    method_annotations: list[str],
    class_annotation_imports_by_class: dict[str, list[JImport]],
    method_imports: list[JImport],
    runtime_view: TestRuntimeView,
    receiver_resolver: RuntimeReceiverResolver,
) -> AuthHandlingDecision:
    signals: dict[str, list[str]] = {}

    # --- Tier 1: Annotations ---
    for expected_set, auth_label in _ANNOTATION_CHECKS:
        key = auth_label.value
        for resolved_annotation in class_annotations:
            for expected_annotation in expected_set:
                if annotation_matches_expected(
                    annotation=resolved_annotation.annotation,
                    expected_annotation=expected_annotation,
                    class_imports=class_annotation_imports_by_class.get(
                        resolved_annotation.declaring_class_name,
                        [],
                    ),
                ):
                    _add_signal(
                        signals,
                        key,
                        f"annotation:{resolved_annotation.annotation}",
                    )

        for annotation in method_annotations:
            for expected_annotation in expected_set:
                if annotation_matches_expected(
                    annotation=annotation,
                    expected_annotation=expected_annotation,
                    class_imports=method_imports,
                ):
                    _add_signal(signals, key, f"annotation:{annotation}")

    # --- Tier 1b: Custom security-context annotations (weak) ---
    # A custom @With* security annotation sets up an auth context we cannot map to
    # a specific strategy: record it as weak `unknown` evidence so it is not lost
    # to `none`. Strong @With* annotations are already handled above and skipped.
    for annotation in [
        resolved.annotation for resolved in class_annotations
    ] + method_annotations:
        if _is_custom_security_context_annotation(annotation):
            _add_signal(
                signals,
                AuthHandling.UNKNOWN.value,
                f"weak:annotation:{annotation}",
            )

    # --- Tier 2: Single runtime pass ---
    for event in runtime_view.iter_events():
        classification = event.node.http_classification
        call_site = event.call_site
        strong_signals_before = _count_strong_signals(signals)

        # 2a: HTTP classification signals
        if classification is not None:
            if classification.request_role == HttpRequestRole.EVENT:
                if _has_auth_route(classification.path):
                    _add_signal(
                        signals,
                        AuthHandling.REAL_FLOW.value,
                        f"route:{classification.path}",
                    )
            if classification.request_role in (
                HttpRequestRole.EVENT,
                HttpRequestRole.BUILDER,
            ):
                if _has_auth_credentials(classification):
                    _add_signal(
                        signals,
                        AuthHandling.TEST_TOKEN.value,
                        "header:authorization",
                    )

        # 2b: Receiver-gated call site signals
        receiver_type = receiver_resolver.resolve_for_event(
            event.owner, call_site
        ).receiver_type
        method_name = call_site.method_name or ""

        if _matches_any_rule(receiver_type, method_name, _MOCKED_CALL_SITE_RULES):
            _add_signal(
                signals,
                AuthHandling.MOCKED.value,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )
        elif _matches_any_rule(
            receiver_type,
            method_name,
            _STATIC_MOCKED_CALL_SITE_RULES,
        ):
            _add_signal(
                signals,
                AuthHandling.MOCKED.value,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )
        elif method_name in _HIGH_CONFIDENCE_MOCKED_METHODS:
            _add_signal(signals, AuthHandling.MOCKED.value, f"method:{method_name}")

        if _matches_any_rule(
            receiver_type,
            method_name,
            _BYPASSED_CALL_SITE_RULES,
        ):
            _add_signal(
                signals,
                AuthHandling.BYPASSED.value,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )

        if _matches_any_rule(receiver_type, method_name, _TEST_TOKEN_CALL_SITE_RULES):
            _add_signal(
                signals,
                AuthHandling.TEST_TOKEN.value,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )
        elif _matches_any_rule(
            receiver_type,
            method_name,
            _STATIC_TEST_TOKEN_CALL_SITE_RULES,
        ):
            _add_signal(
                signals,
                AuthHandling.TEST_TOKEN.value,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )

        if _matches_any_rule(receiver_type, method_name, _REAL_FLOW_CALL_SITE_RULES):
            _add_signal(
                signals,
                AuthHandling.REAL_FLOW.value,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )

        # 2c: Weak evidence — an unrecognized method on an auth-only receiver is
        # auth machinery we cannot categorize, not the absence of auth.
        if _count_strong_signals(
            signals
        ) == strong_signals_before and _matches_auth_specific_receiver(receiver_type):
            _add_signal(
                signals,
                AuthHandling.UNKNOWN.value,
                f"weak:auth-receiver:{receiver_type}.{method_name}",
            )

    # --- Resolve ---
    if not signals:
        return AuthHandlingDecision(label=AuthHandling.NONE.value)
    winner = next(label for label in _AUTH_PRECEDENCE if label.value in signals)
    return AuthHandlingDecision(label=winner.value, signals=signals)
