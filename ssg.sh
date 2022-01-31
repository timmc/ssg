#!/bin/bash
# Run static site generator

set -eu -o pipefail

DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Activate a virtualenv, possibly creating it first
pyver=3.7
venv="$DIR/venv-$pyver"
[ -d "$venv" ] || python$pyver -m venv "$venv"
. "$venv"/bin/activate

# Compute any dependency changes if spec files have changed
(cd -- "$DIR"; redo-ifchange requirements/upgrade)
# Install dependencies if needed
(cd -- "$DIR"; redo-ifchange requirements/install)

PYTHONPATH="$DIR" python -m ssg "$@"
