FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive TZ=Asia/Seoul
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 procps iproute2 ca-certificates tzdata shellcheck \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 analyst
WORKDIR /work
USER analyst
CMD ["bash"]
