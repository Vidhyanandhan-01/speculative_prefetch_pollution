#!/usr/bin/env bash
# Apply champsim_custom's patches to a freshly-cloned/reset champsim/ submodule.
# Run from the repo root, or anywhere -- paths below are relative to this script.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../champsim"
git apply --check ../champsim_custom/patches/ooo_cpu_branch_instrumentation.patch
git apply ../champsim_custom/patches/ooo_cpu_branch_instrumentation.patch
echo "Applied ooo_cpu_branch_instrumentation.patch to champsim/src/ooo_cpu.cc"
