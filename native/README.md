# Native Zed integration — experimental, NOT a verified native release

The product direction remains a native editor backed by Rowdy's shared action service.
`rowdy_view.rs` is a GPUI view using the real active editor buffer; it does not embed HTML.
It requests local preview/verification through `python -m rowdy.bridge`, with tokens read
from the owner-only runtime descriptor rather than command arguments. It neither applies
source edits nor invokes dbt/BigQuery implicitly.

This spike does not yet port the complete trace/live-preview reference interface. Native
rendering, scroll behavior, active-buffer invalidation and Mac packaging require validation.
There is no Rust toolchain or source-network access in the local delivery environment. No
screenshot of the browser reference is represented as a native screenshot.

## Build on an authorized development machine

```sh
# Install Rowdy into the interpreter the editor should use, then start its service.
python3 -m pip install -e .
python3 -m rowdy --project /absolute/project --no-browser

# Keep this checkout separate from an installed production editor.
git clone https://github.com/arezki1990/dbt-zed.git /tmp/rowdy-zed
cd /tmp/rowdy-zed
git checkout 3ee08f10debe9464b53b3e1241b56b6deb4e79fb
python3 /absolute/rowdy/native/apply.py . --check
python3 /absolute/rowdy/native/apply.py .
cargo fmt -p dbt_ui
cargo check -p dbt_ui
cargo build -p zed --features gpui_platform/runtime_shaders
ROWDY_PYTHON=/absolute/venv/bin/python ROWDY_HOME=/absolute/rowdy-home \
  ZED_RELEASE_CHANNEL=dev ./target/debug/zed /absolute/project
```

Select the **Rowdy** tab in the dbt dock. The preview is tied to the selected registered
file's exact buffer. Do not distribute the fork before completing its native audit,
updater/branding isolation, target-platform testing and applicable GPL license requirements.
The native source is GPL-3.0-only for combination with the upstream editor. The independent
service is not silently relicensed by this file.
