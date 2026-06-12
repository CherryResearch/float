# Modules

This tree is the repo-tracked home for packaged Float modules and related assets.

- `modules/skills/` is for the base skills Float ships with.
- `modules/addons/{addon}/` is for packaged add-ons that Float can discover as part of the repository.
- `modules/addons/{addon}/config.json` is the canonical add-on manifest/config entrypoint.

Add-ons are packages of skills, tools, config, and associated assets. A shipped add-on can live here in the repo. A user-loaded local override can also be provided from `data/modules/addons/{addon}/` when that path is used.

Local skill markdown overrides live separately under `data/modules/skills/`; they should not overwrite the repo-shipped files in `modules/skills/`.
