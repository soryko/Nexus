FROM ubuntu:24.04
ARG COMMIT
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common ca-certificates curl gpg-agent \
 && add-apt-repository -y ppa:git-core/ppa && apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
# A normal Ubuntu user: useradd -U gives the user a private group of the same name, with no other member.
RUN useradd -m -U -s /bin/bash dev
USER dev
ENV PATH=/home/dev/.local/bin:$PATH
WORKDIR /home/dev
RUN curl -LsSf "https://astral.sh/uv/0.11.21/install.sh" | sh
COPY --chown=dev:dev src.bundle /home/dev/src.bundle
RUN git clone -q src.bundle nexus && git -C nexus checkout -q "$COMMIT" && rm src.bundle
WORKDIR /home/dev/nexus
ENV UV_PYTHON=3.13.14
RUN /home/dev/.local/bin/uv python install 3.13.14 && /home/dev/.local/bin/uv sync --frozen
