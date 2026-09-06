// SPDX-License-Identifier: GPL-3.0-only
//! Actual native GPUI/Metal workspace, pointer callbacks, native buffer edits,
//! stdio bridge and local HTTP/SQLite. Not a signed application/pilot test.
use super::*;
use anyhow::{anyhow, ensure};
use dbt_ui::{DbtResultsPanel, rowdy_view::RowdyView};
use gpui::Focusable;
use serde_json::{json, Value};
use std::time::Instant;

fn paint(cx: &mut VisualTestAppContext, window: WindowHandle<Workspace>) -> Result<()> {
    cx.run_until_parked();
    cx.update_window(window.into(), |_, window, cx| window.draw(cx).clear(cx))?;
    Ok(())
}

fn click(cx: &mut VisualTestAppContext, window: WindowHandle<Workspace>, id: &str) -> Result<()> {
    let in_content=id.starts_with("rowdy-event-") || id.starts_with("rowdy-open-") || id.starts_with("rowdy-id-");
    for _ in 0..16 {
        paint(cx,window)?;
        let (bounds,content)=cx.update_window(window.into(),|_,window,_|
            (window.rowdy_test_bounds(id),window.rowdy_test_bounds("rowdy-evidence")))?;
        let clip=content.ok_or_else(||anyhow!("Native evidence surface did not render"))?;
        if let Some(bounds)=bounds {
            let p=bounds.center();
            let min_y=if in_content {clip.origin.y+px(4.)} else {px(0.)};
            let max_y=if in_content {clip.bottom()-px(4.)} else {px(790.)};
            if p.y>min_y && p.y<max_y && p.x>px(0.) && p.x<px(1275.) {
                cx.simulate_click(window.into(),p,Modifiers::default());
                return paint(cx,window);
            }
        }
        ensure!(in_content,"Native toolbar control missing: {id}");
        let delta=if bounds.is_some_and(|b|b.center().y<clip.origin.y+px(4.)) {140.} else {-140.};
        cx.simulate_event(window.into(),gpui::ScrollWheelEvent {
            position:clip.center(),delta:gpui::ScrollDelta::Pixels(point(px(0.),px(delta))),
            ..Default::default()
        });
    }
    Err(anyhow!("Native control could not be reached by scrolling: {id}"))
}

fn state(cx: &VisualTestAppContext, view: &Entity<RowdyView>) -> Value {
    cx.read(|cx| view.read(cx).visual_state())
}

fn wait(cx: &mut VisualTestAppContext, window: WindowHandle<Workspace>, view: &Entity<RowdyView>, f: impl Fn(&Value)->bool) -> Result<Value> {
    let start=Instant::now();
    loop {
        cx.advance_clock(Duration::from_millis(50));
        paint(cx, window)?;
        let value=state(cx,view);
        if f(&value) { return Ok(value); }
        ensure!(start.elapsed() < Duration::from_secs(30), "Native action timeout: {}",value["status"]);
        std::thread::sleep(Duration::from_millis(15));
    }
}

fn open(cx: &mut VisualTestAppContext, window: WindowHandle<Workspace>, path: PathBuf) -> Result<()> {
    let task=window.update(cx,|workspace,window,cx|workspace.open_abs_path(path,workspace::OpenOptions::default(),window,cx))?;
    cx.foreground_executor.block_test(task)?;
    paint(cx,window)
}

fn edit(cx: &mut VisualTestAppContext, window: WindowHandle<Workspace>, sql: &str) -> Result<()> {
    window.update(cx,|workspace,window,cx| {
        let editor=workspace.active_item(cx).and_then(|i|i.act_as::<editor::Editor>(cx)).expect("real SQL editor");
        editor.update(cx,|editor,cx|editor.set_text(sql,window,cx));
    })?;
    paint(cx,window)
}

fn image(cx: &mut VisualTestAppContext, window: WindowHandle<Workspace>, out: &Path, name: &str) -> Result<()> {
    paint(cx,window)?;
    let image=cx.capture_screenshot(window.into())?;
    ensure!(image.width() >= 1280 && image.height() >= 800,"Unexpected native image size");
    image.save(out.join(name))?;
    Ok(())
}

pub fn run(cx: &mut VisualTestAppContext, window: WindowHandle<Workspace>, root: PathBuf) -> Result<()> {
    cx.background_executor.allow_parking();
    let out=PathBuf::from(std::env::var("ROWDY_NATIVE_OUTPUT")?);
    std::fs::create_dir_all(&out)?;
    let (weak,async_cx)=window.update(cx,|workspace,window,cx|(workspace.weak_handle(),window.to_async(cx)))?;
    let panel=cx.foreground_executor.block_test(DbtResultsPanel::load(weak,async_cx))?;
    let view=cx.update(|cx|panel.update(cx,|panel,cx|panel.rowdy_visual_handle(cx)));
    window.update(cx,|workspace,window,cx| {
        workspace.add_panel(panel.clone(),window,cx);
        workspace.open_panel::<DbtResultsPanel>(window,cx);
        workspace.bottom_dock().clone().update(cx,|dock,cx|dock.resize_panel_sizes(Some(px(480.)),None,window,cx));
    })?;
    open(cx,window,root.join("models/session_summary.sql"))?;
    click(cx,window,"rowdy-preview")?;
    let first=wait(cx,window,&view,|s|!s["pending"].as_bool().unwrap_or(true) && s["result"].is_object())?;
    ensure!(!first["historical"].as_bool().unwrap(),"First native result historical");
    let origin=first["result"]["receipt"].clone();
    let row=origin["preview"]["rows"].as_array().unwrap().iter().position(|r|r["user_id"]=="U-1042" && r["namespace"]=="web-us").ok_or_else(||anyhow!("Expected source user"))?;
    image(cx,window,&out,"01-native-results.png")?;
    click(cx,window,&format!("rowdy-id-{row}-user_id"))?;
    let traced=wait(cx,window,&view,|s|s["trace"]["receipt"]["origin_run"]==origin["id"] && s["pending"]==false)?;
    let mut events=traced["trace"]["receipt"]["events"].as_array().unwrap().clone();
    events.sort_by_key(|e|(e["event_time"].to_string(),e["id"].to_string()));
    let ix=events.iter().position(|e|e["id"]=="E-006").ok_or_else(||anyhow!("Selected event absent"))?;
    click(cx,window,&format!("rowdy-event-{ix}"))?;
    ensure!(state(cx,&view)["selected_event"]=="E-006","Wrong event selected");
    image(cx,window,&out,"02-native-trace.png")?;
    let stages=events[ix]["stages"].as_array().unwrap();
    let stage=stages.iter().position(|s|s["model"]=="unified_events").unwrap();
    ensure!(stages[stage]["state"]=="not_found_in_scope","Expected scoped missing event");
    click(cx,window,&format!("rowdy-open-{ix}-{stage}"))?;
    wait(cx,window,&view,|s|s["live"]==false)?;
    // Opening the real file is asynchronous; wait for the workspace item.
    let start=Instant::now();
    loop {
        paint(cx,window)?;
        let ready=window.update(cx,|workspace,_,cx| {
            workspace.active_item(cx).and_then(|i|i.act_as::<editor::Editor>(cx)).is_some_and(|e|e.read(cx).text(cx).contains("schema_version = 1"))
        })?;
        if ready { break; }
        ensure!(start.elapsed()<Duration::from_secs(10),"Open SQL did not navigate");
        std::thread::sleep(Duration::from_millis(20));
    }
    click(cx,window,"rowdy-results")?;
    click(cx,window,"rowdy-preview")?;
    let before=wait(cx,window,&view,|s|s["result"]["binding"]["model"]=="unified_events" && s["pending"]==false)?;
    ensure!(before["result"]["receipt"]["output"]["count"]==16,"Original result count");
    let sql=std::fs::read_to_string(root.join("models/unified_events.sql"))?;
    let fixed=sql.replace("schema_version = 1","schema_version IN (1, 2)");
    click(cx,window,"rowdy-live")?;
    ensure!(state(cx,&view)["live"]==true,"Live did not enable");
    edit(cx,window,&fixed)?;
    let after=wait(cx,window,&view,|s|s["result"]["receipt"]["sql"]==fixed && s["historical"]==false && s["pending"]==false)?;
    ensure!(after["result"]["receipt"]["output"]["count"]==17,"Live edit not evaluated");
    ensure!(after["result"]["receipt"]["downstream"][0]["after"][0]["completions"]==1,"Downstream result");
    ensure!(after["trace"]==traced["trace"],"Historical trace mutated");
    image(cx,window,&out,"03-native-live.png")?;
    click(cx,window,"rowdy-changes")?;
    image(cx,window,&out,"04-native-changes.png")?;
    click(cx,window,"rowdy-verify")?;
    let verified=wait(cx,window,&view,|s|s["result"]["receipt"]["kind"]=="verification" && s["pending"]==false)?;
    ensure!(verified["result"]["receipt"]["status"]=="passed","Expected independent checks");
    let bad=fixed.replace("schema_version IN (1, 2)","schema_version IN (1, 2) AND event_id != 'E-024'");
    edit(cx,window,&bad)?;
    wait(cx,window,&view,|s|s["result"]["receipt"]["sql"]==bad && s["pending"]==false)?;
    click(cx,window,"rowdy-verify")?;
    let failed=wait(cx,window,&view,|s|s["result"]["receipt"]["sql"]==bad && s["result"]["receipt"]["kind"]=="verification" && s["pending"]==false)?;
    ensure!(failed["result"]["receipt"]["status"]=="not_passed","Broken control did not fail");
    ensure!(failed["result"]["receipt"]["output"]["rows"].as_array().unwrap().iter().any(|r|r["event_id"]=="E-006"),"Selected record unexpectedly absent");
    image(cx,window,&out,"05-native-failed-control.png")?;
    click(cx,window,"rowdy-live")?;
    ensure!(state(cx,&view)["live"]==false,"Pause failed");
    ensure!(state(cx,&view)["historical"]==false,"Idle pause invalidated current evidence");
    click(cx,window,"rowdy-editor")?;
    let focused=window.update(cx,|workspace,window,cx|workspace.active_item(cx).and_then(|i|i.act_as::<editor::Editor>(cx)).unwrap().read(cx).focus_handle(cx).is_focused(window))?;
    ensure!(focused,"Return to editor lost focus");
    ensure!(std::fs::read_to_string(root.join("models/unified_events.sql"))?==sql,"Native replay wrote source");
    std::fs::write(out.join("result.json"),serde_json::to_vec_pretty(&json!({
        "status":"passed","scope":"native GPUI/Metal visual-test workspace, real pointer callbacks, real editor-buffer edits, stdio, loopback HTTP, SQLite",
        "cloud_enabled":false,"source_written":false,"app_package_verified":false,
        "original_rows":16,"candidate_rows":17,"candidate_checks":"passed","broken_control":"not_passed",
        "historical_trace_preserved":true,"idle_pause_keeps_evidence":true,"editor_focus_restored":true,
        "screenshots":5,"original_receipt":origin["id"],"candidate_receipt":after["result"]["receipt"]["id"]
    }))?)?;
    println!("ROWDY_NATIVE_VISUAL_PASSED: real native workflow; not a packaged app or warehouse test");
    Ok(())
}
