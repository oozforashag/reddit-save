import random
from bs4 import BeautifulSoup


def sample_posts(save_root, sample_size=200):
    """ Write a random selection of posts from the saved and upvoted posts to a new HTML file. """
    # Read the original HTML file
    with open(fr"{save_root}\_updoot\upvoted.html", 'r', encoding='utf-8') as file:
        updt_content = file.read()

    with open(fr"{save_root}\_save\saved.html", 'r', encoding='utf-8') as file:
        save_content = file.read()

    # Parse the HTML content
    soup_up = BeautifulSoup(updt_content, 'html.parser')
    img_tags = soup_up.find_all('img')
    for img in img_tags:
        img['src'] = '_updoot/' + img['src']

    source_tags = soup_up.find_all('source')
    for source in source_tags:
        source['src'] = '_updoot/' + source['src']

    soup_sv = BeautifulSoup(save_content, 'html.parser')
    img_tags = soup_sv.find_all('img')
    for img in img_tags:
        img['src'] = '_save/' + img['src']

    source_tags = soup_sv.find_all('source')
    for source in source_tags:
        source['src'] = '_save/' + source['src']

    # Find all divs with class 'post'
    posts_up = soup_up.find_all('div', class_='post')
    posts_sv = soup_sv.find_all('div', class_='post')

    # Take a random sample of the posts
    sampled_all = []
    sampled_all.extend(random.sample(posts_up, min(sample_size, len(posts_up))))
    sampled_all.extend(random.sample(posts_sv, min(sample_size, len(posts_sv))))
    random.shuffle(sampled_all)

    # Create a new soup_up for the output with the original head
    new_soup = BeautifulSoup('<html><head></head><body></body></html>', 'html.parser')

    # Copy the head from the original soup_up
    new_soup.head.extend(soup_up.head.contents)

    # Add the sampled posts to the new body
    body = new_soup.body
    for post in sampled_all:
        body.append(post)

    # Write the new HTML to the output file
    with open(fr"{save_root}\__sample.html", 'w', encoding='utf-8') as file:
        file.write(str(new_soup))


sample_posts(save_root=r"S:\foo\reddit-save")
