# Tracked add-on manifests

Put packaged add-on manifests here.

An add-on is a package of skills (markdown descriptive files), tools (callable python scripts), config, and assets (any other files needed, such as images or reference files).

Use one directory per add-on:

```text
modules/addons/{addon}/
  config.json
  assets/
```

`config.json` is the add-on manifest/config entrypoint. Float can also read local add-on config from `data/modules/addons/{addon}/config.json`. If the same add-on id exists in both places, the local `data/` copy overrides the packaged repo copy.

Local editable module config should be written under `data/modules/addons/{addon}/config.json`, not into this tracked repo directory.
