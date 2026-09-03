Korean literals the tool tests still need live here, not in the `.py` files.

`tool/checks/lang` rejects a staged Hangul line outside its allowlist, and this directory is on
that allowlist: the migration-window tests have to feed the tools real Korean anchors, and a test
file cannot carry them once the check is wired. The follow-up of shk95-1/cosmai#192 step 4 drops
the anchors and most of this directory with them.
