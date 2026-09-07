// SPDX-License-Identifier: GPL-3.0-only
//! Reviewed source changes. No writes are triggered by preview, live or Verify.
//! A shared buffer is locked during a confirmed mutation and reloaded from disk;
//! navigation never transfers that operation to a different file.
use super::*;
use gpui::Entity;

#[derive(Default)]
pub(super) struct EditState {
    pub review: Option<Value>,
    pub origin: Option<BufferKey>,
    pub last_change: Option<Value>,
    pub uncertain: Option<Value>,
    pub busy: bool,
    pub generation: u64,
}

impl RowdyView {
    pub(super) fn can_review(&self) -> bool {
        !self.edits.busy && self.current_result() && self.state.pending.is_none()
            && self.result.as_ref().is_some_and(|r| r["receipt"]["kind"] == "verification"
                && r["receipt"]["status"] == "passed" && r["receipt"]["stage"] == "result")
    }

    pub(super) fn start_edit(&mut self, operation: &str, cx: &mut Context<Self>) {
        if self.edits.busy { return; }
        self.observe_buffer(cx);
        let Some(buffer_key) = self.active_buffer(cx) else { return; };
        let reference = match operation {
            "review" if self.can_review() => self.result.clone(),
            "review_undo" => self.edits.last_change.clone(),
            "apply" | "undo" if self.edits.origin.as_ref() == Some(&buffer_key) => self.edits.review.clone(),
            "status" => self.edits.uncertain.clone(),
            _ => None,
        };
        let Some(reference) = reference else {
            self.status = "Verify the current full result, then review its exact source difference.".into(); cx.notify(); return;
        };
        self.pause(cx);
        self.edits.generation += 1;
        let generation = self.edits.generation;
        let (record, binding, sql) = if operation == "status" {
            (reference["record_id"].clone(), reference["expected_binding"].clone(), reference["sql"].clone())
        } else if operation == "review" {
            (reference["receipt"]["id"].clone(),reference["binding"]["hash"].clone(),json!(buffer_key.sql))
        } else {
            (reference["receipt"]["id"].clone(),reference["current_binding"].clone(),json!(buffer_key.sql))
        };
        let payload = json!({"protocol":"rowdy.native-edit/1", "request_id":format!("{}-edit-{generation}", self.session),
            "session":self.session, "request":generation,"operation":operation,"path":buffer_key.path,
            "sql":sql,"record_id":record,"expected_binding":binding});
        let mutation = operation == "apply" || operation == "undo";
        // Lock the shared language buffer, not only one editor pane. Other splits
        // cannot type into it while a reviewed mutation is pending. External file
        // changes are independently checked by the service.
        let locked: Option<(Entity<language::Buffer>, language::Capability)> = if mutation {
            let buffer = self.workspace.upgrade().and_then(|w| w.read(cx).active_item(cx)
                .and_then(|i|i.act_as::<Editor>(cx)))
                .and_then(|e|e.read(cx).buffer().read(cx).as_singleton());
            let Some(buffer) = buffer else { return; };
            let capability = buffer.read(cx).capability();
            if buffer.read(cx).read_only() {
                self.status="The source buffer is read-only. No change was submitted.".into(); cx.notify(); return;
            }
            buffer.update(cx, |b,cx| b.set_capability(language::Capability::ReadOnly,cx));
            Some((buffer,capability))
        } else { None };
        self.edits.busy=true;
        self.surface=Surface::Review;
        self.status=if mutation {"Saving the reviewed file atomically. Do not assume cancellation means no write occurred."}
            else {"Preparing a read-only review tied to independent checks and this exact buffer."}.into();
        let payload_copy=payload.clone();
        let operation=operation.to_owned();
        let background=cx.background_spawn(async move {
            let python=std::env::var("ROWDY_PYTHON").unwrap_or_else(|_|"python3".into());
            let mut command=Command::new(python); command.args(["-m","rowdy.native_edits"]);
            let guard=process::RunControl::new();
            let bytes=process::run(&mut command,payload_copy.to_string().into_bytes(),guard.0.clone(),Duration::from_secs(20),process::MAX_OUTPUT)?;
            let response:Value=serde_json::from_slice(&bytes).map_err(|_|"Invalid native change response")?;
            if response["ok"]!=true || response["protocol"]!="rowdy.native-edit/1"
                || response["request_id"]!=payload_copy["request_id"] || response["request"]!=payload_copy["request"]
                || response["session"]!=payload_copy["session"] || response["path"]!=payload_copy["path"]
                || response["operation"]!=payload_copy["operation"] || response["buffer_sql"]!=payload_copy["sql"]
                || response["cloud_enabled"]!=false || response["current_binding"].as_str().map_or(true,|s|s.len()!=64) {
                return Err("Change blocked or response unconfirmed. Inspect the stored write outcome before retrying.".to_owned());
            }
            let r=&response["receipt"];
            if r["kind"]!="edit_status" && (r["model"]!=response["model"] || r["project"]!=response["project"]
                || r["session"]!=payload_copy["session"] || r["committed"]!=false || r["deployed"]!=false
                || r["warehouse_verified"]!=false || r["consumer_verified"]!=false) {
                return Err("Source change receipt has the wrong identity or authority".to_owned());
            }
            let valid_kind=match payload_copy["operation"].as_str().unwrap_or("") {
                "review" => r["kind"]=="native_change_review" && r["direction"]=="apply" && r["after_sql"]==payload_copy["sql"],
                "review_undo" => r["kind"]=="native_change_review" && r["direction"]=="undo" && r["before_sql"]==payload_copy["sql"],
                "apply" => r["kind"]=="native_file_edit" && r["status"]=="applied" && r["after_sql"]==payload_copy["sql"],
                "undo" => r["kind"]=="native_file_undo" && r["status"]=="undone" && r["before_sql"]==payload_copy["sql"],
                "status" => matches!(r["kind"].as_str(),Some("native_file_edit"|"native_file_undo"|"edit_status")),
                _=>false,
            };
            if !valid_kind { return Err("Unexpected source-change outcome".to_owned()); }
            Ok(response)
        });
        // Finish/release the buffer even when the panel closes. The actual write
        // has a persisted single-use journal; there is no fire-and-forget replay.
        cx.spawn(async move |this,cx| {
            let mut reply=background.await;
            if let Some((buffer,capability))=locked {
                if let Ok(response)=&reply {
                    let target=response["receipt"]["after_sql"].as_str().unwrap_or("").to_owned();
                    let unchanged=buffer.read_with(cx,|b,_|b.text()==buffer_key.sql);
                    if unchanged {
                        let reload=buffer.update(cx,|b,cx|b.reload(cx));
                        if reload.await.is_err() || !buffer.read_with(cx,|b,_|b.text()==target) {
                            reply=Err("File operation recorded but the editor could not confirm reload. Inspect the outcome; do not reapply.".into());
                        }
                    } else {
                        reply=Err("File operation may have completed; the buffer changed independently. No buffer contents were discarded.".into());
                    }
                }
                buffer.update(cx,|b,cx|b.set_capability(capability,cx));
            }
            this.update(cx,|this,cx| {
                if this.edits.generation!=generation {return;}
                this.edits.busy=false;
                this.observe_buffer(cx);
                this.state.pause(); this.state.frozen_binding=None;
                match reply {
                    Ok(response) if operation=="review" || operation=="review_undo" => {
                        if this.active_buffer(cx)!=Some(buffer_key.clone()) {
                            this.edits.review=None;
                            this.status="Buffer changed during review. No file was written; review the current buffer.".into();
                        } else {
                            this.edits.review=Some(response); this.edits.origin=Some(buffer_key);
                            this.status="Review only. Apply or undo requires the explicit confirmation below.".into();
                        }
                    }
                    Ok(response) if response["receipt"]["kind"]=="edit_status" => {
                        this.status="No write was started for this review. No file was changed.".into(); this.edits.uncertain=None;
                    }
                    Ok(response) => {
                        this.historical=this.result.is_some(); this.edits.review=None; this.edits.uncertain=None;
                        this.status=if response["receipt"]["status"]=="undone" {"Reviewed change undone. No commit or deployment occurred."}
                            else {"Source change recorded. Prior verification remains historical; preview this saved revision before continuing."}.into();
                        this.edits.last_change=Some(response);
                    }
                    Err(message) => {
                        if mutation {this.edits.uncertain=Some(payload); this.historical=this.result.is_some();}
                        this.edits.review=None; this.status=message;
                    }
                }
                cx.notify();
            }).ok();
        }).detach();
        cx.notify();
    }

    pub(super) fn render_review(&self,cx:&mut Context<Self>) -> gpui::AnyElement {
        let mut body=v_flex().gap_2().w_full();
        if self.edits.busy {return body.child(Label::new("Waiting for a bounded local file operation…")).into_any_element();}
        if let Some(review)=&self.edits.review {
            let r=&review["receipt"]; let undo=r["direction"]=="undo";
            let applicable=self.edits.origin==self.active_buffer(cx);
            body=body.child(Label::new(if undo {"Undo review"} else {"Source change review"}).color(Color::Accent))
                .child(Label::new(format!("{} · {}",text(&r["file"]),if applicable {"Current reviewed buffer"} else {"Buffer changed — review again"})))
                .child(Label::new("Changes only the named local SQL file. No commit, push, build or deployment.").color(Color::Muted));
            for line in r["diff"].as_str().unwrap_or("").lines() {
                let color=if line.starts_with('+') {Color::Success} else if line.starts_with('-') {Color::Warning} else {Color::Default};
                body=body.child(Label::new(line.to_owned()).buffer_font(cx).color(color));
            }
            body=body.child(h_flex().gap_2()
                .child(Button::new("rowdy-confirm-edit",if undo {"Undo this change"} else {"Apply reviewed change"})
                    .disabled(!applicable).on_click(cx.listener(move |this,_,_,cx|this.start_edit(if undo {"undo"} else {"apply"},cx))))
                .child(Button::new("rowdy-dismiss-review","Cancel review").on_click(cx.listener(|this,_,_,cx| {
                    this.edits.review=None;this.edits.origin=None;this.surface=Surface::Results;cx.notify();
                }))));
        } else if let Some(change)=&self.edits.last_change {
            body=body.child(Label::new(format!("{} · receipt {}",text(&change["receipt"]["status"]),text(&change["receipt"]["id"]))).color(Color::Accent))
                .child(Label::new("File-change evidence is not a warehouse verification or a deployment.").color(Color::Muted));
            if change["receipt"]["status"]=="applied" {
                body=body.child(Button::new("rowdy-review-undo","Review undo").on_click(cx.listener(|this,_,_,cx|this.start_edit("review_undo",cx))));
            }
        } else {
            body=body.child(Label::new("Verify the current full result, then review its source difference."));
        }
        if self.edits.uncertain.is_some() {
            body=body.child(Button::new("rowdy-edit-status","Inspect write outcome").on_click(cx.listener(|this,_,_,cx|this.start_edit("status",cx))));
        }
        body.into_any_element()
    }
}
