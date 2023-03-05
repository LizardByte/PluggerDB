"""
test_updater.py

This module contains unit tests for the updater module. The updater module is responsible for
verifying and updating requests to update the PluggerDB database. The tests in this module ensure that the
updater module is functioning correctly by validating URLs and checking that the correct IDs are returned.
"""
# standard imports

# local imports
import updater

valid_submission = dict(
    github_url='https://github.com/LizardByte/Plugger',
    categories='Utility',
    other_category='',
    additional_comments='',
    scanner_mapping="{\n  \"Common\": [\n  ],\n  \"Movies\": [\n  ],\n  \"Music\": [\n  ],\n  \"Series\": [\n  ]\n}\n"
)


def test_check_github():
    """Tests if the provided YouTube url is valid and returns a valid url."""
    git_owner, git_repo = updater.check_github(data=valid_submission)

    assert git_owner == 'LizardByte'
    assert git_repo == 'Plugger'


def test_process_github_url():
    """Tests if the provided GitHub url is valid and returns a valid url."""
    git_owner, git_repo = updater.check_github(data=valid_submission)
    github_data = updater.process_github_url(owner=git_owner, repo=git_repo)

    assert github_data is not None
    assert github_data['id']
    assert github_data['name'] == 'Plugger'
    assert github_data['full_name'] == 'LizardByte/Plugger'
    assert github_data['html_url'] == valid_submission['github_url']
