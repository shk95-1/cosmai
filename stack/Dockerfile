# The scheduled runner behind stack/crontab: `cosmai` on PATH plus the checkout db/migrate.sh reads.
#
# Built from the repo root, not from stack/: `docker build -f stack/Dockerfile -t cosmai .`
# (.dockerignore sits at that root because that is the only place docker reads it from).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# db/migrate.sh resolves contracts/ddl/needs/*.sql and db/views/*.sql against the working directory,
# and contracts/ has exactly one copy in this repo -- so the image carries the checkout rather than
# duplicating contract SQL into the wheel. tests/test_image_payload.py asserts the two agree.
WORKDIR /srv/cosmai
COPY . /srv/cosmai

# Outside the checkout on purpose: a bind mount over /srv/cosmai (a dev override) must not take the
# interpreter with it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

# --no-editable: the image runs the built wheel, so a file the build backend drops is missing here
# instead of being quietly served by the source tree the way `tool/checks/test` sees it.
RUN uv sync --frozen --no-dev --no-editable

# Not the entrypoint -- cron names `cosmai` (contracts/entrypoints.md) and the deploy calls
# db/migrate.sh; this only proves both resolve inside the image, at build time. scope_threshold()
# reads collectors/commerce/scope.json through site-packages, which is the read that broke in #1
# and that no editable-installed test run can reach.
RUN cosmai --help >/dev/null \
    && db/migrate.sh --help >/dev/null \
    && ls contracts/ddl/needs/*.sql >/dev/null \
    && (cd / && python -c 'from analysis.aggregate.ranking import scope_threshold; scope_threshold()')

CMD ["cosmai", "--help"]
