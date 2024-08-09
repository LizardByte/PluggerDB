# PluggerDB

> :warning: **Attention**: Plex is removing ALL support for plugins. This project is no longer maintained.
> See [Plex Forum](https://forums.plex.tv/t/important-information-for-users-running-plex-media-server-on-nvidia-shield-devices/883484)
> for more information.

PluggerDB is a database of Plex Media Server Plugins.

The database is created using codeless contributions.

## Contributing

### Adding/Updating Plugins

1. Create a new issue by following this link:

    [Plugin](https://github.com/LizardByte/PluggerDB/issues/new?assignees=&labels=request-plugin&template=plugin.yml&title=%5BPLUGIN%5D%3A+)

2. Add the requested information to the issue.
3. Submit the issue.

A label will be added to the request. i.e. `request-plugin`

A workflow will run. If necessary the title of the issue will be updated. Additionally, a comment will be added to the
issue. If there are any issues with the submission, the comment will contain the error message in the first section.
The remaining information in the comment is to assist with the review process.

### Content Review

Submitted "issues" will be reviewed by a developer/moderator. Once approved we will add a label, i.e. `add-plugin`.
At this point, the workflow will run and attempt to update the database in the
[database](https://github.com/LizardByte/PluggerDB/tree/database) branch.

## Daily updates

The database will be pushed to the [gh-pages](https://github.com/LizardByte/PluggerDB/tree/gh-pages) branch, once daily
at UTC 12:00. Plugins will not be available until they are published.

## Projects using PluggerDB

- [Plugger](https://github.com/LizardByte/Plugger)
