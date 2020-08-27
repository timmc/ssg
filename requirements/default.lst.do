# Compile a .lst requirements file from a .spec dependencies file
#
# This will not work properly if only some of the .lst files have been
# removed; use `redo upgrade` rather than targeting one of these files
# directly.

redo-ifchange "$2.spec"

# Declare a redo dependency on any constraints (-c) and requirements
# (-r) targets, too. (Whether or not they need to be rebuilt.)
#
# Find the lines, strip down to the path, and mark them as dependencies
grep -h -P '^-[rc]\s+([^#]+?)\s*(#.*)$' "$2.spec" \
| sed 's/^-[rc]\s\+//' | sed 's/\s*\(#.*\)//' \
| sort | uniq \
| while read d; do
    redo-ifchange "$d"
done

export CUSTOM_COMPILE_COMMAND="make upgrade"
pip-compile --rebuild --upgrade -o $3 "$2.spec"
