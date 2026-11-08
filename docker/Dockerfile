FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      qemu-user-static qemu-system-arm \
      gdb-multiarch \
      binutils-arm-linux-gnueabihf gcc-arm-linux-gnueabihf \
      libc6-dev-armhf-cross libc6-armhf-cross \
      python3 python3-pip python3-pyelftools \
      tshark tcpdump netcat-openbsd socat \
      strace ltrace file xxd vim less git curl ca-certificates \
      iproute2 net-tools procps cpio unzip openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

ARG GHIDRA_VER=11.3.2
ARG GHIDRA_DATE=20250415
RUN curl -fsSL -o /tmp/ghidra.zip \
      "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VER}_build/ghidra_${GHIDRA_VER}_PUBLIC_${GHIDRA_DATE}.zip" \
    && unzip -q /tmp/ghidra.zip -d /opt && rm /tmp/ghidra.zip \
    && mv /opt/ghidra_${GHIDRA_VER}_PUBLIC /opt/ghidra \
    && sed -i "s|^JAVA_HOME_OVERRIDE=.*|JAVA_HOME_OVERRIDE=/usr/lib/jvm/java-21-openjdk-amd64|" /opt/ghidra/support/launch.properties

RUN pip3 install --no-cache-dir --break-system-packages pwntools capstone

ENV PATH="/opt/ghidra/support:${PATH}"
WORKDIR /work
CMD ["/bin/bash"]
