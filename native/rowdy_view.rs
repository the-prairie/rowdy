// SPDX-License-Identifier: GPL-3.0-only
//! Experimental native Zed view. This is real GPUI source, not a webview.
//! Build validation and Mac smoke testing remain required before distribution.
use std::io::Write;
use std::process::{Command, Stdio};
use editor::Editor;
use gpui::{App, Context, Task, WeakEntity, Window};
use serde_json::{json, Value};
use ui::{prelude::*, Button, Label};
use workspace::Workspace;

pub struct RowdyView {
    workspace: WeakEntity<Workspace>,
    status: String,
    receipt: Option<Value>,
    generation: u64,
    task: Task<()>,
}

impl RowdyView {
    pub fn new(workspace: WeakEntity<Workspace>, _cx: &mut Context<Self>) -> Self {
        Self { workspace, status: "Open a registered SQL file. Local replay never invokes the warehouse.".into(),
            receipt: None, generation: 0, task: Task::ready(()) }
    }

    fn active_buffer(&self, cx: &App) -> Option<(String, String)> {
        let workspace = self.workspace.upgrade()?;
        let editor = workspace.read(cx).active_item(cx)?.act_as::<Editor>(cx)?;
        let buffer = editor.read(cx).buffer().read(cx).as_singleton()?;
        let buffer = buffer.read(cx);
        let file = buffer.file()?;
        let path = file.as_local()?.abs_path(cx).to_string_lossy().into_owned();
        Some((path, buffer.text()))
    }

    fn execute(&mut self, verify: bool, cx: &mut Context<Self>) {
        let Some((path, sql)) = self.active_buffer(cx) else {
            self.status = "Select a local SQL editor buffer first.".into(); cx.notify(); return;
        };
        self.generation += 1;
        let generation = self.generation;
        let payload = json!({"path": path, "sql": sql, "operation": if verify { "verify" } else { "preview" }});
        self.status = "Evaluating the exact selected buffer on registered local inputs…".into();
        let task = cx.background_spawn(async move {
            let python = std::env::var("ROWDY_PYTHON").unwrap_or_else(|_| "python3".into());
            let mut child = Command::new(python)
                .args(["-m", "rowdy.bridge"])
                .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
                .spawn().map_err(|_| "Could not start the explicitly configured Rowdy Python bridge".to_owned())?;
            if let Some(mut stdin) = child.stdin.take() {
                stdin.write_all(payload.to_string().as_bytes()).map_err(|_| "Could not send native buffer".to_owned())?;
            }
            let out = child.wait_with_output().map_err(|_| "Native bridge did not finish".to_owned())?;
            if !out.status.success() || out.stdout.len() > 2_000_000 {
                return Err("Bridge failed or response exceeded budget. No successful result is claimed.".to_owned());
            }
            let value: Value = serde_json::from_slice(&out.stdout).map_err(|_| "Invalid bridge response".to_owned())?;
            if value["ok"] != true { return Err("Native action failed".to_owned()); }
            Ok(value["receipt"].clone())
        });
        self.task = cx.spawn(async move |this, cx| {
            let result = task.await;
            this.update(cx, |this, cx| {
                if this.generation != generation { return; }
                match result {
                    Ok(receipt) => {
                        this.status = format!("{} · {} rows · local snapshot · not warehouse verification",
                            receipt["status"].as_str().unwrap_or("unknown"), receipt["preview"]["count"]);
                        this.receipt = Some(receipt);
                    }
                    Err(message) => { this.status = message; }
                }
                cx.notify();
            }).ok();
        });
        cx.notify();
    }
}

impl Render for RowdyView {
    fn render(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let mut body = v_flex().id("rowdy-receipt-body").flex_1().min_h_0().overflow_y_scroll().gap_2().p_3();
        if let Some(receipt) = &self.receipt {
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
                }
            }
            if let Some(checks) = receipt["checks"].as_array() {
                for check in checks {
                    body = body.child(Label::new(format!("{} — {}", check["status"].as_str().unwrap_or("unknown"), check["title"].as_str().unwrap_or("check"))).size(LabelSize::Small));
                }
            }
            body = body.child(Label::new(format!("Receipt {} · input {}", receipt["id"].as_str().unwrap_or(""), receipt["identity"]["input_hash"].as_str().unwrap_or(""))).size(LabelSize::XSmall).color(Color::Muted));
        }
        v_flex().size_full().child(
            h_flex().p_2().gap_3().border_b_1().border_color(cx.theme().colors().border)
                .child(Label::new("row(dy)").color(Color::Accent))
                .child(Button::new("rowdy-preview", "Preview buffer").on_click(cx.listener(|this, _, _, cx| this.execute(false, cx))))
                .child(Button::new("rowdy-verify", "Verify locally").on_click(cx.listener(|this, _, _, cx| this.execute(true, cx))))
        ).child(div().p_2().child(Label::new(self.status.clone()).size(LabelSize::Small))).child(body)
    }
}
