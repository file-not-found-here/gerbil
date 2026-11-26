# Shared image for running Gerbil-selected test suites under JaCoCo and PIT.
FROM eclipse-temurin:11-jdk-jammy AS jdk11
FROM eclipse-temurin:17-jdk-jammy AS jdk17
FROM eclipse-temurin:21-jdk-jammy

ARG MAVEN_VERSION=3.9.11
ARG JACOCO_VERSION=0.8.16
ARG PITEST_JUNIT5_VERSION=1.2.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*

# A PostgreSQL server for services that run on nothing else: their EvoMaster drivers
# start a throwaway cluster, so their generated suites need no Docker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql \
    && rm -rf /var/lib/apt/lists/*

COPY --from=jdk11 /opt/java/openjdk /opt/java/jdk11
COPY --from=jdk17 /opt/java/openjdk /opt/java/jdk17
RUN ln -s /opt/java/openjdk /opt/java/jdk21

RUN curl -fsSL "https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
        | tar -xz -C /opt \
    && ln -s "/opt/apache-maven-${MAVEN_VERSION}/bin/mvn" /usr/local/bin/mvn

WORKDIR /opt/gerbil
COPY tools tools
RUN central=https://repo1.maven.org/maven2 \
    && mkdir -p lib/jacoco lib/pitest-junit5 \
    && curl -fsSL -o lib/jacoco/jacocoagent.jar \
        "$central/org/jacoco/org.jacoco.agent/${JACOCO_VERSION}/org.jacoco.agent-${JACOCO_VERSION}-runtime.jar" \
    && curl -fsSL -o lib/jacoco/jacococli.jar \
        "$central/org/jacoco/org.jacoco.cli/${JACOCO_VERSION}/org.jacoco.cli-${JACOCO_VERSION}-nodeps.jar" \
    && curl -fsSL -o lib/pitest-junit5/pitest-junit5-plugin.jar \
        "$central/org/pitest/pitest-junit5-plugin/${PITEST_JUNIT5_VERSION}/pitest-junit5-plugin-${PITEST_JUNIT5_VERSION}.jar" \
    && mvn -B -q -f tools/pitest/pom.xml dependency:copy-dependencies \
        -DincludeScope=runtime -DoutputDirectory=/opt/gerbil/lib/pitest \
    && mvn -B -q -f tools/junit4/pom.xml dependency:copy-dependencies \
        -DincludeScope=runtime -DoutputDirectory=/opt/gerbil/lib/junit4 \
    && rm -rf /root/.m2

COPY GerbilLauncher.java GerbilMutantFilter.java gerbil.gradle ./
RUN javac --release 8 -nowarn -cp 'lib/junit4/*' -d lib/launcher GerbilLauncher.java
# PIT finds the filter through the service file, which names its factory.
RUN javac --release 11 -cp 'lib/pitest/*' -d lib/mutant-filter GerbilMutantFilter.java \
    && mkdir -p lib/mutant-filter/META-INF/services \
    && echo GerbilMutantFilter \
        > lib/mutant-filter/META-INF/services/org.pitest.mutationtest.build.MutationInterceptorFactory
