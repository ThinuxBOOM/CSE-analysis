# The Linux test environment for CI (docs/CI_TEST_STRATEGY.md): Ubuntu 24.04 with Ubuntu's own Python 3.12, pytest,
# psycopg2 and requests packages, PostgreSQL 17 from the PostgreSQL project's apt repository (PGDG), and restic (the P1
# off-site test). It is the recipe of the local image `cse-p1-test` that the earlier R-F4 and Phase 2 evidence ran in
# (docs/R_F4_IMPLEMENTATION.md section 5.1), now tracked so that CI rebuilds the same environment. The base image is
# pinned by digest; apt resolves current package versions at build time, and every CI run records the versions it got
# (environment.txt in its artifact).
#
# Added for CI (not in that image): Ubuntu 24.04's poppler-utils, which is Poppler 24.02.0, the version F4 and HB-4 pin
# (worker/pdf_words.py SUPPORTED_POPPLER_VERSIONS, worker/backfill_documents POPPLER_VERSION). The build fails unless
# pdftotext reports exactly 24.02.0, so a different Poppler can never be used silently. With it, the tests gated on the
# pinned Poppler execute instead of skipping (docs/CI_TEST_STRATEGY.md).
#
#   docker build -t cse-ci-linux -f .github/ci/linux.Dockerfile .github/ci      (needs network: Ubuntu and PGDG apt)
#
# Tests then run in a container started with --network none, as the unprivileged user `tester` (initdb refuses root).
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ca-certificates curl gnupg postgresql-common \
    && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
    && printf "create_main_cluster = false\n" >> /etc/postgresql-common/createcluster.conf \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends postgresql-17 python3 python3-psycopg2 \
       python3-requests python3-pytest restic locales poppler-utils \
    && rm -rf /var/lib/apt/lists/*
RUN pdftotext -v 2>&1 | head -1 | grep -qx "pdftotext version 24.02.0" \
    || { echo "poppler-utils is not Poppler 24.02.0: $(pdftotext -v 2>&1 | head -1)" >&2; exit 1; }
RUN useradd -m tester
