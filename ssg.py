"""
Static site generator. Run with ssg.sh.
"""

import click
import datetime
import dateutil.tz
import ftfy
import html
import itertools
import json
import markdown
import os
from os import path
import re
import shutil
import sys
import xml.etree.ElementTree as ET

#### Settings

posts_src_dir = "/home/timmc/www/bof/blog-posts"
static_src_dir = "/home/timmc/www/bof/blog-posts"

gen_root = "/home/timmc/www/bof/public/blog"
base_path = "/blog" # Absolute path for most cases
base_authority = "https://www.brainonfire.net" # for a few links

site_title = "Brain on Fire"
site_subtitle = "Tim McCormack says words"


#### CLI

# Commands later hook into this as @cli.command()
@click.group()
def cli():
    pass


##### Utilities


def log(msg):
    """Log messages to STDERR."""
    print(str(msg), file=sys.stderr)


def update_value(dictionary, key, fn):
    """
    If the key is in the dictionary, call fn with the value and store that back.
    """
    if key in dictionary:
        dictionary[key] = fn(dictionary[key])


#### Loading


def list_post_dirs():
    """
    Generator yielding all post directories (as paths) that contain
    an index file.
    """
    for post_dirname in os.listdir(posts_src_dir):
        post_dir = path.join(posts_src_dir, post_dirname)
        index_file = path.join(post_dir, "index.md")
        if path.isfile(index_file):
            yield post_dir


re_comment_file_name = re.compile(r'^comment_(?P<type>[a-z]+)_(?P<id>[0-9]+)\.md$')


def list_comments_for_post(post_dir):
    """
    Generator yielding paths to comment files in a post's dir.
    """
    for fp in os.listdir(post_dir):
        m = re_comment_file_name.match(fp)
        if m is None:
            continue
        comment_path = path.join(post_dir, fp)
        yield comment_path


fm_sep = '---'
# Consume the newline following the separator as well -- it's not part
# of the content.
fm_sep_re = re.compile('^' + re.escape(fm_sep) + '\n', re.MULTILINE)


def split_front_matter(file_path):
    """
    Return parsed JSON and post/comment content as data/string tuple, or None
    if could not split.
    """
    with open(file_path, 'r') as f:
        combo_raw = f.read()
    m = fm_sep_re.search(combo_raw)
    if m is None:
        log("Couldn't find front-matter separator")
        return None
    fm_end, content_begin = m.span()

    json_str = combo_raw[:fm_end]
    content = combo_raw[content_begin:]
    try:
        meta = json.loads(json_str)
    except Exception as e:
        log(f"ERROR: Could not parse front matter in file {file_path}: {e}")
        raise e
    return (meta, content)


def compose_with_front_matter(meta, content_raw, file_path):
    """
    Given metadata and text content, recompose to file.
    Pretty-prints JSON in a canonical way.
    """
    # Pretty-print, sort keys, and don't escape Unicode
    json_norm = json.dumps(meta, indent=4, sort_keys=True, ensure_ascii=False)
    output = json_norm.strip() + '\n' + fm_sep + '\n' + content_raw
    with open(file_path, 'w') as pif:
        pif.write(output)


meta_keys_required = {'url', 'title'}
meta_keys_optional = {'date', 'author', 'tags', 'draft', 'id'}
re_post_url_format = re.compile(
    r'^/(?P<year>[0-9]{4})/(?P<month>[0-9]{2})/(?P<day>[0-9]{2})/(?P<slug>[a-z0-9_\-]+)/$'
)


def load_comments_for_post(post_dir):
    """
    Returns comments from post dir in sorted order (chronologically ascending).
    """
    comments = []

    for comment_path in list_comments_for_post(post_dir):
        (meta, content_raw) = split_front_matter(comment_path)

        update_value(meta, 'date', datetime.datetime.fromisoformat)
        update_value(meta, 'updated', datetime.datetime.fromisoformat)

        comments.append({'meta': meta, 'raw': content_raw})

    return sorted(comments, key=lambda c: c['meta']['date'])


def load_post(post_dir):
    """
    Given the path to a post dir, parse the post's metadata and content,
    returning a dict of:

    - meta: Dictionary
    - raw: String
    - comments: List of comments (each a dict of meta and raw)

    The metadata dict gains an additional key ``_internal`` with information
    used in later processing. This contains:

    - source_dir: String path indicating the directory the post was loaded from
    - path_parts: For non-drafts only, a list of strings for the
      directory path segments.
    """
    index_file = path.join(post_dir, "index.md")
    if not path.isfile(index_file):
        log(f"ERROR: No index file for post dir")
        return None

    (meta, content_raw) = split_front_matter(index_file)

    post = {
        'meta': meta,
        'raw': content_raw,
        'comments': load_comments_for_post(post_dir),
    }

    unknown_keys = meta.keys() - meta_keys_required - meta_keys_optional
    if unknown_keys:
        log(f"WARN: Unexpected front-matter keys in {post_dir}: {unknown_keys}")

    missing_keys = meta_keys_required - meta.keys()
    if missing_keys and not meta.get('draft'):
        log(f"ERROR: Missing required keys: {missing_keys}")
        return None

    # Add path information to publishable posts
    comments_feed_path = None
    path_parts = None
    if not meta.get('draft'):
        comments_feed_path = base_path + meta['url'] + "comments.atom"
        m = re_post_url_format.match(meta['url'])
        if not m:
            log(f"Malformed URL in post: {meta['url']}")
            return None
        path_parts = m.groups()

    if 'date' not in meta and not meta.get('draft'):
        log(f"Non-draft post is missing its publish date")
        return None

    update_value(meta, 'date', datetime.datetime.fromisoformat)
    update_value(meta, 'updated', datetime.datetime.fromisoformat)

    meta['_internal'] = {
        'source_dir': post_dir,
        'path_parts': path_parts,
        'comments_feed_path': comments_feed_path
    }

    return post


#### Command: generate


main_feed_path = f"{base_path}/posts.atom"

# The timezone my blog is written in (timestamps should be presented
# in this time zone)
tz_ET = dateutil.tz.gettz('US/Eastern')

safe_html_pre_content = f"""
    <div id="header">
      <h1>
        <a href="/" title="To root of site">{html.escape(site_title)}</a>
        &raquo; <a href="/blog/" title="To main page of blog">Blog</a>
        <a href="{html.escape(main_feed_path)}" title="Subscribe to feed of posts"><img src="/img/feed.svg" alt="feed icon" style="height: 1em; display: inline"></a>
        <small class="subtitle">{html.escape(site_subtitle)}</small>
      </h1>
    </div>

    <hr id="after-header" />
"""

safe_html_post_content = f"""
    <hr id="before-sitenav" />

    <div id="sitenav">
      <ul>
        <li><a href="/about/tim-mccormack/" title="About Tim McCormack">About me</a></li>
        <li><a href="/contact/" title="Contact information for Tim McCormack">Contact</a></li>
        <li><a href="/sitemap/" title="List of pages and links to archives">Sitemap</a></li>
      </ul>
    </div>

    <hr id="before-footer" />

    <div id="footer">
      <p>
        {html.escape(site_title)} uses a custom static blog generator.<br />
        Hosted for pennies a day at
        <a href="https://www.nearlyfreespeech.net/">NearlyFreeSpeech.net</a>.<br />
        Feed: <a href="{html.escape(main_feed_path)}">all entries</a>.
      </p>
    </div>
"""


def generate_index_page(posts_desc):
    # TODO use safe-by-default templating instead of manual calls to html.escape
    safe_html_listing = ""
    for year, posts_in_year in itertools.groupby(posts_desc, key=lambda p: p['meta']['date'].year):
        safe_html_listing += f"<h1>{html.escape(str(year))}</h1>\n"
        safe_html_listing += "<ul>\n"
        for post in posts_in_year:
            meta = post['meta']
            url = f"{base_path}{meta['url']}"
            safe_html_listing += f"""<li><a href="{html.escape(url)}">{html.escape(meta['title'])}</a></li>\n"""
        safe_html_listing += "</ul>\n"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <title>Blog | {html.escape(site_title)}</title>

  <link rel="stylesheet" href="/style/cleaner/generic.css" type="text/css" />
  <link rel="stylesheet" href="/style/cleaner/stylemods/posts.css" type="text/css" />

  <link rel="alternate" type="application/atom+xml" href="{html.escape(main_feed_path)}" />
</head>
<body>
  <div id="page">
    {safe_html_pre_content}

    <div id="content" class="multi-post">
      <div id="primary-content">
        <div class="most-recent-posts">
          <ul>
            {safe_html_listing}
          </ul>
        </div>
      </div>

      <div id="sidebar">
        <!-- TODO search -->

        <div class="page-state">
          <h2>Most recent posts</h2>
          <p>These are the most recent entries I've published.
            Older posts are organized by date in the archives.
          </p>
        </div>
      </div>
    </div>

    {safe_html_post_content}
  </div>
</body>
</html>
"""

def ordinal_suffix(i):
    ones = abs(i) % 10
    tens = abs(i) % 100 // 10

    if tens == 1: # 11, 12, 13 are an exception
        return "th"
    elif ones == 1:
        return "st"
    elif ones == 2:
        return "nd"
    elif ones == 3:
        return "rd"
    else:
        return "th"


def format_readable_date(date):
    local_date = date.astimezone(tz_ET)
    # Python date formatting doesn't have ordinal suffixes and I
    # didn't see a way to skip zero-padding for day and year.
    return "{}, {} {}{}, {} at {}".format(
        local_date.strftime('%A'),
        local_date.strftime('%B'),
        local_date.day,
        ordinal_suffix(local_date.day),
        local_date.year,
        local_date.strftime('%H:%M (%Z)')
    )


def generate_comment_html(content_raw):
    return markdown.markdown(content_raw, output_format='html5')


re_comment_safe_author_url = re.compile(r'^https?://', re.IGNORECASE)


def generate_comment_section(post):
    """Generate HTML for the comment section for one post page."""
    comments = post['comments']

    safe_html_feed_link = f"""
<a href="{html.escape(post['meta']['_internal']['comments_feed_path'])}" title="Comment feed for this post"><img src="/img/feed-14sq.png" class="feed-icon" alt="Feed icon"></a>
"""

    if not comments:
        return f"""<p>No comments yet. {safe_html_feed_link}</p>"""

    comment_count = str(len(comments))
    output = f"""
<h2>Responses: {html.escape(comment_count)} so far {safe_html_feed_link}</h2>
<ol class="commentlist">
"""
    for comment in comments:
        meta = comment['meta']
        id_str = str(meta['id'])
        readable_date = format_readable_date(meta['date'])

        unchecked_url = meta['authorUrl']
        good_url = None
        if unchecked_url and re_comment_safe_author_url.match(unchecked_url):
            good_url = unchecked_url
        if good_url:
            safe_html_authorlink = f"""
<a href="{html.escape(meta['authorUrl'])}" rel="external nofollow" class="url">{html.escape(meta['author'])}</a>"""
        else:
            safe_html_authorlink = html.escape(meta['author'])

        safe_html_comment_content = generate_comment_html(comment['raw'])

        # TODO: Add openid indicator when openID=True
        output += f"""
<li class="comment" id="comment-{html.escape(id_str)}">
  <small class="commentmetadata">
    <a href="#comment-{html.escape(id_str)}" title="Permanent link to comment" rel="bookmark">#{html.escape(id_str)}</a>
    |
    {html.escape(readable_date)}
  </small>

  <p class="commentattribution"><cite>{safe_html_authorlink}</cite> says:</p>
  <div class="commentdata userformat">{safe_html_comment_content}</div>
</li>
"""
    output + """</ol>n"""
    return output


def generate_post_content_html(post_content_raw):
    return post_content_raw # TODO - process for markdown?


def generate_post_page(post):
    meta = post['meta']

    permalink = f"{base_path}{meta['url']}"
    title = meta['title']
    local_date = meta['date'].astimezone(tz_ET)
    # Python date formatting doesn't have ordinal suffixes and I
    # didn't see a way to skip zero-padding for day and year.
    readable_date = format_readable_date(meta['date'])
    # TODO updated_date
    safe_html_content = generate_post_content_html(post['raw'])

    tags = meta.get('tags')
    if tags:
        safe_html_tag_list = ", ".join(html.escape(tag) for tag in tags)
    else:
        safe_html_tag_list = "[none]"

    safe_html_comments = generate_comment_section(post)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <title>{html.escape(title)} | {html.escape(site_title)}</title>

  <link rel="stylesheet" href="/style/cleaner/generic.css" type="text/css" />
  <link rel="stylesheet" href="/style/cleaner/stylemods/single.css" type="text/css" />

  <link rel="canonical" href="{html.escape(permalink)}" />
</head>
<body>
  <div id="page">
    {safe_html_pre_content}

    <div id="content">
      <div id="primary-content">
        <div class="post">
          <h2 class="post-title">
            <a href="{html.escape(permalink)}" rel="bookmark" title="Permanent link for post">{html.escape(title)}</a>
          </h2>
          <div class="entrytext userformat">
            {safe_html_content}
          </div>
        </div>
      </div>

      <div id="sidebar">
        <!-- TODO search -->

        <div class="author">
          <img class="avatar" src="/img/timmc-avatar-75.jpg" width="75" height="75" />
          <h2>Author</h2>
          <p>Tim McCormack lives in Somerville, MA, USA and works as a software developer. (Updated 2019.)</p>
        </div>

        <div class="postmetadata">
          <h2>Entry</h2>
          <ul>
            <li>Posted on {html.escape(readable_date)}</li>
            <li>Tags: {safe_html_tag_list}</li> <!-- TODO link to tag pages -->
          </ul>
        </div>
      </div>

      <hr id="after-primary" />

      <div id="secondary-content">
        <div id="comments">
          {safe_html_comments}
        </div>
      </div>
    </div>

    {safe_html_post_content}
  </div>
</body>
</html>
"""


def generate_posts_atom_feed(posts_desc, out_path):
    """
    Given posts in descending chronological order, write out an Atom XML feed
    to the specified path.

    This feed is assumed to be the main feed.
    """
    # Generate full public-site URL here -- it's a global identifier
    # in some places in the feed, and needs to be absolute in others.
    blog_url = base_authority + base_path
    feed_full_url = base_authority + main_feed_path

    root = ET.Element('feed', {
        'xmlns': "http://www.w3.org/2005/Atom",
        'xml:lang': 'en-US',
        'xml:base': blog_url + '/'
    })
    ET.SubElement(root, 'title').text = site_title
    ET.SubElement(root, 'subtitle').text = site_subtitle
    ET.SubElement(root, 'link', rel='alternate', type='text/html', href=blog_url)
    ET.SubElement(root, 'id').text = feed_full_url
    ET.SubElement(root, 'link', rel='self', type="application/atom+xml", href=feed_full_url)
    for post in posts_desc[:20]:
        meta = post['meta']
        # Absolute URL, not absolute path
        permalink = f"{base_authority}{base_path}{meta['url']}"
        entry = ET.SubElement(root, 'entry')
        author = ET.SubElement(entry, 'author')
        ET.SubElement(author, 'name').text = "Tim McCormack"
        ET.SubElement(author, 'uri').text = "https://www.brainonfire.net/"
        ET.SubElement(entry, 'title').text = meta['title']
        ET.SubElement(entry, 'link', rel='alternate', type='text/html', href=permalink)
        ET.SubElement(entry, 'id').text = permalink
        ET.SubElement(entry, 'updated').text = meta.get('updated', meta['date']).isoformat(sep='T')
        ET.SubElement(entry, 'published').text = meta['date'].isoformat(sep='T')
        for tag in meta.get('tags', []):
            ET.SubElement(entry, 'category', term=tag)
        ET.SubElement(entry, 'content', {'xml:base': permalink}, type='html').text = generate_post_content_html(post['raw'])
        # TODO: Comment link, feed, and count
			  # <link rel="replies" type="text/html" href="https://www.brainonfire.net/blog/2020/07/13/letter-to-ma-governor-covid-19/#comments" thr:count="0"/>
		    # <link rel="replies" type="application/atom+xml" href="https://www.brainonfire.net/blog/2020/07/13/letter-to-ma-governor-covid-19/feed/atom/" thr:count="0"/>
		    # <thr:total>0</thr:total>
    with open(out_path, 'w') as feedf:
        ET.ElementTree(root).write(feedf, encoding="unicode", xml_declaration=True)


def generate_post_comments_atom_feed(post, out_path):
    """
    Given a post, write out an Atom feed of its comments to the specified path.
    """
    # Generate full public-site URL here -- it's a global identifier
    # in some places in the feed, and needs to be absolute in others.
    post_full_url = base_authority + base_path + post['meta']['url']
    feed_full_url = base_authority + post['meta']['_internal']['comments_feed_path']

    root = ET.Element('feed', {
        'xmlns': "http://www.w3.org/2005/Atom",
        'xml:lang': 'en-US',
        'xml:base': post_full_url
    })
    ET.SubElement(root, 'title').text = f"Comments on “{post['meta']['title']}”"
    ET.SubElement(root, 'link', rel='alternate', type='text/html', href=f"{post_full_url}#comments")
    ET.SubElement(root, 'id').text = feed_full_url
    ET.SubElement(root, 'link', rel='self', type="application/atom+xml", href=feed_full_url)
    for comment in reversed(post['comments']):
        meta = comment['meta']
        # Absolute URL, not absolute path
        permalink = f"{base_authority}{base_path}{post['meta']['url']}#comment-{meta['id']}"
        entry = ET.SubElement(root, 'entry')
        ET.SubElement(entry, 'title').text = f"By: {meta['author']}"
        ET.SubElement(entry, 'link', rel='alternate', type='text/html', href=permalink)
        author = ET.SubElement(entry, 'author')
        ET.SubElement(author, 'name').text = meta['author']
        author_url = meta['authorUrl']
        if author_url and re_comment_safe_author_url.match(author_url):
            ET.SubElement(author, 'uri').text = author_url
        ET.SubElement(entry, 'id').text = permalink
        ET.SubElement(entry, 'updated').text = meta.get('updated', meta['date']).isoformat(sep='T')
        ET.SubElement(entry, 'published').text = meta['date'].isoformat(sep='T')
        ET.SubElement(entry, 'content', {'xml:base': post_full_url}, type='html').text = generate_comment_html(comment['raw'])
    with open(out_path, 'w') as feedf:
        ET.ElementTree(root).write(feedf, encoding="unicode", xml_declaration=True)


@cli.command(name='generate')
def cmd_generate():
    """Generate the site."""

    # Load all posts into memory -- drafts filtering is done at this step
    posts = []
    for post_dir in list_post_dirs():
        post = load_post(post_dir)
        if post is None:
            # Don't proceed to deleting all generated pages -- maybe
            # the parser is wrong, not this one file.
            raise Exception(f"ERROR: Could not process post in directory {post_dir}")
        if not post['meta'].get('draft'):
            posts.append(post)
    posts_desc = sorted(posts, key=lambda p: p['meta']['date'], reverse=True)
    del posts

    # Clear the target directory
    if shutil.rmtree.avoids_symlink_attacks:
        shutil.rmtree(gen_root)
        os.mkdir(gen_root)
    else:
        log("FATAL: This version of Python doesn't support `shutil.rmtree.avoids_symlink_attacks`")
        exit(1)

    # Generate

    with open(path.join(gen_root, 'index.html'), 'w') as indf:
        indf.write(generate_index_page(posts_desc))

    generate_posts_atom_feed(posts_desc, path.join(gen_root, 'posts.atom'))

    for post in posts_desc:
        post_gen_dir = path.join(gen_root, *post['meta']['_internal']['path_parts'])
        os.makedirs(post_gen_dir)
        with open(path.join(post_gen_dir, 'index.html'), 'w') as postf:
            postf.write(generate_post_page(post))
        generate_post_comments_atom_feed(post, path.join(post_gen_dir, 'comments.atom'))

    log(f"INFO: Processed {len(posts_desc)} posts")


#### Command: normalize


def normalize_file(file_path):
    """Just split a file and write it back again."""
    (meta, content_raw) = split_front_matter(file_path)
    compose_with_front_matter(meta, content_raw, file_path)


@cli.command(name='normalize')
def cmd_normalize():
    """
    Normalize the front matter of posts and comments.

    Makes existing files conform to standards such as sorted keys in front
    matter. This allows for automated changes to posts without causing
    spurious diffs.
    """
    for post_dir in list_post_dirs():
        normalize_file(path.join(post_dir, 'index.md'))
        for comment_path in list_comments_for_post(post_dir):
            normalize_file(comment_path)


#### Command: update


@cli.command(name='update')
@click.argument('post_path', type=click.Path(exists=True))
def cmd_update(post_path):
    """
    Add or update the "updated" timestamp on a published post.
    """
    (meta, content_raw) = split_front_matter(post_path)
    if meta.get('draft', False):
        print("Not updating date, since not yet published.", file=sys.stderr)
        return
    meta['updated'] = datetime.datetime.now(tz_ET) \
                                       .isoformat(sep='T', timespec='seconds')
    compose_with_front_matter(meta, content_raw, post_path)
    # TODO: Open editor?


#### Command: new


@cli.command(name='new')
@click.argument('tmp_name')
def cmd_new(tmp_name):
    """
    Create a new post with the specified temporary name..
    """
    post_dir = path.join(posts_src_dir, tmp_name)
    if path.exists(post_dir):
        print("Path already exists for that working title: %s" % post_dir)
        exit(1)
    os.mkdir(post_dir)
    now = datetime.datetime.now(tz_ET)
    meta = {
        'author': 'Tim McCormack',
        'date': now.isoformat(sep='T', timespec='seconds'),
        'draft': True,
        'tags': [],
        'title': '',
        'url': now.strftime('/%Y/%m/%d/_/'),
    }
    content_raw = ""
    post_path = path.join(post_dir, 'index.md')
    compose_with_front_matter(meta, content_raw, post_path)
    # TODO: Open editor?
    print(post_path)  # to stdout


#### Command: public


@cli.command(name='public')
@click.argument('post_path', type=click.Path(exists=True))
def cmd_public(post_path):
    """
    Turn the specified draft into a public post, prompting for URL slug.
    """
    (meta, content_raw) = split_front_matter(post_path)
    if not meta.get('draft', False):
        print("This is already public.", file=sys.stderr)
        exit(1)
    now = datetime.datetime.now(tz_ET)
    ymd = now.strftime('%Y/%m/%d')

    meta['date'] = now.isoformat(sep='T', timespec='seconds')
    meta.pop('updated', None)  # shouldn't exist
    meta.pop('draft', None)
    # Preserve the URL slug, or the whole thing if the format is weird.
    old_url = meta.get('url')
    if not old_url:
        print("URL field is missing, please add one. Suggested: /{ymd}/___/", file=sys.stderr)
    else:
        m_url = re_post_url_format.match(old_url)
        if not m_url:
            print("URL field is malformed. Please add one matching /{ymd}/___/", file=sys.stderr)
        else:
            old_slug = m_url.groupdict()['slug']
            meta['url'] = f'/{ymd}/{old_slug}/'
            print("Remember to update the URL slug before generating and pushing.", file=sys.stderr)

    compose_with_front_matter(meta, content_raw, post_path)


#### Command: fix-encoding


@cli.command(name='fix-encoding')
def cmd_fix_encoding():
    """
    Fix encoding issues in posts and comments.

    This should generally only be needed once, after an initial import.
    """
    def fixer(s):
        return ftfy.fix_encoding(s)

    def fix_one_file(file_path):
        (meta, content_raw) = split_front_matter(file_path)

        update_value(meta, 'title', fixer)
        update_value(meta, 'author', fixer)
        update_value(meta, 'tags', lambda tags: list(fixer(t) for t in tags))
        content_raw = fixer(content_raw)

        compose_with_front_matter(meta, content_raw, file_path)

    for post_dir in list_post_dirs():
        fix_one_file(path.join(post_dir, 'index.md'))
        for comment_path in list_comments_for_post(post_dir):
            fix_one_file(comment_path)
    


#### Main


if __name__ == '__main__':
    cli()
