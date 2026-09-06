// SPDX-License-Identifier: GPL-3.0-only
//! Native buffer preview. A checked crate is not a launched/packaged editor.
#[path = "rowdy_process.rs"]
mod process;
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use editor::Editor;
use gpui::{App, Context, EntityId, Subscription, Task, WeakEntity, Window};
use serde_json::{json, Value};
use ui::{prelude::*, Button, Label};
use workspace::Workspace;

pub struct RowdyView {
    workspace: WeakEntity<Workspace>,
    status: String,
    receipt: Option<Value>,
    receipt_buffer: Option<(String, String)>,
    pending_buffer: Option<(String, String)>,
    historical: bool,
    generation: u64,
    session: String,
    control: Option<process::RunControl>,
    observed_editor: Option<EntityId>,
    editor_subscription: Option<Subscription>,
    _workspace_subscription: Option<Subscription>,
    task: Task<()>,
}

impl RowdyView {
    pub fn new(workspace: WeakEntity<Workspace>, cx: &mut Context<Self>) -> Self {
        let subscription = workspace.upgrade().map(|entity| cx.subscribe(&entity, |this: &mut Self, _, event, cx| {
            if matches!(event, workspace::Event::ActiveItemChanged) { this.invalidate_for_buffer(cx); }
        }));
        Self { workspace, status: "Open a registered SQL file. No automatic warehouse execution.".into(),
            receipt: None, receipt_buffer: None, pending_buffer: None, historical: false,
            generation: 0, session: format!("native-{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos()),
            control: None, observed_editor: None, editor_subscription: None,
            _workspace_subscription: subscription, task: Task::ready(()) }
    }

    fn active_buffer(&self, cx: &App) -> Option<(String, String)> {
        let workspace = self.workspace.upgrade()?;
        let editor = workspace.read(cx).active_item(cx)?.act_as::<Editor>(cx)?;
        let buffer = editor.read(cx).buffer().read(cx).as_singleton()?;
        let buffer = buffer.read(cx);
        let file = buffer.file()?;
        let path = file.as_local()?.abs_path(cx).to_string_lossy().into_owned();
        if !path.ends_with(".sql") { return None; }
        Some((path, buffer.text()))
    }

    fn observe_active_editor(&mut self, cx: &mut Context<Self>) {
        let editor = self.workspace.upgrade().and_then(|w|
            w.read(cx).active_item(cx).and_then(|item| item.act_as::<Editor>(cx)));
        let id = editor.as_ref().map(|e| e.entity_id());
        if self.observed_editor != id {
            self.observed_editor = id;
            self.editor_subscription = editor.map(|e| cx.observe(&e, |this, _, cx| this.invalidate_for_buffer(cx)));
        }
    }

    fn invalidate_for_buffer(&mut self, cx: &mut Context<Self>) {
        let current = self.active_buffer(cx);
        if self.pending_buffer.is_some() && self.pending_buffer != current {
            self.cancel(cx);
            self.status = "Buffer changed. Obsolete bridge stopped; rerun for this buffer.".into();
        }
        if self.receipt.is_some() && self.receipt_buffer != current && !self.historical {
            self.historical = true;
            self.status = "Historical result: file or SQL changed. This is not proof of the current buffer.".into();
            cx.notify();
        }
    }

    fn cancel(&mut self, cx: &mut Context<Self>) {
        self.generation += 1;
        self.control.take(); // Drop signals the worker, which kills and reaps the bridge.
        self.pending_buffer = None;
        self.historical = self.receipt.is_some();
        self.status = "Cancelled native wait. A submitted local service job may finish within its own limit.".into();
        cx.notify();
    }

    fn execute(&mut self, verify: bool, cx: &mut Context<Self>) {
        let Some((path, sql)) = self.active_buffer(cx) else {
            self.status = "Select a local SQL editor buffer first.".into(); cx.notify(); return;
        };
        self.control.take();
        self.generation += 1;
        let generation = self.generation;
        let request_id = format!("{}-{generation}", self.session);
        let payload = json!({"protocol":"rowdy.native/1", "request_id":request_id,
            "session":self.session, "request":generation, "path":path,"sql":sql,
            "operation":if verify {"verify"} else {"preview"}});
        let identity = (path.clone(), sql.clone());
        self.pending_buffer = Some(identity.clone());
        self.historical = self.receipt.is_some();
        self.status = "Evaluating this exact buffer on registered local inputs…".into();
        let control = process::RunControl::new(); let cancel = control.0.clone(); self.control = Some(control);
        let task = cx.background_spawn(async move {
            let python = std::env::var("ROWDY_PYTHON").unwrap_or_else(|_| "python3".into());
            let mut command = Command::new(python); command.args(["-m", "rowdy.bridge"]);
            let bytes = process::run(&mut command, payload.to_string().into_bytes(), cancel,
                Duration::from_secs(20), process::MAX_OUTPUT)?;
            let value: Value = serde_json::from_slice(&bytes).map_err(|_| "Invalid native protocol response")?;
            let r = &value["receipt"];
            if value["ok"] != true || value["protocol"] != "rowdy.native/1"
                || value["request_id"] != payload["request_id"] || value["path"] != payload["path"]
                || value["operation"] != payload["operation"] || r["sql"] != payload["sql"]
                || r["request"] != payload["request"] || r["session"] != payload["session"]
                || r["kind"] != (if verify {"verification"} else {"preview"})
                || r["warehouse_verified"] != false || r["deployed"] != false {
                return Err("Native response does not match the requested buffer, operation, or authority".to_owned());
            }
            Ok(r.clone())
        });
        self.task = cx.spawn(async move |this, cx| {
            let result = task.await;
            this.update(cx, |this, cx| {
                if this.generation != generation { return; }
                this.control.take(); this.pending_buffer = None;
                if this.active_buffer(cx) != Some(identity.clone()) {
                    this.historical = this.receipt.is_some();
                    this.status = "Buffer changed before completion. The reply was not adopted.".into();
                    cx.notify(); return;
                }
                match result {
                    Ok(receipt) => {
                        this.status = format!("{} · {} rows · local snapshot only", receipt["status"].as_str().unwrap_or("unknown"), receipt["preview"]["count"]);
                        this.receipt = Some(receipt); this.receipt_buffer = Some(identity); this.historical = false;
                    }
                    Err(message) => { this.status = message; this.historical = this.receipt.is_some(); }
                }
                cx.notify();
            }).ok();
        });
        cx.notify();
    }
}

impl Render for RowdyView {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        self.observe_active_editor(cx);
        self.invalidate_for_buffer(cx);
        let mut body = v_flex().id("rowdy-receipt-body").flex_1().min_h_0().overflow_y_scroll().gap_2().p_3();
        if let Some(receipt) = &self.receipt {
            if self.historical {
                body = body.child(Label::new("Historical evidence — not verified for the current buffer").color(Color::Warning));
            }
            body = body.child(Label::new(format!("{} · {}", receipt["model"].as_str().unwrap_or("model"), receipt["engine"].as_str().unwrap_or("unknown engine"))).size(LabelSize::Small));
            if let Some(columns) = receipt["preview"]["columns"].as_array() {
                body = body.child(h_flex().gap_3().children(columns.iter().map(|c|
                    div().w(px(150.)).flex_none().child(Label::new(c.as_str().unwrap_or("").to_owned()).size(LabelSize::Small).color(Color::Muted)))));
                if let Some(rows) = receipt["preview"]["rows"].as_array() {
                    for row in rows.iter().take(100) {
                        body = body.child(h_flex().gap_3().children(columns.iter().map(|c| {
                            let value = &row[c.as_str().unwrap_or("")];
                            let text = value.as_str().map(str::to_owned).unwrap_or_else(|| value.to_string());
                            div().w(px(150.)).flex_none().overflow_hidden().child(Label::new(text).size(LabelSize::Small))
                        })));
                    }
                    if rows.len()>100 { body=body.child(Label::new(format!("Showing 100 of {} returned rows",rows.len())).size(LabelSize::Small)); }
                }
            }
            if let Some(checks) = receipt["checks"].as_array() {
                for check in checks {
                    body = body.child(Label::new(format!("{} — {}", check["status"].as_str().unwrap_or("unknown"), check["title"].as_str().unwrap_or("check"))).size(LabelSize::Small));
                }
            }
            body = body.child(Label::new(format!("Receipt {} · project context checked at run completion", receipt["id"].as_str().unwrap_or(""))).size(LabelSize::Small).color(Color::Muted));
        }
        let busy = self.pending_buffer.is_some();
        v_flex().size_full().child(
            h_flex().p_2().gap_3().border_b_1().border_color(cx.theme().colors().border)
                .child(Label::new("row(dy)").color(Color::Accent))
                .child(Button::new("rowdy-preview", "Preview buffer").on_click(cx.listener(|this, _, _, cx| this.execute(false, cx))))
                .child(Button::new("rowdy-verify", "Verify locally").on_click(cx.listener(|this, _, _, cx| this.execute(true, cx))))
                .when(busy, |bar| bar.child(Button::new("rowdy-cancel", "Cancel").on_click(cx.listener(|this, _, _, cx| this.cancel(cx)))))
        ).child(div().p_2().child(Label::new(self.status.clone()).size(LabelSize::Small))).child(body)
    }
}
