# Milestone 8 Docker staging images (docs/ops/STAGING.md; Master Architecture section 43.1).
#
#   base    Ubuntu 24.04 (the CI digest) + systemd + exactly what P1's frozen `provision.sh packages` installs, run at
#           build time (the only step with network access), + Poppler 24.02.0 (HB-4's pin; provision.sh installs no
#           Poppler) + the package manifest. The cluster apt creates on install is dropped, so `provision.sh postgres`
#           creates it at provisioning time with data checksums (its pg_createcluster branch).
#   host    base + the exported, unmodified commit at /opt/cse/app (root-owned, no group/world write); systemd as PID 1.
#   runner  host + python3-pytest, locales and the unprivileged user `tester` (CI's harness); test tooling never goes
#           into the host image.
#
# Built by ops/staging/staging.sh from a context holding app/ (git archive of the commit, LF) and this directory's
# files. Nothing secret enters any layer: the off-site password is copied into a running container (/run) only.
ARG BASE=ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3

FROM ${BASE} AS base
ENV DEBIAN_FRONTEND=noninteractive container=docker
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends systemd systemd-sysv dbus ca-certificates \
    && systemctl mask getty.target console-getty.service systemd-logind.service systemd-udevd.service \
       systemd-udev-trigger.service systemd-firstboot.service \
    && mkdir -p /var/log/journal
COPY app/ops/provision/provision.sh /opt/cse-staging/provision.sh
RUN bash /opt/cse-staging/provision.sh packages \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends poppler-utils \
    && pdftotext -v 2>&1 | head -1 | grep -qx "pdftotext version 24.02.0" \
    && if pg_lsclusters -h | grep -q "^17 main"; then pg_dropcluster 17 main; fi \
    && rm -rf /var/lib/apt/lists/* /opt/cse-staging/provision.sh \
    && mkdir -p /usr/share/cse-staging \
    && dpkg-query -W -f '${Package}\t${Version}\t${Architecture}\n' | sort > /usr/share/cse-staging/packages.txt
COPY init.sh /usr/local/sbin/cse-staging-init
RUN chmod 0755 /usr/local/sbin/cse-staging-init

FROM base AS host
ARG REVISION=unknown
LABEL org.opencontainers.image.revision=${REVISION} cse.staging.role=host
# A tar, not a directory: Docker Desktop on Windows makes every file copied from a Windows directory executable, and
# ADD of a local tar keeps git's own modes (ST-02 checks blob ids, kinds and modes against `git ls-tree`).
ADD app.tar /opt/cse/app/
RUN chown -R root:root /opt/cse/app && chmod -R go-w /opt/cse/app && echo "${REVISION}" > /usr/share/cse-staging/revision
STOPSIGNAL SIGRTMIN+3
CMD ["/usr/local/sbin/cse-staging-init"]

FROM host AS runner
LABEL cse.staging.role=runner
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends python3-pytest locales \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m tester
COPY run_group.sh /usr/local/bin/cse-staging-run-group
RUN chmod 0755 /usr/local/bin/cse-staging-run-group
STOPSIGNAL SIGTERM
CMD ["bash", "-c", "echo 'runner: use cse-staging-run-group <group>'; exit 2"]
