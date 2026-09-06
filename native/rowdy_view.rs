// SPDX-License-Identifier: GPL-3.0-only
//! One native evidence surface: results, record trace, live differences, checks.
//! The bridge only permits local fixed-input actions. Application acceptance is
//! separate from cargo check; no browser screenshot stands in for native proof.
#[path = "rowdy_process.rs"]
mod process;
#[path = "rowdy_state.rs"]
mod lifecycle;

use std::path::{Component, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use editor::Editor;
use gpui::{App, Context, EntityId, Focusable, Subscription, Task, WeakEntity, Window};
use serde_json::{json, Value};
use ui::{prelude::*, Button, Label};
use workspace::{OpenOptions, Workspace};
use lifecycle::{BufferKey, Change, Operation, State};

#[derive(Clone, Copy, PartialEq)]
enum Surface { Results, Changes, Checks, Trace }

pub struct RowdyView {
    workspace: WeakEntity<Workspace>,
    state: State,
    status: String,
    result: Option<Value>,
    result_buffer: Option<BufferKey>,
    result_revision: u64,
    historical: bool,
    visible: bool,
    trace: Option<Value>,
    selected_event: Option<String>,
    arrival_clock: bool,
    surface: Surface,
    scope: String,
    show_scopes: bool,
    session: String,
    control: Option<process::RunControl>,
    observed_editor: Option<EntityId>,
    editor_subscription: Option<Subscription>,
    _workspace_subscription: Option<Subscription>,
    task: Task<()>,
    debounce: Task<()>,
}

fn text(value: &Value) -> String {
    match value { Value::Null => "NULL".into(), Value::String(s) => s.clone(), other => other.to_string() }
}

impl RowdyView {
    pub fn new(workspace: WeakEntity<Workspace>, cx: &mut Context<Self>) -> Self {
        let subscription = workspace.upgrade().map(|entity| cx.subscribe(&entity, |this: &mut Self, _, event, cx| {
            if matches!(event, workspace::Event::ActiveItemChanged) { this.observe_buffer(cx); }
        }));
        Self {
            workspace, state: State::default(), status: "Open a registered SQL file. Cloud adapters stay disabled.".into(),
            result: None, result_buffer: None, result_revision: 0, historical: false, visible: false,
            trace: None, selected_event: None, arrival_clock: false,
            surface: Surface::Results, scope: "result".into(), show_scopes: false,
            session: format!("native-{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos()),
            control: None, observed_editor: None, editor_subscription: None,
            _workspace_subscription: subscription, task: Task::ready(()), debounce: Task::ready(()),
        }
    }

    fn active_buffer(&self, cx: &App) -> Option<BufferKey> {
        let workspace = self.workspace.upgrade()?;
        let editor = workspace.read(cx).active_item(cx)?.act_as::<Editor>(cx)?;
        let buffer = editor.read(cx).buffer().read(cx).as_singleton()?;
        let buffer = buffer.read(cx);
        let path = buffer.file()?.as_local()?.abs_path(cx).to_string_lossy().into_owned();
        path.ends_with(".sql").then(|| BufferKey { path, sql: buffer.text() })
    }

    fn observe_buffer(&mut self, cx: &mut Context<Self>) {
        let editor = self.workspace.upgrade().and_then(|w| w.read(cx).active_item(cx).and_then(|item| item.act_as::<Editor>(cx)));
        let id = editor.as_ref().map(|e| e.entity_id());
        if self.observed_editor != id {
            self.observed_editor = id;
            self.editor_subscription = editor.map(|e| cx.observe(&e, |this, _, cx| this.observe_buffer(cx)));
        }
        let current = self.active_buffer(cx);
        let change = self.state.observe(current);
        if change == Change::None { return; }
        self.control.take();
        self.debounce = Task::ready(());
        self.historical = self.result.is_some();
        if change == Change::File {
            self.scope = "result".into();
            self.status = "File changed. Live paused; retained evidence keeps its original identity.".into();
        } else {
            self.status = "Edited buffer. Previous result is historical until this revision succeeds.".into();
            if self.state.live { self.schedule(cx); }
        }
        cx.notify();
    }

    fn schedule(&mut self, cx: &mut Context<Self>) {
        let revision = self.state.revision;
        self.debounce = cx.spawn(async move |this, cx| {
            cx.background_executor().timer(Duration::from_millis(300)).await;
            this.update(cx, |this, cx| {
                if this.state.should_replay(revision) { this.execute(Operation::Preview, None, cx); }
            }).ok();
        });
    }

    fn pause(&mut self, cx: &mut Context<Self>) {
        let interrupted = self.state.pending.is_some();
        self.state.pause(); self.control.take(); self.debounce = Task::ready(());
        // Pausing an idle, current result does not invalidate its observation.
        // In particular its ID cells should remain traceable after Live is off.
        if interrupted { self.historical = self.result.is_some(); }
        self.status = "Paused. No automatic execution; an already submitted local job may finish within its bound.".into();
        cx.notify();
    }

    pub fn set_visible(&mut self, visible: bool, cx: &mut Context<Self>) {
        if self.visible == visible { return; }
        self.visible = visible;
        if !visible { self.pause(cx); }
    }

    fn current_result(&self) -> bool {
        self.result.is_some() && !self.historical && self.result_revision == self.state.revision
            && self.result_buffer == self.state.buffer
    }

    fn toggle_live(&mut self, cx: &mut Context<Self>) {
        self.observe_buffer(cx);
        if self.state.live { self.pause(cx); return; }
        let Some(buffer) = self.result_buffer.clone() else { return; };
        let binding = self.result.as_ref().and_then(|r| r["binding"]["hash"].as_str()).unwrap_or("").to_owned();
        if self.current_result() && self.state.enable(&buffer, binding) {
            self.status = "Live on · fixed local inputs · 300 ms after settled edits · no cloud fallback".into();
        } else {
            self.status = "Preview this buffer explicitly to establish a current fixed context before enabling Live.".into();
        }
        cx.notify();
    }

    fn execute(&mut self, operation: Operation, clicked: Option<(usize, String)>, cx: &mut Context<Self>) {
        self.observe_buffer(cx);
        if operation == Operation::Trace && !self.current_result() {
            self.status = "Preview the current buffer before tracing a result cell.".into(); cx.notify(); return;
        }
        self.debounce = Task::ready(());
        self.control.take();
        let Some(ticket) = self.state.start(operation) else {
            self.status = "Select a registered local SQL editor buffer first.".into(); cx.notify(); return;
        };
        let mut payload = json!({"protocol":"rowdy.native/2", "request_id":format!("{}-{}",self.session,ticket.generation),
            "session":self.session,"request":ticket.generation,"path":ticket.buffer.path,
            "sql":ticket.buffer.sql,"operation":operation.name()});
        if operation == Operation::Trace {
            let Some((row, column)) = clicked else { self.state.cancel(); return; };
            payload["origin_id"] = self.result.as_ref().unwrap()["receipt"]["id"].clone();
            payload["row_index"] = json!(row); payload["column"] = json!(column);
        } else {
            payload["stage"] = json!(self.scope);
            if self.state.live {
                if let Some(binding) = &self.state.frozen_binding { payload["expected_binding"] = json!(binding); }
            }
            self.historical = self.result.is_some();
        }
        self.status = if operation == Operation::Trace {
            "Tracing the exact clicked row in the registered saved-source snapshot…".into()
        } else { "Evaluating the exact buffer on fixed local inputs…".into() };
        let control = process::RunControl::new();
        let cancel = control.0.clone(); self.control = Some(control);
        let background = cx.background_spawn(async move {
            let python = std::env::var("ROWDY_PYTHON").unwrap_or_else(|_| "python3".into());
            let mut command = Command::new(python); command.args(["-m", "rowdy.native_actions"]);
            let bytes = process::run(&mut command, payload.to_string().into_bytes(), cancel, Duration::from_secs(20), process::MAX_OUTPUT)?;
            let response: Value = serde_json::from_slice(&bytes).map_err(|_| "Invalid native protocol response")?;
            if response["protocol"] == "rowdy.native/2" && response["ok"] == false
                && response["request_id"] == payload["request_id"] && response["request"] == payload["request"]
                && response["session"] == payload["session"] && response["code"] == "evaluation_unavailable" {
                return Err("Preview unavailable for this edit; last valid result retained".to_owned());
            }
            if response["ok"] != true || response["protocol"] != "rowdy.native/2"
                || response["request_id"] != payload["request_id"] || response["request"] != payload["request"]
                || response["session"] != payload["session"] || response["path"] != payload["path"]
                || response["operation"] != payload["operation"] || response["cloud_enabled"] != false
                || response["source_writes_enabled"] != false || response["binding"]["hash"].as_str().map_or(true, |s| s.len()!=64) {
                return Err("Native response identity or local-only authority mismatch".to_owned());
            }
            let r = &response["receipt"];
            if r["project"] != response["binding"]["project"] || r["context_hash"] != response["binding"]["context_hash"] {
                return Err("Native receipt is not bound to the requested project context".to_owned());
            }
            if operation == Operation::Trace {
                if r["kind"] != "trace" || r["origin_run"] != payload["origin_id"] || !r["events"].is_array() {
                    return Err("Trace origin or event shape mismatch".to_owned());
                }
            } else if r["sql"] != payload["sql"] || r["request"] != payload["request"] || r["session"] != payload["session"]
                || r["stage"] != payload["stage"] || r["kind"] != (if operation == Operation::Verify { "verification" } else { "preview" })
                || r["warehouse_verified"] != false || r["deployed"] != false || r["consumer_verified"] != false {
                return Err("Preview is not evidence for this buffer and operation".to_owned());
            }
            Ok(response)
        });
        self.task = cx.spawn(async move |this, cx| {
            let reply = background.await;
            this.update(cx, |this, cx| {
                this.observe_buffer(cx);
                if !this.state.accept(&ticket) { return; }
                this.control.take();
                match reply {
                    Ok(response) if ticket.operation == Operation::Trace => {
                        this.selected_event = response["receipt"]["events"].as_array().and_then(|a| a.first()).and_then(|e| e["id"].as_str()).map(str::to_owned);
                        this.trace = Some(response); this.surface = Surface::Trace;
                        this.status = "Observed saved-source snapshot · not unsaved candidate or physical warehouse lineage".into();
                    }
                    Ok(response) => {
                        this.status = format!("{} · {} rows · local snapshot only", response["receipt"]["status"].as_str().unwrap_or("unknown"), response["receipt"]["preview"]["count"]);
                        this.result = Some(response); this.result_buffer = Some(ticket.buffer);
                        this.result_revision = this.state.revision; this.historical = false;
                        if ticket.operation == Operation::Verify { this.surface = Surface::Checks; }
                    }
                    Err(message) => {
                        // Invalid syntax keeps the last good output. A changed context must
                        // be re-established explicitly; do not automatically adopt new data.
                        this.historical = this.result.is_some();
                        if message.starts_with("Preview unavailable") {
                            this.status = format!("{message}. {}", if this.state.live { "Live will retry after the next edit." } else { "Preview explicitly to retry." });
                        } else {
                            this.state.pause();
                            this.status = format!("{message}. Live paused; preview explicitly to re-establish context.");
                        }
                    }
                }
                cx.notify();
            }).ok();
        });
        cx.notify();
    }

    fn focus_editor(&self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(editor) = self.workspace.upgrade().and_then(|w| w.read(cx).active_item(cx).and_then(|i| i.act_as::<Editor>(cx))) {
            editor.read(cx).focus_handle(cx).focus(window);
        }
    }

    fn open_model(&mut self, model: &str, window: &mut Window, cx: &mut Context<Self>) {
        let Some(trace) = &self.trace else { return; };
        let Some(root) = trace["binding"]["root"].as_str() else { return; };
        let Some(entry) = trace["binding"]["models"].as_array().and_then(|a| a.iter().find(|m| m["name"].as_str()==Some(model))) else { return; };
        let Some(file) = entry["file"].as_str() else { return; };
        let relative = PathBuf::from(file);
        if relative.is_absolute() || relative.components().any(|c| !matches!(c, Component::Normal(_))) { return; }
        let Ok(root) = PathBuf::from(root).canonicalize() else { return; };
        let Ok(path) = root.join(relative).canonicalize() else { return; };
        if !path.starts_with(&root) || path.extension().and_then(|e| e.to_str()) != Some("sql") { return; }
        self.pause(cx);
        self.workspace.update(cx, |workspace, cx| { workspace.open_abs_path(path, OpenOptions::default(), window, cx).detach(); }).ok();
    }

    fn render_result(&self, cx: &mut Context<Self>) -> gpui::AnyElement {
        let Some(response) = self.result.as_ref() else {
            return div().p_3().child(Label::new("Preview a registered buffer. Click a typed ID to trace it; enable Live only when ready.")).into_any_element();
        };
        let receipt = &response["receipt"];
        let columns = receipt["preview"]["columns"].as_array().cloned().unwrap_or_default();
        let rows = receipt["preview"]["rows"].as_array().cloned().unwrap_or_default();
        let trace_cfg = &response["binding"]["trace"];
        let mut body = v_flex().gap_2().child(Label::new(format!("{} · scope {} · {} rows", text(&receipt["model"]), text(&receipt["stage"]), rows.len())).size(LabelSize::Small));
        if let Some(watch) = self.render_watch(response) { body = body.child(watch); }
        let mut table = v_flex().w(px(columns.len() as f32 * 170.)).gap_1().child(h_flex().children(columns.iter().map(|c| div().w(px(170.)).flex_none().child(Label::new(text(c)).size(LabelSize::Small)))));
        for (row_index, row) in rows.iter().take(100).enumerate() {
            let mut line = h_flex();
            for column in &columns {
                let name = column.as_str().unwrap_or("");
                let value = &row[name];
                let namespace = trace_cfg["namespace"].as_str().unwrap_or("");
                let traceable = self.current_result() && !self.state.live && self.state.pending.is_none()
                    && trace_cfg["identities"].as_array().is_some_and(|a| a.contains(column))
                    && value.as_str().is_some_and(|s| !s.is_empty()) && row[namespace].as_str().is_some_and(|s| !s.is_empty());
                let cell = if traceable {
                    let name = name.to_owned();
                    Button::new(SharedString::from(format!("rowdy-id-{row_index}-{name}")), text(value))
                        .label_size(LabelSize::Small)
                        .on_click(cx.listener(move |this, _, _, cx| this.execute(Operation::Trace, Some((row_index, name.clone())), cx))).into_any_element()
                } else { Label::new(text(value)).size(LabelSize::Small).into_any_element() };
                line = line.child(div().w(px(170.)).flex_none().overflow_hidden().child(cell));
            }
            table = table.child(line);
        }
        body = body.child(div().id("rowdy-grid-horizontal").w_full().overflow_x_scroll().child(table));
        if rows.len() > 100 { body = body.child(Label::new(format!("Showing 100 of {} returned rows",rows.len())).size(LabelSize::Small)); }
        if !trace_cfg.is_null() {
            body = body.child(Label::new(format!("Trace uses the registered window: {} to {}. Pause Live before tracing a cell.", text(&trace_cfg["window"][0]), text(&trace_cfg["window"][1]))).size(LabelSize::Small));
        }
        body.into_any_element()
    }

    fn render_watch(&self, response: &Value) -> Option<gpui::AnyElement> {
        let trace = self.trace.as_ref()?;
        let id = self.selected_event.as_ref()?;
        if response["binding"]["project"] != trace["binding"]["project"] || response["binding"]["context_hash"] != trace["binding"]["context_hash"] { return None; }
        let cfg = &trace["binding"]["trace"];
        let key = cfg["event_key"].as_str()?; let ns = cfg["namespace"].as_str()?;
        let output = &response["receipt"]["output"];
        if !output["columns"].as_array()?.contains(&json!(key)) || !output["columns"].as_array()?.contains(&json!(ns)) { return None; }
        let root_ns = &trace["receipt"]["root"]["namespace"];
        let count = |value: &Value| value["rows"].as_array().map_or(0,|a| a.iter().filter(|r| r[key].as_str()==Some(id.as_str()) && &r[ns]==root_ns).count());
        Some(v_flex().gap_1()
            .child(Label::new(format!("Watching {id} · saved output {} → edited output {}", count(&response["receipt"]["baseline"]), count(output))).color(Color::Accent))
            .child(Label::new("Prospective local result; the original trace has not changed.").size(LabelSize::Small)).into_any_element())
    }

    fn render_changes(&self) -> gpui::AnyElement {
        let Some(response) = &self.result else { return Label::new("Preview a buffer first.").into_any_element(); };
        let receipt = &response["receipt"];
        let difference = &receipt["difference"];
        let mut view = v_flex().gap_2().child(Label::new("Edited buffer compared with saved-source output on the same local inputs").size(LabelSize::Small));
        if difference["available"] == false {
            return view.child(Label::new(text(&difference["reason"])).color(Color::Warning)).into_any_element();
        }
        view = view.child(Label::new(format!("Added {} · Removed {} · Changed {}", text(&difference["added"]), text(&difference["removed"]), text(&difference["changed"]))).size(LabelSize::Small));
        if let Some(changes) = difference["changes"].as_array() {
            for change in changes.iter().take(30) {
                view = view.child(Label::new(format!("{} · key {}", text(&change["kind"]), text(&change["key"]))).size(LabelSize::Small));
                view = view.child(Label::new(format!("Before: {}", text(&change["before"]))).size(LabelSize::Small));
                view = view.child(Label::new(format!("After: {}", text(&change["after"]))).size(LabelSize::Small));
            }
            if changes.len() > 30 { view = view.child(Label::new("First 30 differences shown.").size(LabelSize::Small)); }
        }
        if let Some(items) = receipt["downstream"].as_array() {
            for item in items {
                view=view.child(Label::new(format!("{} · {} → {}",text(&item["title"]),text(&item["before"]),text(&item["after"]))).size(LabelSize::Small));
            }
        }
        view.into_any_element()
    }

    fn render_checks(&self) -> gpui::AnyElement {
        let Some(response) = &self.result else { return Label::new("Verify a buffer first.").into_any_element(); };
        let r = &response["receipt"];
        let mut view = v_flex().gap_2().child(Label::new(if r["kind"]=="verification" { "Explicit local verification" } else { "Preview observations — not a verification checkpoint" }).size(LabelSize::Small));
        for c in r["checks"].as_array().into_iter().flatten() {
            view=view.child(Label::new(format!("{} — {}",text(&c["status"]),text(&c["title"]))).size(LabelSize::Small).color(if c["status"]=="passed" {Color::Default} else {Color::Warning}));
        }
        view.child(Label::new(format!("Receipt {} · warehouse, deployment and consumer verification have NOT run",text(&r["id"]))).size(LabelSize::Small)).into_any_element()
    }

    fn render_trace(&self, cx: &mut Context<Self>) -> gpui::AnyElement {
        let Some(response) = &self.trace else { return Label::new("Click a registered ID in a current result to trace it.").into_any_element(); };
        let trace=&response["receipt"];
        let mut events=trace["events"].as_array().cloned().unwrap_or_default();
        let clock=if self.arrival_clock {"arrival_time"} else {"event_time"};
        events.sort_by_key(|e| (text(&e[clock]),text(&e["id"])));
        let mut view=v_flex().gap_2()
            .child(Label::new(format!("{} = {} · {}",text(&trace["root"]["type"]),text(&trace["root"]["value"]),text(&trace["root"]["namespace"]))).color(Color::Accent))
            .child(Label::new(format!("Captured saved-source snapshot · {} to {} · origin {}",text(&trace["start"]),text(&trace["end"]),text(&trace["origin_run"]))).size(LabelSize::Small))
            .child(Button::new("rowdy-trace-clock",if self.arrival_clock {"Arrival time"} else {"Event time"}).on_click(cx.listener(|this,_,_,cx| {this.arrival_clock=!this.arrival_clock;cx.notify();})));
        for (index,event) in events.iter().take(100).enumerate() {
            let id=text(&event["id"]); let selected=self.selected_event.as_deref()==Some(id.as_str());
            view=view.child(Button::new(SharedString::from(format!("rowdy-event-{index}")),format!("{} · {} · {}",text(&event[clock]),id,text(&event["name"])))
                .toggle_state(selected).on_click(cx.listener(move |this,_,_,cx| {this.selected_event=Some(id.clone());cx.notify();})));
            if selected {
                view=view.child(Label::new(format!("{} delivery attempt(s) · {}",event["attempts"].as_array().map_or(0,Vec::len),text(&event["reason"]["detail"]))).size(LabelSize::Small));
                for (stage_index,stage) in event["stages"].as_array().into_iter().flatten().enumerate() {
                    let rows=stage["rows"].as_array().map_or(0,Vec::len);
                    let mut line=h_flex().gap_2().child(Label::new(format!("{} · {} · {rows} record(s) · {}",text(&stage["name"]),text(&stage["state"]),text(&stage["kind"]))).size(LabelSize::Small));
                    if let Some(model)=stage["model"].as_str() {
                        let model=model.to_owned();
                        line=line.child(Button::new(SharedString::from(format!("rowdy-open-{index}-{stage_index}")),"Open SQL")
                            .on_click(cx.listener(move |this,_,window,cx| this.open_model(&model,window,cx))));
                    }
                    view=view.child(line);
                }
            }
        }
        if events.len()>100 { view=view.child(Label::new(format!("First 100 of {} events displayed; original trace remains complete.",events.len())).size(LabelSize::Small)); }
        view=view.child(Label::new(format!("Unresolved identity evidence: {} record(s). No person merge inferred.",trace["unresolved"].as_array().map_or(0,Vec::len))).size(LabelSize::Small));
        for source in trace["coverage"].as_array().into_iter().flatten() {
            view=view.child(Label::new(format!("{} · {}",text(&source["name"]),text(&source["status"]))).size(LabelSize::Small));
        }
        view.into_any_element()
    }
}

impl Render for RowdyView {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.observe_buffer(cx);
        let busy=self.state.pending.is_some();
        let mut bar=h_flex().gap_2().flex_wrap().p_2().border_b_1().border_color(cx.theme().colors().border)
            .child(Label::new("row(dy)").color(Color::Accent))
            .child(Button::new("rowdy-preview","Preview").on_click(cx.listener(|this,_,_,cx|this.execute(Operation::Preview,None,cx))))
            .child(Button::new("rowdy-live",if self.state.live {"Live: on"} else {"Live: paused"}).toggle_state(self.state.live).on_click(cx.listener(|this,_,_,cx|this.toggle_live(cx))))
            .child(Button::new("rowdy-verify","Verify locally").on_click(cx.listener(|this,_,_,cx|this.execute(Operation::Verify,None,cx))))
            .child(Button::new("rowdy-editor","Return to editor").on_click(cx.listener(|this,_,window,cx|this.focus_editor(window,cx))));
        if busy {bar=bar.child(Button::new("rowdy-cancel","Cancel").on_click(cx.listener(|this,_,_,cx|this.pause(cx))));}
        let mut tabs=h_flex().gap_1().flex_wrap().px_2();
        for (id,label,surface) in [("rowdy-results","Results",Surface::Results),("rowdy-changes","Changes",Surface::Changes),("rowdy-checks","Checks",Surface::Checks),("rowdy-trace","Trace",Surface::Trace)] {
            tabs=tabs.child(Button::new(id,label).toggle_state(self.surface==surface).on_click(cx.listener(move |this,_,_,cx|{this.surface=surface;cx.notify();})));
        }
        tabs=tabs.child(Button::new("rowdy-scope",format!("Scope: {}",self.scope)).on_click(cx.listener(|this,_,_,cx|{this.show_scopes=!this.show_scopes;cx.notify();})));
        let mut content=v_flex().id("rowdy-evidence").flex_1().min_h_0().overflow_y_scroll().p_3().gap_2();
        if self.historical && self.surface!=Surface::Trace {content=content.child(Label::new("Historical result — not evidence for the current buffer").size(LabelSize::Small).color(Color::Warning));}
        if self.show_scopes {
            let mut names=vec!["result".to_owned()];
            if let Some(result)=&self.result {
                for cte in result["receipt"]["stages"].as_array().into_iter().flatten() {
                    if let Some(name)=cte["name"].as_str() {names.push(name.to_owned());}
                }
            }
            let mut scopes=h_flex().gap_1().flex_wrap();
            for (i,name) in names.into_iter().enumerate() {
                scopes=scopes.child(Button::new(SharedString::from(format!("rowdy-scope-{i}")),name.clone()).on_click(cx.listener(move|this,_,_,cx|{this.scope=name.clone();this.show_scopes=false;this.execute(Operation::Preview,None,cx);}))); 
            }
            content=content.child(scopes);
        }
        content=content.child(match self.surface {
            Surface::Results=>self.render_result(cx),Surface::Changes=>self.render_changes(),
            Surface::Checks=>self.render_checks(),Surface::Trace=>self.render_trace(cx),
        });
        v_flex().size_full().child(bar).child(div().px_2().py_1().child(Label::new(self.status.clone()).size(LabelSize::Small))).child(tabs).child(content)
    }
}
