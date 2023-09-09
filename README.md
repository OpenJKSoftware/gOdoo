# gOdoo Dev Environment

![OdooLogo](https://raw.githubusercontent.com/OpenJKSoftware/gOdoo/main/assets/odoo_logo.png)
![ComposeLogo](https://raw.githubusercontent.com/docker/compose/v2/logo.png)

[<img src="https://raw.githubusercontent.com/OpenJKSoftware/gOdoo/main/assets/godoo-main-cli.png" width="1000"/>](image.png)

**gOdoo** is short for **go Odoo**. \
It is a [Vscode Devcontainer](https://code.visualstudio.com/docs/remote/containers) Environment for [Odoo](https://odoo.com/)
with Python CLI `godoo` convenience wrapper around `odoo-bin`.

This repository is the base source for the Python package [godoo-cli](https://pypi.org/project/godoo-cli/) and serves as
an all batteries included development environment.

This is the source repository for `gOdoo`. If you want to use `gOdoo` please refer to [./docker/Dockerfile](./docker/Dockerfile) and modify it to install godoo using Pip.

Made Possible by: [WEMPE Elektronic GmbH](https://wetech.de)

# gOdoo-cli

Python package that provides `godoo` command line interface around `odoo-bin`.

It's build with [Typer](https://github.com/tiangolo/typer) to provide some convenience Wrappers for Odoo development and
Deployment.

Most flags can be configured by Env variables. \
Use `godoo --help` to find out more. HINT: Install tab-completion with `godoo --install-completion`

# Docker

This workspace also contains Docker and Docker-Compose files. \

They are used to provide either easy Odoo instances where the source is pulled according to
[ODOO_MANIFEST.yml](odoo_manifest.yml), or as a all batteries included devcontainer for VScode.

## Requirements

- [Docker Compose](https://github.com/docker/compose)
- [Traefik](https://doc.traefik.io/traefik/) container running with docker provider and "traefik" named docker network.
  Example: [Traefik Devproxy](https://github.com/joshkreud/traefik_devproxy)
- SSH Agent running. (check `echo $SSH_AUTH_SOCK`)\
  This gets passed trough in the Buildprocess to clone Thirdparty repos (Optional).

## Just wanna have a quick and easy Odoo Instance?

```bash
git clone https://github.com/OpenJKSoftware/gOdoo
cd godoo
. scripts/container_requirements.sh # Check Requirements
docker-compose build
docker-compose up
# wait......
# wait a bit mode ...
# just a little bit longer ..
# There we go.
# Odoo should be reachable on 'https://godoo.docker.localhost' assuming you didn't change .env TRAEFIK_HOST_RULE or COMPOSE_PROJECT_NAME
```

# Devcontainer

## Features

- All batteries included [Devcontainer](https://code.visualstudio.com/docs/remote/containers) with postgres service
  Container and local DNS resolvig managed by [Traefik](https://doc.traefik.io/traefik/).
- Easy fully working Odoo instance by `docker-compose up` with https access.
- `godoo` CLI wrapper around Odoo. (Most flags can be configured by Environment Variables and are already preconfigured
  in the Containers. See [.env.sample](./.env.sample))
- Cups Container, that provides a CUPS Printserver
- `odoo-bin` is added to PATH and can thus be invoked from every folder.
- Odoo will run in Proxy_Mode behind a Traefik reverse proxy for easy access on
  `https://$COMPOSE_PROJECT_NAME.docker.localhost`
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

## Usage

1. For Docker on windows: Clone the repo into the WSL2 Filesystem for better IO performance
2. Have [Traefik](https://github.com/traefik/traefik) Running on `docker.localhost`
   [Example](https://github.com/joshkreud/traefik_devproxy) \
   There must be a Docker network called `traefik` that can reach traefik.
3. Open Devcontianer:
   - If you have the Devcontainer CLI: `devcontainer open .`
   - If not open the workspace in Local Vscode. In the Command pallete search for `Reopen in container`
4. From **within the container** start Odoo using one of the following commands:
   - You can enable godoo tab-completion by `godoo --install-completion`
   - `make` -> Loads Odoo + Workspace Addons
   - `make bare` -> Loads Odoo with ony `web` installed.
   - `make kill` -> Search for `odoo-bin` processes and kill them
   - `make reset` -> Drops DB, deletes config file and datafolder
   - The full init script is available via "`godoo`". (See --help for Options)
5. Open Odoo `https://$COMPOSE_PROJECT_NAME.docker.localhost`\
   For example `COMPOSE_PROJECT_NAME=godoo` --> [https://godoo.docker.localhost](https://godoo.docker.localhost)
6. Login with `admin:admin`
7. Profit!

### Access to Odoo and Thirdparty addon Source

You can access the Odoo source by opening the VsCode workspace [full.code-workspace](full.code-workspace) from within
the Container. This will open a [Multi-Root Workspace](https://code.visualstudio.com/docs/editor/multi-root-workspaces).
Really waiting for https://github.com/microsoft/vscode-remote-release/issues/3665 here.

## Reset Devcontainer Data

When you screwed up so bad its time to just start Over godoo has you covered:

### Automatic Reset

There are 3 Options to reset the Dev Env.

1. From **Outside** the Container run `make reset` in the project root to delete docker volumes and restart the
   container. (Vscode will prompt to reconnect if still open)
2. From **Outside** the Container run `make reset-hard` in the project root to force rebuild the main Odoo container and
   then do the same as `make reset`
3. From **Inside** the Container run `make reset` to drop the DB and delete filestore and config file, which is way
   quicker than the other options.

### Manual Reset

1. Close vscode
2. Remove `app` and `db` container from docker.
3. Remove volumes: `db, odoo_thirdparty, odoo_web, vscode_extensions`
4. Restart Devcontainer

## Python Debugging

### VsCode Debugging

Debugging doesn't reliably work with
[Odoo Multiprocess](https://www.odoo.com/documentation/14.0/developer/misc/other/cmdline.html#multiprocessing) mode
enabled. \
The container ships with a Vscode Debug profile, that sets `--workers 0` to allow for Debugging Breakpoints. See [.vscode/launch.json](./.vscode/launch.json)

### Interactive Shell

Use `godoo shell` to enter an interactive shell on the Database.

# Odoo Modules

## Third Party Modules (manifest.yml)

The `godoo` bootstrap function, will download some modules using git. \
Which Repos to download is specified in `ODOO_MANIFEST.yml` ([Default](odoo_manifest.yml)) \
Not all of the cloned addons are automatically installed. \
Install them via the Apps Page in Odoo using `godoo rpc modules install` or using `odoo-bin`.\
Modules downloaded on the Odoo Marketplace can be dropped as a `.zip` archive in [./thirdparty](./thirdparty)
