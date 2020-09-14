# Install requirements used for development

[ -d "$VIRTUAL_ENV" ] || {
  echo "Not in virtualenv!" >&2
  exit 1
}

redo-ifchange dev.lst
pip-sync pip-tools.lst dev.lst >&2

# Stamp *after* changes are made to virtualenv
redo-ifchange .venv-stamp
