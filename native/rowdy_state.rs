// SPDX-License-Identifier: GPL-3.0-only
//! UI-independent scheduling rules. File switches pause live work; edits do not
//! let old replies become current. No code here can execute a process or query.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BufferKey {
    pub path: String,
    pub sql: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Operation { Preview, Verify, Trace }
impl Operation {
    pub fn name(self) -> &'static str {
        match self { Self::Preview => "preview", Self::Verify => "verify", Self::Trace => "trace" }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Ticket {
    pub generation: u64,
    pub revision: u64,
    pub buffer: BufferKey,
    pub operation: Operation,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Change { None, Edit, File }

#[derive(Default)]
pub struct State {
    pub buffer: Option<BufferKey>,
    pub revision: u64,
    pub generation: u64,
    pub pending: Option<Ticket>,
    pub live: bool,
    pub frozen_binding: Option<String>,
}
impl State {
    pub fn observe(&mut self, buffer: Option<BufferKey>) -> Change {
        if self.buffer == buffer { return Change::None; }
        let same_file = self.buffer.as_ref().zip(buffer.as_ref()).is_some_and(|(a,b)| a.path == b.path);
        self.buffer = buffer;
        self.revision += 1;
        self.cancel();
        if !same_file {
            self.live = false;
            self.frozen_binding = None;
            Change::File
        } else { Change::Edit }
    }
    pub fn start(&mut self, operation: Operation) -> Option<Ticket> {
        let buffer = self.buffer.clone()?;
        if operation == Operation::Trace { self.pause(); }
        self.generation += 1;
        let ticket = Ticket { generation: self.generation, revision: self.revision, buffer, operation };
        self.pending = Some(ticket.clone());
        Some(ticket)
    }
    pub fn accept(&mut self, ticket: &Ticket) -> bool {
        if self.pending.as_ref() != Some(ticket) || self.revision != ticket.revision || self.buffer.as_ref() != Some(&ticket.buffer) { return false; }
        self.pending = None;
        true
    }
    pub fn cancel(&mut self) { self.generation += 1; self.pending = None; }
    pub fn pause(&mut self) { self.live = false; self.cancel(); }
    pub fn enable(&mut self, verified_buffer: &BufferKey, binding: String) -> bool {
        if self.buffer.as_ref() != Some(verified_buffer) || binding.is_empty() || self.pending.is_some() { return false; }
        self.live = true; self.frozen_binding = Some(binding); true
    }
    pub fn should_replay(&self, revision: u64) -> bool {
        self.live && self.buffer.is_some() && self.revision == revision && self.pending.is_none()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn key(path: &str, sql: &str) -> BufferKey { BufferKey { path:path.into(), sql:sql.into() } }
    fn state() -> State { let mut s=State::default(); s.observe(Some(key("a.sql","select 1"))); s }
    fn live() -> State { let mut s=state(); let b=s.buffer.clone().unwrap(); assert!(s.enable(&b,"snapshot-a".into())); s }
    #[test] fn no_request_without_a_buffer() { assert!(State::default().start(Operation::Preview).is_none()); }
    #[test] fn unchanged_observation_is_inert() { let mut s=live(); let g=s.generation; assert_eq!(s.observe(s.buffer.clone()),Change::None); assert_eq!(g,s.generation); assert!(s.live); }
    #[test] fn edit_invalidates_reply() { let mut s=state(); let t=s.start(Operation::Preview).unwrap(); s.observe(Some(key("a.sql","select 2"))); assert!(!s.accept(&t)); }
    #[test] fn same_revision_verification_wins() { let mut s=state(); let p=s.start(Operation::Preview).unwrap(); let v=s.start(Operation::Verify).unwrap(); assert!(!s.accept(&p)); assert!(s.accept(&v)); }
    #[test] fn cancelled_reply_not_adopted() { let mut s=state(); let t=s.start(Operation::Preview).unwrap(); s.cancel(); assert!(!s.accept(&t)); }
    #[test] fn accepted_reply_only_once() { let mut s=state(); let t=s.start(Operation::Preview).unwrap(); assert!(s.accept(&t)); assert!(!s.accept(&t)); }
    #[test] fn file_switch_pauses_and_clears_binding() { let mut s=live(); assert_eq!(s.observe(Some(key("b.sql","select 1"))),Change::File); assert!(!s.live); assert!(s.frozen_binding.is_none()); }
    #[test] fn edit_keeps_fixed_context() { let mut s=live(); s.observe(Some(key("a.sql","select 2"))); assert!(s.live); assert_eq!(s.frozen_binding.as_deref(),Some("snapshot-a")); }
    #[test] fn old_debounce_does_not_fire() { let mut s=live(); let r=s.revision; s.observe(Some(key("a.sql","select 2"))); assert!(!s.should_replay(r)); assert!(s.should_replay(s.revision)); }
    #[test] fn live_requires_current_successful_context() { let mut s=state(); assert!(!s.enable(&key("b.sql","select 1"),"a".into())); assert!(!s.live); }
    #[test] fn pending_verification_blocks_debounce() { let mut s=live(); s.start(Operation::Verify); assert!(!s.should_replay(s.revision)); }
    #[test] fn explicit_once_does_not_enable_live() { let mut s=state(); let t=s.start(Operation::Preview).unwrap(); assert!(s.accept(&t)); assert!(!s.live); }
    #[test] fn trace_pauses_live_and_supersedes_preview() { let mut s=live(); let p=s.start(Operation::Preview).unwrap(); let t=s.start(Operation::Trace).unwrap(); assert!(!s.live); assert!(!s.accept(&p)); assert!(s.accept(&t)); }
    #[test] fn closing_editor_cancels_pending_work() { let mut s=live(); let t=s.start(Operation::Preview).unwrap(); s.observe(None); assert!(!s.live); assert!(!s.accept(&t)); }
    #[test] fn restoring_same_text_never_revives_old_ticket() { let mut s=state(); let t=s.start(Operation::Preview).unwrap(); s.observe(Some(key("a.sql","select 2"))); s.observe(Some(key("a.sql","select 1"))); assert!(!s.accept(&t)); }
    #[test] fn rapid_edits_settle_on_latest_revision() { let mut s=live(); for n in 0..20 { s.observe(Some(key("a.sql",&format!("select {n}")))); } let r=s.revision; assert!(s.should_replay(r)); let t=s.start(Operation::Preview).unwrap(); assert_eq!(t.buffer.sql,"select 19"); assert!(!s.should_replay(r)); }
}
