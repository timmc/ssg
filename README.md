# Static site generator for blog

## Usage

Run `ssg.sh generate`. Requires Python3 and redo.

If you need to upgrade Python dependencies, activate the virtualenv
with `source venv-3.9/bin/activate` and run `redo requirements/upgrade`.
If things break, try `pip install -U pip-tools pip` before upgrading;
pip and pip-tools tend to get out of sync.

Post attachments can be placed in a directory called `attach` under
each post directory. They'll be hardlinked into the published
directory. In `markdown-v1` posts, the template string `{{attach_url}}` will be replaced
with an HTML-escaped absolute URL path to the published directory
(without a trailing slash.)

Drafts will be rendered to a non-synced, non-versioned `/blog/draft`
directory.

## Developer notes

In general, this script tries hard to avoid touching any output file
unless its content or metadata is changing. This allows rsync-based
publishing to be as fast as possible.

Generation code calls the `write_and_record` function when writing any
file, which only writes if needed and then records it in a
list. Anything written without using this mechanism will be deleted at
the end of generation.
