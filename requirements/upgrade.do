# Rebuild all .lst files whether they need it or not

# Handle pip-tools specially, since the result of upgrading it can
# affect dependency resolution. Make sure we converge on a pip-tools
# version that no longer changes its mind about what pip-tools to
# install.
cp pip-tools.lst pip-tools.lst.tmp-old
ok=
for i in $(seq 5); do
    redo-ifchange install-bootstrap
    rm pip-tools.lst
    redo pip-tools.lst

    if diff pip-tools.lst pip-tools.lst.tmp-old >/dev/null; then
        ok=yes # converged!
        break
    fi
    echo "pip-tools.lst changed, reupgrading it (try #$i)" >&2
    cp pip-tools.lst pip-tools.lst.tmp-old
done

if [ "$ok" = "" ]; then
    echo "$0: fatal: pip-tools.lst did not converge!" >&2
    exit 1
fi
rm pip-tools.lst.tmp-old


# Find all .lst files with corresponding .spec files (except pip-tools)
normal_output_files() {
    for input in *.spec; do
        # pip-tools is handled specially
        if [ "$input" != "pip-tools.spec" ]; then
            echo "${input%.spec}.lst"
        fi
    done
}

# Delete the outputs and rebuild them all as a batch
rm -f -- `normal_output_files`
redo-ifchange `normal_output_files`
