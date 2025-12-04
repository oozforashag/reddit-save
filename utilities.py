import time
import os
import praw
import requests
from redvid import Downloader
import yt_dlp
import re
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

try:
    from logindata import REDDIT_USERNAME, REDDIT_PASSWORD
    from logindata import REDDIT_CLIENT_ID, REDDIT_SECRET
except ImportError:
    REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
    REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
    REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
    REDDIT_SECRET = os.getenv("REDDIT_SECRET")

IMAGE_EXTENSIONS = ["gif", "gifv", "jpg", "jpeg", "png"]
VIDEO_EXTENSIONS = ["mp4"]
PLATFORMS = ["redgifs.com", "imgur.com", "youtube.com"]


def make_client():
    """Creates a PRAW client with the details in the secrets.py file."""

    logger.info(REDDIT_USERNAME)

    return praw.Reddit(
        username=REDDIT_USERNAME,
        password=REDDIT_PASSWORD,
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_SECRET,
        user_agent="reddit-save",
    )


def get_previous(location, html_file):
    html_files = [f for f in os.listdir(location) if f.endswith(".html")]
    pattern = html_file.replace(".html", r"\.(\d+)?\.html")
    matches = [re.match(pattern, f) for f in html_files]
    matches = [m[0] for m in matches if m]
    matches.sort(key=lambda x: int(x.split(".")[1]))
    existing_ids = []
    existing_posts_html = []
    existing_comments_html = []
    if html_file in html_files:
        matches.append(html_file)

    for match in matches:
        with open(os.path.join(location, match), encoding="utf-8") as f:
            current_html = f.read()
            for post_id in re.findall(r'id="(.+?)"', current_html):
                if post_id not in existing_ids:
                    existing_ids.append(post_id)
            posts = re.findall(
                r'(<div class="post"[\S\n\t\v ]+?<!--postend--></div>)',
                current_html
            )
            comments = re.findall(
                r'(<div class="comment"[\S\n\t\v ]+?<!--commentend--></div>)',
                current_html
            )
            for post in posts:
                if post not in existing_posts_html:
                    existing_posts_html.append(post)
            for comment in comments:
                if comment not in existing_comments_html:
                    existing_comments_html.append(comment)
    return existing_ids, existing_posts_html, existing_comments_html


def get_saved_posts(client):
    """Gets a list of posts that the user has saved."""

    return [
        saved for saved in client.user.me().saved(limit=None)
        if saved.__class__.__name__ == "Submission"
    ]


def get_upvoted_posts(client):
    """Gets a list of posts that the user has upvoted."""

    return [
        upvoted for upvoted in client.user.me().upvoted(limit=None)
        if upvoted.__class__.__name__ == "Submission"
    ]


def get_saved_comments(client):
    """Gets a list of comments that the user has saved."""

    return [
        saved for saved in client.user.me().saved(limit=None)
        if saved.__class__.__name__ != "Submission"
    ]


def get_user_posts(client, username):
    """Gets a list of posts that the user has made."""

    return [
        post for post in client.redditor(username).submissions.new(limit=None)
    ]


def get_user_comments(client, username):
    """Gets a list of comments that the user has made."""

    return [
        comment for comment in client.redditor(username).comments.new(limit=None)
    ]


def get_post_html(post):
    """Takes a post object and creates a HTML for it - but not including the
    preview HTML."""

    with open(os.path.join("html", "post-div.html"), encoding="utf-8") as f:
        html = f.read()
    dt = datetime.fromtimestamp(post.created_utc, timezone.utc)
    html = html.replace("<!--title-->", post.title)
    html = html.replace("<!--subreddit-->", f"/r/{str(post.subreddit)}")
    html = html.replace("<!--user-->", f"/u/{post.author.name}" if post.author else "[deleted]")
    html = html.replace("<!--link-->", f"posts/{post.id}.html")
    html = html.replace("<!--reddit-link-->", f"https://reddit.com{post.permalink}")
    html = html.replace("<!--content-link-->", post.url)
    html = html.replace("<!--id-->", post.id)
    html = html.replace("<!--body-->", (post.selftext_html or "").replace(
        '<a href="/r/', '<a href="https://reddit.com/r/'
    ))
    html = html.replace("<!--timestamp-->", str(dt))
    html = html.replace("<!--date-->", dt.strftime("%d %B, %Y"))
    return html


def save_media(post, location):
    """Takes a post object and tries to download any image/video it might be
    associated with. If it can, it will return the filename(s).
    Service/domain handlers should return -1 on error to indicate upstream that
    it tried and failed to fetch the media, so that we have the option of
    how to handle it in the upstream HTML rendering.
    """

    url = post.url
    stripped_url = url.split("?")[0]

    if url.endswith(post.permalink):
        return None

    # Extract key information
    extension = stripped_url.split(".")[-1].lower()
    domain = ".".join(post.url.split("/")[2].split(".")[-2:])
    readable_name = list(filter(bool, post.permalink.split("/")))[-1]

    # Route to appropriate handler
    # Dead.
    if domain == "gfycat.com":
        return -1

    # Too hard to handle, I guess?
    if domain == "imgur.com" and "gallery" in url:
        return -1

    # Might be directly fetchable via curl and the like.
    if extension in IMAGE_EXTENSIONS + VIDEO_EXTENSIONS:
        return _handle_direct_media(url, post.id, readable_name, extension, location)

    if domain == "reddit.com" and "gallery" in url:
        return _handle_reddit_gallery(url, post.id, readable_name, location)

    if domain == "redd.it":
        return _handle_vreddit(url, post.id, readable_name, location)

    if domain == "imgur.com" and extension != "gifv":
        return _handle_imgur(url, post.id, readable_name, location)

    # Things that yt-dlp can handle, I guess.
    if domain in PLATFORMS:
        return _handle_ytdlp(url, post.id, readable_name, location)

    # Special case of direct for reddituploads.com
    if domain == "reddituploads.com":
        if not hasattr(post, "preview"):
            return -1
        url = post.preview['images'][0]['source']['url']
        extension = url.split("?")[0].split(".")[-1]
        return _handle_direct_media(url, post.id, readable_name, extension, location)

    return None


def _handle_direct_media(url, post_id, readable_name, extension, location):
    """Download media that can be obtained directly from URL."""
    filename = f"{readable_name}_{post_id}.{extension}"
    try:
        response = requests.get(url)
        if response.status_code // 100 == 2:
            media_type = response.headers.get("Content-Type", "")
            if media_type.startswith("image") or media_type.startswith("video"):
                filepath = os.path.join(location, "media", filename)
                with open(filepath, "wb") as f:
                    f.write(response.content)
                return filename
    except Exception as e:
        logger.error(f"(direct media) Error downloading {url}: {e}")

    return -1


def _handle_reddituploads(url, post_id, readable_name, extension, location):
    """Download media that can be obtained directly from URL."""
    filename = f"{readable_name}_{post_id}.{extension}"
    try:
        response = requests.get(url)
        if response.status_code // 100 == 2:
            media_type = response.headers.get("Content-Type", "")
            if media_type.startswith("image") or media_type.startswith("video"):
                filepath = os.path.join(location, "media", filename)
                with open(filepath, "wb") as f:
                    f.write(response.content)
                return filename
    except Exception as e:
        logger.error(f"(reddituploads) Error downloading {url}: {e}")

    return -1


def _handle_reddit_gallery(url, post_id, readable_name, location):
    """Download all images from a Reddit gallery post."""
    json_url = url + ".json"
    resp = requests.get(json_url)

    # Handle rate limiting
    sleep = 1
    while resp.status_code == 429:
        time.sleep(sleep)
        logger.warning(f"Rate limited, sleeping for {sleep} seconds")
        resp = requests.get(json_url)
        sleep *= 2

    data = resp.json()
    post_data = data[0]["data"]["children"][0]["data"]
    media = post_data.get("media_metadata")

    if not media:
        return None

    filenames = []
    for idx, data in enumerate(list(media.values()), 1):
        if "m" not in data:
            continue

        ext = data["m"].split("/")[-1]

        if "u" not in data["s"]:
            continue

        base_url = data["s"]["u"].replace("&amp;", "&")

        try:
            response = requests.get(base_url)
            if response.status_code == 200:
                filename = f"{readable_name}_{post_id}_{idx}.{ext}"
                filepath = os.path.join(location, "media", filename)
                with open(filepath, "wb") as f:
                    f.write(response.content)
                filenames.append(filename)
        except Exception as e:
            logger.error(f"(reddit gallery) Error downloading {base_url}: {e}")
            return -1

    return filenames if filenames else None


def _handle_vreddit(url, post_id, readable_name, location):
    """Download video from v.redd.it using Downloader."""
    downloader = Downloader(max_q=True, log=False)
    downloader.url = url
    current = os.getcwd()

    try:
        name = downloader.download()
        extension = name.split(".")[-1]
        filename = f"{readable_name}_{post_id}.{extension}"
        filepath = os.path.join(location, "media", filename)
        os.rename(name, filepath)
        return filename
    except Exception as e:
        logger.error(f"(vreddit) Error downloading {url}: {e}")
        return -1
    finally:
        os.chdir(current)


def _handle_imgur(url, post_id, readable_name, location):
    """Download image from imgur by trying different extensions."""
    base_url = f'https://i.{url[url.find("//") + 2:]}'
    base_url = base_url.replace("i.imgur.com", "imgur.com")
    base_url = base_url.replace("m.imgur.com", "imgur.com")

    for i, extension in enumerate(IMAGE_EXTENSIONS):
        direct_url = f"{base_url}.{extension}"

        try:
            response = requests.get(direct_url)
            if response.status_code // 100 == 2:
                filename = f"{readable_name}_{post_id}.{extension}"
                filepath = os.path.join(location, "media", filename)
                with open(filepath, "wb") as f:
                    f.write(response.content)
                return filename
        except Exception as e:
            continue
    logger.error(f"(imgur) Error downloading {base_url}")
    return -1


def _handle_ytdlp(url, post_id, readable_name, location):
    """Download media using yt-dlp for supported platforms."""
    options = {
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,  # I think this was keeping us from getting the exception
        "no-progress": True,
        "outtmpl": os.path.join(
            location, "media", f"{readable_name}_{post_id}" + ".%(ext)s"
        )
    }

    current = os.getcwd()

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as e:
        logger.error(f"(ytdlp) Error downloading {url}: {e}")
        return -1
    finally:
        os.chdir(current)

    # Find the downloaded file
    media_dir = os.path.join(location, "media")
    for f in os.listdir(media_dir):
        if f.startswith(f"{readable_name}_{post_id}"):
            return f

    return None


def add_media_preview_to_html(post_html, media):
    """Takes post HTML and returns a modified version with the preview
    inserted."""
    if not isinstance(media, list):
        media = [media]

    if len(media) == 1:
        media = media[0]
        extension = media.split(".")[-1]
        location = "/".join(["media", media])
        if extension in IMAGE_EXTENSIONS:
            return post_html.replace(
                "<!--preview-->",
                f'<img src="{location}">'
            )
        if extension in VIDEO_EXTENSIONS:
            return post_html.replace(
                "<!--preview-->",
                f'<video controls><source src="{location}"></video>'
            )
    else:
        gallery = ""
        for i, media_file in enumerate(media):
            extension = media_file.split(".")[-1]
            location = "/".join(["media", media_file])
            if extension in IMAGE_EXTENSIONS:
                gallery += f'<figure><img src="{location}"><figcaption>Image {i + 1} of {len(media)}</figcaption></figure><br/><br/>'
            elif extension in VIDEO_EXTENSIONS:
                gallery += f'<video controls><source src="{location}"></video><br/>{i + 1} of {len(media)}<br/><br/>'
        post_html = post_html.replace("<!--preview-->", gallery)
    return post_html


def create_post_page_html(post, post_html):
    """Creates the HTML for a post's own page."""

    with open(os.path.join("html", "post.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("<!--title-->", post.title)
    html = html.replace("<!--post-->", post_html.replace("h2>", "h1>").replace(
        '<img src="media/', '<img src="../media/'
    ).replace(
        '<source src="media/', '<source src="../media/'
    ))
    html = re.sub(r'<a href="posts(.+?)</a>', "", html)

    # noinspection DuplicatedCode
    with open(os.path.join("html", "style.css"), encoding="utf-8") as f:
        html = html.replace("<style></style>", f"<style>\n{f.read()}\n</style>")
    with open(os.path.join("html", "main.js"), encoding="utf-8") as f:
        html = html.replace("<script></script>", f"<script>\n{f.read()}\n</script>")
    comments_html = []
    post.comments.replace_more(limit=0)
    for comment in post.comments:
        comments_html.append(get_comment_html(
            comment, op=post.author.name if post.author else None
        ))
    html = html.replace("<!--comments-->", "\n".join(comments_html))
    return html


def get_comment_html(comment, children=True, op=None):
    """Takes a post object and creates a HTML for it - it will get its children
    too unless you specify otherwise."""

    with open(os.path.join("html", "comment-div.html"), encoding="utf-8") as f:
        html = f.read()
    dt = datetime.fromtimestamp(comment.created_utc, timezone.utc)
    author = "[deleted]"
    if comment.author:
        if comment.author == op:
            author = f'<span class="op">/u/{comment.author.name}</span>'
        else:
            author = f"/u/{comment.author.name}"
    html = html.replace("<!--user-->", author)
    html = html.replace("<!--body-->", (comment.body_html or "").replace(
        '<a href="/r/', '<a href="https://reddit.com/r/'
    ))
    html = html.replace("<!--score-->", str(comment.score))
    html = html.replace("<!--link-->", f"https://reddit.com{comment.permalink}")
    html = html.replace("<!--timestamp-->", str(dt))
    html = html.replace("<!--id-->", comment.id)
    html = html.replace("<!--date-->", dt.strftime("%H:%M - %d %B, %Y"))
    if children:
        children_html = []
        for child in comment.replies:
            children_html.append(get_comment_html(child, children=False, op=op))
        html = html.replace("<!--children-->", "\n".join(children_html))
    return html


def save_html(posts, comments, location, html_file, page, has_next, username=None):
    if username:
        with open(os.path.join("html", "username.html"), encoding="utf-8") as f:
            html = f.read().replace("[username]", username)
    else:
        with open(os.path.join("html", html_file), encoding="utf-8") as f:
            html = f.read()

    # noinspection DuplicatedCode
    with open(os.path.join("html", "style.css"), encoding="utf-8") as f:
        html = html.replace("<style></style>", f"<style>\n{f.read()}\n</style>")
    with open(os.path.join("html", "main.js"), encoding="utf-8") as f:
        html = html.replace("<script></script>", f"<script>\n{f.read()}\n</script>")
    if page == 0 or page is None:
        html = html.replace("Previous</a>", "</a>")
    else:
        html = html.replace(".p.html", f".{page - 1}.html")
    if not has_next or page is None:
        html = html.replace("Next</a>", "</a>")
    else:
        html = html.replace(".n.html", f".{page + 1}.html")
    html = html.replace("<!--posts-->", "\n".join(posts))
    html = html.replace("<!--comments-->", "\n".join(comments))
    file_name = html_file if page is None else html_file.replace(".html", f".{page}.html")
    with open(os.path.join(location, file_name), "w", encoding="utf-8") as f:
        f.write(html)
