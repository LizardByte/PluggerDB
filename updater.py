# standard imports
import argparse
import json
import os
import re
from queue import Queue
import threading
import time
from typing import Callable, Optional

# lib imports
import requests

# load env
from dotenv import load_dotenv
load_dotenv()

# setup queue and lock
queue = Queue()
lock = threading.RLock()

# GitHub headers
github_headers = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'Bearer {os.getenv("GH_TOKEN")}'
}

plugin_file = os.path.join('database', 'plugins.json')
if os.path.isfile(plugin_file):
    with open(file=plugin_file, mode='r') as og_f:
        og_data = json.load(fp=og_f)  # get currently saved data
else:
    og_data = dict()


def exception_writer(error: Exception, site: str):
    print(f'Error processing {site} url: {error}')

    files = ['comment.md', 'exceptions.md']
    for file in files:
        with open(file, "a") as f:
            f.write(f'# :bangbang: **Exception Occurred** :bangbang:\n\n```txt\n{error}\n```\n\n')


def requests_loop(url: str,
                  headers: Optional[dict] = None,
                  method: Callable = requests.get,
                  max_tries: int = 10) -> requests.Response:
    count = 0
    while count <= max_tries:
        try:
            response = method(url=url, headers=headers)
            if response.status_code == requests.codes.ok:
                return response
        except requests.exceptions.RequestException:
            time.sleep(2**count)
            count += 1


def process_queue() -> None:
    """
    Add items to the queue.
    This is an endless loop to add items to the queue.
    Examples
    --------
    >>> threads = threading.Thread(target=process_queue, daemon=True)
    ...
    """
    while True:
        item = queue.get()
        queue_handler(item=item)  # process the item from the queue
        queue.task_done()  # tells the queue that we are done with this item


def queue_handler(item: str) -> None:
    git_owner, git_repo = check_github(data=dict(github_url=item))

    process_github_url(owner=git_owner, repo=git_repo)


# create multiple threads for processing items faster
# number of threads
for t in range(3):
    try:
        # for each thread, start it
        t = threading.Thread(target=process_queue)
        # when we set daemon to true, that thread will end when the main thread ends
        t.daemon = True
        # start the daemon thread
        t.start()
    except RuntimeError as r_e:
        print(f'RuntimeError encountered: {r_e}')
        break


def process_github_url(owner: str, repo: str, categories: Optional[str] = None) -> dict:
    api_repo_url = f'https://api.github.com/repos/{owner}/{repo}'
    response = requests_loop(url=api_repo_url, headers=github_headers)

    github_data = response.json()

    issue_response = requests_loop(url=f'{api_repo_url}/issues', headers=github_headers)
    issue_data = issue_response.json()

    open_issues = 0
    open_pull_requests = 0
    for issue in issue_data:
        try:
            issue['pull_request']
        except KeyError:
            open_issues += 1
        else:
            open_pull_requests += 1

    try:
        github_data['id']
    except KeyError as e:
        raise Exception(f'Error processing plugin: {e}')
    else:
        with lock:
            og_data[str(github_data['id'])] = {
                'name': github_data['name'],
                'full_name': github_data['full_name'],
                'description': github_data['description'],
                'avatar_url': github_data['owner']['avatar_url'],
                'html_url': github_data['html_url'],
                'homepage': github_data['homepage'],
                'stargazers_count': github_data['stargazers_count'],
                'forks_count': github_data['forks_count'],
                'open_issues_count': open_issues,
                'open_pull_requests_count': open_pull_requests,
                'has_issues': github_data['has_issues'],
                'has_downloads': github_data['has_downloads'],
                'has_wiki': github_data['has_wiki'],
                'has_discussions': github_data['has_discussions'],
                'archived': github_data['archived'],
                'disabled': github_data['disabled'],
                'license': github_data['license']['name'],
                'license_url': github_data['license']['url'],
                'default_branch': github_data['default_branch'],
            }
        try:
            args.issue_update
        except NameError:
            pass
        else:
            if args.issue_update:
                # add the categories to the data
                if categories:
                    categories = categories.split(', ')
                    og_data[str(github_data['id'])]['categories'] = categories
                else:
                    exception_writer(Exception('No categories selected'), site='GitHub')
                    categories = ':bangbang: NONE :bangbang:'

                # create the issue comment and title files
                issue_comment = f"""
| Property | Value |
| --- | --- |
| name | {github_data['name']} |
| full_name | {github_data['full_name']} |
| description | {github_data['description']} |
| avatar_url | {github_data['owner']['avatar_url']} |
| html_url | {github_data['html_url']} |
| homepage | {github_data['homepage']} |
| stargazers_count | {github_data['stargazers_count']} |
| forks_count | {github_data['forks_count']} |
| open_issues_count | {open_issues} |
| open_pull_requests_count | {open_pull_requests} |
| has_issues | {github_data['has_issues']} |
| has_downloads | {github_data['has_downloads']} |
| has_wiki | {github_data['has_wiki']} |
| has_discussions | {github_data['has_discussions']} |
| archived | {github_data['archived']} |
| disabled | {github_data['disabled']} |
| license | {github_data['license']['name']} |
| license_url | {github_data['license']['url']} |
| default_branch | {github_data['default_branch']} |
| categories | {categories} |
"""
                with open("comment.md", "a") as comment_f:
                    comment_f.write(issue_comment)

                with open("title.md", "w") as title_f:
                    title_f.write(f'[PLUGIN]: {github_data["full_name"]}')

                # update user ids
                original_submission = False
                with lock:
                    try:
                        og_data[str(github_data['id'])]['plugin_added_by']
                    except KeyError:
                        original_submission = True
                        og_data[str(github_data['id'])]['plugin_added_by'] = os.environ['ISSUE_AUTHOR_USER_ID']
                    finally:
                        og_data[str(github_data['id'])]['plugin_edited_by'] = os.environ['ISSUE_AUTHOR_USER_ID']

                # update contributor info
                update_contributor_info(original=original_submission, base_dir='database')

    return github_data


def update_contributor_info(original: bool, base_dir: str) -> None:
    contributor_file_path = os.path.join(base_dir, 'contributors.json')

    # create file if it doesn't exist
    if not os.path.exists(contributor_file_path):
        with open(contributor_file_path, 'w') as contributor_f:
            json.dump(obj={}, indent=4, fp=contributor_f, sort_keys=True)

    with open(contributor_file_path, 'r') as contributor_f:
        contributor_data = json.load(contributor_f)
        try:
            contributor_data[os.environ['ISSUE_AUTHOR_USER_ID']]
        except KeyError:
            contributor_data[os.environ['ISSUE_AUTHOR_USER_ID']] = dict(
                items_added=1,
                items_edited=0
            )
        else:
            if original:
                contributor_data[os.environ['ISSUE_AUTHOR_USER_ID']]['items_added'] += 1
            else:
                contributor_data[os.environ['ISSUE_AUTHOR_USER_ID']]['items_edited'] += 1

    with open(contributor_file_path, 'w') as contributor_f:
        json.dump(obj=contributor_data, indent=4, fp=contributor_f, sort_keys=True)


def process_issue_update() -> None:
    # process submission file
    submission = process_submission()

    # check validity of provided GitHub url
    git_owner, git_repo = check_github(data=submission)

    process_github_url(owner=git_owner, repo=git_repo, categories=submission['categories'])


def check_github(data: dict) -> tuple:
    print('Checking GitHub url')
    url = data['github_url'].strip()
    print(f'github_url: {url}')

    # extract GitHub user and repo from url using regex
    match = re.search(pattern=r'github.com/([a-zA-Z0-9-]+)/([a-zA-Z0-9-]+)', string=url)
    if match:
        owner = match.group(1)
        repo = match.group(2)

        return owner, repo

    else:
        raise SystemExit('Invalid GitHub url')


def process_submission() -> dict:
    with open(file='submission.json') as file:
        data = json.load(file)

    return data


if __name__ == '__main__':
    # setup arguments using argparse
    parser = argparse.ArgumentParser(description="Add plugin to database.")
    parser.add_argument('--daily_update', action='store_true', help='Run in daily update mode.')
    parser.add_argument('--issue_update', action='store_true', help='Run in issue update mode.')

    args = parser.parse_args()

    destination_dir = os.path.dirname(plugin_file)
    os.makedirs(name=destination_dir, exist_ok=True)  # create directory if it doesn't exist

    if args.issue_update:
        process_issue_update()

    elif args.daily_update:
        # migration tasks go here

        for key in og_data:
            queue.put(og_data[key]['html_url'])

        # finish queue before writing final files
        queue.join()

    with open(plugin_file, "w") as dest_f:
        json.dump(obj=og_data, indent=4, fp=dest_f, sort_keys=True)
