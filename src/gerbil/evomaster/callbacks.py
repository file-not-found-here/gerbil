"""Give each class of a split EvoMaster suite the SSRF callback servers its tests probe.

EvoMaster 6.2.0 declares a callback server only in classes with an SSRF fault, though any
test probing for one uses it, and never stops it, so a later class in the same JVM
cannot bind its port.
"""

from __future__ import annotations

import re

from gerbil.execution.errors import ExecutionError

# Marks every addition, so a suite shows what is not EvoMaster's own.
MARKER = "// gerbil:"
_AS_EVOMASTER_WRITES = f"{MARKER} as in classes with an SSRF fault"
_SERVER = re.compile(r"\bhttpCallbackVerifier(\d+)\b")
_IMPORTS = (
    "import static com.github.tomakehurst.wiremock.client.WireMock.*;",
    "import  com.github.tomakehurst.wiremock.WireMockServer;",
    "import  com.github.tomakehurst.wiremock.common.Metadata;",
    "import  com.github.tomakehurst.wiremock.core.WireMockConfiguration;",
    "import  com.github.tomakehurst.wiremock.extension.responsetemplating"
    ".ResponseTemplateTransformer;",
)
_IMPORT = re.compile(r"^import .*;\n", re.M)
_BASE_URL = re.compile(r"^    private static String baseUrlOfSut;\n", re.M)
_TAG = '    private static final String SSRF_METADATA_TAG = "SSRF";'
_HELPER = "public static boolean verifierHasReceivedRequests("
_HELPER_METHOD = f"""\
    {_AS_EVOMASTER_WRITES}
    /**
    * Method to verify whether the HttpCallbackVerifier has received any requests.
    */
    {_HELPER}WireMockServer verifier, String actionName) {{
        return verifier
            .getAllServeEvents()
            .stream().filter( r -> r.getWasMatched() && r.getStubMapping().getMetadata() != null)
            .anyMatch( r -> r.getStubMapping().getMetadata().getString(SSRF_METADATA_TAG).equals(actionName));
    }}
"""


def own_callback_servers(source: str) -> str:
    """The class's source with each callback server it probes declared and started as
    in a class with an SSRF fault, and stopped when the class ends."""
    for port in sorted({int(port) for port in _SERVER.findall(source)}):
        name = f"httpCallbackVerifier{port}"
        if f"private static WireMockServer {name};" not in source:
            source = _declare(source, name, port)
        if f"{name}.stop();" not in source:
            source = _insert(
                source,
                _method_end(source, "tearDown"),
                f"        {name}.stop(); {MARKER} frees its port for the next class\n",
            )
    return source


def _declare(source: str, name: str, port: int) -> str:
    lines = source.splitlines()
    missing = [line for line in _IMPORTS if line not in lines]
    if missing:
        imports = list(_IMPORT.finditer(source))
        if not imports:
            raise ExecutionError("a class probing for SSRF has no imports")
        added = "".join(f"{line}\n" for line in (_AS_EVOMASTER_WRITES, *missing))
        source = _insert(source, imports[-1].end(), added)
    base_url = _BASE_URL.search(source)
    if base_url is None:
        raise ExecutionError("a class probing for SSRF has no baseUrlOfSut")
    fields = f"    {_AS_EVOMASTER_WRITES}\n    private static WireMockServer {name};\n"
    if _TAG not in lines:
        fields += f"{_TAG}\n"
    source = _insert(source, base_url.end(), fields)
    source = _insert(
        source,
        _method_end(source, "initClass"),
        f"        {_AS_EVOMASTER_WRITES}\n"
        f"        {name} = new WireMockServer(new WireMockConfiguration()\n"
        f"            .port({port})\n"
        "            .extensions(new ResponseTemplateTransformer(false)));\n"
        f"        {name}.start();\n"
        f"        assertNotNull({name});\n",
    )
    if _HELPER not in source:
        source = _insert(source, source.rstrip().rfind("}"), _HELPER_METHOD)
    return source


def _method_end(source: str, method: str) -> int:
    """Where the closing brace of the class's static `method()` begins."""
    match = re.search(
        rf"^    public static void {method}\(\) \{{\n(?:.*\n)*?(    \}}\n)",
        source,
        re.M,
    )
    if match is None:
        raise ExecutionError(f"a class probing for SSRF has no {method}()")
    return match.start(1)


def _insert(source: str, index: int, text: str) -> str:
    return f"{source[:index]}{text}{source[index:]}"
