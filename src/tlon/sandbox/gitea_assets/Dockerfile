FROM ubuntu:22.04
# Fixed CLI releases; credentials are supplied only when an execution unit starts.
RUN apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 update \
 && apt-get install -y --no-install-recommends ca-certificates curl git=1:2.34.1-1ubuntu1.18 \
 && rm -rf /var/lib/apt/lists/*
RUN arch="$(dpkg --print-architecture)" \
 && case "$arch" in \
      amd64) checksum=9c142c8a0b31485eea2387031c1c404f12b7141075eb4ffbd7ced49441590636 ;; \
      arm64) checksum=0e14a036322eeb1fc55e266acafeb8490e523ed99f9232034ae74861e2339879 ;; \
      *) echo 'Unsupported architecture' >&2; exit 1 ;; \
    esac \
 && curl --fail --location --retry 2 --max-time 180 "https://gitea.com/gitea/tea/releases/download/v0.16.0/tea-0.16.0-linux-${arch}" -o /usr/local/bin/tea \
 && echo "$checksum  /usr/local/bin/tea" | sha256sum --check \
 && chmod 755 /usr/local/bin/tea
COPY git-credential-tlon /usr/local/bin/git-credential-tlon
RUN chmod 755 /usr/local/bin/git-credential-tlon
