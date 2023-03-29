# standard imports
import argparse
from datetime import datetime
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


def exception_writer(error: Exception, name: str, end_program: bool = False) -> None:
    print(f'Error processing {name}: {error}')

    files = ['comment.md', 'exceptions.md']
    for file in files:
        with open(file, "a") as f:
            f.write(f'# :bangbang: **Exception Occurred** :bangbang:\n\n```txt\n{error}\n```\n\n')

    if end_program:
        raise error


def requests_loop(url: str,
                  headers: Optional[dict] = None,
                  method: Callable = requests.get,
                  max_tries: int = 8,
                  allow_statuses: list = [requests.codes.ok],
                  github_wait: bool = False) -> requests.Response:
    count = 1
    while count <= max_tries:
        if github_wait:
            wait_github_api_limit(headers=headers, resources=['core'])  # core is the only resource we're using

        print(f'Processing {url} ... (attempt {count} of {max_tries})')
        try:
            response = method(url=url, headers=headers)
        except requests.exceptions.RequestException as e:
            print(f'Error processing {url} - {e}')
            time.sleep(2 ** count)
            count += 1
        except Exception as e:
            print(f'Error processing {url} - {e}')
            time.sleep(2 ** count)
            count += 1
        else:
            if response.status_code in allow_statuses:
                return response
            else:
                print(f'Error processing {url} - {response.status_code}')
                time.sleep(2 ** count)
                count += 1


def wait_github_api_limit(headers: Optional[dict] = None, resources: Optional[list] = None) -> None:
    while True:
        # test if we are hitting the GitHub API limit
        response = requests.get(url='https://api.github.com/rate_limit', headers=headers)
        rate_limit = response.json()

        # current time as a UTC timestamp
        current_time = datetime.utcnow().timestamp()

        # don't use more than 1/4 of the rate limit
        sleep_time = 0
        if not resources:
            if rate_limit['rate']['limit'] > 0 and rate_limit['rate']['remaining'] < rate_limit['rate']['limit'] / 4:
                wait_time = rate_limit['rate']['reset'] - current_time
                print(f'rate wait_time: {wait_time}')
                sleep_time = wait_time if wait_time > sleep_time else sleep_time

        for resource in rate_limit['resources']:
            if (resources and resource in resources) or not resources:
                if rate_limit['resources'][resource]['limit'] > 0 and \
                        rate_limit['resources'][resource]['remaining'] < rate_limit['resources'][resource]['limit'] / 4:
                    wait_time = rate_limit['resources'][resource]['reset'] - current_time
                    print(f'{resource} wait_time: {wait_time}')
                    sleep_time = wait_time if wait_time > sleep_time else sleep_time

        if sleep_time > 0:
            print(f'Waiting {sleep_time} seconds to avoid GitHub API limit...')
            time.sleep(sleep_time)
        else:
            return


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
for t in range(10):
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


def process_github_url(owner: str, repo: str, submission: Optional[dict] = None) -> dict:
    api_repo_url = f'https://api.github.com/repos/{owner}/{repo}'
    response = requests_loop(url=api_repo_url, headers=github_headers, github_wait=True)

    github_data = response.json()

    try:
        github_data['id']
    except KeyError as e:
        raise Exception(f'Error processing plugin: {e}')
    else:
        # get issues data
        issue_data = requests_loop(url=f'{api_repo_url}/issues', headers=github_headers, github_wait=True).json()
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
                                     allow_statuses=[requests.codes.ok, 404], github_wait=True)
            if response.status_code == 404:
                gh_pages_url = None
            else:
                gh_pages_data = response.json()
                gh_pages_url = gh_pages_data['html_url']
        else:
            gh_pages_url = None

        # setup downloads, i.e. releases and branches data
        downloads = []

        # get releases data
        releases_data = requests_loop(url=f'{api_repo_url}/releases', headers=github_headers, github_wait=True).json()
        for release in releases_data:
            if release['draft']:
                continue
            download_assets = dict()
            if release['assets']:
                for asset in release['assets']:
                    if asset['name'].lower().endswith('.zip'):
                        download_assets[asset['name']] = asset['browser_download_url']

            # add the zipball url at the end
            download_assets['zipball'] = release['zipball_url']
            downloads.append(dict(
                type='release',
                date=release['published_at'],
                name=release['name'],
                release_tag=release['tag_name'],
                download_assets=download_assets,
                prerelease=release['prerelease'],
            ))

        # get branch data
        branches_data = requests_loop(url=f'{api_repo_url}/branches', headers=github_headers, github_wait=True).json()
        for branch in branches_data:
            # get commit date
            commit_data = requests_loop(url=f'{api_repo_url}/commits/{branch["commit"]["sha"]}',
                                        headers=github_headers, github_wait=True).json()
            date = commit_data['commit']['author']['date']

            download_assets = dict(
                zipball=f'https://github.com/{owner}/{repo}/archive/refs/heads/{branch["name"]}.zip'
            )

            downloads.append(dict(
                type='branch',
                date=date,
                name=branch['name'],
                commit_sha=branch['commit']['sha'],
                download_assets=download_assets,
                default_branch=True if branch['name'] == github_data['default_branch'] else False,
            ))

        # sort downloads by date
        downloads = sorted(downloads, key=lambda sort_key: sort_key['date'], reverse=True)

        # find icon-default.png in repo and use that as the thumb icon
        image_extensions = ['png', 'jpg', 'jpeg']
        directory_list = ['Contents', 'Resources']
        attribution_image_url = None
        thumb_image_url = None
        path = ''

        loop = True  # loop while this is true
        while loop:
            next_loop = False
            repo_contents = requests_loop(
                url=f'{api_repo_url}/contents{path}',
                headers=github_headers,
                max_tries=5,
                github_wait=True
            ).json()

            for item in repo_contents:
                # directories
                if item['type'] == 'dir' and (item['name'] in directory_list or item['name'].endswith('.bundle')):
                    path = f'{path}/{item["name"]}'
                    next_loop = True
                    break  # break the for loop and continue the while loop
                elif item['type'] == 'file' and item['name'].rsplit('.', 1)[-1] in image_extensions:
                    file_name = item['name'].rsplit('.', 1)[0]
                    if file_name == 'icon-default':
                        thumb_image_url = item['download_url']
                    elif file_name == 'attribution':
                        attribution_image_url = item['download_url']
            loop = next_loop

        # get the original data, not available through APIs
        non_github_data = dict()

        categories = None
        scanner_mapping = None

        try:
            args
        except NameError:
            pass
        else:
            # move these keys to non GitHub data dict as they don't exist in the GitHub API
            obsolete_keys = [
                'branches',
                'releases',
            ]
            try:
                for k in og_data[str(github_data['id'])]:
                    if k in obsolete_keys:
                        continue
                    if k not in github_data:
                        non_github_data[k] = og_data[str(github_data['id'])][k]
            except KeyError as e:
                if args.daily_update:
                    exception_writer(error=Exception(f'Error processing plugin: {e}'), name='og_data', end_program=True)
                # okay if issue update

            if args.daily_update:
                categories = og_data[str(github_data['id'])]['categories']

                try:
                    scanner_mapping = og_data[str(github_data['id'])]['scanner_mapping']
                except KeyError:
                    scanner_mapping = dict(  # default dictionary for migration purposes
                        Common=[],
                        Movies=[],
                        Music=[],
                        Series=[]
                    )
            elif args.issue_update:
                # add the categories to the data
                if submission['categories']:
                    categories = submission['categories']
                else:
                    exception_writer(error=Exception('No categories selected'), name='categories')
                    categories = ':bangbang: NONE :bangbang:'

                scanner_mapping = submission['scanner_mapping']

                scanners = []

                # check the scanner mapping
                for k in scanner_mapping:
                    if scanner_mapping[k]:
                        for scanner in scanner_mapping[k]:
                            if not scanner.endswith('.py'):
                                exception_writer(error=Exception(f'Invalid file extension for scanner: {scanner}'),
                                                 name='scanner_mapping')
                                break

                            file_check_response = requests_loop(
                                url=f'{api_repo_url}/contents/{scanner}',
                                headers=github_headers,
                                max_tries=5,
                                github_wait=True,
                                allow_statuses=[requests.codes.ok, 404]  # process 404 later, reduce API usage
                            )

                            if not file_check_response or file_check_response.status_code == 404:
                                exception_writer(error=Exception(f'Invalid scanner path: {scanner}'),
                                                 name='scanner_mapping')
                                break

                            # check if file
                            if file_check_response.json()['type'] != 'file':
                                exception_writer(error=Exception(f'Found "{scanner}" but it is not a file.'),
                                                 name='scanner_mapping')
                                break

                            # if we made it this far, add the scanner to the list
                            scanners.append(scanner)

                if "Scanner" in categories:
                    if not scanners:
                        # check if "Scanners" directory exists
                        file_check_response = requests_loop(
                            url=f'{api_repo_url}/contents/Scanners',
                            headers=github_headers,
                            max_tries=5,
                            github_wait=True
                        )

                        if not file_check_response:
                            exception_writer(error=Exception('No "Scanners" directory found in repo.'), name='scanners')
                        else:
                            file_check_data = file_check_response.json()
                            # check if directory
                            valid_scanner_directories = ['Common', 'Movies', 'Music', 'Series']
                            for item in file_check_data:
                                if item['name'] in valid_scanner_directories:
                                    # check if directory
                                    if item['type'] != 'dir':
                                        exception_writer(
                                            error=Exception(f'Found "{item["name"]}" but it is not a directory.'),
                                            name='scanners')
                                    else:
                                        # assume scanner(s) are present in the directory
                                        scanners = True
                                        break

                    if not scanners:
                        exception_writer(error=Exception('No valid scanners found.'), name='scanners')

        with lock:
            # only GitHub data first, where keys match exactly
            og_data[str(github_data['id'])] = {
                'archived': github_data['archived'],
                'default_branch': github_data['default_branch'],
                'description': github_data['description'],
                'disabled': github_data['disabled'],
                'forks_count': github_data['forks_count'],
                'full_name': github_data['full_name'],
                'has_discussions': github_data['has_discussions'],
                'has_downloads': github_data['has_downloads'],
                'has_issues': github_data['has_issues'],
                'has_wiki': github_data['has_wiki'],
                'homepage': github_data['homepage'],
                'html_url': github_data['html_url'],
                'name': github_data['name'],
                'stargazers_count': github_data['stargazers_count'],
            }

            # combine the non-github data
            og_data[str(github_data['id'])].update(non_github_data)

            # then add data where keys don't match, or value adjusted manually
            og_data[str(github_data['id'])]['attribution_image_url'] = attribution_image_url
            og_data[str(github_data['id'])]['avatar_image_url'] = github_data['owner']['avatar_url']
            og_data[str(github_data['id'])]['categories'] = categories
            og_data[str(github_data['id'])]['downloads'] = downloads
            og_data[str(github_data['id'])]['gh_pages_url'] = gh_pages_url
            og_data[str(github_data['id'])]['license'] = None if not github_data['license'] else \
                github_data['license']['name']
            og_data[str(github_data['id'])]['license_url'] = None if not github_data['license'] else \
                github_data['license']['url']
            og_data[str(github_data['id'])]['open_issues_count'] = open_issues
            og_data[str(github_data['id'])]['open_pull_requests_count'] = open_pull_requests
            og_data[str(github_data['id'])]['scanner_mapping'] = scanner_mapping
            og_data[str(github_data['id'])]['thumb_image_url'] = thumb_image_url

            # remove `.bundle` from end of name and full name
            test = '.bundle'
            t_len = len(test)
            test_keys = ['name', 'full_name']
            for k in test_keys:
                if og_data[str(github_data['id'])][k].endswith(test):
                    og_data[str(github_data['id'])][k] = og_data[str(github_data['id'])][k][:-t_len]

        # test wiki pages and overwrite value if wiki is empty
        with lock:  # ensure only one thread is making a request to GitHub at a time
            if github_data['has_wiki']:
                test_url = f'https://github.com/search?q=repo:{owner}/{repo}&type=wikis'
                test_wiki = requests_loop(url=test_url, github_wait=True)
                if test_wiki.status_code == requests.codes.ok:
                    # see if string in contents
                    # not logged in
                    if f'We couldn’t find any wiki pages matching &#39;repo:{owner}/{repo}&#39;' in test_wiki.text:
                        og_data[str(github_data['id'])]['has_wiki'] = False
                    # logged in
                    if 'Your search did not match any <!-- -->wikis' in test_wiki.text:
                        og_data[str(github_data['id'])]['has_wiki'] = False
                else:
                    og_data[str(github_data['id'])]['has_wiki'] = False
                    exception_writer(error=Exception(f'Unable to search wiki for {owner}/{repo}'), name='GitHub Wiki')

        try:
            args.issue_update
        except NameError:
            pass
        else:
            if args.issue_update:
                # create the issue comment and title files
                issue_comment = """
| Property | Value |
| --- | --- |
"""
                # dynamically create the Markdown table
                for data_key, value in og_data[str(github_data['id'])].items():
                    if 'image_url' in data_key and 'avatar' not in data_key and value:
                        issue_comment += f'| {data_key} | ![{data_key}]({value}) |\n'
                    else:
                        issue_comment += f'| {data_key} | {value} |\n'

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

    process_github_url(owner=git_owner, repo=git_repo, submission=submission)


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

    # convert string to list
    try:
        data['categories'] = data['categories'].split(', ')
    except KeyError:
        exception_writer(error=Exception('No categories provided'), name='categories', end_program=True)

    # convert json string to dict, removing ```JSON from start and ``` from end of string
    try:
        data['scanner_mapping'] = json.loads(data['scanner_mapping'].strip().strip('`').strip('JSON'))
    except KeyError:
        exception_writer(error=Exception('No scanner mapping provided'), name='scanner_mapping', end_program=True)
    except json.decoder.JSONDecodeError:
        exception_writer(error=Exception('Invalid scanner mapping provided'), name='scanner_mapping', end_program=True)

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
