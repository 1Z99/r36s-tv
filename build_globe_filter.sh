#!/usr/bin/env bash
set -euo pipefail

if (( $# > 1 )); then
    printf 'Usage: bash %s [output.so]\n' "$0" >&2
    exit 2
fi
root="$(dirname -- "$(realpath -- "$0")")"
output="${1:-$root/native/libtvglobe.so}"
if [[ ! -d "$(dirname -- "$output")" ]]; then
    printf 'Output parent directory does not exist: %s\n' "$output" >&2
    exit 2
fi
"${CC:-gcc}" -std=c99 -O3 -Wall -Wextra -Werror -fPIC -shared \
    "$root/globe_filter.c" -o "$output" -lm
