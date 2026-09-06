// SPDX-License-Identifier: GPL-3.0-only
//! Bounded child transport, independent of GPUI so its failure paths can run in CI.
//! Only the configured Rowdy Python bridge is launched. No shell or project command.
use std::io::{Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::{atomic::{AtomicBool, Ordering}, mpsc, Arc};
use std::time::{Duration, Instant};

pub const MAX_INPUT: usize = 100_000;
pub const MAX_OUTPUT: usize = 2_000_000;

struct Reap(Child);
impl Drop for Reap {
    fn drop(&mut self) {
        // kill alone leaves a zombie on Unix. Always wait, including I/O errors.
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

/// Dropping a view or replacing its request cancels the matching bridge process.
pub struct RunControl(pub Arc<AtomicBool>);
impl RunControl {
    pub fn new() -> Self { Self(Arc::new(AtomicBool::new(false))) }
    pub fn cancel(&self) { self.0.store(true, Ordering::SeqCst); }
}
impl Drop for RunControl { fn drop(&mut self) { self.cancel(); } }

enum Io { Written(std::io::Result<()>), Read(std::io::Result<Vec<u8>>) }

pub fn run(command: &mut Command, input: Vec<u8>, cancel: Arc<AtomicBool>,
           timeout: Duration, limit: usize) -> Result<Vec<u8>, String> {
    if input.len() > MAX_INPUT || limit == 0 || limit > MAX_OUTPUT {
        return Err("Native request or response budget is invalid".into());
    }
    if cancel.load(Ordering::SeqCst) { return Err("Native request cancelled before start".into()); }
    let started = Instant::now();
    let mut child = Reap(command.stdin(Stdio::piped()).stdout(Stdio::piped())
        .stderr(Stdio::null()).spawn().map_err(|_| "Could not launch Rowdy bridge")?);
    let mut stdin = child.0.stdin.take().ok_or("Bridge stdin missing")?;
    let stdout = child.0.stdout.take().ok_or("Bridge stdout missing")?;
    let (tx, rx) = mpsc::channel();
    let writer = tx.clone();
    // Concurrent writes and reads avoid full-pipe deadlocks. Reads stop at the
    // budget + 1 byte, instead of buffering an unlimited wait_with_output().
    std::thread::spawn(move || { let _ = writer.send(Io::Written(stdin.write_all(&input))); });
    std::thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout.take((limit + 1) as u64).read_to_end(&mut bytes).map(|_| bytes);
        let _ = tx.send(Io::Read(result));
    });
    let mut written = false;
    let mut output = None;
    loop {
        if cancel.load(Ordering::SeqCst) { return Err("Native request cancelled; bridge stopped".into()); }
        if started.elapsed() >= timeout { return Err("Native bridge deadline exceeded".into()); }
        match rx.recv_timeout(Duration::from_millis(10)) {
            Ok(Io::Written(Ok(()))) => written = true,
            Ok(Io::Read(Ok(bytes))) => {
                if bytes.len() > limit { return Err("Native response exceeded budget".into()); }
                output = Some(bytes);
            }
            Ok(Io::Written(Err(_))) | Ok(Io::Read(Err(_))) => return Err("Native bridge I/O failed".into()),
            Err(_) => std::thread::sleep(Duration::from_millis(2)),
        }
        if let Some(status) = child.0.try_wait().map_err(|_| "Could not inspect bridge status")? {
            if !status.success() { return Err("Native bridge failed; no successful result claimed".into()); }
            if written {
                if let Some(bytes) = output.take() { return Ok(bytes); }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn python(script: &str) -> Command {
        let mut c = Command::new(std::env::var("ROWDY_TEST_PYTHON").unwrap_or("python3".into()));
        c.args(["-c", script]); c
    }
    fn call(script: &str, input: &[u8], limit: usize) -> Result<Vec<u8>, String> {
        run(&mut python(script), input.to_vec(), Arc::new(AtomicBool::new(false)), Duration::from_secs(2), limit)
    }
    #[test] fn round_trip() { assert_eq!(call("import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())", b"hello", 100).unwrap(), b"hello"); }
    #[test] fn large_bidirectional_pipes() {
        let output = call("import sys; sys.stdout.write('x'*80000); sys.stdout.flush(); assert len(sys.stdin.buffer.read()) == 80000", &vec![b'a';80000], 90000).unwrap();
        assert_eq!(output.len(),80000);
    }
    #[test] fn response_is_bounded_before_exit() { assert!(call("import sys,time; sys.stdout.write('x'*3000); sys.stdout.flush(); time.sleep(30)", b"", 1000).unwrap_err().contains("budget")); }
    #[test] fn nonzero_exit_is_not_success() { assert!(call("import sys; print('{}'); sys.exit(7)", b"", 100).unwrap_err().contains("failed")); }
    #[test] fn stderr_is_not_protocol() { assert_eq!(call("import sys; sys.stderr.write('secret'*30000); print('ok')", b"", 100).unwrap(), b"ok\n"); }
    #[test] fn timeout_stops_non_reader() {
        let t=Instant::now();
        let result=run(&mut python("import time; time.sleep(30)"),vec![0;90000],Arc::new(AtomicBool::new(false)),Duration::from_millis(120),100);
        assert!(result.unwrap_err().contains("deadline")); assert!(t.elapsed()<Duration::from_secs(2));
    }
    #[test] fn cancelled_during_execution() {
        let control=RunControl::new(); let cancel=control.0.clone();
        let thread=std::thread::spawn(move || run(&mut python("import time; time.sleep(30)"),vec![],cancel,Duration::from_secs(10),100));
        std::thread::sleep(Duration::from_millis(100)); control.cancel();
        assert!(thread.join().unwrap().unwrap_err().contains("cancelled"));
    }
    #[test] fn dropping_owner_cancels() { let c=RunControl::new(); let state=c.0.clone(); drop(c); assert!(state.load(Ordering::SeqCst)); }
    #[test] fn cancelled_before_start() { let c=Arc::new(AtomicBool::new(true)); assert!(run(&mut Command::new("not-a-binary"),vec![],c,Duration::from_secs(1),100).unwrap_err().contains("before start")); }
    #[test] fn oversize_input_does_not_spawn() { assert!(call("raise Exception('should not start')",&vec![0;MAX_INPUT+1],100).unwrap_err().contains("budget")); }
}
