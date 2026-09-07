"""Exact, reviewed hooks for the optional native source-change component.

The host overlay stays small; the guarded write implementation lives separately
in rowdy_edits.rs. No fuzzy patching or partial writes are allowed.
"""

def extend_view(source):
    replacements = [
        ('mod lifecycle;', 'mod lifecycle;\n#[path = "rowdy_edits.rs"]\nmod edits;'),
        ('enum Surface { Results, Changes, Checks, Trace }', 'enum Surface { Results, Changes, Checks, Trace, Review }'),
        ('    state: State,', '    state: State,\n    edits: edits::EditState,'),
        ('scrolls: [gpui::ScrollHandle; 4]', 'scrolls: [gpui::ScrollHandle; 5]'),
        ('workspace, state: State::default(), status:', 'workspace, state: State::default(), edits: edits::EditState::default(), status:'),
        ('    fn observe_buffer(&mut self, cx: &mut Context<Self>) {', '    fn observe_buffer(&mut self, cx: &mut Context<Self>) {\n        if self.edits.busy { return; }'),
        ('    fn execute(&mut self, operation: Operation, clicked: Option<(usize, String)>, cx: &mut Context<Self>) {', '    fn execute(&mut self, operation: Operation, clicked: Option<(usize, String)>, cx: &mut Context<Self>) {\n        if self.edits.busy { return; }'),
        ('    fn toggle_live(&mut self, cx: &mut Context<Self>) {', '    fn toggle_live(&mut self, cx: &mut Context<Self>) {\n        if self.edits.busy { return; }'),
        ('let busy=self.state.pending.is_some();', 'let busy=self.state.pending.is_some();\n        let editing=self.edits.busy;'),
        ('Button::new("rowdy-preview","Preview").on_click', 'Button::new("rowdy-preview","Preview").disabled(editing).on_click'),
        ('.toggle_state(self.state.live).on_click', '.toggle_state(self.state.live).disabled(editing).on_click'),
        ('Button::new("rowdy-verify","Verify locally").on_click', 'Button::new("rowdy-verify","Verify locally").disabled(editing).on_click'),
        ('        if busy {bar=', '''        if self.can_review() || self.edits.review.is_some() || self.edits.last_change.is_some() || self.edits.uncertain.is_some() {
            bar=bar.child(Button::new("rowdy-review","Review source change").disabled(editing).on_click(cx.listener(|this,_,_,cx| {
                if this.can_review() {this.start_edit("review",cx);} else {this.surface=Surface::Review;cx.notify();}
            })));
        }
        if busy {bar='''),
        ('Surface::Checks=>self.render_checks(),Surface::Trace=>self.render_trace(cx),', 'Surface::Checks=>self.render_checks(),Surface::Trace=>self.render_trace(cx),\n            Surface::Review=>self.render_review(cx),'),
    ]
    for old,new in replacements:
        if source.count(old) != 1:
            raise ValueError('Native source-change hook missing or ambiguous; no files changed')
        source=source.replace(old,new,1)
    return source
