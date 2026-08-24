# The scheduled runner behind stack/crontab.d/: `cosmai` on PATH plus the checkout db/migrate.sh reads.
#
# Built from the repo root, not from stack/, and both steps at once with `tool/stack-build`:
#     docker build -f stack/Dockerfile -t cosmai-needs:local .
# (.dockerignore sits at that root because that is the only place docker reads it from.)
#
# The tag is `cosmai-needs:local`, not `cosmai`. On the deploy host `cosmai` is already the archived
# fleet's app image and three shared-db containers are running it -- building over that tag would
# take them with it, and a `compose build` that skipped this step would silently use it as the base.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# db/migrate.sh resolves contracts/ddl/needs/*.sql and db/views/*.sql against the working directory,
# and contracts/ has exactly one copy in this repo -- so the image carries the checkout rather than
# duplicating contract SQL into the wheel. tests/test_image_payload.py asserts the two agree.
WORKDIR /srv/cosmai
COPY . /srv/cosmai

# Outside the checkout on purpose: a bind mount over /srv/cosmai (a dev override) must not take the
# interpreter with it. PLAYWRIGHT_BROWSERS_PATH is outside any home directory for the same kind of
# reason: the browsers are installed as root and have to stay readable to whoever the container ends
# up running as.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# --no-editable: the image runs the built wheel, so a file the build backend drops is missing here
# instead of being quietly served by the source tree the way `tool/checks/test` sees it.
RUN uv sync --frozen --no-dev --no-editable

# Chromium for oliveyoung, whose ranking answers plain HTTP with a Cloudflare wall
# (collectors/commerce/transport/browser.py). In the base image and not in stack/Dockerfile.cron,
# because this is the image the deploy checks and the one `docker compose run --rm` uses for a
# one-off walk -- a browser only the scheduler layer had would be missing from both. `uv sync`
# installs the playwright *package*; the browser is a separate download, and leaving it to first use
# turns a 03:00 collection into a 150 MB fetch inside a container that may have no egress. The
# scheduler layer runs its own `apt-get update` afterwards, so the list removed here costs it
# nothing. --with-deps is what brings the shared libraries the slim base does not carry.
RUN playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* \
 && chmod -R a+rX /ms-playwright

# Not the entrypoint -- cron names `cosmai` (contracts/entrypoints.md) and the deploy calls
# db/migrate.sh; this only proves both resolve inside the image, at build time. scope_threshold()
# reads collectors/commerce/scope.json through site-packages, which is the read that broke in #1
# and that no editable-installed test run can reach.
RUN cosmai --help >/dev/null \
    && db/migrate.sh --help >/dev/null \
    && ls contracts/ddl/needs/*.sql >/dev/null \
    && (cd / && python -c 'from analysis.aggregate.ranking import scope_threshold; scope_threshold()') \
    && (cd / && python -c 'from collectors.commerce.transport import HttpFetcher, LiveFetchers')

# The browser half of that check, kept apart because it is the one that costs a driver start. It
# asks playwright itself where chromium is and whether that file can be executed -- not whether the
# package imports, which it does just as happily with no browser behind it. Without this the image
# builds green and the only source that needs a browser reports blocked at 03:00 for a reason that
# has nothing to do with the site.
RUN cd / && python -c 'import os, sys; from playwright.sync_api import sync_playwright; p = sync_playwright().start(); exe = p.chromium.executable_path; p.stop(); sys.exit(0 if os.access(exe, os.X_OK) else f"no runnable chromium at {exe}")'

CMD ["cosmai", "--help"]
