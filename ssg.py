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
import math
import os
from os import path
import re
import shutil
import sys
import urllib.parse as urls
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


def chunk_stable(items, chunk_size, min_size):
    """
    Stable chunking algorithm that partitions ``items`` into
    contiguous sublists (as a new list) where all sublists are of size
    ``chunk_size``, possibly except for the first sublist. The first
    sublist is as small as possible without being smaller than
    ``min_size``, which means it may also be as large as ``chunk_size
    + min_size - 1``. (If the whole list is smaller than
    ``chunk_size``, the first chunk may be as small as zero elements
    long.)

    This means that the boundaries of all but the first chunk do
    not change when new items are added to the beginning of the
    list, resulting in smaller diffs to upload.
    """
    chunks = []
    remaining = len(items)
    while remaining > 0:
        if remaining >= chunk_size + min_size:
            take = chunk_size
        else:
            take = remaining
        chunks.insert(0, items[remaining - take:remaining])
        remaining -= take
    return chunks


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
meta_keys_optional = {'date', 'author', 'tags', 'draft', 'id', 'updated', 'unlisted'}
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


def generate_post_content_html(post_content_raw):
    return post_content_raw # TODO - process for markdown?


def post_excerpt_html(post):
    parts = post['raw'].split('\n\n<!--more-->\n\n', maxsplit=1)
    if len(parts) < 2:
        return None
    else:
        return generate_post_content_html(parts[0])


def generate_quicklinks_page(posts_desc, page_title, safe_html_page_desc, content_class):
    """
    Return HTML for a year-bucketed listing of posts, given posts in descending order by timestamp.

    - page_title is the name of the page
    - safe_html_page_desc is some HTML describing the page, for the sidebar
    - content_class is the classname to use inside the #primary-content div
    """
    # TODO use safe-by-default templating instead of manual calls to html.escape
    safe_html_listing = ""
    for year, posts_in_year in itertools.groupby(posts_desc, key=lambda p: p['meta']['date'].year):
        safe_html_listing += f"<h3>{html.escape(str(year))}</h3>\n"
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
  <title>{html.escape(page_title)} | Blog | {html.escape(site_title)}</title>

  <link rel="stylesheet" href="/style/cleaner/generic.css" type="text/css" />
  <link rel="stylesheet" href="/style/cleaner/stylemods/posts.css" type="text/css" />

  <link rel="alternate" type="application/atom+xml" href="{html.escape(main_feed_path)}" />
</head>
<body>
  <div id="page">
    {safe_html_pre_content}

    <div id="content" class="multi-post">
      <div id="primary-content">
        <div class="{html.escape(content_class)}">
          <ul>
            {safe_html_listing}
          </ul>
        </div>
      </div>

      <div id="sidebar">
        <!-- TODO search -->

        <div class="page-state">
          <h2>{html.escape(page_title)}</h2>
          <p>{safe_html_page_desc}</p>
        </div>
      </div>
    </div>

    {safe_html_post_content}
  </div>
</body>
</html>
"""


def generate_multipost_page(
        posts_desc, page_title, safe_html_page_desc, content_class,
        older_url, newer_url
):
    """
    Return HTML for a listing of posts with excerpts, given posts in descending order by timestamp.

    - page_title is the name of the page
    - safe_html_page_desc is some HTML describing the page, for the sidebar
    - content_class is the classname to use inside the #primary-content div
    - older_url/newer_url are URLs to multiposts of older and newer posts, or None.
      If there's a newer_url, add a noindex meta tag.
    """
    # TODO use safe-by-default templating instead of manual calls to html.escape
    safe_html_listing = ""
    for post in posts_desc:
        meta = post['meta']
        url = f"{base_path}{meta['url']}"
        excerpt_html = post_excerpt_html(post)
        if excerpt_html is None:
            safe_html_excerpt_more = '<p>(No excerpt available.)</p>'
        else:
            safe_html_excerpt_more = excerpt_html + f'<a href="{html.escape(url)}" class="more-link">Read more</a>'
        comment_counter = f"{len(post['comments'])} comment{'' if len(post['comments']) == 1 else 's'}"
        safe_html_listing += f"""
<article class="post">
  <header>
    <h2><a href="{html.escape(url)}">{html.escape(meta['title'])}</a></h2>
    <p class="postmetadata">
      <span class="timestamp">{html.escape(meta['date'].date().strftime('%B %d, %Y'))}</span> |
      <span class="comment-count">{html.escape(comment_counter)}</span>
    </p>
  </header>
  {safe_html_excerpt_more}
</article>
"""

    safe_html_listing += '<div class="backforth">'
    if newer_url is not None:
        safe_html_meta_tags = '<meta name="robots" content="noindex" />'
        safe_html_listing += f'<a class="later" href="{html.escape(newer_url)}">More recent entries</a>'
    else:
        safe_html_meta_tags = ''
    if older_url is not None:
        if newer_url is not None:
            safe_html_listing += ' | '
        safe_html_listing += f'<a class="earlier" href="{html.escape(older_url)}">Older entries</a>'
    safe_html_listing += '</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <title>{html.escape(page_title)} | Blog | {html.escape(site_title)}</title>
  {safe_html_meta_tags}

  <link rel="stylesheet" href="/style/cleaner/generic.css" type="text/css" />
  <link rel="stylesheet" href="/style/cleaner/stylemods/posts.css" type="text/css" />

  <link rel="alternate" type="application/atom+xml" href="{html.escape(main_feed_path)}" />
</head>
<body>
  <div id="page">
    {safe_html_pre_content}

    <div id="content" class="multi-post">
      <div id="primary-content">
        <div class="{html.escape(content_class)}">
          <ul>
            {safe_html_listing}
          </ul>
        </div>
      </div>

      <div id="sidebar">
        <!-- TODO search -->

        <div class="page-state">
          <h2>{html.escape(page_title)}</h2>
          <p>{safe_html_page_desc}</p>
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


def tag_to_slug(tag):
    """Given a tag, normalize to a URL-safe tag slug."""
    safe = re.sub(r'[^a-z0-9]', '-', tag.lower())
    short = re.sub(r'\-+', '-', safe)
    trimmed = short.strip('-')
    return trimmed or '-'


def generate_comment_html(content_raw):
    return markdown.markdown(content_raw, output_format='html5')


re_comment_safe_author_url = re.compile(r'^https?://', re.IGNORECASE)


def generate_comment_section(post):
    """Generate HTML for the comment section for one post page."""
    comments = post['comments']

    safe_html_feed_link = f"""
<a href="{html.escape(post['meta']['_internal']['comments_feed_path'])}" title="Comment feed for this post"><img src="/img/feed-14sq.png" class="feed-icon" alt="Feed icon"></a>
"""

    email_params = {'subject': "re: " + post['meta']['title']}
    safe_html_no_commenting = f"""
Self-service commenting is
<a href="/blog/2020/08/18/from-wordpress-to-ssg/">not yet reimplemented</a>
after the Wordpress migration, sorry!
For now, you can <a href="mailto:cortex&#x0000040;brainonfire&#x000002E;net?{html.escape(urls.urlencode(email_params, quote_via=urls.quote))}">respond by email</a>;
please indicate whether you're OK with having your response posted publicly
(and if so, under what name).
"""
    if not comments:
        return f"""<p>No comments yet. {safe_html_feed_link}</p> <p>{safe_html_no_commenting}</p>"""

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
    output += f"""</ol><p>{safe_html_no_commenting}</p>"""
    return output


def generate_post_page(post):
    meta = post['meta']

    permalink = f"{base_path}{meta['url']}"
    title = meta['title']
    if meta.get('unlisted'):
        title = "[UNLISTED POST] " + title + " [UNLISTED POST]"
    readable_posted_date = format_readable_date(meta['date'])
    if 'updated' in meta:
        readable_updated_date = format_readable_date(meta['updated'])
        safe_html_updated_date_item = f"""\n            <li>Last updated on {html.escape(readable_updated_date)}</li>"""
    else:
        safe_html_updated_date_item = ""
    safe_html_content = generate_post_content_html(post['raw'])

    def tag_to_link(tag):
        return f"""
<a href="{html.escape("/blog/tag/" + tag_to_slug(tag))}/"
   title="Posts tagged &quot;{html.escape(tag)}&quot;">{html.escape(tag)}</a>
""".strip()
    tags = meta.get('tags')
    if tags:
        safe_html_tag_list = ", ".join(tag_to_link(tag) for tag in tags)
    else:
        safe_html_tag_list = "[none]"

    years_old = (datetime.datetime.now(tz_ET) - meta['date']).days / 365.25
    if years_old <= 5:
        safe_html_old_stuff_warning = ''
    else:
        safe_html_old_stuff_warning = f"""
<div class="content_age_warning">
  <p>Automated note: This post was <strong>written more than {math.floor(years_old)} years ago</strong>
     and I have probably not looked at it since.</p>

  <p>Older posts may not align with who I am today and how I would
     think or write. In particular, some of my oldest posts (high
     school and college age) are fairly cringeworthy in places, or are
     in reaction to a cultural context that no longer
     applies. However, I have left them public because I believe in
     keeping old web pages alive, and it's interesting to look back
     and see how I've changed.</p>

  <p>(And if there were nothing I wrote 10 years ago that I disagreed
     with today, what would that say about me?)</p>
</div>
"""

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
          {safe_html_old_stuff_warning}
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
            <li>Posted on {html.escape(readable_posted_date)}</li>{safe_html_updated_date_item}
            <li>Tags: {safe_html_tag_list}</li>
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


def generate_posts_atom_feed(posts_desc):
    """
    Given posts in descending chronological order, generate an Atom XML feed.

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
    # xml_declaration=True not present until Python 3.8
    return "<?xml version='1.0' encoding='UTF-8'?>\n" + ET.tostring(root, encoding="unicode")


def generate_post_comments_atom_feed(post):
    """
    Given a post, generate an Atom feed of its comments.
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
    return "<?xml version='1.0' encoding='UTF-8'?>\n" + ET.tostring(root, encoding="unicode")


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

    # If we simply wipe out the generated directory and regenerate
    # everything, rsync is going to have to sync every file on
    # publish, since the mtime and inode have changed for every
    # path.
    #
    # Instead, we'll avoid rewriting a file if the content hasn't
    # changed, and at the end will delete any files that are on disk
    # but wouldn't have been generated. The below is the pair of sets
    # used to track this.

    # Collect all existing non-directory paths.
    existing_paths = set()
    for parent, _dirnames, filenames in os.walk(gen_root):
        for filename in filenames:
            existing_paths.add(path.join(parent, filename))
    # Mutable: All files
    paths_written = set()

    def write_and_record(abs_path, content):
        """
        Write to the path if the contents differ, and note as written.
        """
        newbytes = content.encode()

        if path.exists(abs_path):
            with open(abs_path, 'rb') as f:
                oldbytes = f.read()
        else:
            oldbytes = None

        if newbytes != oldbytes:
            with open(abs_path, 'wb') as f:
                f.write(newbytes)

        paths_written.add(abs_path)


    # Generate post pages and their comments feeds
    for post in posts_desc:
        post_gen_dir = path.join(gen_root, *post['meta']['_internal']['path_parts'])
        os.makedirs(post_gen_dir, exist_ok=True)
        write_and_record(
            path.join(post_gen_dir, 'index.html'),
            generate_post_page(post)
        )
        write_and_record(
            path.join(post_gen_dir, 'comments.atom'),
            generate_post_comments_atom_feed(post)
        )

    # Trim down the posts to just public ones now -- and calculate any
    # information we need before that filtering.
    posts_count = len(posts_desc)
    posts_desc = [post for post in posts_desc if not post['meta'].get('unlisted')]

    # Chunk out archive pages in a way that produces the smallest
    # movement of chunk boundaries when a new post is created.
    archive_chunks = chunk_stable(posts_desc, chunk_size=5, min_size=3)
    # Index the list, *ascending with time* -- oldest (last-in-list) archive will be 1.html.
    archive_chunks = list(reversed(list(enumerate(reversed(archive_chunks), start=1))))

    # Break out the first chunk as the index page.
    if len(archive_chunks) > 0:
        index_listing = archive_chunks[0][1]
        archive_chunks = archive_chunks[1:]
    else:
        index_listing = []

    # Generate main index page
    if len(archive_chunks) > 0:
        most_recent_archive = f"{base_path}/archive/{archive_chunks[0][0]}.html"
    else:
        most_recent_archive = None
    write_and_record(
        path.join(gen_root, 'index.html'),
        generate_multipost_page(
            index_listing, page_title="Recent posts",
            safe_html_page_desc=(
                f"My most recent posts. If you'd like to know "
                "when new posts come out, I invite you to "
                f'<a href="{html.escape(main_feed_path)}">subscribe to the feed</a>.'
            ),
            content_class="recent-posts",
            older_url=most_recent_archive,
            newer_url=None
        )
    )

    # Generate archive pages
    archives_dir = path.join(gen_root, 'archive')
    os.makedirs(archives_dir, exist_ok=True)
    oldest_index = archive_chunks[-1][0]
    newest_index = archive_chunks[0][0]
    for (archive_index, chunk) in archive_chunks:
        newest_date = chunk[ 0]['meta']['date'].date().isoformat()
        oldest_date = chunk[-1]['meta']['date'].date().isoformat()
        if newest_date == oldest_date:
            date_range_descr = newest_date
        else:
            date_range_descr = f"{newest_date} back to {oldest_date}"

        if archive_index > oldest_index:
            older_url = f"{base_path}/archive/{archive_index - 1}.html"
        else:
            older_url = None

        if archive_index < newest_index:
            newer_url = f"{base_path}/archive/{archive_index + 1}.html"
        else:
            newer_url = f"{base_path}/"

        write_and_record(
            path.join(archives_dir, f"{archive_index}.html"),
            generate_multipost_page(
                chunk, page_title=f"Archive",
                safe_html_page_desc=(
                    "These are older posts, "
                    f"from <strong>{html.escape(date_range_descr)}</strong>.  "
                    "If you're doing an archive binge, tread carefully; "
                    "these posts go back aways, and there's some cringeworthy stuff "
                    "in the deep archives that's really only of archaeological interest."
                ),
                content_class="archive-posts",
                older_url=older_url,
                newer_url=newer_url
            )
        )

    # Generate main posts feed
    write_and_record(
        path.join(gen_root, 'posts.atom'),
        generate_posts_atom_feed(posts_desc)
    )

    # Generate tags pages
    def post_tag_slugs(post):
        return [tag_to_slug(tag) for tag in post['meta'].get('tags', [])]

    all_tag_slugs = {slug for post in posts_desc for slug in post_tag_slugs(post)}
    for tag_slug in all_tag_slugs:
        filtered_posts_desc = [p for p in posts_desc if tag_slug in post_tag_slugs(p)]
        tag_dir = path.join(gen_root, 'tag', tag_slug)
        os.makedirs(tag_dir, exist_ok=True)
        write_and_record(path.join(tag_dir, 'index.html'),
            generate_quicklinks_page(
                filtered_posts_desc, page_title=f'Tagged "{tag_slug}"',
                safe_html_page_desc=(
                    f'All posts tagged with "{html.escape(tag_slug)}". '
                    "Please note that some of these posts may be quite old, "
                    "and may not be a good representation of who I am today."
                ),
                content_class="tagged-posts"
            )
        )

    # Remove all files that weren't re-generated
    for parent, _dirnames, filenames in os.walk(gen_root, topdown=False):
        for filename in filenames:
            filepath = path.join(parent, filename)
            if filepath not in paths_written:
                os.remove(filepath)
    # Remove any remaining empty directories
    for parent, dirnames, filenames in os.walk(gen_root):
        if not dirnames and not filenames:
            os.removedirs(parent)

    log(f"INFO: Processed {posts_count} posts")


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
