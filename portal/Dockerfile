# Bakes the static pages straight into the nginx image. Having no build step is this
# project's nature — no bundler, no transpiler, and public/ is exactly what the browser
# runs (the same reason as data-portal/Dockerfile, rewritten here rather than touching that repo).
FROM nginx:alpine

# The default document root. index.html (metrics)·ops.html (ops, #139)·map.html (structure, #142) and the
# js·css they call sit flat here (why './query.js' imports resolve against the web root). The three pages
# share style.css and query.js but each boots on its own -- opening only the ops page must never also fetch
# metrics, and opening only the structure page must never fetch metrics or ops either.
COPY public/ /usr/share/nginx/html/

# This container has neither DB credentials nor a backend — the browser calls PostgREST (3000) directly.
EXPOSE 80
