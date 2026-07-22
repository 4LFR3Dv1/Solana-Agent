FROM python:3.11.9-slim-bullseye@sha256:fe8df05de0fe659668a0b6fa204c652092e3e96832a9c7d6f8cc1e0e03e663d8 AS python-runtime

FROM ubuntu:22.04@sha256:1ec65b2719518e27d4d25f104d93f9fac60dc437f81452302406825c46fcc9cb

ARG DEBIAN_FRONTEND=noninteractive
ARG RUST_VERSION=1.97.1
ARG AGAVE_VERSION=v4.1.2
ARG ANCHOR_VERSION=v1.1.2
ARG NODE_VERSION=v22.22.3
ARG NODE_SHA256=2e5d13569282d016861fae7c8f935e741693c269101a5bebcf761a5376d1f99f
ARG PNPM_VERSION=10.28.0
ARG DEVNET_POW_VERSION=0.1.4

ENV HOME=/root
ENV CARGO_HOME=/opt/cargo
ENV RUSTUP_HOME=/opt/rustup
ENV PATH=/opt/node/bin:/opt/cargo/bin:/root/.local/share/solana/install/active_release/bin:${PATH}
ENV PNPM_HOME=/opt/pnpm
ENV PATH=/opt/pnpm:${PATH}

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update -qq \
    && apt-get install -qq --no-install-recommends \
        build-essential ca-certificates curl git jq libssl-dev libudev-dev \
        libbz2-1.0 libdb5.3 libexpat1 libgdbm-compat4 libgdbm6 liblzma5 \
        libncursesw6 libreadline8 libsqlite3-0 libuuid1 pkg-config xz-utils zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-runtime /usr/local /usr/local
COPY --from=python-runtime /usr/lib/*/libcrypto.so.1.1 /usr/lib/
COPY --from=python-runtime /usr/lib/*/libffi.so.7* /usr/lib/
COPY --from=python-runtime /usr/lib/*/libssl.so.1.1 /usr/lib/
RUN ldconfig \
    && python3 --version | grep -F "Python 3.11.9" \
    && python3 -c "import bz2, ctypes, dbm.gnu, lzma, readline, sqlite3, ssl, uuid, zlib; print(ssl.OPENSSL_VERSION, sqlite3.sqlite_version)"

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup.sh \
    && sh /tmp/rustup.sh -y --no-modify-path --default-toolchain "${RUST_VERSION}" --profile minimal \
    && rustup component add rustfmt clippy \
    && rm /tmp/rustup.sh

RUN curl --proto '=https' --tlsv1.2 -sSf \
        "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" \
        -o /tmp/node.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum --check --strict \
    && mkdir -p /opt/node \
    && tar -xJf /tmp/node.tar.xz --strip-components=1 -C /opt/node \
    && corepack enable \
    && corepack prepare "pnpm@${PNPM_VERSION}" --activate \
    && rm /tmp/node.tar.xz

RUN sh -c "$(curl -sSfL https://release.anza.xyz/${AGAVE_VERSION}/install)" \
    && solana --version \
    && solana-test-validator --version

RUN cargo install \
        --git https://github.com/otter-sec/anchor \
        --tag "${ANCHOR_VERSION}" \
        anchor-cli \
        --locked \
    && anchor --version

# Solana's official faucet guide documents this maintained proof-of-work
# fallback for periods when public RPC airdrops are rate limited.
RUN cargo install devnet-pow --version "${DEVNET_POW_VERSION}" --locked \
    && devnet-pow --version

COPY toolchain.lock.json /opt/solana-agent/toolchain.lock.json
COPY environment/verify-toolchain.py /opt/solana-agent/verify-toolchain.py
RUN python3 /opt/solana-agent/verify-toolchain.py /opt/solana-agent/toolchain.lock.json

WORKDIR /workspace
CMD ["bash"]
