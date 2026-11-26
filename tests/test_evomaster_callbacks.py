from __future__ import annotations

from difflib import SequenceMatcher

import pytest

from gerbil.evomaster.callbacks import MARKER, own_callback_servers
from gerbil.execution.errors import ExecutionError

# Trimmed from Data Flow's 1-hour suite, split at 500 tests per class.
_IMPORTS = """\
package em.generated;

import  org.junit.jupiter.api.AfterAll;
import static org.junit.jupiter.api.Assertions.*;
import  io.restassured.config.HttpClientConfig;
{wiremock}import  io.restassured.config.JsonConfig;
import static org.hamcrest.Matchers.*;
"""
_WIREMOCK = """\
import static com.github.tomakehurst.wiremock.client.WireMock.*;
import  com.github.tomakehurst.wiremock.WireMockServer;
import  com.github.tomakehurst.wiremock.common.Metadata;
import  com.github.tomakehurst.wiremock.core.WireMockConfiguration;
import  com.github.tomakehurst.wiremock.extension.responsetemplating.ResponseTemplateTransformer;
"""
_FIELDS = """\
    private static WireMockServer httpCallbackVerifier44399;
    private static final String SSRF_METADATA_TAG = "SSRF";
"""
_START = """\
        httpCallbackVerifier44399 = new WireMockServer(new WireMockConfiguration()
            .port(44399)
            .extensions(new ResponseTemplateTransformer(false)));
        httpCallbackVerifier44399.start();
        assertNotNull(httpCallbackVerifier44399);

"""
_HELPER = """\
    /**
    * Method to verify whether the HttpCallbackVerifier has received any requests.
    */
    public static boolean verifierHasReceivedRequests(WireMockServer verifier, String actionName) {
        return verifier
            .getAllServeEvents()
            .stream().filter( r -> r.getWasMatched() && r.getStubMapping().getMetadata() != null)
            .anyMatch( r -> r.getStubMapping().getMetadata().getString(SSRF_METADATA_TAG).equals(actionName));
    }
"""
_CLASS = """\
public class EvoMaster_spring_cloud_dataflow_16_Test {{


    private static final SutHandler controller = new em.embedded.dataflow.EmbeddedEvoMasterController();
    private static String baseUrlOfSut;
{fields}

    @BeforeAll
    public static void initClass() {{
        controller.setupForGeneratedTest();
        baseUrlOfSut = controller.startSut();
        RestAssured.config = RestAssured.config()
            .redirect(redirectConfig().followRedirects(false));
{start}    }}


    @AfterAll
    public static void tearDown() {{
        controller.stopSut();
    }}


    @BeforeEach
    public void initTest() {{
        controller.resetStateOfSUT();
    }}



{helper}
    @Test @Timeout(60)
    public void test_202_getOnStreamReturnsMismatchResponseWithSchema() throws Exception {{

        assertNotNull(httpCallbackVerifier44399.isRunning());

        httpCallbackVerifier44399.resetAll();
        httpCallbackVerifier44399.stubFor(
            get("/EM_SSRF_1")
                .withMetadata(Metadata.metadata().attr(SSRF_METADATA_TAG, "POST:/apps"))
        );
        assertFalse(verifierHasReceivedRequests(httpCallbackVerifier44399, "POST:/apps"));
    }}

    @Test @Timeout(60)
    public void test_262_optionsOnScaleInstancReturns200() throws Exception {{

        given().accept("*/*")
                .options(baseUrlOfSut + "/scale");
    }}

}}
"""


def _source(with_fault: bool, probing: bool = True) -> str:
    source = _IMPORTS.format(wiremock=_WIREMOCK if with_fault else "") + _CLASS.format(
        fields=_FIELDS if with_fault else "",
        start=_START if with_fault else "",
        helper=_HELPER if with_fault else "",
    )
    if not probing:
        start = source.index("    @Test @Timeout(60)\n    public void test_202")
        end = source.index("    @Test @Timeout(60)\n    public void test_262")
        source = source[:start] + source[end:]
    return source


def _between(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end)]


def test_a_probing_class_without_an_ssrf_fault_gets_evomasters_own_setup() -> None:
    owned = own_callback_servers(_source(with_fault=False))

    lines = owned.splitlines()
    for setup in (_WIREMOCK, _FIELDS, _START, _HELPER):
        assert [line for line in setup.splitlines() if line.strip()] == [
            line for line in setup.splitlines() if line.strip() and line in lines
        ]
    assert "httpCallbackVerifier44399.start();" in _between(
        owned, "initClass()", "tearDown()"
    )
    assert owned.endswith(f"{_HELPER}}}\n")


def test_every_class_stops_its_callback_servers_when_it_ends() -> None:
    for with_fault in (True, False):
        owned = own_callback_servers(_source(with_fault))

        teardown = _between(owned, "tearDown()", "@BeforeEach")
        assert "controller.stopSut();\n" in teardown
        assert "httpCallbackVerifier44399.stop();" in teardown


def test_a_class_with_an_ssrf_fault_only_gains_the_stop() -> None:
    source = _source(with_fault=True)

    owned = own_callback_servers(source)

    added = [line for line in owned.splitlines() if line not in source.splitlines()]
    assert added == [
        f"        httpCallbackVerifier44399.stop(); {MARKER} frees its port for the "
        "next class"
    ]


def test_gerbil_only_adds_blocks_it_marks() -> None:
    source = _source(with_fault=False).splitlines()

    owned = own_callback_servers("\n".join(source) + "\n").splitlines()

    changes = [
        (tag, owned[start:end])
        for tag, _, _, start, end in SequenceMatcher(
            None, source, owned, autojunk=False
        ).get_opcodes()
        if tag != "equal"
    ]
    # The imports, fields, start, stop and helper.
    assert len(changes) == 5
    assert all(tag == "insert" and MARKER in lines[0] for tag, lines in changes)


def test_owning_callback_servers_is_idempotent() -> None:
    for with_fault in (True, False):
        owned = own_callback_servers(_source(with_fault))

        assert own_callback_servers(owned) == owned


def test_a_class_that_never_probes_is_unchanged() -> None:
    source = _source(with_fault=False, probing=False)

    assert own_callback_servers(source) == source


def test_a_probing_class_of_an_unknown_shape_is_reported() -> None:
    source = _source(with_fault=False).replace("tearDown()", "stopAll()")

    with pytest.raises(ExecutionError, match="tearDown"):
        own_callback_servers(source)
