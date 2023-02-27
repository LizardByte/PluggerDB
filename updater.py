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
lock = threading.Lock()

# GitHub headers
github_headers = {
    'Accept': 'application/vnd.github+json',
    'Authorization': f'Bearer {os.getenv("PAT_TOKEN") if os.getenv("PAT_TOKEN") else os.getenv("GH_TOKEN")}'
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
                  max_tries: int = 10,
                  allow_statuses: list = [requests.codes.ok]) -> requests.Response:
    count = 0
    while count <= max_tries:
        print(f'Processing {url} ... (attempt {count + 1} of {max_tries})')
        try:
            response = method(url=url, headers=headers)
        except requests.exceptions.RequestException as e:
            print(f'Error processing {url} - {e}')
            time.sleep(2**count)
            count += 1
        except Exception as e:
            print(f'Error processing {url} - {e}')
            time.sleep(2**count)
            count += 1
        else:
            if response.status_code in allow_statuses:
                return response
            else:
                print(f'Error processing {url} - {response.status_code}')
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

    try:
        github_data['id']
    except KeyError as e:
        raise Exception(f'Error processing plugin: {e}')
    else:
        # get issues data
        issue_data = requests_loop(url=f'{api_repo_url}/issues', headers=github_headers).json()
        open_issues = 0
        open_pull_requests = 0
        for issue in issue_data:
            try:
                issue['pull_request']
            except KeyError:
                open_issues += 1
            else:
                open_pull_requests += 1

        # get gh-pages data, this will return a 404 if the repo doesn't have gh-pages
        # GitHub token requires repo scope for this end point, so can't use this for PRs from forks :(
        if os.getenv("PAT_TOKEN"):
            response = requests_loop(url=f'{api_repo_url}/pages', headers=github_headers,
                                     allow_statuses=[requests.codes.ok, 404])
            if response.status_code == 404:
                gh_pages_url = None
            else:
                gh_pages_data = response.json()
                gh_pages_url = gh_pages_data['html_url']
        else:
            gh_pages_url = None

        # get releases data
        releases_data = requests_loop(url=f'{api_repo_url}/releases', headers=github_headers).json()
        releases = []
        for release in releases_data:
            if release['draft']:
                continue
            bundle_url = None
            if release['assets']:
                for asset in release['assets']:
                    if asset['name'].lower().endswith('bundle.zip'):
                        bundle_url = asset['browser_download_url']
                        break
            if not bundle_url:
                bundle_url = release['zipball_url']
            releases.append(dict(
                tag_name=release['tag_name'],
                name=release['name'],
                prerelease=release['prerelease'],
                release_date=release['published_at'],
                bundle_url=bundle_url,
            ))

        # get branch data
        branches_data = requests_loop(url=f'{api_repo_url}/branches', headers=github_headers).json()
        branches = []
        for branch in branches_data:
            # get commit date
            commit_data = requests_loop(url=f'{api_repo_url}/commits/{branch["commit"]["sha"]}',
                                        headers=github_headers).json()
            commit_date = commit_data['commit']['author']['date']

            branches.append(dict(
                name=branch['name'],
                commit_sha=branch['commit']['sha'],
                commit_date=commit_date,
                download_url=f'https://github.com/{owner}/{repo}/archive/refs/heads/{branch["name"]}.zip',
            ))

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
                'license': None if not github_data['license'] else github_data['license']['name'],
                'license_url': None if not github_data['license'] else github_data['license']['url'],
                'default_branch': github_data['default_branch'],
                'gh_pages_url': gh_pages_url,
                'releases': releases,
                'branches': branches,
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
| license | {None if not github_data['license'] else github_data['license']['name']} |
| license_url | {None if not github_data['license'] else github_data['license']['url']} |
| default_branch | {github_data['default_branch']} |
| gh_pages_url | {gh_pages_url} |
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
    match = re.search(pattern=r'github\.com/([a-zA-Z0-9-]+)/(.*)/?.*', string=url)
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
