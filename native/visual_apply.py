"""Install test-only observers and a real Metal-rendered workspace walkthrough.

This patches only the pinned CI checkout. It does not fake bridge responses,
execute cloud commands, change OS permissions, or change application behavior.
Button bounds are exposed for real pointer-event dispatch; actions still use the
shipped GPUI callbacks and the real local Rowdy service.
"""
from pathlib import Path
import subprocess
import sys
from apply import PIN


def install(root):
    root = Path(root).resolve()
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() != PIN:
        raise ValueError('Unexpected visual test host')
    changes = {}

    def replace(file, old, new):
        path = root / file
        text = changes.get(path, path.read_text())
        if text.count(old) != 1:
            raise ValueError('Visual test source anchor missing or ambiguous: ' + file)
        changes[path] = text.replace(old, new, 1)

    replace('crates/ui/src/components/button/button.rs',
            '''    pub fn new(id: impl Into<ElementId>, label: impl Into<SharedString>) -> Self {
        Self {
            base: ButtonLike::new(id),''',
            '''    pub fn new(id: impl Into<ElementId>, label: impl Into<SharedString>) -> Self {
        let id: ElementId = id.into();
        let selector = id.to_string();
        let mut base = ButtonLike::new(id);
        base.base = base.base.debug_selector(|| selector);
        Self {
            base,''')
    replace('crates/gpui/src/window.rs',
            '    pub fn render_to_image(&self) -> anyhow::Result<image::RgbaImage> {',
            '''    pub fn rowdy_test_bounds(&self, selector: &str) -> Option<Bounds<Pixels>> {
        self.rendered_frame.debug_bounds.get(selector).copied()
    }

    #[cfg(any(test, feature = "test-support"))]
    pub fn render_to_image(&self) -> anyhow::Result<image::RgbaImage> {''')
    replace('crates/dbt_ui/src/results_panel.rs',
            'impl DbtResultsPanel {',
            '''impl DbtResultsPanel {
    pub fn rowdy_visual_handle(&mut self, cx: &mut Context<Self>) -> Entity<crate::rowdy_view::RowdyView> {
        self.view = ResultsView::Rowdy;
        cx.notify();
        self.rowdy_view.clone()
    }
''')
    path = root / 'crates/dbt_ui/src/rowdy_view.rs'
    changes[path] = path.read_text() + '''
// Installed only in the pinned CI visual-test checkout.
impl RowdyView {
    pub fn visual_state(&self) -> Value {
        json!({"live":self.state.live,"pending":self.state.pending.is_some(),
            "historical":self.historical,"visible":self.visible,"status":self.status,
            "generation":self.state.generation,"revision":self.state.revision,
            "result":self.result,"trace":self.trace,"selected_event":self.selected_event})
    }
}
'''
    replace('crates/zed/src/visual_test_runner.rs',
            '''    let project_path = canonical_temp.join("project");
    std::fs::create_dir_all(&project_path).expect("Failed to create project directory");

    // Create test files in the real filesystem''',
            '''    let project_path = std::env::var("ROWDY_NATIVE_PROJECT").map(PathBuf::from)
        .unwrap_or_else(|_| canonical_temp.join("project"));
    std::fs::create_dir_all(&project_path).expect("Failed to create project directory");

    // Create test files in the real filesystem''')
    replace('crates/zed/src/visual_test_runner.rs',
            '    create_test_files(&project_path);',
            '    if std::env::var("ROWDY_NATIVE_PROJECT").is_err() { create_test_files(&project_path); }')
    replace('crates/zed/src/visual_test_runner.rs',
            '        editor::init(cx);',
            '''        editor::init(cx);
        dbt_ui::init(cx);
        let mut dbt = dbt_ui::dbt_settings::DbtSettings::get_global(cx).clone();
        dbt.auto_install = false;
        dbt.parse_on_load = false;
        dbt.distribution = "core".into();
        dbt_ui::dbt_settings::DbtSettings::override_global(dbt, cx);''')
    replace('crates/zed/src/visual_test_runner.rs',
            '    // Open main.rs in the editor',
            '''    if std::env::var("ROWDY_NATIVE_PROJECT").is_ok() {
        return rowdy_visual_smoke::run(&mut cx, workspace_window, project_path);
    }

    // Open main.rs in the editor''')
    path = root / 'crates/zed/src/visual_test_runner.rs'
    changes[path] += '\n#[cfg(target_os = "macos")]\nmod rowdy_visual_smoke;\n'
    changes[root / 'crates/zed/src/rowdy_visual_smoke.rs'] = (Path(__file__).parent / 'rowdy_visual_smoke.rs').read_text()
    for path, text in changes.items():
        path.write_text(text)
    print('Native visual observers installed; real GPUI callbacks and local SQL unchanged')


if __name__ == '__main__':
    install(sys.argv[1])
