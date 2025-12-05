#!/usr/bin/env python 

import argparse
import sys

from tqdm import tqdm
from utilities import *
from colorlogger import logger
from pathlib import Path


def validate_mode(mode: str) -> str:
    """Validate the mode argument."""
    if mode in ["saved", "upvoted"]:
        return mode
    if mode.startswith("user:"):
        return mode
    raise argparse.ArgumentTypeError(f"Invalid mode: {mode}")


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Save reddit posts to file.")
    parser.add_argument("--mode", type=validate_mode, required=True,
                        help="Mode: 'saved', 'upvoted', or 'user:USERNAME'")
    parser.add_argument("--location", type=str,
                        help="The path to save to (default: ./archive/ in Docker)")
    parser.add_argument("--page-size", type=int, default=0,
                        help="The number of posts to save per page.")
    parser.add_argument("--blacklist", type=str,
                        help="Path to blacklist file containing post IDs to skip")

    args = parser.parse_args()

    # Set location based on environment
    if os.getenv("DOCKER", "0") == "1":
        args.location = "./archive/"
    else:
        if not args.location:
            parser.error("--location is required when not running in Docker")

    assert os.path.isdir(args.location), f"{args.location} is not a directory!"

    # Validate blacklist file if provided
    if args.blacklist and not os.path.isfile(args.blacklist):
        logger.info(f"Creating blacklist file: {args.blacklist}")
        Path(args.blacklist).touch()

    return args


def configure_mode(mode: str):
    """Configure post/comment getters and HTML filename based on mode."""
    if mode == "saved":
        html_file = "saved.html"
        get_posts = get_saved_posts
        get_comments = get_saved_comments
        username = "saved"
    elif mode == "upvoted":
        html_file = "upvoted.html"
        get_posts = get_upvoted_posts
        get_comments = lambda c: []
        username = "upvoted"
    elif mode.startswith("user:"):
        username = mode.split(":")[-1]
        html_file = f"{username}.html"
        get_posts = lambda c: get_user_posts(c, username)
        get_comments = lambda c: get_user_comments(c, username)
    else:
        logger.error("Invalid mode.")
        sys.exit(1)

    return html_file, get_posts, get_comments, username


def ensure_directories(location: str):
    """Create media and posts directories if they don't exist."""
    os.makedirs(os.path.join(location, "media"), exist_ok=True)
    os.makedirs(os.path.join(location, "posts"), exist_ok=True)


def process_posts(posts, location: str, existing_posts_html: list, blacklist_file: str = None) -> list:
    """Process posts, download media, and generate HTML."""
    posts_html = []

    blacklisted_ids = set()
    if blacklist_file and os.path.exists(blacklist_file):
        with open(blacklist_file, "r") as f:
            blacklisted_ids = {line.strip() for line in f if line.strip()}
        logger.info(f"Ignoring {len(blacklisted_ids)} blacklisted posts.")

    if not posts:
        logger.info("No new posts to process!")
        return existing_posts_html

    for post in posts:
        if post.id in blacklisted_ids:
            continue

        post_html = get_post_html(post)
        media = save_media(post, location)

        # If media is -1, that's because the fetch failed.  The post shouldn't be added to the HTML
        #  because it won't look like the author intended; warn and skip.
        #  If it's truthy but not -1, then we'll assume that the media was successfully downloaded
        #  and add it to the HTML.
        if media == -1:
            logger.error(f'Skipping post {post.id}, which contained unfetchable media: "{post.title}"')
            blacklisted_ids.add(post.id)
            continue
        elif media:
            post_html = add_media_preview_to_html(post_html, media)

        posts_html.append(post_html)

        # Save individual post page
        page_html = create_post_page_html(post, post_html)
        post_file = os.path.join(location, "posts", f"{post.id}.html")
        with open(post_file, "w", encoding="utf-8") as f:
            f.write(page_html)

    if blacklist_file:
        logger.info("Updating blacklist.")
        with open(blacklist_file, "w") as f:
            f.writelines(f"{x}\n" for x in sorted(blacklisted_ids))

    return posts_html + existing_posts_html


def process_comments(comments, existing_comments_html: list) -> list:
    """Process comments and generate HTML."""
    comments_html = []

    if not comments:
        logger.info("No new comments to process!")
        return existing_comments_html

    for comment in tqdm(comments, desc="Processing comments"):
        comment_html = get_comment_html(comment)
        # Note: Comments typically don't have media, but we could handle it if needed
        # media = save_media(comment, location)
        comments_html.append(comment_html)

    return comments_html + existing_comments_html


def save_paginated_html(posts_html: list, comments_html: list, location: str,
                        html_file: str, page_size: int, username: str):
    """Save HTML with pagination if page_size is specified."""
    if not page_size:
        return

    length = max(len(posts_html), len(comments_html))
    page_count = (length + page_size - 1) // page_size  # Ceiling division

    for i in range(page_count):
        start_idx = i * page_size
        end_idx = (i + 1) * page_size

        posts_on_page = posts_html[start_idx:end_idx]
        comments_on_page = comments_html[start_idx:end_idx]
        has_next = i < page_count - 1

        save_html(posts_on_page, comments_on_page, location, html_file,
                  i, has_next, username=username)


def main():
    """Main entry point for the Reddit archiver."""
    clas = parse_arguments()
    mode = clas.mode
    location = clas.location
    page_size = clas.page_size
    blacklist_file = clas.blacklist

    # Configure based on mode
    client = make_client()
    html_file, get_posts, get_comments, username = configure_mode(mode)

    # Ensure directories exist
    ensure_directories(location)

    # Load existing content
    logger.info(f"Getting previously {mode} posts and comments...")
    existing_ids, existing_posts_html, existing_comments_html = get_previous(location, html_file)

    # Get new posts and comments.  Skip blacklisted posts.
    new_posts = sorted([p for p in get_posts(client) if p.id not in existing_ids],key=lambda p: p.id)
    new_comments = sorted([c for c in get_comments(client) if c.id not in existing_ids], key=lambda c: c.id)

    # Make one message showing the number of new and existing posts/comments.
    msg = f"You have {len(existing_posts_html)} existing {mode} post(s) and {len(existing_comments_html)} comment(s)."
    msg += f"\nFound {len(new_posts)} new {mode} post(s) and {len(new_comments)} comment(s)."
    logger.info(msg)

    # Process everything
    posts_html = process_posts(new_posts, location, existing_posts_html, blacklist_file=blacklist_file)
    comments_html = process_comments(new_comments, existing_comments_html)

    # Save HTML
    logger.info("Saving HTML...")
    save_paginated_html(posts_html, comments_html, location, html_file, page_size, username)
    save_html(posts_html, comments_html, location, html_file, None, False,
              username=username)


if __name__ == "__main__":
    main()
