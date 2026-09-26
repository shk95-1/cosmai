# ownership — one active development and deployment repository

## Active owner

```ownership:active-repositories
shk95-1/cosmai
```

This repository owns every module, contract, guard and new migration. Production database actions
are performed by the coordinator from the primary checkout only (`STATE.md` §3). Issue branches
and same-channel waves remain the development model; there is no cross-repository wave or sync duty.

## Migration numbers and provenance

New DDL uses one sequence per schema: choose an unused number greater than every existing migration
in that schema. Historical files retain their names and contents; the former 020 boundary no longer
allocates ownership. DDL remains additive under `contracts/versioning.md` and the existing guard.

The temporary `shk95/cosmai-import-ydc` fork is retired as an executable queue. Its main and branch
history has been absorbed; keep the checkout, issue dispositions and imported module lineage as
historical inputs. There are no fork-owned paths or upstream-only guards within this repository.
Fully qualified issue closures remain required. `tool/checks/foreign-closes` is a manual import
check, not a recurring fork audit.

## Historical boundary

The former number blocks, ownership lists and shared-surface sync rules were introduced under
#192 after incidents #150, #103 and #115. Their complete text remains in Git history before #322.
The current rule removes that partition, not the secrets, additive-DDL, data-lineage, verification
or deployment protections it accompanied. Historical DDL and the YDC import pin remain immutable.

Archived repositories remain read-only development inputs. The user's #181 restoration includes
local runtime copies of the four legacy viewing services and the independent Run package; running
those services does not reopen archived development queues or authorize pushes to those repositories.
