# Compute a stamp of the virtualenv dir's file tree metadata.
#
# This is a "phony" target that doesn't actually produce a file,
# and it will be computed each time.
#
# File attributes used in stamp, to match redo's own general behavior:
# - %t: mtime
# - %s: size
# - %i: inode number
# - %m: file mode
# - %U and %G: owner uid and gid
# - %P: path
#
# Null byte is used as delimiter for best generality but it doesn't really matter.
#
# Reference:
# - https://github.com/apenwarr/redo/blob/670abbe305341e8c160418e7a80c3b6b396e8486/redo/state.py#L450

[ -d "$VIRTUAL_ENV" ] || {
  echo "Not in virtualenv!" >&2
  exit 1
}

find "$VIRTUAL_ENV" -printf '%t %s %i %m %U %G %P\0' | redo-stamp
redo-always
