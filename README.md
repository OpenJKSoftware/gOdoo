# Wodoo Dev Environment

![OdooLogo](assets/odoo_logo.png)

[Vscode Devcontainer](https://code.visualstudio.com/docs/remote/containers) Environment for [Odoo](.odoo.com/)

Made Possible by: [WEMPE Elektronic GmbH](https://wetech.de)

## Devcontainer Features

- Devcontainer workspace [full.code-workspace](full.code-workspace) with Odoo source, Workspace and Thirdparty Source.
- `wodoo` CLI wrapper around Odoo.
- `odoo-bin` is added to PATH and can thus be invoked from every folder.
- Odoo will run in Proxy_Mode behind a Traefik reverse proxy.
- [Odoo Pylint plugin](https://github.com/OCA/pylint-odoo) preconfigured in vscode
- Preinstalled vscode Extensions Highlights:
  - [SQL Tools](https://marketplace.visualstudio.com/items?itemName=mtxr.sqltools) with preconfigured connection for
    easy Database access in the Sidebar.
  - [Docker Extension](https://marketplace.visualstudio.com/items?itemName=ms-azuretools.vscode-docker) controls
    container host.
  - [Odoo Snippets](https://marketplace.visualstudio.com/items?itemName=mstuttgart.odoo-snippets)
  - [Odoo Developments](https://marketplace.visualstudio.com/items?itemName=scapigliato.vsc-odoo-development) can Grab
    Odoo Model information from a running Server
  - [Todo Tree](https://marketplace.visualstudio.com/items?itemName=Gruntfuggly.todo-tree)

## Basic Usage

1. For Docker on windows: Clone the repo into the WSL2 Filesystem for better IO performance
2. Have [Traefik](https://github.com/traefik/traefik) Running on `docker.localhost`
   [Example](https://github.com/joshkreud/traefik_devproxy)
3. Create `.env` file (see [.env.sample](.env.sample))
4. Open Devcontianer:
   1. If you have the Devcontainer CLI: `devcontainer open .`
   2. Open the Workspace in vscode, press `Cmd+Shift+P`, select `Rebuild and open in Devcontainer`.
5. From **within the container** start Odoo using one of the following commands:
   - `make` -> Loads Odoo + Workspace Addons
   - `make bare` -> Loads Odoo with ony `web` installed.
   - `make stg` -> Loads copy of staging Odoo Server
   - The full init script is available via "`wodoo`". (See --help for Options)
6. Open Odoo `https://${COMPOSE_PROJECT_NAME}.docker.localhost`\
   For example `COMPOSE_PROJECT_NAME=wodoo` --> [https://wodoo.docker.localhost](https://wodoo.docker.localhost)
7. Login with `admin:admin`

## Access to Odoo and Thirdparty addon Source

You can access the Odoo source by opening the VsCode workspace [full.code-workspace](full.code-workspace) from within
the Container.

## Reset Devcontainer Data

### Automatic Reset

There are 3 Options to reset the Dev Env.

1. From **Outside** the Container run `make reset` in the project root to delete docker volumes and restart the
   container. (Vscode will prompt to reconnect if still open)
2. From **Outside** the Container run `make reset-hard` in the project root to force rebuild the main Odoo container and
   then do the same as `make reset`
3. From **Inside** the Container run `make reset` to drop the DB and delete varlib and the bootstrap flag.

### Manual Reset

1. Close vscode
2. Remove app and db container.
3. Remove volumes: db, odoo_thirdparty, odoo_web, vscode_extensions
4. Restart Devcontainer

## Debugging

Debugging doesn't reliably work in
[Odoo Multiprocess](https://www.odoo.com/documentation/14.0/developer/misc/other/cmdline.html#multiprocessing) mode. The
container ships with a Vscode Debug profile, that sets `--workers 0` to enable Debugging Breakpoints.

Use `wodoo shell` to enter an interactive shell.

## Odoo Modules

### Third Party Modules

The `wodoo` bootstrap function, will download some modules using git. \
Which Repos to download is specified in `odoo_repospec.yml` \
Not all of the cloned addons are automatically installed. \
Install them via the Apps Page in Odoo or using `odoo-bin`.\
Others can be dropped as a `.zip` archive in [./thirdparty](./thirdparty)
