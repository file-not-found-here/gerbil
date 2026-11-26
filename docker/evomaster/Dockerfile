# EvoMaster's command line on the execution image, so a target's driver, its service and
# the search share one container that has no network.
FROM gerbil-execution

ARG EVOMASTER_VERSION=6.2.0
ARG EVOMASTER_SHA256=93f9c4a0de88b01395d2a1009776aa739973e9f64343778d2fd49c212fd17915

ADD --checksum=sha256:${EVOMASTER_SHA256} \
    https://github.com/WebFuzzing/EvoMaster/releases/download/v${EVOMASTER_VERSION}/evomaster.jar \
    /opt/evomaster/evomaster.jar

COPY with-driver.sh fetch-schema.sh /opt/evomaster/
