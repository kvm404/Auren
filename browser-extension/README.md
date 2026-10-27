# Auren Tabs

This optional Manifest V3 extension exposes two native messaging operations to Auren:

- `list_tabs`: return existing tab IDs, titles, URLs, windows, positions and active state.
- `activate_tab`: recheck a tab's ID, URL and window, mark it active, focus its window, and verify
  the selected tab before returning success.

It never injects scripts or changes page content, tab URLs, tab groups or tab order. The fixed
public key in the manifest gives unpacked installations a stable extension ID, which the native
host registration uses to restrict allowed origins. There is no private key in this repository.

Run Auren's installer, then use **Load unpacked** on the intended browser's Extensions page and
select the installed `~/.local/share/auren/browser-extension` folder. See
[setup and permissions](../docs/install.md#direct-tab-navigation). A browser permission step is
required; the installer only registers the local native host.

The native host speaks Chrome's length-prefixed JSON protocol on stdin/stdout and serves Auren's
requests through a private Unix socket under its XDG runtime directory. Requests have generated
IDs and bounded timeouts. Only the two tab operations above are forwarded to the extension.

Run the extension's mocked browser API checks with:

```sh
node --test tests/test_tab_extension.mjs
```
