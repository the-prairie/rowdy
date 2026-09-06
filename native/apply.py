"""Apply the native spike to one pinned upstream without guessing changed anchors."""
from pathlib import Path
import argparse
import subprocess

PIN='3ee08f10debe9464b53b3e1241b56b6deb4e79fb'

def apply(root,check=False):
    root=Path(root).resolve(); head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    if head!=PIN:raise ValueError('Upstream revision is not the reviewed pin; do not apply by fuzzy matching')
    lib=root/'crates/dbt_ui/src/dbt_ui.rs'; panel=root/'crates/dbt_ui/src/results_panel.rs'
    text=panel.read_text(); original=text
    if 'rowdy_view' in lib.read_text() and 'rowdy_view' in text:return 'already applied'
    replacements=[
      ('    _run: Task<()>,','    rowdy_view: Entity<crate::rowdy_view::RowdyView>,\n    _run: Task<()>,') ,
      ('    Connection,\n}', '    Connection,\n    Rowdy,\n}'),
      ('            Self {\n            focus_handle:', '            let rowdy_view = cx.new(|cx| crate::rowdy_view::RowdyView::new(workspace_handle.clone(), cx));\n            Self {\n            focus_handle:'),
      ('                _run: Task::ready(()),','                rowdy_view,\n                _run: Task::ready(()),'),
      ('                        Button::new("dbt-view-connection", "Connection")', '                        Button::new("dbt-view-rowdy", "Rowdy")\n                            .toggle_state(self.view == ResultsView::Rowdy)\n                            .on_click(cx.listener(|this, _, _, cx| { this.view = ResultsView::Rowdy; cx.notify(); })),\n                    )\n                    .child(\n                        Button::new("dbt-view-connection", "Connection")'),
      ('        if self.view == ResultsView::Connection {\n            return self.render_connection(cx);', '        if self.view == ResultsView::Rowdy {\n            return div().size_full().child(self.rowdy_view.clone()).into_any_element();\n        }\n        if self.view == ResultsView::Connection {\n            return self.render_connection(cx);')]
    for old,new in replacements:
        if text.count(old)!=1:raise ValueError('Native patch anchor missing or ambiguous; no files changed')
        text=text.replace(old,new,1)
    libtext=lib.read_text()
    if libtext.count('pub mod results_panel;')!=1:raise ValueError('Native module anchor changed')
    if not check:
        panel.write_text(text);lib.write_text(libtext.replace('pub mod results_panel;','pub mod results_panel;\npub mod rowdy_view;'))
        (lib.parent/'rowdy_view.rs').write_text((Path(__file__).parent/'rowdy_view.rs').read_text())
    return 'all anchors verified; '+('no writes' if check else 'native source applied')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('--check',action='store_true');a=p.parse_args();print(apply(a.root,a.check))
