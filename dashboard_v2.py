DASHBOARD_HTML_V2 = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Decifer 2.0</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Syne:wght@700;800;900&family=Playfair+Display:ital,wght@0,700;0,900;1,700&display=swap');

/* ── CSS VARIABLES ─────────────────────────────────────────── */
:root {
  --bg:  #0A0A0A;
  --bg2: #111111;
  --bg3: #161616;
  --border:  #1E1E1E;
  --border2: #282828;
  --orange:  #FF6B00;
  --orange2: #FF8C33;
  --orange_dim: rgba(255,107,0,.08);
  --green:  #00C853;
  --red:    #FF1744;
  --yellow: #FFD600;
  --text:   #E8E8E8;
  --muted:  #444;
  --muted2: #777;
  --row-h:  32px;
  --section-gap: 16px;
  --radius-sm: 2px;
  --glow-green:  0 0 14px rgba(0,200,83,.35);
  --glow-red:    0 0 14px rgba(255,23,68,.35);
  --glow-orange: 0 0 14px rgba(255,107,0,.35);
}

/* ── RESET & BASE ───────────────────────────────────────────── */
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden}
body{
  background:var(--bg);
  color:var(--text);
  font-family:'JetBrains Mono',monospace;
  font-size:12px;
  line-height:1.4;
}
button{cursor:pointer;font-family:'JetBrains Mono',monospace}
input,select{font-family:'JetBrains Mono',monospace}
::-webkit-scrollbar{width:3px;height:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--muted)}

/* ── VALUE FLASH ANIMATIONS ────────────────────────────────── */
@keyframes flash-pos {
  0%,100%{color:inherit;text-shadow:none}
  40%{color:var(--green);text-shadow:var(--glow-green)}
}
@keyframes flash-neg {
  0%,100%{color:inherit;text-shadow:none}
  40%{color:var(--red);text-shadow:var(--glow-red)}
}
@keyframes flash-neu {
  0%,100%{opacity:1}
  40%{opacity:.5}
}
.flash-pos{animation:flash-pos .4s ease-out}
.flash-neg{animation:flash-neg .4s ease-out}
.flash-neu{animation:flash-neu .4s ease-out}

/* Tab content stagger */
@keyframes row-in {
  from{opacity:0;transform:translateY(4px)}
  to{opacity:1;transform:translateY(0)}
}
.stagger>*{animation:row-in .15s ease-out both}
.stagger>*:nth-child(1){animation-delay:.00s}
.stagger>*:nth-child(2){animation-delay:.03s}
.stagger>*:nth-child(3){animation-delay:.06s}
.stagger>*:nth-child(4){animation-delay:.09s}
.stagger>*:nth-child(5){animation-delay:.12s}
.stagger>*:nth-child(6){animation-delay:.15s}
.stagger>*:nth-child(n+7){animation-delay:.18s}

/* Pulse dot */
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(.7)}}
.pulse{animation:pulse 1.5s infinite}
@keyframes panic-flash{0%,100%{opacity:1}50%{opacity:.4}}

/* ── HEADER ─────────────────────────────────────────────────── */
.hdr{
  display:flex;align-items:center;justify-content:space-between;
  padding:0 16px;height:42px;
  border-bottom:1px solid var(--border);
  background:var(--bg2);
  flex-shrink:0;
}
.logo{display:flex;align-items:center;gap:10px}
.logo-mark{font-family:'Syne',sans-serif;font-size:18px;font-weight:900;color:var(--orange);letter-spacing:-2px}
.logo-name{font-family:'Syne',sans-serif;font-size:15px;font-weight:800;color:#fff;letter-spacing:-.5px}
.logo-ver{font-size:9px;color:var(--muted2);letter-spacing:1px;margin-left:2px;padding-top:2px;align-self:flex-end}
.hdr-right{display:flex;align-items:center;gap:10px;flex-shrink:0}
.pill{
  display:inline-flex;align-items:center;gap:4px;
  padding:2px 8px;border-radius:var(--radius-sm);
  font-size:10px;font-weight:600;border:1px solid;white-space:nowrap;
}
.pill-green{border-color:var(--green);color:var(--green);background:rgba(0,200,83,.07)}
.pill-red{border-color:var(--red);color:var(--red);background:rgba(255,23,68,.07)}
.pill-orange{border-color:var(--orange);color:var(--orange);background:var(--orange_dim)}
.pill-muted{border-color:var(--muted);color:var(--muted2);background:transparent}
.pill-yellow{border-color:var(--yellow);color:var(--yellow);background:rgba(255,214,0,.07)}
.dot{width:5px;height:5px;border-radius:50%;background:currentColor;flex-shrink:0}
.hdr-ts{font-size:9px;color:var(--muted);letter-spacing:.5px}

/* ── STATS STRIP ───────────────────────────────────────────── */
.stats-strip{
  display:grid;grid-template-columns:repeat(6,1fr);
  height:44px;border-bottom:1px solid var(--border);
  background:var(--bg2);flex-shrink:0;overflow:hidden;
}
.stats-strip2{
  display:grid;grid-template-columns:repeat(6,1fr);
  height:40px;border-bottom:1px solid var(--border);
  background:var(--bg);flex-shrink:0;overflow:hidden;
}
.stat{
  padding:5px 12px;border-right:1px solid var(--border);
  display:flex;flex-direction:column;justify-content:center;
  overflow:hidden;min-width:0;
}
.stat:last-child{border-right:none}
.sl{font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:2px;white-space:nowrap}
.sv{font-family:'Syne',sans-serif;font-size:17px;font-weight:800;line-height:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ss{font-size:10px;color:var(--muted2);margin-top:1px}
.co{color:var(--orange)}.cg{color:var(--green)}.cr{color:var(--red)}.cw{color:#fff}.cy{color:var(--yellow)}

/* ── TABS ───────────────────────────────────────────────────── */
.tabs{
  display:flex;background:var(--bg2);
  border-bottom:1px solid var(--border);
  height:32px;flex-shrink:0;overflow-x:auto;
}
.tabs::-webkit-scrollbar{height:0}
.tab{
  padding:0 14px;font-size:10px;letter-spacing:.3px;
  cursor:pointer;color:var(--muted2);
  border-bottom:2px solid transparent;
  transition:color .12s;
  display:flex;align-items:center;white-space:nowrap;flex-shrink:0;
}
.tab:hover{color:var(--text)}
.tab.active{color:var(--orange);border-bottom-color:var(--orange)}

/* ── VIEWS ──────────────────────────────────────────────────── */
.view{
  display:none;
  height:calc(100vh - 42px - 44px - 40px - 32px);
  overflow:hidden;
}
.view.active{display:flex}

/* ── SECTION LABELS / RULES ─────────────────────────────────── */
.sec-hdr{
  display:flex;align-items:center;justify-content:space-between;
  padding:6px 12px 4px;
  border-bottom:1px solid var(--border);
  flex-shrink:0;
}
.sec-label{
  font-size:9px;letter-spacing:2px;color:var(--muted2);
  text-transform:uppercase;font-weight:500;
}
.sec-actions{display:flex;gap:8px;align-items:center}

/* ── COLUMN LAYOUT (Live) ───────────────────────────────────── */
.live-grid{
  display:grid;
  grid-template-columns:200px 1fr 340px;
  width:100%;height:100%;overflow:hidden;
}
.col{display:flex;flex-direction:column;border-right:1px solid var(--border);overflow:hidden}
.col:last-child{border-right:none}
.col-body{overflow-y:auto;flex:1;min-height:0}

/* ── CONTROLS COLUMN ────────────────────────────────────────── */
.ctrl-zone{border-bottom:1px solid var(--border);padding:8px 10px}
.ctrl-zone-label{
  font-size:8px;letter-spacing:2px;color:var(--muted);
  text-transform:uppercase;margin-bottom:6px;
}
.ctrl-btn{
  display:block;width:100%;padding:6px 10px;margin-bottom:4px;
  border-radius:var(--radius-sm);font-size:10px;font-weight:700;
  letter-spacing:.5px;border:1px solid;text-align:center;
  transition:background .12s;background:transparent;
}
.ctrl-btn:last-child{margin-bottom:0}
.btn-kill{border-color:var(--red);color:var(--red)}
.btn-kill:hover{background:rgba(255,23,68,.15)}
.btn-pause{border-color:var(--orange);color:var(--orange)}
.btn-pause:hover{background:var(--orange_dim)}
.btn-neutral{border-color:var(--muted);color:var(--muted2)}
.btn-neutral:hover{background:rgba(255,255,255,.04)}
.btn-green{border-color:var(--green);color:var(--green)}
.btn-green:hover{background:rgba(0,200,83,.1)}

/* Regime box */
.regime-box{
  margin:8px 10px;padding:8px 10px;
  border-radius:var(--radius-sm);border:1px solid var(--muted);
}
.regime-box.bull{border-color:var(--green);background:rgba(0,200,83,.06)}
.regime-box.bear{border-color:var(--red);background:rgba(255,23,68,.06)}
.regime-box.choppy{border-color:var(--yellow);background:rgba(255,214,0,.06)}
.regime-box.panic{border-color:var(--red);background:rgba(255,23,68,.18);animation:panic-flash 1s infinite}
.regime-label{font-family:'Syne',sans-serif;font-size:12px;font-weight:800;margin-bottom:3px}
.regime-meta{font-size:10px;color:var(--muted2)}

/* Monitor row */
.mon-row{
  padding:5px 10px;
  border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center;
  font-size:10px;flex-shrink:0;
}
.mon-label{color:var(--muted2)}
.mon-val{font-weight:600}

/* Progress bar */
.bar-bg{height:3px;background:var(--border2);border-radius:1px;overflow:hidden;margin:3px 0}
.bar-fill{height:100%;border-radius:1px;transition:width .4s}

/* Phase gate pill */
.phase-pill{
  display:inline-flex;align-items:center;gap:4px;
  padding:2px 7px;border-radius:var(--radius-sm);
  font-size:9px;font-weight:700;letter-spacing:.5px;
  border:1px solid var(--yellow);color:var(--yellow);
  background:rgba(255,214,0,.07);
}

/* Favourites chips */
.fav-chip{
  display:inline-flex;align-items:center;gap:3px;
  padding:2px 6px;border-radius:var(--radius-sm);
  font-size:9px;border:1px solid var(--border2);color:var(--muted2);
  background:var(--bg3);margin:2px;
}
.fav-chip .rm{color:var(--muted);cursor:pointer;font-size:10px;line-height:1}
.fav-chip .rm:hover{color:var(--red)}
.fav-input-row{padding:6px 10px;display:flex;gap:4px}
.fav-input{
  flex:1;background:var(--bg3);border:1px solid var(--border2);
  color:var(--text);padding:3px 6px;font-size:10px;
  border-radius:var(--radius-sm);outline:none;
}
.fav-input:focus{border-color:var(--orange)}
.fav-add-btn{
  padding:3px 8px;background:var(--orange_dim);border:1px solid var(--orange);
  color:var(--orange);font-size:10px;border-radius:var(--radius-sm);
}

/* ── REGIME BRIEFING (center column) ───────────────────────── */
.briefing{padding:8px 12px;border-bottom:1px solid var(--border);flex-shrink:0}
.briefing-header{display:flex;align-items:baseline;gap:8px;margin-bottom:6px}
.briefing-regime{font-family:'Syne',sans-serif;font-size:14px;font-weight:900}
.briefing-vix{font-size:10px;color:var(--muted2)}
.sector-chips{display:flex;flex-wrap:wrap;gap:4px}
.sector-chip{
  padding:2px 6px;border-radius:var(--radius-sm);
  font-size:9px;font-weight:600;border:1px solid;
}
.sector-chip.lead{border-color:var(--green);color:var(--green);background:rgba(0,200,83,.07)}
.sector-chip.lag{border-color:var(--red);color:var(--red);background:rgba(255,23,68,.07)}
.sector-chip-label{font-size:8px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin:4px 0 2px}

/* Scan bar */
.scan-bar-wrap{padding:4px 12px;border-bottom:1px solid var(--border);flex-shrink:0}
.scan-info{display:flex;justify-content:space-between;font-size:9px;color:var(--muted2);margin-top:2px}

/* ── DECISION FEED ──────────────────────────────────────────── */
.decision-feed-item{
  padding:6px 12px;border-bottom:1px solid var(--border);
  cursor:pointer;transition:background .1s;
}
.decision-feed-item:hover{background:var(--bg3)}
.dfi-header{display:flex;align-items:center;gap:6px;margin-bottom:2px}
.dfi-sym{font-family:'Syne',sans-serif;font-size:12px;font-weight:800}
.dfi-thesis{font-size:10px;color:var(--muted2);font-style:italic;line-height:1.3;margin-top:2px}
.dfi-outcome{font-size:10px;font-weight:600}

/* ── ACTIVITY LOG ───────────────────────────────────────────── */
.log-row{
  display:grid;grid-template-columns:54px 64px 1fr;
  gap:6px;padding:4px 12px;
  border-bottom:1px solid rgba(30,30,30,.8);
}
@keyframes log-in{from{opacity:0;transform:translateY(-2px)}to{opacity:1;transform:translateY(0)}}
.log-row{animation:log-in .15s ease-out}
.lt{color:var(--muted2);font-size:9px}
.lk{font-size:8px;font-weight:700;padding:1px 4px;border-radius:var(--radius-sm);text-align:center;white-space:nowrap}
.lk-TRADE{background:rgba(255,107,0,.15);color:var(--orange)}
.lk-SIGNAL{background:rgba(0,200,83,.1);color:var(--green)}
.lk-ANALYSIS{background:rgba(255,214,0,.1);color:var(--yellow)}
.lk-ERROR{background:rgba(255,23,68,.12);color:var(--red)}
.lk-INFO{background:rgba(68,68,68,.2);color:var(--muted2)}
.lk-RISK{background:rgba(255,23,68,.08);color:var(--red)}
.lk-SCAN{background:var(--orange_dim);color:var(--orange2)}
.lm{font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ── POSITION ROWS ──────────────────────────────────────────── */
.pos-row{
  display:flex;align-items:stretch;
  border-bottom:1px solid var(--border);
  cursor:pointer;transition:background .1s;min-height:var(--row-h);
}
.pos-row:hover{background:var(--bg3)}
.pos-stripe{width:3px;flex-shrink:0}
.pos-stripe.long{background:var(--green)}
.pos-stripe.short{background:var(--red)}
.pos-body{flex:1;padding:5px 10px;min-width:0}
.pos-line1{display:flex;align-items:center;gap:6px}
.pos-sym{font-family:'Syne',sans-serif;font-size:12px;font-weight:800}
.pos-pnl{margin-left:auto;font-size:12px;font-weight:700}
.pos-thesis{font-size:9px;color:var(--muted2);font-style:italic;margin-top:2px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pos-meta{display:flex;gap:6px;margin-top:2px}
.pos-meta span{font-size:9px;color:var(--muted2)}

/* ── BADGE ──────────────────────────────────────────────────── */
.badge{
  padding:1px 5px;border-radius:var(--radius-sm);
  font-size:8px;font-weight:700;border:1px solid;letter-spacing:.3px;
}
.badge-scalp{border-color:var(--orange);color:var(--orange);background:var(--orange_dim)}
.badge-swing{border-color:#8B5CF6;color:#8B5CF6;background:rgba(139,92,246,.08)}
.badge-hold{border-color:var(--green);color:var(--green);background:rgba(0,200,83,.08)}
.badge-long{border-color:var(--green);color:var(--green)}
.badge-short{border-color:var(--red);color:var(--red)}

/* Thesis status dot */
.thesis-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.thesis-dot.ok{background:var(--green)}
.thesis-dot.warn{background:var(--yellow)}
.thesis-dot.breach{background:var(--red)}

/* ── SORT BTNS ──────────────────────────────────────────────── */
.sort-btn{
  background:none;border:none;font-size:9px;
  color:var(--muted2);padding:0 2px;
}
.sort-btn.active{color:var(--orange)}

/* ── TODAY RESULTS ──────────────────────────────────────────── */
.result-row{
  padding:4px 10px 4px 13px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:6px;min-height:28px;
}
.result-stripe{width:3px;height:100%;min-height:28px;margin-left:-13px;margin-right:7px;flex-shrink:0}
.result-stripe.win{background:var(--green)}
.result-stripe.loss{background:var(--red)}

/* ── RISK VIEW ──────────────────────────────────────────────── */
.risk-view{flex-direction:column;overflow-y:auto;padding:12px;gap:10px}
.risk-section{border-bottom:1px solid var(--border);padding-bottom:10px;margin-bottom:0}
.risk-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.risk-meter{padding:8px 10px;background:var(--bg3);border-radius:var(--radius-sm)}
.rm-label{font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:6px}
.rm-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--muted2);margin-top:3px}

/* Kill in risk */
.risk-kill-btn{
  width:100%;padding:7px;background:rgba(255,23,68,.1);
  border:1px solid var(--red);border-radius:var(--radius-sm);
  color:var(--red);font-family:'JetBrains Mono',monospace;
  font-size:11px;font-weight:700;letter-spacing:1px;
  transition:background .12s;margin-bottom:10px;
}
.risk-kill-btn:hover{background:rgba(255,23,68,.22)}

/* Pos risk table */
.pos-risk-row{
  display:grid;grid-template-columns:80px 1fr 60px 70px;
  gap:6px;padding:4px 10px;border-bottom:1px solid var(--border);
  font-size:10px;align-items:center;
}
.pos-risk-bar{height:4px;background:var(--red);border-radius:1px;opacity:.7}

/* ── AGENTS VIEW ────────────────────────────────────────────── */
.agents-view{flex-direction:column;overflow-y:auto;padding:12px;gap:10px}
.vote-summary{
  display:flex;align-items:center;gap:8px;
  padding:8px 12px;background:var(--bg3);
  border-radius:var(--radius-sm);border:1px solid var(--border2);
  flex-wrap:wrap;
}
.vote-item{display:flex;align-items:center;gap:4px;font-size:10px}
.vote-result{
  margin-left:auto;font-family:'Syne',sans-serif;
  font-size:12px;font-weight:800;
}
.agent-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.agent-card{
  background:var(--bg3);border:1px solid var(--border2);
  border-radius:var(--radius-sm);overflow:hidden;
}
.agent-card-hdr{
  padding:6px 10px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:6px;
}
.agent-card-name{font-family:'Syne',sans-serif;font-size:11px;font-weight:800}
.agent-card-role{font-size:9px;color:var(--muted2);margin-top:1px}
.agent-card-body{
  padding:8px 10px;font-size:10px;color:var(--muted2);
  line-height:1.5;max-height:160px;overflow-y:auto;white-space:pre-wrap;
}
.raw-toggle{
  background:none;border:1px solid var(--border2);color:var(--muted2);
  padding:4px 10px;font-size:9px;border-radius:var(--radius-sm);
  cursor:pointer;width:100%;text-align:left;
}
.raw-toggle:hover{border-color:var(--orange);color:var(--orange)}
.raw-outputs{display:none;margin-top:8px}
.raw-outputs.open{display:block}
.raw-output-block{
  background:var(--bg3);border:1px solid var(--border2);
  border-radius:var(--radius-sm);padding:8px 10px;margin-bottom:6px;
}
.raw-output-key{font-size:9px;color:var(--orange);letter-spacing:1px;margin-bottom:4px}
.raw-output-val{font-size:9px;color:var(--muted2);white-space:pre-wrap;word-break:break-all;max-height:100px;overflow-y:auto}

/* ── ORDERS / HISTORY TABLE ─────────────────────────────────── */
.table-view{flex-direction:column;overflow:hidden}
.table-filters{
  display:flex;align-items:center;gap:6px;
  padding:6px 12px;border-bottom:1px solid var(--border);
  flex-shrink:0;background:var(--bg2);flex-wrap:wrap;
}
.f-btn{
  padding:2px 8px;background:transparent;
  border:1px solid var(--border2);color:var(--muted2);
  font-size:9px;border-radius:var(--radius-sm);transition:.1s;
}
.f-btn:hover,.f-btn.active{border-color:var(--orange);color:var(--orange);background:var(--orange_dim)}
.table-wrap{overflow:auto;flex:1}
.t-head{
  display:grid;padding:4px 12px;
  border-bottom:1px solid var(--border);
  background:var(--bg2);position:sticky;top:0;z-index:1;
  font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;
}
.t-head.orders-head{grid-template-columns:60px 70px 44px 60px 50px 70px 70px 70px 70px 30px}
.t-head.history-head{grid-template-columns:60px 70px 44px 50px 70px 70px 70px 55px 110px 1fr}
.t-row{
  display:grid;padding:5px 12px;
  border-bottom:1px solid var(--border);font-size:10px;
  transition:background .08s;align-items:center;
}
.t-row:hover{background:var(--bg3)}
.t-row.orders-row{grid-template-columns:60px 70px 44px 60px 50px 70px 70px 70px 70px 30px}
.t-row.history-row{grid-template-columns:60px 70px 44px 50px 70px 70px 70px 55px 110px 1fr}
.t-row .sym{font-family:'Syne',sans-serif;font-weight:800;font-size:11px}
.expand-row{
  background:var(--bg3);border-bottom:1px solid var(--border);
  padding:8px 12px;font-size:10px;color:var(--muted2);
  line-height:1.5;display:none;
}
.expand-row.open{display:block}
.cancel-btn{
  background:none;border:1px solid var(--red);color:var(--red);
  font-size:9px;padding:1px 5px;border-radius:var(--radius-sm);
}
.thesis-class-badge{
  padding:1px 5px;border-radius:var(--radius-sm);font-size:8px;font-weight:700;
  border:1px solid;letter-spacing:.3px;
}
.tc-confirmed{border-color:var(--green);color:var(--green);background:rgba(0,200,83,.08)}
.tc-breached{border-color:var(--red);color:var(--red);background:rgba(255,23,68,.08)}
.tc-noise{border-color:var(--muted2);color:var(--muted2);background:transparent}
.tc-stale{border-color:var(--yellow);color:var(--yellow);background:rgba(255,214,0,.07)}
.tc-manual{border-color:var(--muted);color:var(--muted);background:transparent}

/* ── PERFORMANCE VIEW ───────────────────────────────────────── */
.perf-view{flex-direction:column;overflow-y:auto;padding:12px;gap:10px}
.metric-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.metric-strip2{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.metric-card{padding:8px 10px;background:var(--bg3);border-radius:var(--radius-sm)}
.metric-label{font-size:9px;color:var(--muted2);letter-spacing:1px;text-transform:uppercase;margin-bottom:4px}
.metric-val{font-family:'Syne',sans-serif;font-size:18px;font-weight:800}
.chart-wrap{background:var(--bg3);border-radius:var(--radius-sm);padding:10px 12px}
.chart-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.chart-title{font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase}
.tf-btns{display:flex;gap:4px}
.tf-btn{
  padding:2px 6px;background:transparent;border:1px solid var(--border2);
  color:var(--muted2);font-size:9px;border-radius:var(--radius-sm);
}
.tf-btn.active{border-color:var(--orange);color:var(--orange);background:var(--orange_dim)}

/* ── PORTFOLIO VIEW ─────────────────────────────────────────── */
.port-view{flex-direction:column;overflow-y:auto;padding:12px;gap:10px}
.port-kpi-strip{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.port-kpi{padding:8px 10px;background:var(--bg3);border-radius:var(--radius-sm)}
.exposure-bars{padding:10px 12px;background:var(--bg3);border-radius:var(--radius-sm)}
.exp-label{display:flex;justify-content:space-between;font-size:9px;color:var(--muted2);margin-bottom:4px}
.port-charts{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.port-chart-card{background:var(--bg3);border-radius:var(--radius-sm);padding:10px 12px}

/* ── INTELLIGENCE VIEW ──────────────────────────────────────── */
.intel-view{flex-direction:column;overflow-y:auto;padding:12px;gap:10px}
.phase-card{
  padding:10px 12px;background:var(--bg3);
  border-radius:var(--radius-sm);border:1px solid var(--border2);
}
.phase-header{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.phase-num{font-family:'Syne',sans-serif;font-size:22px;font-weight:900;color:var(--orange)}
.phase-desc{font-size:11px;color:var(--text)}
.phase-criteria{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:8px}
.phase-criterion{display:flex;align-items:center;gap:6px;font-size:10px}
.crit-ok{color:var(--green)}
.crit-fail{color:var(--muted2)}
.thesis-perf-table .t-head{grid-template-columns:80px 120px 60px 80px 80px}
.thesis-perf-table .t-row{grid-template-columns:80px 120px 60px 80px 80px}
.ic-grid{display:grid;grid-template-columns:90px 1fr 50px 50px;gap:4px 8px;align-items:center;font-size:10px}
.ic-bar-bg{height:5px;background:var(--border2);border-radius:1px;overflow:hidden}
.ic-bar-fill{height:100%;background:var(--orange);border-radius:1px;transition:width .4s}

/* ── SETTINGS VIEW ──────────────────────────────────────────── */
.settings-view{flex-direction:column;overflow-y:auto;padding:12px;gap:10px}
.settings-section{background:var(--bg3);border-radius:var(--radius-sm);padding:10px 12px}
.settings-title{
  font-family:'Syne',sans-serif;font-size:12px;font-weight:800;
  margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border);
}
.s-row{
  display:flex;align-items:center;justify-content:space-between;
  padding:4px 0;font-size:10px;border-bottom:1px solid rgba(30,30,30,.5);
}
.s-row:last-child{border-bottom:none}
.s-label{color:var(--muted2)}
.s-val{color:var(--text);font-weight:600}
.s-input{
  background:var(--bg2);border:1px solid var(--border2);color:var(--text);
  padding:3px 6px;font-size:10px;border-radius:var(--radius-sm);
  width:90px;text-align:right;outline:none;
}
.s-input:focus{border-color:var(--orange)}
.s-input.dirty{border-color:var(--yellow)}
.s-select{
  background:var(--bg2);border:1px solid var(--border2);color:var(--text);
  padding:3px 6px;font-size:10px;border-radius:var(--radius-sm);outline:none;
}
.s-select:focus{border-color:var(--orange)}
.apply-btn{
  padding:6px 16px;background:var(--orange_dim);border:1px solid var(--orange);
  color:var(--orange);font-family:'JetBrains Mono',monospace;font-size:10px;
  font-weight:700;border-radius:var(--radius-sm);letter-spacing:.5px;
}
.apply-btn:hover{background:rgba(255,107,0,.18)}
.unsaved-banner{
  display:none;padding:5px 12px;background:rgba(255,214,0,.08);
  border:1px solid var(--yellow);color:var(--yellow);
  font-size:10px;border-radius:var(--radius-sm);margin-bottom:6px;
}
.unsaved-banner.show{display:flex;align-items:center;justify-content:space-between}

/* ── CONFIRM MODAL ──────────────────────────────────────────── */
.modal-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
  z-index:1000;align-items:center;justify-content:center;
}
.modal-overlay.open{display:flex}
.modal{
  background:var(--bg2);border:1px solid var(--border2);
  border-radius:var(--radius-sm);padding:20px;width:400px;max-width:95vw;
}
.modal-title{font-family:'Syne',sans-serif;font-size:14px;font-weight:800;margin-bottom:12px}
.modal-change{
  display:flex;justify-content:space-between;font-size:10px;
  padding:4px 0;border-bottom:1px solid var(--border);color:var(--muted2);
}
.modal-change .new-val{color:var(--orange);font-weight:700}
.modal-actions{display:flex;gap:8px;margin-top:14px;justify-content:flex-end}
.modal-cancel{
  padding:6px 14px;background:transparent;border:1px solid var(--border2);
  color:var(--muted2);font-size:10px;border-radius:var(--radius-sm);
}
.modal-confirm{
  padding:6px 14px;background:var(--orange_dim);border:1px solid var(--orange);
  color:var(--orange);font-size:10px;font-weight:700;border-radius:var(--radius-sm);
}

/* ── POSITION MODAL ─────────────────────────────────────────── */
.pos-modal-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
  z-index:1000;align-items:flex-start;justify-content:center;padding-top:60px;
}
.pos-modal-overlay.open{display:flex}
.pos-modal{
  background:var(--bg2);border:1px solid var(--border2);
  border-radius:var(--radius-sm);padding:16px;width:480px;max-width:95vw;
}
.pos-modal-close{
  float:right;background:none;border:none;color:var(--muted2);font-size:14px;cursor:pointer;
}
.pos-modal-close:hover{color:var(--text)}

/* ── NEWS (WSJ) ─────────────────────────────────────────────── */
.news-view{flex-direction:column;overflow:hidden}
.news-toolbar{
  display:flex;align-items:center;gap:8px;
  padding:5px 12px;border-bottom:1px solid var(--border);
  background:var(--bg2);flex-shrink:0;
}
.news-search{
  background:var(--bg3);border:1px solid var(--border2);color:var(--text);
  padding:3px 8px;font-size:10px;border-radius:var(--radius-sm);width:160px;outline:none;
}
.news-search:focus{border-color:var(--orange)}
.news-select{
  background:var(--bg3);border:1px solid var(--border2);color:var(--text);
  padding:3px 6px;font-size:10px;border-radius:var(--radius-sm);
}
.news-refresh{
  padding:3px 8px;background:var(--bg3);border:1px solid var(--border2);
  color:var(--orange);font-size:10px;border-radius:var(--radius-sm);
}
.news-updated{font-size:9px;color:var(--muted);margin-left:auto}
.news-body{display:flex;gap:0;flex:1;overflow:hidden}
.news-main{flex:1;overflow-y:auto;padding:12px 14px}
.news-sidebar{
  width:240px;border-left:1px solid var(--border);
  overflow-y:auto;flex-shrink:0;
}

/* Lead story */
.news-lead{
  display:grid;grid-template-columns:1fr 1fr;gap:14px;
  padding-bottom:12px;margin-bottom:12px;border-bottom:1px solid var(--border);
}
.news-lead-img{
  width:100%;aspect-ratio:16/9;object-fit:cover;
  border-radius:var(--radius-sm);background:var(--bg3);
}
.news-lead-img-placeholder{
  width:100%;aspect-ratio:16/9;background:var(--bg3);
  border-radius:var(--radius-sm);display:flex;align-items:center;justify-content:center;
  color:var(--muted);font-size:10px;
}
.news-lead-content{display:flex;flex-direction:column;justify-content:center;gap:6px}
.news-lead-headline{
  font-family:'Playfair Display',Georgia,serif;
  font-size:22px;font-weight:900;line-height:1.2;color:var(--text);
}
.news-lead-standfirst{font-size:11px;color:var(--muted2);line-height:1.5;font-style:italic}
.news-lead-meta{display:flex;align-items:center;gap:6px;flex-wrap:wrap}

/* Columns */
.news-columns{display:grid;grid-template-columns:1fr 1fr;gap:0}
.news-col{border-right:1px solid var(--border);padding:0 12px}
.news-col:first-child{padding-left:0}
.news-col:last-child{border-right:none;padding-right:0}
.news-story{
  padding:8px 0;border-bottom:1px solid var(--border);
  border-left:3px solid transparent;padding-left:6px;margin-left:-6px;
}
.news-story:last-child{border-bottom:none}
.news-story.bull{border-left-color:var(--green)}
.news-story.bear{border-left-color:var(--red)}
.news-headline{
  font-family:'Playfair Display',Georgia,serif;
  font-size:14px;font-weight:700;line-height:1.25;color:var(--text);margin-bottom:3px;
}
.news-standfirst{font-size:10px;color:var(--muted2);line-height:1.4;margin-bottom:4px}
.news-meta{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.news-ticker{
  font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;
  padding:1px 5px;border-radius:var(--radius-sm);border:1px solid var(--border2);color:var(--muted2);
}
.news-ticker.held{border-color:var(--orange);color:var(--orange)}
.news-age{font-size:9px;color:var(--muted)}
.news-src{font-size:9px;color:var(--muted)}
.news-watch{
  font-size:9px;color:var(--muted2);background:none;border:none;
  cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:2px;
}
.news-watch:hover{color:var(--orange)}

/* Sentiment pill on news */
.sent-bull{padding:1px 5px;background:rgba(0,200,83,.12);color:var(--green);font-size:8px;font-weight:700;border-radius:var(--radius-sm);border:1px solid rgba(0,200,83,.3)}
.sent-bear{padding:1px 5px;background:rgba(255,23,68,.1);color:var(--red);font-size:8px;font-weight:700;border-radius:var(--radius-sm);border:1px solid rgba(255,23,68,.3)}
.sent-neu{padding:1px 5px;background:transparent;color:var(--muted2);font-size:8px;font-weight:700;border-radius:var(--radius-sm);border:1px solid var(--border2)}

/* Sentinel sidebar */
.sentinel-hdr{
  padding:6px 10px;border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center;
  font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--muted2);
  background:var(--bg2);position:sticky;top:0;
}
.sentinel-count{color:var(--orange);font-weight:700}
.sentinel-item{padding:8px 10px;border-bottom:1px solid var(--border);font-size:10px}
.sentinel-sym{font-family:'Syne',sans-serif;font-weight:800;color:var(--orange);margin-bottom:2px}
.sentinel-reason{font-size:9px;color:var(--muted2);line-height:1.4}

/* ── EMPTY STATE ────────────────────────────────────────────── */
.empty{padding:20px 12px;font-size:10px;color:var(--muted);text-align:center}
</style>
</head>
<body>

<!-- ── HEADER ──────────────────────────────────────────────── -->
<div class="hdr">
  <div class="logo">
    <span class="logo-mark">&lt;&gt;</span>
    <span class="logo-name">Decifer</span>
    <span class="logo-ver">2.0</span>
  </div>
  <div class="hdr-right">
    <span class="pill pill-muted" id="hdr-status"><span class="dot pulse"></span>Starting…</span>
    <span class="pill pill-muted" id="hdr-regime">DETECTING</span>
    <span class="pill pill-muted" id="hdr-session">—</span>
    <span class="hdr-ts" id="hdr-ts">—</span>
  </div>
</div>

<!-- ── STATS ROW 1 ──────────────────────────────────────────── -->
<div class="stats-strip">
  <div class="stat">
    <div class="sl">Portfolio Value</div>
    <div class="sv cw" id="s-pv">—</div>
  </div>
  <div class="stat">
    <div class="sl">Day P&amp;L</div>
    <div class="sv" id="s-pnl">—</div>
    <div class="ss" id="s-pnl-pct">—</div>
  </div>
  <div class="stat">
    <div class="sl">Session P&amp;L</div>
    <div class="sv" id="s-session-pnl">—</div>
  </div>
  <div class="stat">
    <div class="sl">Scans Run</div>
    <div class="sv co" id="s-scans">0</div>
  </div>
  <div class="stat">
    <div class="sl">Open Positions</div>
    <div class="sv cw" id="s-pos">0</div>
  </div>
  <div class="stat">
    <div class="sl">Trades Today</div>
    <div class="sv co" id="s-trades">0</div>
  </div>
</div>

<!-- ── STATS ROW 2 ──────────────────────────────────────────── -->
<div class="stats-strip2">
  <div class="stat">
    <div class="sl">Cash</div>
    <div class="sv" id="s2-cash" style="font-size:14px">—</div>
  </div>
  <div class="stat">
    <div class="sl">Buying Power</div>
    <div class="sv" id="s2-bp" style="font-size:14px">—</div>
  </div>
  <div class="stat">
    <div class="sl">Unrealised P&amp;L</div>
    <div class="sv" id="s2-unreal" style="font-size:14px">—</div>
  </div>
  <div class="stat">
    <div class="sl">Realised P&amp;L</div>
    <div class="sv" id="s2-real" style="font-size:14px">—</div>
  </div>
  <div class="stat">
    <div class="sl">Margin Used</div>
    <div class="sv" id="s2-margin" style="font-size:14px">—</div>
  </div>
  <div class="stat">
    <div class="sl">Excess Liquidity</div>
    <div class="sv" id="s2-excess" style="font-size:14px">—</div>
  </div>
</div>

<!-- ── TABS ────────────────────────────────────────────────── -->
<div class="tabs" id="tab-bar">
  <div class="tab active" onclick="switchTab('live',this)">⚡ Live</div>
  <div class="tab" onclick="switchTab('risk',this)">🛡 Risk</div>
  <div class="tab" onclick="switchTab('news',this)">📰 News</div>
  <div class="tab" onclick="switchTab('agents',this)">🧠 Agents</div>
  <div class="tab" onclick="switchTab('orders',this)">📝 Orders</div>
  <div class="tab" onclick="switchTab('history',this)">📋 Closed Trades</div>
  <div class="tab" onclick="switchTab('performance',this)">📈 Performance</div>
  <div class="tab" onclick="switchTab('portfolio',this)">🏦 Portfolio</div>
  <div class="tab" onclick="switchTab('intelligence',this)">🔬 Intelligence</div>
  <div class="tab" onclick="switchTab('settings',this)">⚙️ Settings</div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 1: LIVE                                              -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view active" id="view-live">
  <div class="live-grid">

    <!-- LEFT: Controls -->
    <div class="col">
      <div class="col-body">

        <!-- Zone A: Emergency Actions -->
        <div class="ctrl-zone">
          <div class="ctrl-zone-label">Emergency</div>
          <button class="ctrl-btn btn-kill" onclick="killSwitch()">🚨 KILL SWITCH</button>
          <button class="ctrl-btn btn-pause" id="pause-btn" onclick="togglePause()">⏸ PAUSE BOT</button>
          <button class="ctrl-btn btn-neutral" onclick="restartBot()">↺ RESTART</button>
          <button class="ctrl-btn btn-green" onclick="forceScan()">⚡ FORCE SCAN</button>
        </div>

        <!-- Zone B: Monitoring -->
        <div class="ctrl-zone">
          <div class="ctrl-zone-label">System Status</div>

          <!-- Phase Gate -->
          <div class="mon-row">
            <span class="mon-label">Phase Gate</span>
            <span id="phase-pill" class="phase-pill">PHASE —</span>
          </div>

          <!-- Regime (compact) -->
          <div class="regime-box unknown" id="ctrl-regime-box">
            <div class="regime-label" id="ctrl-regime-label">DETECTING…</div>
            <div class="regime-meta" id="ctrl-regime-meta">VIX: — | SPY: —</div>
          </div>

          <!-- Agent Agreement -->
          <div class="mon-row">
            <span class="mon-label">Agent Agreement</span>
            <span class="mon-val" id="ctrl-agree">—</span>
          </div>

          <!-- Daily Risk Budget -->
          <div style="padding:5px 10px;border-bottom:1px solid var(--border)">
            <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted2);margin-bottom:3px">
              <span>Daily Risk Budget</span>
              <span id="ctrl-risk-left">—</span>
            </div>
            <div class="bar-bg"><div class="bar-fill" id="ctrl-risk-bar" style="width:0%;background:var(--green)"></div></div>
            <div style="font-size:9px;color:var(--muted2);margin-top:2px"><span id="ctrl-risk-used">$0 used</span></div>
          </div>

          <!-- Directional Skew -->
          <div style="padding:5px 10px;border-bottom:1px solid var(--border)">
            <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted2);margin-bottom:3px">
              <span>Directional Skew</span>
              <span id="ctrl-skew-val" style="color:var(--orange);font-weight:700">0.0</span>
            </div>
            <div style="position:relative;height:6px;background:var(--bg);border:1px solid var(--border2);border-radius:1px;overflow:hidden">
              <div style="position:absolute;left:50%;width:1px;height:100%;background:var(--muted)"></div>
              <div id="ctrl-skew-bar" style="position:absolute;top:0;height:100%;background:var(--orange);left:50%;width:0%;border-radius:1px;transition:all .3s"></div>
            </div>
            <div style="display:flex;justify-content:space-between;font-size:8px;color:var(--muted);margin-top:2px">
              <span>SHORT</span><span>LONG</span>
            </div>
          </div>
        </div>

        <!-- Zone C: Favourites -->
        <div class="ctrl-zone" style="border-bottom:none">
          <div class="ctrl-zone-label">⭐ Watchlist</div>
          <div id="fav-chips" style="padding:0 0 4px;display:flex;flex-wrap:wrap"></div>
          <div class="fav-input-row">
            <input class="fav-input" id="ctrl-fav-input" placeholder="NVDA, GLD…" maxlength="10"
                   onkeydown="if(event.key==='Enter')addFavFromCtrl()">
            <button class="fav-add-btn" onclick="addFavFromCtrl()">+</button>
          </div>
        </div>

      </div>
    </div>

    <!-- CENTRE: Regime Briefing + Decision Feed + Activity Log -->
    <div class="col">

      <!-- Regime Briefing -->
      <div class="briefing" id="regime-briefing">
        <div class="briefing-header">
          <span class="briefing-regime" id="br-label">DETECTING…</span>
          <span class="briefing-vix" id="br-vix">VIX — | SPY —</span>
          <span id="br-updated" style="font-size:9px;color:var(--muted);margin-left:auto">—</span>
        </div>
        <div class="sector-chip-label">SECTOR LEADERS</div>
        <div class="sector-chips" id="br-leaders">
          <span style="font-size:10px;color:var(--muted2)">Loading sector data…</span>
        </div>
        <div class="sector-chip-label" style="margin-top:5px">LAGGARDS</div>
        <div class="sector-chips" id="br-laggards"></div>
      </div>

      <!-- Scan Progress -->
      <div class="scan-bar-wrap">
        <div class="bar-bg"><div class="bar-fill" id="scan-fill" style="width:0%;background:var(--orange)"></div></div>
        <div class="scan-info">
          <span id="scan-status">Waiting for first scan…</span>
          <span id="scan-eta">—</span>
        </div>
      </div>

      <!-- Decision Feed -->
      <div class="sec-hdr">
        <span class="sec-label">Decision Feed</span>
        <span id="decision-count" style="font-size:9px;color:var(--muted2)">0 decisions</span>
      </div>
      <div style="flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column">
        <!-- Top half: Decision Feed -->
        <div id="decision-feed" style="flex:1;min-height:0;overflow-y:auto;border-bottom:1px solid var(--border)">
          <div class="empty">No decisions yet. Decisions appear after the first scan.</div>
        </div>

        <!-- Bottom: Activity Log -->
        <div class="sec-hdr" style="flex-shrink:0">
          <span class="sec-label">Activity Log</span>
          <span id="log-count" style="font-size:9px;color:var(--muted2)">0 events</span>
        </div>
        <div id="log-area" style="flex:1;min-height:0;overflow-y:auto"></div>
      </div>

    </div>

    <!-- RIGHT: Open Positions + Today's Results -->
    <div class="col">
      <div class="sec-hdr">
        <span class="sec-label">Open Positions</span>
        <span id="pos-count" style="color:var(--orange);font-size:9px">0</span>
        <div class="sec-actions">
          <button class="sort-btn active" id="ps-recency" onclick="sortPos('recency',this)">Recent</button>
          <button class="sort-btn" id="ps-size" onclick="sortPos('size',this)">Size</button>
          <button class="sort-btn" id="ps-pnl" onclick="sortPos('pnl',this)">P&amp;L</button>
        </div>
      </div>
      <div id="pos-list" style="flex:0 0 auto;max-height:50%;overflow-y:auto;border-bottom:1px solid var(--border)">
        <div class="empty">No open positions</div>
      </div>
      <div class="sec-hdr" style="flex-shrink:0">
        <span class="sec-label">Today's Results</span>
        <span id="today-count" style="font-size:9px;color:var(--muted2)">0 trades</span>
      </div>
      <div id="today-list" style="flex:1;overflow-y:auto">
        <div class="empty">No closed trades today</div>
      </div>
    </div>

  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 2: RISK                                              -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-risk">
  <div class="risk-view stagger" style="width:100%">

    <button class="risk-kill-btn" onclick="killSwitch()">🚨 KILL SWITCH — Flatten All Positions</button>

    <!-- Open Position Risk (top — most actionable) -->
    <div>
      <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:6px">Open Position Risk</div>
      <div class="t-head" style="grid-template-columns:80px 1fr 60px 80px;font-size:9px;background:transparent;position:static;border-bottom:1px solid var(--border);padding:4px 0">
        <span>Symbol</span><span>Risk Bar</span><span>% of Budget</span><span>Unrealised</span>
      </div>
      <div id="r-pos-detail"><div class="empty">No open positions</div></div>
    </div>

    <!-- 4 Meters -->
    <div class="risk-grid">
      <div class="risk-meter">
        <div class="rm-label">Daily Loss Budget Used</div>
        <div class="bar-bg"><div class="bar-fill" id="r-daily-bar" style="width:0%;background:var(--green)"></div></div>
        <div class="rm-meta"><span id="r-daily-used">$0 of $0</span><span id="r-daily-pct">0%</span></div>
      </div>
      <div class="risk-meter">
        <div class="rm-label">Portfolio Exposure</div>
        <div class="bar-bg"><div class="bar-fill" id="r-exp-bar" style="width:0%;background:var(--orange)"></div></div>
        <div class="rm-meta"><span id="r-exp-used">0 positions</span><span id="r-exp-pct">0% deployed</span></div>
      </div>
      <div class="risk-meter">
        <div class="rm-label">Consecutive Losses</div>
        <div class="bar-bg"><div class="bar-fill" id="r-loss-bar" style="width:0%;background:var(--red)"></div></div>
        <div class="rm-meta"><span id="r-loss-n">0</span><span id="r-loss-status">OK</span></div>
      </div>
      <div class="risk-meter">
        <div class="rm-label">Cash Reserve</div>
        <div class="bar-bg"><div class="bar-fill" id="r-cash-bar" style="width:100%;background:var(--green)"></div></div>
        <div class="rm-meta"><span id="r-cash-pct">—</span><span id="r-cash-min">Min: —</span></div>
      </div>
    </div>

  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 3: NEWS (WSJ layout)                                 -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-news">
  <div class="news-view" style="width:100%;flex-direction:column">
    <div class="news-toolbar">
      <span id="news-count" style="font-size:10px;color:var(--orange);font-weight:700;min-width:60px">0 stories</span>
      <input class="news-search" id="news-keyword" placeholder="Search headlines…" oninput="filterNews()">
      <select class="news-select" id="news-sentiment" onchange="filterNews()">
        <option value="all">All Sentiment</option>
        <option value="BULLISH">▲ Bullish</option>
        <option value="BEARISH">▼ Bearish</option>
        <option value="NEUTRAL">— Neutral</option>
      </select>
      <select class="news-select" id="news-sort-sel" onchange="filterNews()">
        <option value="time">Newest First</option>
        <option value="score">By Score</option>
      </select>
      <button class="news-refresh" onclick="fetchNews()">↻ Refresh</button>
      <span class="news-updated" id="news-updated">—</span>
    </div>
    <div class="news-body" style="flex:1;overflow:hidden">
      <div class="news-main" id="news-main">
        <div class="empty" style="padding:40px">Loading news…</div>
      </div>
      <div class="news-sidebar">
        <div class="sentinel-hdr">
          <span>Sentinel Alerts</span>
          <span class="sentinel-count" id="sentinel-count">0</span>
        </div>
        <div id="sentinel-list">
          <div style="padding:16px 10px;font-size:10px;color:var(--muted2)">No alerts yet</div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 4: AGENTS (4-agent architecture)                     -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-agents">
  <div class="agents-view stagger" style="width:100%">

    <!-- Vote Summary Bar -->
    <div class="vote-summary" id="vote-summary">
      <span style="font-size:9px;color:var(--muted2)">Last scan: <span id="agents-scan-time">—</span></span>
      <div id="vote-items" style="display:flex;gap:10px;flex-wrap:wrap"></div>
      <div class="vote-result" id="vote-result">—</div>
    </div>

    <!-- 4 Agent Cards (2×2 grid) -->
    <div class="agent-grid" id="agent-cards">
      <div class="empty" style="grid-column:1/-1;padding:30px">Agent outputs appear after the first scan completes.</div>
    </div>

    <!-- Raw Output Toggle -->
    <div>
      <button class="raw-toggle" onclick="toggleRaw()">▶ Show raw agent outputs</button>
      <div class="raw-outputs" id="raw-outputs"></div>
    </div>

  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 5: ORDERS                                            -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-orders">
  <div class="table-view" style="width:100%">
    <div class="table-filters">
      <span style="font-size:9px;color:var(--muted2)">Filter:</span>
      <button class="f-btn active" onclick="filterOrders('all',this)">All</button>
      <button class="f-btn" onclick="filterOrders('submitted',this)">Pending</button>
      <button class="f-btn" onclick="filterOrders('filled',this)">Filled</button>
      <button class="f-btn" onclick="filterOrders('cancelled',this)">Cancelled</button>
      <button class="f-btn" onclick="filterOrders('stocks',this)">Stocks</button>
      <button class="f-btn" onclick="filterOrders('options',this)">Options</button>
      <span id="orders-count" style="font-size:9px;color:var(--muted2);margin-left:auto">0 orders</span>
    </div>
    <div class="table-wrap">
      <div class="t-head orders-head">
        <span>Time</span><span>Symbol</span><span>Side</span><span>Type</span>
        <span>Qty</span><span>Price</span><span>Fill Px</span><span>Status</span>
        <span>Entry/Exit</span><span></span>
      </div>
      <div id="orders-body"><div class="empty">No orders yet.</div></div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 6: CLOSED TRADES                                     -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-history">
  <div class="table-view" style="width:100%">
    <div class="table-filters">
      <span style="font-size:9px;color:var(--muted2)">Filter:</span>
      <button class="f-btn active" onclick="filterTrades('all',this)">All</button>
      <button class="f-btn" onclick="filterTrades('wins',this)">Wins</button>
      <button class="f-btn" onclick="filterTrades('losses',this)">Losses</button>
      <button class="f-btn" onclick="filterTrades('stocks',this)">Stocks</button>
      <button class="f-btn" onclick="filterTrades('options',this)">Options</button>
      <button class="f-btn" onclick="filterTrades('SCALP',this)">Scalp</button>
      <button class="f-btn" onclick="filterTrades('SWING',this)">Swing</button>
      <button class="f-btn" onclick="filterTrades('HOLD',this)">Hold</button>
      <span style="border-left:1px solid var(--border);margin:0 4px"></span>
      <button class="f-btn" onclick="filterTrades('tc_confirmed',this)">Confirmed</button>
      <button class="f-btn" onclick="filterTrades('tc_breached',this)">Breached</button>
      <button class="f-btn" onclick="filterTrades('tc_noise',this)">Noise</button>
      <span id="history-count" style="font-size:9px;color:var(--muted2);margin-left:auto">0 trades</span>
    </div>
    <div class="table-wrap">
      <div class="t-head history-head">
        <span>Time</span><span>Symbol</span><span>Side</span><span>Size</span>
        <span>Entry</span><span>Exit</span><span>P&amp;L</span><span>Hold</span>
        <span>Thesis Class</span><span>Reason</span>
      </div>
      <div id="hist-body"><div class="empty">No closed trades yet.</div></div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 7: PERFORMANCE                                       -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-performance">
  <div class="perf-view stagger" style="width:100%">
    <div class="metric-strip">
      <div class="metric-card"><div class="metric-label">Total P&amp;L</div><div class="metric-val co" id="g-pnl">—</div></div>
      <div class="metric-card"><div class="metric-label">Win Rate</div><div class="metric-val" id="g-wr">—</div></div>
      <div class="metric-card"><div class="metric-label">Profit Factor</div><div class="metric-val" id="g-pf">—</div></div>
      <div class="metric-card"><div class="metric-label">Expectancy</div><div class="metric-val" id="g-exp">—</div></div>
    </div>
    <div class="metric-strip2">
      <div class="metric-card"><div class="metric-label">Total Trades</div><div class="metric-val co" id="g-total">0</div></div>
      <div class="metric-card"><div class="metric-label">Best Trade</div><div class="metric-val cg" id="g-best">—</div></div>
      <div class="metric-card"><div class="metric-label">Worst Trade</div><div class="metric-val cr" id="g-worst">—</div></div>
      <div class="metric-card"><div class="metric-label">Max Drawdown</div><div class="metric-val cr" id="g-dd">—</div></div>
    </div>
    <div class="chart-wrap">
      <div class="chart-header">
        <span class="chart-title">Equity Curve</span>
        <div class="tf-btns">
          <button class="tf-btn" onclick="setEquityTF('1D',this)">1D</button>
          <button class="tf-btn" onclick="setEquityTF('1W',this)">1W</button>
          <button class="tf-btn" onclick="setEquityTF('1M',this)">1M</button>
          <button class="tf-btn active" onclick="setEquityTF('MTD',this)">MTD</button>
          <button class="tf-btn" onclick="setEquityTF('YTD',this)">YTD</button>
          <button class="tf-btn" onclick="setEquityTF('ALL',this)">ALL</button>
        </div>
      </div>
      <div style="position:relative;height:160px"><canvas id="equity-chart"></canvas></div>
    </div>
    <div class="chart-wrap">
      <div class="chart-header">
        <span class="chart-title">Daily P&amp;L</span>
        <div class="tf-btns">
          <button class="tf-btn active" onclick="setDailyTF('1W',this)">1W</button>
          <button class="tf-btn" onclick="setDailyTF('1M',this)">1M</button>
          <button class="tf-btn" onclick="setDailyTF('MTD',this)">MTD</button>
          <button class="tf-btn" onclick="setDailyTF('YTD',this)">YTD</button>
          <button class="tf-btn" onclick="setDailyTF('ALL',this)">ALL</button>
        </div>
      </div>
      <div style="position:relative;height:120px"><canvas id="daily-chart"></canvas></div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 8: PORTFOLIO                                         -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-portfolio">
  <div class="port-view stagger" style="width:100%">
    <div class="port-kpi-strip">
      <div class="port-kpi"><div class="sl">Gross Exposure</div><div class="sv co" id="pf-gross">—</div></div>
      <div class="port-kpi"><div class="sl">Net Exposure</div><div class="sv" id="pf-net">—</div></div>
      <div class="port-kpi"><div class="sl">Unrealised P&amp;L</div><div class="sv" id="pf-unreal">—</div></div>
      <div class="port-kpi"><div class="sl">Realised P&amp;L</div><div class="sv" id="pf-real">—</div></div>
      <div class="port-kpi"><div class="sl">Long / Short Split</div><div class="sv" id="pf-ls">—</div></div>
      <div class="port-kpi"><div class="sl">Accounts</div><div class="sv cw" id="pf-accts">—</div></div>
    </div>
    <div class="exposure-bars">
      <div class="exp-label"><span>LONG EXPOSURE</span><span id="pf-long-pct">0%</span></div>
      <div class="bar-bg" style="height:5px"><div id="pf-long-bar" style="height:100%;background:var(--green);border-radius:1px;transition:width .4s;width:0%"></div></div>
      <div class="exp-label" style="margin-top:8px"><span>SHORT EXPOSURE</span><span id="pf-short-pct">0%</span></div>
      <div class="bar-bg" style="height:5px"><div id="pf-short-bar" style="height:100%;background:var(--red);border-radius:1px;transition:width .4s;width:0%"></div></div>
    </div>
    <div class="port-charts" id="pf-charts-row">
      <div class="port-chart-card">
        <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:8px">Sector Mix</div>
        <div style="position:relative;height:140px"><canvas id="pf-sector-chart"></canvas></div>
      </div>
      <div class="port-chart-card">
        <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:8px">Concentration</div>
        <div style="position:relative;height:140px"><canvas id="pf-conc-chart"></canvas></div>
      </div>
      <div class="port-chart-card">
        <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:8px">Trade Type</div>
        <div style="position:relative;height:140px"><canvas id="pf-type-chart"></canvas></div>
      </div>
    </div>
    <div style="background:var(--bg3);border-radius:var(--radius-sm);overflow:hidden">
      <div style="padding:6px 12px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between">
        <span style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase">Aggregated Positions</span>
        <span id="pf-count" style="font-size:9px;color:var(--orange)">0 positions</span>
      </div>
      <div id="pf-table"><div class="empty">No positions</div></div>
    </div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 9: INTELLIGENCE                                      -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-intelligence">
  <div class="intel-view stagger" style="width:100%">

    <!-- Phase Gate (first — most critical) -->
    <div class="phase-card">
      <div class="phase-header">
        <div class="phase-num" id="ig-phase-num">—</div>
        <div>
          <div style="font-size:11px;font-weight:700" id="ig-phase-desc">Loading…</div>
          <div style="font-size:10px;color:var(--muted2);margin-top:2px" id="ig-phase-trades">—</div>
        </div>
        <div id="ig-phase-pill" style="margin-left:auto"></div>
      </div>
      <div class="phase-criteria" id="ig-criteria"></div>
    </div>

    <!-- Thesis Performance -->
    <div>
      <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:6px">
        Thesis Performance — North Star Learning Loop
      </div>
      <div class="thesis-perf-table">
        <div class="t-head" style="background:transparent;position:static;padding:4px 0">
          <span>Trade Type</span><span>Thesis Class</span><span>Count</span><span>Win Rate</span><span>Avg P&amp;L</span>
        </div>
        <div id="ig-thesis-table"><div class="empty">Loading thesis performance…</div></div>
      </div>
    </div>

    <!-- IC Weights -->
    <div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
        <span style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase">IC-Weighted Signal Composite</span>
        <span id="ig-ic-status" style="font-size:9px;color:var(--muted2)">—</span>
      </div>
      <div style="font-size:9px;color:var(--muted2);margin-bottom:8px">
        Spearman IC between each signal dimension and 5-day forward return — rolling 60-trade window.
      </div>
      <div class="ic-grid" id="ig-ic-grid">
        <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase">Dimension</div>
        <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase">Weight</div>
        <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase;text-align:right">IC</div>
        <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase;text-align:right">4W</div>
      </div>
      <div style="font-size:9px;color:var(--muted2);margin-top:6px" id="ig-ic-updated">—</div>
    </div>

    <!-- Forward Returns Chart -->
    <div class="chart-wrap">
      <div class="chart-header">
        <span class="chart-title">Alpha Decay — Forward Returns</span>
        <span id="ig-trade-count" style="font-size:9px;color:var(--muted2)">—</span>
      </div>
      <div style="position:relative;height:160px"><canvas id="alpha-chart"></canvas></div>
    </div>

  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- VIEW 10: SETTINGS                                         -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="view" id="view-settings">
  <div class="settings-view stagger" style="width:100%">

    <div class="unsaved-banner" id="unsaved-banner">
      <span>⚠ Unsaved changes</span>
      <button class="apply-btn" onclick="showConfirmModal()">Apply Changes</button>
    </div>

    <!-- Bot Control -->
    <div class="settings-section">
      <div class="settings-title">&lt;&gt; Bot Control</div>
      <div class="s-row"><span class="s-label">Active Account</span><span class="s-val" id="cfg-account">—</span></div>
      <div class="s-row"><span class="s-label">Bot Status</span><span class="s-val" id="cfg-status">—</span></div>
      <div style="display:flex;gap:8px;padding-top:8px">
        <button class="apply-btn" onclick="showConfirmModal()" style="flex:1">✅ Apply Settings</button>
        <button class="apply-btn" onclick="restartBot()" style="flex:1;border-color:var(--red);color:var(--red);background:rgba(255,23,68,.08)">↺ Restart Bot</button>
      </div>
    </div>

    <!-- Risk Parameters -->
    <div class="settings-section">
      <div class="settings-title">Risk Parameters</div>
      <div class="s-row"><span class="s-label">Risk per trade (%)</span><input class="s-input" id="cfg-risk-pct" type="number" step="0.1" min="0.1" max="10" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Daily loss limit (%)</span><input class="s-input" id="cfg-daily-limit" type="number" step="0.5" min="1" max="20" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Max positions</span><input class="s-input" id="cfg-max-pos" type="number" step="1" min="1" max="30" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Min cash reserve (%)</span><input class="s-input" id="cfg-cash-reserve" type="number" step="5" min="0" max="80" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Max single position (%)</span><input class="s-input" id="cfg-max-single" type="number" step="1" min="1" max="30" oninput="markDirty(this)"></div>
    </div>

    <!-- Scoring & Agents -->
    <div class="settings-section">
      <div class="settings-title">Scoring &amp; Agents</div>
      <div class="s-row"><span class="s-label">Min score to trade (/50)</span><input class="s-input" id="cfg-min-score" type="number" step="1" min="10" max="50" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">High conviction score</span><input class="s-input" id="cfg-high-score" type="number" step="1" min="20" max="50" oninput="markDirty(this)"></div>
      <div class="s-row">
        <span class="s-label">Agents required</span>
        <select class="s-select" id="agree-select" onchange="markDirty(this)">
          <option value="2">2 / 4</option>
          <option value="3">3 / 4</option>
          <option value="4">4 / 4</option>
        </select>
      </div>
    </div>

    <!-- Options -->
    <div class="settings-section">
      <div class="settings-title">Options</div>
      <div class="s-row"><span class="s-label">Min score for options</span><input class="s-input" id="cfg-opt-min-score" type="number" step="1" min="20" max="50" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Options risk per trade (%)</span><input class="s-input" id="cfg-opt-risk" type="number" step="0.25" min="0.25" max="5" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Max IV Rank</span><input class="s-input" id="cfg-opt-ivr" type="number" step="5" min="20" max="100" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Target delta</span><input class="s-input" id="cfg-opt-delta" type="number" step="0.05" min="0.2" max="0.7" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Delta range (±)</span><input class="s-input" id="cfg-opt-delta-range" type="number" step="0.05" min="0.05" max="0.45" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">DTE range</span><span class="s-val" id="cfg-dte-range">— — — days</span></div>
    </div>

    <!-- News Sentinel -->
    <div class="settings-section">
      <div class="settings-title">📡 News Sentinel</div>
      <div class="s-row"><span class="s-label">Sentinel enabled</span><select class="s-select" id="cfg-sentinel-enabled" onchange="markDirty(this)"><option value="true">On</option><option value="false">Off</option></select></div>
      <div class="s-row"><span class="s-label">Poll interval (sec)</span><input class="s-input" id="cfg-sentinel-poll" type="number" step="5" min="15" max="120" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Cooldown per symbol (min)</span><input class="s-input" id="cfg-sentinel-cooldown" type="number" step="1" min="1" max="60" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Max trades / hour</span><input class="s-input" id="cfg-sentinel-max-trades" type="number" step="1" min="1" max="10" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Position size multiplier</span><input class="s-input" id="cfg-sentinel-risk-mult" type="number" step="0.05" min="0.25" max="1.5" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Keyword threshold</span><input class="s-input" id="cfg-sentinel-kw-thresh" type="number" step="1" min="1" max="10" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Min confidence to trade</span><input class="s-input" id="cfg-sentinel-min-conf" type="number" step="1" min="1" max="10" oninput="markDirty(this)"></div>
      <div class="s-row"><span class="s-label">Use IBKR news</span><select class="s-select" id="cfg-sentinel-ibkr" onchange="markDirty(this)"><option value="true">On</option><option value="false">Off</option></select></div>
      <div class="s-row"><span class="s-label">Use Finviz news</span><select class="s-select" id="cfg-sentinel-finviz" onchange="markDirty(this)"><option value="true">On</option><option value="false">Off</option></select></div>
    </div>

    <!-- Capital Management -->
    <div class="settings-section">
      <div class="settings-title">💰 Capital Management</div>
      <div class="s-row"><span class="s-label">Starting Capital</span><span class="s-val" id="cfg-start-cap">—</span></div>
      <div class="s-row"><span class="s-label">Effective Capital</span><span class="s-val" id="cfg-eff-cap">—</span></div>
      <div class="s-row"><span class="s-label">Current P&amp;L</span><span class="s-val" id="cfg-current-pnl">—</span></div>
      <div style="display:flex;gap:6px;align-items:center;padding-top:8px;flex-wrap:wrap">
        <select class="s-select" id="cap-type"><option value="deposit">Deposit</option><option value="withdrawal">Withdrawal</option></select>
        <input class="s-input" id="cap-amount" type="number" step="1000" min="0" placeholder="Amount ($)" style="flex:1;width:auto">
        <input class="s-input" id="cap-note" type="text" placeholder="Note" style="flex:1;width:auto;text-align:left">
      </div>
      <div style="padding-top:6px">
        <button class="apply-btn" onclick="recordCapitalAdjustment()" style="width:100%">💰 Record Adjustment</button>
      </div>
      <div id="cap-history" style="margin-top:8px;font-size:9px;color:var(--muted2)"></div>
    </div>

  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- MODALS                                                    -->
<!-- ══════════════════════════════════════════════════════════ -->
<div class="modal-overlay" id="confirm-modal">
  <div class="modal">
    <div class="modal-title">Confirm Settings Change</div>
    <div style="font-size:10px;color:var(--muted2);margin-bottom:10px">These changes will take effect immediately on the live bot.</div>
    <div id="modal-changes"></div>
    <div class="modal-actions">
      <button class="modal-cancel" onclick="closeModal()">Cancel</button>
      <button class="modal-confirm" onclick="applySettings()">Confirm & Apply</button>
    </div>
  </div>
</div>

<div class="pos-modal-overlay" id="pos-modal">
  <div class="pos-modal">
    <button class="pos-modal-close" onclick="closePosModal()">✕</button>
    <div id="pos-modal-content"></div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════ -->
<!-- SCRIPTS                                                   -->
<!-- ══════════════════════════════════════════════════════════ -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
'use strict';

// ── CONSTANTS ─────────────────────────────────────────────────
const POLL_MS     = 8000;
const LOG_MAX     = 200;
const DECISION_MAX = 10;

// ── STATE ──────────────────────────────────────────────────────
let S = {
  status: 'starting', account: '', portfolio_value: 0, daily_pnl: 0,
  session: 'UNKNOWN', scan_count: 0, last_scan: null, scanning: false,
  next_scan_seconds: 0, scan_interval_seconds: 300,
  regime: {regime:'UNKNOWN', vix:0, spy_price:0},
  positions: [], trades: [], all_trades: [], logs: [],
  paused: false, killed: false, ibkr_disconnected: false,
  favourites: [], agent_outputs: {}, agent_conversation: [],
  last_agents_agreed: null, agents_required: 2,
  all_orders: [], skew: null, last_decision: null, decision_history: [],
  settings: {}, performance: {}, equity_history: [], account_details: {},
  sector_bias: {}, effective_capital: 0,
};

let _prevValues = {};       // for flash detection
let _currentTab = 'live';
let _posSort    = 'recency';
let _orderFilter= 'all';
let _tradeFilter= 'all';
let _newsData   = [];
let _equityTF   = 'MTD';
let _dailyTF    = '1W';
let _settingsDirty = false;
let _pendingSettings = {};
let _newsLoaded = false;
let _icData     = null;
let _alphaData  = null;
let _thesisData = null;
let _gateData   = null;
let _portData   = null;
let _eqChart    = null;
let _dailyChart = null;
let _alphaChart = null;
let _pfSectorChart = null;
let _pfConcChart   = null;
let _pfTypeChart   = null;
let _dimensions = null;

// ── UTILS ──────────────────────────────────────────────────────
const $  = id => document.getElementById(id);
const $$ = sel => document.querySelectorAll(sel);

function fmt$(v) {
  if (v == null || v === '') return '—';
  const n = parseFloat(v);
  if (isNaN(n)) return '—';
  return '$' + Math.abs(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
}
function fmt$sign(v) {
  if (v == null || v === '') return '—';
  const n = parseFloat(v);
  if (isNaN(n)) return '—';
  const s = n >= 0 ? '+' : '-';
  return s + '$' + Math.abs(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
}
function fmtPct(v, decimals=2) {
  if (v == null || v === '') return '—';
  const n = parseFloat(v);
  if (isNaN(n)) return '—';
  return (n >= 0 ? '+' : '') + n.toFixed(decimals) + '%';
}
function fmtNum(v, d=2) {
  const n = parseFloat(v);
  return isNaN(n) ? '—' : n.toFixed(d);
}
function clsSign(v) {
  const n = parseFloat(v);
  return isNaN(n) ? '' : n >= 0 ? 'cg' : 'cr';
}
function ageStr(ts) {
  if (!ts) return '—';
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (diff < 60)  return Math.floor(diff) + 's';
  if (diff < 3600) return Math.floor(diff/60) + 'm';
  return Math.floor(diff/3600) + 'h';
}

// Flash animation when a numeric value changes
function setVal(id, newVal, fmt) {
  const el = $(id);
  if (!el) return;
  const displayed = fmt ? fmt(newVal) : newVal;
  if (el.textContent === displayed) return;
  const prev = _prevValues[id];
  if (prev !== undefined) {
    const pn = parseFloat(prev), nn = parseFloat(newVal);
    if (!isNaN(pn) && !isNaN(nn) && pn !== nn) {
      el.classList.remove('flash-pos', 'flash-neg', 'flash-neu');
      void el.offsetWidth; // reflow
      el.classList.add(nn > pn ? 'flash-pos' : 'flash-neg');
      setTimeout(() => el.classList.remove('flash-pos','flash-neg'), 450);
    }
  }
  _prevValues[id] = newVal;
  el.textContent  = displayed;
}

// ── API ────────────────────────────────────────────────────────
async function fetchState() {
  try {
    const r = await fetch('/api/state');
    if (!r.ok) return;
    const d = await r.json();
    S = Object.assign(S, d);
    render();
  } catch(e) { /* silent */ }
}

async function fetchSectors() {
  try {
    const r = await fetch('/api/sectors');
    if (!r.ok) return;
    const d = await r.json();
    renderRegimeBriefing(d);
  } catch(e) {}
}

async function fetchICWeights() {
  try {
    const r = await fetch('/api/ic_weights');
    if (!r.ok) return;
    _icData = await r.json();
    renderICWeights();
  } catch(e) {}
}

async function fetchAlphaDecay() {
  try {
    const r = await fetch('/api/alpha_decay');
    if (!r.ok) return;
    _alphaData = await r.json();
    renderAlphaChart();
  } catch(e) {}
}

async function fetchThesisPerf() {
  try {
    const r = await fetch('/api/thesis-performance');
    if (!r.ok) return;
    _thesisData = await r.json();
    renderThesisPerf();
  } catch(e) {}
}

async function fetchGate() {
  try {
    const r = await fetch('/api/alpha-gate');
    if (!r.ok) return;
    _gateData = await r.json();
    renderPhaseGate();
  } catch(e) {}
}

async function fetchPortfolio() {
  try {
    const r = await fetch('/api/portfolio');
    if (!r.ok) return;
    _portData = await r.json();
    renderPortfolio();
  } catch(e) {}
}

async function fetchNews() {
  try {
    const r = await fetch('/api/news');
    if (!r.ok) return;
    const d = await r.json();
    _newsData = d.articles || [];
    renderNews(_newsData);
    renderSentinel(d.sentinel_triggers || []);
    $('news-updated').textContent = 'Updated ' + new Date().toLocaleTimeString();
    $('news-count').textContent = _newsData.length + ' stories';
    _newsLoaded = true;
  } catch(e) {}
}

async function fetchDimensions() {
  if (_dimensions) return;
  try {
    const r = await fetch('/api/dimensions');
    if (!r.ok) return;
    const d = await r.json();
    _dimensions = d.dimensions || [];
  } catch(e) {}
}

// ── MAIN RENDER ────────────────────────────────────────────────
function render() {
  renderHeader();
  renderStats();
  if (_currentTab === 'live')        renderLive();
  if (_currentTab === 'risk')        renderRisk();
  if (_currentTab === 'agents')      renderAgents();
  if (_currentTab === 'orders')      renderOrders();
  if (_currentTab === 'history')     renderHistory();
  if (_currentTab === 'performance') renderPerformance();
  if (_currentTab === 'settings')    renderSettings();
}

// ── HEADER ─────────────────────────────────────────────────────
function renderHeader() {
  const d = S;
  // Status pill
  const sp = $('hdr-status');
  if (sp) {
    const killed  = d.killed;
    const paused  = d.paused;
    const discon  = d.ibkr_disconnected;
    sp.textContent  = killed ? '🚨 KILLED' : paused ? '⏸ PAUSED' : discon ? '⚠ DISCONNECTED' : '● LIVE';
    sp.className    = 'pill ' + (killed ? 'pill-red' : paused ? 'pill-yellow' : discon ? 'pill-yellow' : 'pill-green');
  }
  // Regime pill
  const rp = $('hdr-regime');
  if (rp) {
    const rg = (d.regime||{}).regime || 'UNKNOWN';
    rp.textContent = rg;
    rp.className   = 'pill ' + (
      rg.includes('BULL') ? 'pill-green' :
      rg.includes('BEAR') ? 'pill-red' :
      rg === 'PANIC'      ? 'pill-red' : 'pill-yellow'
    );
  }
  // Session pill
  const sp2 = $('hdr-session');
  if (sp2) {
    sp2.textContent = d.session || '—';
    sp2.className   = 'pill ' + (d.session === 'MARKET_HOURS' ? 'pill-green' : 'pill-muted');
  }
  // Timestamp
  const ts = $('hdr-ts');
  if (ts) ts.textContent = new Date().toLocaleTimeString('en-US', {hour12:false});
}

// ── STATS ──────────────────────────────────────────────────────
function renderStats() {
  const d = S;
  const ad = d.account_details || {};
  const pv = d.portfolio_value || 0;
  const pnl = d.daily_pnl || 0;

  setVal('s-pv',  pv,  v => fmt$(v));
  const pnlEl = $('s-pnl');
  if (pnlEl) {
    pnlEl.textContent = fmt$sign(pnl);
    pnlEl.className   = 'sv ' + clsSign(pnl);
  }
  const pnlPct = pv > 0 ? (pnl / (pv - pnl)) * 100 : 0;
  setVal('s-pnl-pct', pnlPct, v => fmtPct(v));

  const effCap = d.effective_capital || 0;
  const totPnl = pv - effCap;
  setVal('s-session-pnl', totPnl, fmt$sign);

  setVal('s-scans',  d.scan_count || 0,            v => v);
  setVal('s-pos',    (d.positions||[]).length,      v => v);
  setVal('s-trades', (d.all_trades||[]).filter(t => {
    if (!t.exit_time) return false;
    const d_ = new Date(t.exit_time), n = new Date();
    return d_.toDateString() === n.toDateString();
  }).length, v => v);

  setVal('s2-cash',   parseFloat(ad.CashBalance||0),      fmt$);
  setVal('s2-bp',     parseFloat(ad.BuyingPower||0),      fmt$);
  setVal('s2-unreal', parseFloat(ad.UnrealizedPnL||0),    v => {
    const n = parseFloat(v);
    return isNaN(n) ? '—' : (n>=0?'+':'') + fmt$(n).replace('$','$');
  });
  const unreal = $('s2-unreal');
  if (unreal) unreal.className = 'sv ' + clsSign(ad.UnrealizedPnL||0) + ' ' + (parseFloat(ad.UnrealizedPnL||0)>=0?'cg':'cr');

  setVal('s2-real',   parseFloat(ad.RealizedPnL||ad.DayTradesRemaining||0), fmt$);
  setVal('s2-margin', parseFloat(ad.RegTMargin||ad.InitMargin||0), fmt$);
  setVal('s2-excess', parseFloat(ad.ExcessLiquidity||0), fmt$);
}

// ── LIVE TAB ───────────────────────────────────────────────────
function renderLive() {
  renderControls();
  renderDecisionFeed();
  renderLog();
  renderPositions();
  renderTodayResults();
}

function renderControls() {
  const d = S;
  // Pause button
  const pb = $('pause-btn');
  if (pb) pb.textContent = d.paused ? '▶ RESUME BOT' : '⏸ PAUSE BOT';

  // Phase Gate pill
  if (_gateData) {
    const pp = $('phase-pill');
    if (pp) {
      const ph = _gateData.current_phase || 1;
      pp.textContent = 'PHASE ' + ph;
      pp.style.borderColor = ph >= 2 ? 'var(--green)' : 'var(--yellow)';
      pp.style.color       = ph >= 2 ? 'var(--green)' : 'var(--yellow)';
      pp.style.background  = ph >= 2 ? 'rgba(0,200,83,.07)' : 'rgba(255,214,0,.07)';
    }
  }

  // Regime box
  const rg = (d.regime||{}).regime || 'UNKNOWN';
  const rb = $('ctrl-regime-box');
  if (rb) {
    rb.className = 'regime-box ' + (
      rg.includes('BULL') ? 'bull' :
      rg.includes('BEAR') ? 'bear' :
      rg === 'PANIC'      ? 'panic' :
      rg === 'CHOPPY'     ? 'choppy' : 'unknown'
    );
  }
  const rl = $('ctrl-regime-label');
  if (rl) rl.textContent = rg;
  const rm = $('ctrl-regime-meta');
  if (rm) rm.textContent = 'VIX: ' + fmtNum(d.regime?.vix,1) + ' | SPY: $' + fmtNum(d.regime?.spy_price,2);

  // Agent agreement
  const ca = $('ctrl-agree');
  if (ca) {
    const agreed = d.last_agents_agreed || 0;
    const req    = d.agents_required || 2;
    ca.textContent = agreed + ' / ' + req + ' required';
    ca.className   = 'mon-val ' + (agreed >= req ? 'cg' : 'cr');
  }

  // Risk budget (use settings values — no hardcoding)
  const pv = S.portfolio_value || 0;
  const limit = (S.settings.daily_loss_limit || 0.06) * 100;
  const limitAmt = pv * (S.settings.daily_loss_limit || 0.06);
  const usedAmt = Math.max(0, -(S.daily_pnl || 0));
  const pct = limitAmt > 0 ? Math.min(100, (usedAmt / limitAmt) * 100) : 0;
  const rb2 = $('ctrl-risk-bar');
  if (rb2) {
    rb2.style.width = pct + '%';
    rb2.style.background = pct > 75 ? 'var(--red)' : pct > 50 ? 'var(--yellow)' : 'var(--green)';
  }
  if ($('ctrl-risk-used')) $('ctrl-risk-used').textContent = fmt$(usedAmt) + ' used';
  if ($('ctrl-risk-left')) $('ctrl-risk-left').textContent = fmt$(Math.max(0, limitAmt - usedAmt)) + ' left';

  // Skew
  const skewData = S.skew;
  const skewVal = skewData ? (skewData['48h'] || 0) : 0;
  const sv = $('ctrl-skew-val');
  if (sv) {
    sv.textContent = fmtNum(skewVal, 1);
    sv.style.color = skewVal > 20 ? 'var(--green)' : skewVal < -20 ? 'var(--red)' : 'var(--orange)';
  }
  const sb = $('ctrl-skew-bar');
  if (sb) {
    const halfPct = Math.min(50, Math.abs(skewVal) / 2);
    sb.style.width    = halfPct + '%';
    sb.style.left     = skewVal >= 0 ? '50%' : (50 - halfPct) + '%';
    sb.style.background = skewVal >= 0 ? 'var(--green)' : 'var(--red)';
  }

  // Favourites chips
  renderFavChips();
}

function renderFavChips() {
  const favs = S.favourites || [];
  const el = $('fav-chips');
  if (!el) return;
  el.innerHTML = favs.map(f =>
    `<span class="fav-chip">${f}<span class="rm" onclick="removeFav('${f}')">✕</span></span>`
  ).join('');
}

function renderRegimeBriefing(sectorData) {
  const d = S;
  const rg = (d.regime||{}).regime || 'UNKNOWN';

  const brl = $('br-label');
  if (brl) {
    brl.textContent = rg;
    brl.style.color = rg.includes('BULL') ? 'var(--green)' : rg.includes('BEAR') ? 'var(--red)' : 'var(--yellow)';
  }
  if ($('br-vix')) $('br-vix').textContent = 'VIX ' + fmtNum(d.regime?.vix,1) + ' | SPY $' + fmtNum(d.regime?.spy_price,2);

  const leaderEl  = $('br-leaders');
  const laggardEl = $('br-laggards');
  if (!leaderEl || !laggardEl) return;

  if (!sectorData || !sectorData.available) {
    leaderEl.innerHTML  = '<span style="font-size:9px;color:var(--muted2)">Sector data loading…</span>';
    laggardEl.innerHTML = '';
    return;
  }
  leaderEl.innerHTML = (sectorData.leaders || []).map(s =>
    `<span class="sector-chip lead">${s.name || s.etf} <span style="font-size:8px">${s.rs_5d >= 0 ? '+' : ''}${(s.rs_5d||0).toFixed(1)}%</span></span>`
  ).join('');
  laggardEl.innerHTML = (sectorData.laggards || []).map(s =>
    `<span class="sector-chip lag">${s.name || s.etf} <span style="font-size:8px">${(s.rs_5d||0).toFixed(1)}%</span></span>`
  ).join('');
  if ($('br-updated')) $('br-updated').textContent = sectorData.updated ? 'Sectors @ ' + sectorData.updated : '';
}

function renderDecisionFeed() {
  const el = $('decision-feed');
  if (!el) return;
  const history = (S.decision_history || []).slice(-DECISION_MAX).reverse();
  if ($('decision-count')) $('decision-count').textContent = history.length + ' decisions';
  if (!history.length) {
    el.innerHTML = '<div class="empty">No decisions yet.</div>';
    return;
  }
  el.innerHTML = history.map(dec => {
    const dir    = dec.direction || dec.side || '—';
    const sym    = dec.symbol || dec.ticker || '—';
    const tt     = dec.trade_type || '';
    const score  = dec.score || dec.final_score || 0;
    const thesis = dec.entry_thesis || dec.reasoning || '';
    const agreed = dec.agents_agreed || 0;
    const pnl    = dec.pnl || dec.realised_pnl;
    const outcome = pnl != null
      ? `<span class="${parseFloat(pnl)>=0?'cg':'cr'} dfi-outcome">${fmt$sign(pnl)}</span>`
      : `<span style="color:var(--muted2);font-size:9px">open</span>`;
    return `<div class="decision-feed-item">
      <div class="dfi-header">
        <span class="dfi-sym">${sym}</span>
        ${dir ? `<span class="badge ${dir==='LONG'?'badge-long':'badge-short'}">${dir}</span>` : ''}
        ${tt  ? `<span class="badge badge-${tt.toLowerCase()}">${tt}</span>` : ''}
        <span style="font-size:9px;color:var(--muted2)">Score ${score}</span>
        <span style="font-size:9px;color:var(--muted2);margin-left:4px">${agreed} agents</span>
        <span style="margin-left:auto">${outcome}</span>
      </div>
      ${thesis ? `<div class="dfi-thesis">${thesis.substring(0,120)}${thesis.length>120?'…':''}</div>` : ''}
    </div>`;
  }).join('');
}

function renderLog() {
  const el = $('log-area');
  if (!el) return;
  const logs = (S.logs || []).slice(0, LOG_MAX);
  if ($('log-count')) $('log-count').textContent = logs.length + ' events';
  if (!logs.length) { el.innerHTML = '<div class="empty">No activity yet.</div>'; return; }
  el.innerHTML = logs.map(l =>
    `<div class="log-row">
      <span class="lt">${l.time||''}</span>
      <span class="lk lk-${l.type||'INFO'}">${l.type||'INFO'}</span>
      <span class="lm">${(l.msg||'').replace(/</g,'&lt;')}</span>
    </div>`
  ).join('');
}

function renderPositions() {
  const el = $('pos-list');
  if (!el) return;
  let pos = [...(S.positions || [])];
  if ($('pos-count')) $('pos-count').textContent = pos.length;

  if (!pos.length) { el.innerHTML = '<div class="empty">No open positions</div>'; return; }

  if (_posSort === 'pnl')     pos.sort((a,b) => (b.pnl||0) - (a.pnl||0));
  else if (_posSort === 'size') pos.sort((a,b) => Math.abs(b.market_value||0) - Math.abs(a.market_value||0));

  const heldSyms = new Set(pos.map(p => p.symbol?.toUpperCase()));

  el.innerHTML = pos.map(p => {
    const dir     = (p.direction||'').toUpperCase();
    const isLong  = dir === 'LONG' || p.quantity > 0;
    const pnl     = p.pnl || p.unrealised_pnl || 0;
    const pnlPct  = p.pnl_pct || 0;
    const thesis  = p.entry_thesis || p.thesis || '';
    const tt      = p.trade_type || '';
    const thesis_status = p.thesis_status || ((() => {
      const er = (p.entry_regime||'').toUpperCase();
      const cr = ((S.regime||{}).regime||'').toUpperCase();
      if (!er || !cr) return 'ok';
      const epol = er.includes('BULL') ? 1 : er.includes('BEAR') ? -1 : 0;
      const cpol = cr.includes('BULL') ? 1 : cr.includes('BEAR') ? -1 : 0;
      return (epol !== 0 && cpol !== 0 && epol !== cpol) ? 'breach' : 'ok';
    })());
    return `<div class="pos-row" onclick="openPosModal(${JSON.stringify(JSON.stringify(p))})">
      <div class="pos-stripe ${isLong?'long':'short'}"></div>
      <div class="pos-body">
        <div class="pos-line1">
          <span class="pos-sym">${p.symbol||'—'}</span>
          ${dir ? `<span class="badge ${isLong?'badge-long':'badge-short'}">${dir}</span>` : ''}
          ${tt  ? `<span class="badge badge-${tt.toLowerCase()}">${tt}</span>` : ''}
          ${thesis ? `<span class="thesis-dot ${thesis_status}" title="Thesis status"></span>` : ''}
          <span class="pos-pnl ${pnl>=0?'cg':'cr'}">${fmt$sign(pnl)}</span>
        </div>
        ${thesis ? `<div class="pos-thesis">${thesis.substring(0,90)}${thesis.length>90?'…':''}</div>` : ''}
        <div class="pos-meta">
          <span>${p.quantity||0} shares</span>
          <span>Entry ${fmt$(p.entry_price)}</span>
          <span class="${pnlPct>=0?'cg':'cr'}">${fmtPct(pnlPct)}</span>
          ${p.entry_regime ? `<span style="color:var(--muted)">${p.entry_regime}</span>` : ''}
        </div>
      </div>
    </div>`;
  }).join('');
}

function renderTodayResults() {
  const el = $('today-list');
  if (!el) return;
  const today = new Date().toDateString();
  const trades = (S.all_trades || []).filter(t => {
    if (!t.exit_time) return false;
    return new Date(t.exit_time).toDateString() === today;
  });
  if ($('today-count')) $('today-count').textContent = trades.length + ' trades';
  if (!trades.length) { el.innerHTML = '<div class="empty">No closed trades today</div>'; return; }
  el.innerHTML = trades.slice(-20).reverse().map(t => {
    const pnl = t.pnl || 0;
    const isWin = pnl >= 0;
    return `<div class="result-row">
      <div class="result-stripe ${isWin?'win':'loss'}"></div>
      <span class="pos-sym" style="font-size:11px">${t.symbol||'—'}</span>
      ${t.trade_type ? `<span class="badge badge-${t.trade_type.toLowerCase()}">${t.trade_type}</span>` : ''}
      <span style="margin-left:auto;font-size:11px;font-weight:700" class="${isWin?'cg':'cr'}">${fmt$sign(pnl)}</span>
      <span style="font-size:9px;color:var(--muted2);margin-left:6px">${t.exit_reason||''}</span>
    </div>`;
  }).join('');
}

// ── RISK TAB ───────────────────────────────────────────────────
function renderRisk() {
  const d = S;
  const pv = d.portfolio_value || 0;
  const ad = d.account_details || {};
  const s  = d.settings || {};

  // Meters (all read from settings — no hardcoded values)
  const dailyLimit    = s.daily_loss_limit || 0.06;
  const limitAmt      = pv * dailyLimit;
  const usedAmt       = Math.max(0, -(d.daily_pnl || 0));
  const dailyPct      = limitAmt > 0 ? Math.min(100, (usedAmt / limitAmt) * 100) : 0;

  const rb = $('r-daily-bar');
  if (rb) { rb.style.width = dailyPct + '%'; rb.style.background = dailyPct>75?'var(--red)':dailyPct>50?'var(--yellow)':'var(--green)'; }
  if ($('r-daily-used')) $('r-daily-used').textContent = fmt$(usedAmt) + ' of ' + fmt$(limitAmt);
  if ($('r-daily-pct'))  $('r-daily-pct').textContent  = dailyPct.toFixed(0) + '%';

  const maxPos   = s.max_positions || 15;
  const openPos  = (d.positions||[]).length;
  const expPct   = maxPos > 0 ? Math.min(100, (openPos / maxPos) * 100) : 0;
  const expBar   = $('r-exp-bar');
  if (expBar) expBar.style.width = expPct + '%';
  if ($('r-exp-used')) $('r-exp-used').textContent = openPos + ' positions';
  if ($('r-exp-pct'))  $('r-exp-pct').textContent  = expPct.toFixed(0) + '% of max ' + maxPos;

  const conLoss = d.consecutive_losses || 0;
  const maxLoss = s.max_consecutive_losses || 3;
  const lossPct = maxLoss > 0 ? Math.min(100, (conLoss / maxLoss) * 100) : 0;
  const lossBar = $('r-loss-bar');
  if (lossBar) lossBar.style.width = lossPct + '%';
  if ($('r-loss-n'))      $('r-loss-n').textContent      = conLoss + ' of ' + maxLoss;
  if ($('r-loss-status')) $('r-loss-status').textContent = conLoss >= maxLoss ? 'LIMIT' : 'OK';

  const cash    = parseFloat(ad.CashBalance || 0);
  const minCash = s.min_cash_reserve || 0.10;
  const cashPct = pv > 0 ? Math.min(100, (cash / pv) * 100) : 100;
  const cashBar = $('r-cash-bar');
  if (cashBar) { cashBar.style.width = cashPct + '%'; cashBar.style.background = cashPct < minCash*100 ? 'var(--red)' : 'var(--green)'; }
  if ($('r-cash-pct')) $('r-cash-pct').textContent = cashPct.toFixed(0) + '% cash';
  if ($('r-cash-min')) $('r-cash-min').textContent = 'Min: ' + (minCash*100).toFixed(0) + '%';

  // Position risk detail
  const el = $('r-pos-detail');
  if (el) {
    const pos = d.positions || [];
    if (!pos.length) { el.innerHTML = '<div class="empty">No open positions</div>'; return; }
    el.innerHTML = pos.map(p => {
      const pnl = p.pnl || 0;
      const budgetUsed = limitAmt > 0 ? Math.min(100, (Math.abs(pnl) / limitAmt) * 100) : 0;
      return `<div class="pos-risk-row">
        <span style="font-family:'Syne',sans-serif;font-weight:800">${p.symbol||'—'}</span>
        <div class="pos-risk-bar" style="width:${budgetUsed.toFixed(1)}%"></div>
        <span class="${pnl>=0?'cg':'cr'}">${budgetUsed.toFixed(1)}%</span>
        <span class="${pnl>=0?'cg':'cr'}">${fmt$sign(pnl)}</span>
      </div>`;
    }).join('');
  }
}

// ── AGENTS TAB ─────────────────────────────────────────────────
function renderAgents() {
  const d = S;
  const convo   = d.agent_conversation || [];
  const outputs = d.agent_outputs || {};
  const agreed  = d.last_agents_agreed || 0;
  const req     = d.agents_required || 2;

  if ($('agents-scan-time')) $('agents-scan-time').textContent = d.last_scan || '—';

  // Vote summary
  const agentDefs = [
    {key:'technical',       label:'Technical'},
    {key:'trading_analyst', label:'Trading'},
    {key:'risk',            label:'Risk'},
  ];
  const voteItems = $('vote-items');
  if (voteItems) {
    voteItems.innerHTML = agentDefs.map(a => {
      const hasOutput = !!outputs[a.key];
      return `<span class="vote-item">
        <span style="color:${hasOutput?'var(--green)':'var(--muted)'}">
          ${hasOutput?'✅':'○'}
        </span>
        <span style="font-size:10px;color:${hasOutput?'var(--text)':'var(--muted2)'}">${a.label}</span>
      </span>`;
    }).join('');
  }
  const vr = $('vote-result');
  if (vr) {
    const taken = convo.some(c => c.agent === 'Final Decision Maker' && c.output && c.output !== 'No trades this cycle.');
    vr.textContent = agreed + '/' + (agentDefs.length + 1) + (taken ? ' — TRADE TAKEN' : ' — NO TRADE');
    vr.className   = 'vote-result ' + (taken ? 'co' : 'cg');
  }

  // Agent cards
  const acEl = $('agent-cards');
  if (acEl) {
    if (!convo.length) {
      acEl.innerHTML = '<div class="empty" style="grid-column:1/-1;padding:30px">Agent outputs appear after the first scan.</div>';
    } else {
      const cardDefs = [
        ...agentDefs,
        {key:'final', label:'Final Decision Maker', role:'Synthesises all agent reports into executable trade instructions'},
      ];
      acEl.innerHTML = cardDefs.map(def => {
        const entry = convo.find(c =>
          def.key === 'final'
            ? c.agent === 'Final Decision Maker'
            : (c.agent||'').toLowerCase().includes(def.label.toLowerCase())
        ) || {};
        const out  = entry.output || (def.key === 'final' ? 'No trades this cycle.' : '—');
        const role = entry.role || def.role || '';
        return `<div class="agent-card">
          <div class="agent-card-hdr">
            <div>
              <div class="agent-card-name">${def.label}</div>
              <div class="agent-card-role">${role}</div>
            </div>
            <span style="margin-left:auto;font-size:9px;color:var(--muted2)">${entry.time||''}</span>
          </div>
          <div class="agent-card-body">${(out||'').replace(/</g,'&lt;')}</div>
        </div>`;
      }).join('');
    }
  }

  // Raw outputs
  const rawEl = $('raw-outputs');
  if (rawEl) {
    rawEl.innerHTML = Object.entries(outputs).map(([k,v]) =>
      `<div class="raw-output-block">
        <div class="raw-output-key">${k}</div>
        <div class="raw-output-val">${(String(v||'')).replace(/</g,'&lt;').substring(0,600)}</div>
      </div>`
    ).join('');
  }
}

function toggleRaw() {
  const el = $('raw-outputs');
  const btn = document.querySelector('.raw-toggle');
  if (!el || !btn) return;
  const open = el.classList.toggle('open');
  btn.textContent = (open ? '▼' : '▶') + ' ' + (open ? 'Hide' : 'Show') + ' raw agent outputs';
}

// ── ORDERS TAB ─────────────────────────────────────────────────
function renderOrders() {
  const el = $('orders-body');
  if (!el) return;
  let orders = S.all_orders || [];
  if ($('orders-count')) $('orders-count').textContent = orders.length + ' orders';

  const f = _orderFilter;
  if (f !== 'all') {
    if (f === 'submitted') orders = orders.filter(o => (o.status||'').toLowerCase().includes('submit'));
    else if (f === 'filled')    orders = orders.filter(o => (o.status||'').toLowerCase() === 'filled');
    else if (f === 'cancelled') orders = orders.filter(o => (o.status||'').toLowerCase().includes('cancel'));
    else if (f === 'stocks')    orders = orders.filter(o => !(o.symbol||'').match(/[CP]\d/));
    else if (f === 'options')   orders = orders.filter(o => (o.symbol||'').match(/[CP]\d/));
  }

  if (!orders.length) { el.innerHTML = '<div class="empty">No orders match filter.</div>'; return; }
  el.innerHTML = orders.slice(-100).reverse().map(o => {
    const isPending = (o.status||'').toLowerCase().includes('submit');
    return `<div class="t-row orders-row">
      <span>${o.time||o.timestamp||'—'}</span>
      <span class="sym">${o.symbol||'—'}</span>
      <span class="${o.side==='BUY'?'cg':'cr'}">${o.side||'—'}</span>
      <span>${o.order_type||o.type||'—'}</span>
      <span>${o.quantity||o.qty||'—'}</span>
      <span>${fmt$(o.price||o.limit_price)}</span>
      <span>${o.fill_price ? fmt$(o.fill_price) : '—'}</span>
      <span class="${(o.status||'').includes('filled')?'cg':'cw'}">${o.status||'—'}</span>
      <span>${o.role||o.action||'—'}</span>
      <span>${isPending ? `<button class="cancel-btn" onclick="cancelOrder('${o.order_id||o.id}')">✕</button>` : ''}</span>
    </div>`;
  }).join('');
}

function filterOrders(f, btn) {
  _orderFilter = f;
  $$('.table-filters .f-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderOrders();
}

async function cancelOrder(orderId) {
  if (!orderId) return;
  try {
    await fetch('/api/cancel-order', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({order_id: orderId})});
    await fetchState();
  } catch(e) {}
}

// ── HISTORY TAB ────────────────────────────────────────────────
function renderHistory() {
  const el = $('hist-body');
  if (!el) return;
  let trades = S.all_trades || [];
  if ($('history-count')) $('history-count').textContent = trades.length + ' trades';

  const f = _tradeFilter;
  if (f === 'wins')         trades = trades.filter(t => (t.pnl||0) >= 0);
  else if (f === 'losses')  trades = trades.filter(t => (t.pnl||0) < 0);
  else if (f === 'stocks')  trades = trades.filter(t => !(t.symbol||'').match(/[CP]\d/));
  else if (f === 'options') trades = trades.filter(t => (t.symbol||'').match(/[CP]\d/));
  else if (['SCALP','SWING','HOLD'].includes(f)) trades = trades.filter(t => t.trade_type === f);
  else if (f === 'tc_confirmed') trades = trades.filter(t => (t.exit_reason||'').includes('confirmed'));
  else if (f === 'tc_breached')  trades = trades.filter(t => (t.exit_reason||'').includes('breached'));
  else if (f === 'tc_noise')     trades = trades.filter(t => (t.exit_reason||'').includes('noise_stop'));

  if (!trades.length) { el.innerHTML = '<div class="empty">No trades match filter.</div>'; return; }

  el.innerHTML = trades.slice(-100).reverse().map((t,i) => {
    const pnl  = t.pnl || 0;
    const tc   = extractThesisClass(t.exit_reason||'');
    const tcBadge = tc ? `<span class="thesis-class-badge tc-${tc}">${tc}</span>` : '—';
    const holdStr = t.hold_minutes ? (t.hold_minutes < 60 ? t.hold_minutes+'m' : Math.floor(t.hold_minutes/60)+'h') : '—';
    return `<div>
      <div class="t-row history-row" onclick="toggleExpand('hexp-${i}')">
        <span>${(t.exit_time||'').split('T')[1]?.split('.')[0]||t.exit_time||'—'}</span>
        <span class="sym">${t.symbol||'—'}</span>
        <span class="${(t.direction||t.side||'')===('LONG')?'cg':'cr'}">${t.direction||t.side||'—'}</span>
        <span>${t.quantity||'—'}</span>
        <span>${fmt$(t.entry_price)}</span>
        <span>${fmt$(t.exit_price)}</span>
        <span class="${pnl>=0?'cg':'cr'}">${fmt$sign(pnl)}</span>
        <span>${holdStr}</span>
        <span>${tcBadge}</span>
        <span style="color:var(--muted2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9px">${(t.exit_reason||'').substring(0,40)}</span>
      </div>
      <div class="expand-row" id="hexp-${i}">
        ${t.entry_thesis ? `<div style="margin-bottom:4px"><strong>Thesis:</strong> ${t.entry_thesis}</div>` : ''}
        <div><strong>Exit reason:</strong> ${t.exit_reason||'—'}</div>
        ${t.trade_type ? `<div style="margin-top:2px"><strong>Type:</strong> ${t.trade_type}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

function filterTrades(f, btn) {
  _tradeFilter = f;
  $$('#view-history .f-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderHistory();
}

function toggleExpand(id) {
  const el = $(id);
  if (el) el.classList.toggle('open');
}

function extractThesisClass(reason) {
  if (!reason) return '';
  const m = reason.match(/thesis:(\w+)/);
  if (m) return m[1];
  if (reason.includes('confirmed'))    return 'confirmed';
  if (reason.includes('breached'))     return 'breached';
  if (reason.includes('noise_stop'))   return 'noise';
  if (reason.includes('stale_scalp'))  return 'stale';
  if (reason.includes('manual'))       return 'manual';
  return '';
}

// ── PERFORMANCE TAB ────────────────────────────────────────────
function renderPerformance() {
  const perf = S.performance || {};
  const trades = S.all_trades || [];

  // Metrics from performance dict or compute from trades
  const winCount = trades.filter(t => (t.pnl||0) >= 0).length;
  const lossCount = trades.length - winCount;
  const wr = trades.length > 0 ? (winCount / trades.length * 100).toFixed(1) + '%' : '—';
  const wins  = trades.filter(t => (t.pnl||0) >= 0).map(t => t.pnl||0);
  const losses = trades.filter(t => (t.pnl||0) < 0).map(t => Math.abs(t.pnl||0));
  const avgWin  = wins.length  ? wins.reduce((a,b)=>a+b,0)/wins.length   : 0;
  const avgLoss = losses.length ? losses.reduce((a,b)=>a+b,0)/losses.length : 0;
  const pf = avgLoss > 0 ? (avgWin * winCount / (avgLoss * lossCount)).toFixed(2) : '—';
  const exp = trades.length > 0
    ? fmt$sign(avgWin * (winCount/trades.length) - avgLoss * (lossCount/trades.length))
    : '—';

  const allPnls = trades.map(t => t.pnl||0);
  const best  = allPnls.length ? Math.max(...allPnls) : null;
  const worst = allPnls.length ? Math.min(...allPnls) : null;

  // Max drawdown from equity history
  const eq = S.equity_history || [];
  let maxDD = 0, peak = 0;
  for (const v of eq) {
    const val = typeof v === 'number' ? v : v.value;
    if (val > peak) peak = val;
    const dd = peak > 0 ? (peak - val) / peak * 100 : 0;
    if (dd > maxDD) maxDD = dd;
  }

  const pnlEl = $('g-pnl');
  if (pnlEl) {
    const tp = S.portfolio_value - (S.effective_capital||S.portfolio_value);
    pnlEl.textContent = fmt$sign(tp);
    pnlEl.className   = 'metric-val ' + clsSign(tp);
  }
  if ($('g-wr'))    $('g-wr').textContent    = wr;
  if ($('g-pf'))    $('g-pf').textContent    = pf;
  if ($('g-exp'))   $('g-exp').textContent   = exp;
  if ($('g-total')) $('g-total').textContent  = trades.length;
  if ($('g-best'))  $('g-best').textContent   = best  != null ? fmt$sign(best)  : '—';
  if ($('g-worst')) $('g-worst').textContent  = worst != null ? fmt$sign(worst) : '—';
  if ($('g-dd'))    $('g-dd').textContent     = maxDD > 0 ? '-' + maxDD.toFixed(1) + '%' : '0%';

  renderEquityChart();
  renderDailyChart();
}

function renderEquityChart() {
  const canvas = $('equity-chart');
  if (!canvas) return;
  let data = S.equity_history || [];
  data = filterTimeframe(data, _equityTF);
  if (!data.length) return;

  const labels = data.map(p => (p.date || p.timestamp || '').split('T')[0]);
  const vals   = data.map(p => typeof p === 'number' ? p : (p.value || p.portfolio_value || 0));
  const startVal = vals[0] || 0;
  const spyLine  = vals.map(() => startVal); // placeholder benchmark

  const cfg = {
    type:'line',
    data:{
      labels,
      datasets:[
        {label:'Portfolio', data:vals, borderColor:'#FF6B00', borderWidth:2,
         pointRadius:0, tension:.3, fill:true,
         backgroundColor:'rgba(255,107,0,.06)'},
        {label:'Baseline', data:spyLine, borderColor:'#444', borderWidth:1,
         pointRadius:0, borderDash:[4,4]},
      ]
    },
    options:{
      responsive:true, maintainAspectRatio:false, animation:false,
      plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false}},
      scales:{
        x:{ticks:{color:'#555',font:{size:9,family:'JetBrains Mono'}},grid:{color:'rgba(255,255,255,.03)'}},
        y:{ticks:{color:'#555',font:{size:9,family:'JetBrains Mono'},callback:v=>'$'+Math.round(v/1000)+'k'},grid:{color:'rgba(255,255,255,.03)'}},
      }
    }
  };
  if (_eqChart) { _eqChart.data = cfg.data; _eqChart.update('none'); }
  else { _eqChart = new Chart(canvas.getContext('2d'), cfg); }
}

function renderDailyChart() {
  const canvas = $('daily-chart');
  if (!canvas) return;
  let data = S.all_trades || [];
  data = data.filter(t => !!t.exit_time);
  // Group by date
  const byDate = {};
  for (const t of data) {
    const d = (t.exit_time||'').split('T')[0];
    byDate[d] = (byDate[d]||0) + (t.pnl||0);
  }
  let entries = Object.entries(byDate).sort(([a],[b])=>a<b?-1:1);
  entries = filterTimeframeEntries(entries, _dailyTF);
  if (!entries.length) return;

  const cfg = {
    type:'bar',
    data:{
      labels: entries.map(([d]) => d),
      datasets:[{
        data: entries.map(([,v]) => v),
        backgroundColor: entries.map(([,v]) => v >= 0 ? 'rgba(0,200,83,.6)' : 'rgba(255,23,68,.6)'),
        borderRadius:1,
      }]
    },
    options:{
      responsive:true, maintainAspectRatio:false, animation:false,
      plugins:{legend:{display:false},tooltip:{callbacks:{label:ctx=>'P&L: ' + fmt$sign(ctx.raw)}}},
      scales:{
        x:{ticks:{color:'#555',font:{size:8}},grid:{display:false}},
        y:{ticks:{color:'#555',font:{size:9},callback:v=>fmt$sign(v)},grid:{color:'rgba(255,255,255,.03)'}},
      }
    }
  };
  if (_dailyChart) { _dailyChart.data = cfg.data; _dailyChart.update('none'); }
  else { _dailyChart = new Chart(canvas.getContext('2d'), cfg); }
}

function filterTimeframe(arr, tf) {
  const now = Date.now();
  const msDay = 86400000;
  const cutoffs = {
    '1D': now - msDay,
    '1W': now - 7*msDay,
    '1M': now - 30*msDay,
    'MTD': new Date(new Date().getFullYear(), new Date().getMonth(), 1).getTime(),
    'YTD': new Date(new Date().getFullYear(), 0, 1).getTime(),
    'ALL': 0,
  };
  const cut = cutoffs[tf] || 0;
  return arr.filter(p => {
    const ts = new Date(p.timestamp || p.date || 0).getTime();
    return ts >= cut;
  });
}

function filterTimeframeEntries(entries, tf) {
  const now = new Date();
  const msDay = 86400000;
  const cutoffDate = {
    '1W':  new Date(Date.now()-7*msDay).toISOString().split('T')[0],
    '1M':  new Date(Date.now()-30*msDay).toISOString().split('T')[0],
    'MTD': new Date(now.getFullYear(), now.getMonth(), 1).toISOString().split('T')[0],
    'YTD': new Date(now.getFullYear(), 0, 1).toISOString().split('T')[0],
    'ALL': '0000-00-00',
  }[tf] || '0000-00-00';
  return entries.filter(([d]) => d >= cutoffDate);
}

function setEquityTF(tf, btn) {
  _equityTF = tf;
  $$('#equity-chart').forEach(()=>{});
  document.querySelectorAll('#view-performance .tf-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderEquityChart();
}
function setDailyTF(tf, btn) {
  _dailyTF = tf;
  document.querySelectorAll('#view-performance .tf-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderDailyChart();
}

// ── PORTFOLIO TAB ──────────────────────────────────────────────
function renderPortfolio() {
  if (!_portData) return;
  const d = _portData;
  const totals = d.totals || {};
  const positions = d.positions || {};
  const posArr = Object.values(positions);

  if ($('pf-gross'))  $('pf-gross').textContent  = fmt$(totals.total_market_value);
  if ($('pf-net'))    $('pf-net').textContent     = fmt$(totals.total_market_value);
  if ($('pf-unreal')) $('pf-unreal').textContent  = fmt$sign(posArr.reduce((a,p)=>a+(p.pnl||0),0));
  if ($('pf-real'))   $('pf-real').textContent    = fmt$sign(S.daily_pnl||0);
  const longs  = posArr.filter(p => p.quantity > 0);
  const shorts = posArr.filter(p => p.quantity < 0);
  if ($('pf-ls')) $('pf-ls').textContent = longs.length + 'L / ' + shorts.length + 'S';
  if ($('pf-accts')) $('pf-accts').textContent = (d.accounts||[]).length;
  if ($('pf-count')) $('pf-count').textContent = posArr.length + ' positions';

  const mv = totals.total_market_value || 0;
  const longMV  = longs.reduce((a,p)=>a+Math.abs(p.market_value||0),0);
  const shortMV = shorts.reduce((a,p)=>a+Math.abs(p.market_value||0),0);
  const longPct  = mv > 0 ? (longMV/mv*100).toFixed(0) : 0;
  const shortPct = mv > 0 ? (shortMV/mv*100).toFixed(0) : 0;
  if ($('pf-long-pct'))   $('pf-long-pct').textContent   = longPct + '%';
  if ($('pf-short-pct'))  $('pf-short-pct').textContent  = shortPct + '%';
  if ($('pf-long-bar'))   $('pf-long-bar').style.width   = longPct + '%';
  if ($('pf-short-bar'))  $('pf-short-bar').style.width  = shortPct + '%';

  // Positions table
  const ptEl = $('pf-table');
  if (ptEl) {
    if (!posArr.length) { ptEl.innerHTML = '<div class="empty">No positions</div>'; return; }
    ptEl.innerHTML = `
      <div style="display:grid;grid-template-columns:80px 60px 60px 80px 80px 80px;gap:6px;padding:4px 12px;font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;border-bottom:1px solid var(--border)">
        <span>Symbol</span><span>Dir</span><span>Qty</span><span>Mkt Val</span><span>P&amp;L</span><span>Sector</span>
      </div>` +
    posArr.map(p => {
      const pnl = p.pnl||0;
      return `<div style="display:grid;grid-template-columns:80px 60px 60px 80px 80px 80px;gap:6px;padding:5px 12px;border-bottom:1px solid var(--border);font-size:10px;align-items:center">
        <span style="font-family:'Syne',sans-serif;font-weight:800">${p.symbol||'—'}</span>
        <span class="${p.quantity>0?'cg':'cr'}">${p.quantity>0?'LONG':'SHORT'}</span>
        <span>${Math.abs(p.quantity||0)}</span>
        <span>${fmt$(p.market_value)}</span>
        <span class="${pnl>=0?'cg':'cr'}">${fmt$sign(pnl)}</span>
        <span style="color:var(--muted2)">${p.sector||'—'}</span>
      </div>`;
    }).join('');
  }

  // Charts only when enough data
  renderPortfolioCharts(posArr);
}

function renderPortfolioCharts(posArr) {
  if (!posArr.length || posArr.length < 3) {
    $('pf-charts-row').style.opacity = '.3';
    return;
  }
  $('pf-charts-row').style.opacity = '1';
  const COLORS = ['#FF6B00','#00C853','#FF1744','#FFD600','#8B5CF6','#06B6D4','#F97316','#84CC16','#EC4899'];

  // Sector donut
  const sectorMap = {};
  for (const p of posArr) { const s = p.sector||'Other'; sectorMap[s]=(sectorMap[s]||0)+Math.abs(p.market_value||0); }
  _pfSectorChart = renderDonut('pf-sector-chart', _pfSectorChart, sectorMap, COLORS);

  // Concentration donut
  const concMap = {};
  for (const p of posArr) concMap[p.symbol]= Math.abs(p.market_value||0);
  _pfConcChart = renderDonut('pf-conc-chart', _pfConcChart, concMap, COLORS);

  // Trade type donut
  const ttMap = {};
  for (const p of posArr) { const t = p.trade_type||'Unknown'; ttMap[t]=(ttMap[t]||0)+1; }
  _pfTypeChart = renderDonut('pf-type-chart', _pfTypeChart, ttMap, ['#FF6B00','#8B5CF6','#00C853']);
}

function renderDonut(canvasId, existingChart, dataMap, colors) {
  const canvas = $(canvasId);
  if (!canvas) return existingChart;
  const entries = Object.entries(dataMap);
  const cfg = {
    type:'doughnut',
    data:{ labels: entries.map(([k])=>k), datasets:[{ data: entries.map(([,v])=>v), backgroundColor: colors, borderWidth:0 }]},
    options:{ responsive:true, maintainAspectRatio:false, animation:false,
      plugins:{legend:{display:true, position:'bottom', labels:{color:'#777',font:{size:9,family:'JetBrains Mono'},boxWidth:8}}} }
  };
  if (existingChart) { existingChart.data = cfg.data; existingChart.update('none'); return existingChart; }
  return new Chart(canvas.getContext('2d'), cfg);
}

// ── INTELLIGENCE TAB ───────────────────────────────────────────
function renderPhaseGate() {
  if (!_gateData) return;
  const g = _gateData;

  if ($('ig-phase-num'))   $('ig-phase-num').textContent   = 'Phase ' + (g.current_phase||1);
  if ($('ig-phase-desc'))  $('ig-phase-desc').textContent  = g.phase_description || '—';
  if ($('ig-phase-trades')) {
    const closed = g.closed_trades || 0;
    const minCl  = g.min_closed_trades || 100;
    $('ig-phase-trades').textContent = closed + ' / ' + minCl + ' closed trades to next phase';
    const pct = Math.min(100, (closed / minCl) * 100);
    $('ig-phase-trades').innerHTML +=
      `<div class="bar-bg" style="margin-top:6px"><div class="bar-fill" style="width:${pct}%;background:var(--orange)"></div></div>`;
  }

  const crEl = $('ig-criteria');
  if (crEl && g.criteria_met) {
    crEl.innerHTML = Object.entries(g.criteria_met).map(([k,v]) =>
      `<div class="phase-criterion ${v?'crit-ok':'crit-fail'}">
        <span>${v?'✅':'○'}</span>
        <span>${k.replace(/_/g,' ')}</span>
      </div>`
    ).join('');
  }
}

function renderThesisPerf() {
  const el = $('ig-thesis-table');
  if (!el) return;
  if (!_thesisData || !_thesisData.rows || !_thesisData.rows.length) {
    el.innerHTML = '<div class="empty">Not enough trades for thesis performance analysis (min 3 per group).</div>';
    return;
  }
  el.innerHTML = _thesisData.rows.map(r => {
    const wr = (r.win_rate * 100).toFixed(0) + '%';
    const pnl = (r.avg_pnl_pct >= 0 ? '+' : '') + r.avg_pnl_pct.toFixed(2) + '%';
    return `<div class="t-row" style="grid-template-columns:80px 120px 60px 80px 80px">
      <span class="badge badge-${(r.trade_type||'').toLowerCase()}">${r.trade_type||'—'}</span>
      <span class="thesis-class-badge tc-${r.thesis_class||'manual'}">${r.thesis_class||'—'}</span>
      <span>${r.count}</span>
      <span class="${r.win_rate >= 0.5 ? 'cg' : 'cr'}">${wr}</span>
      <span class="${r.avg_pnl_pct >= 0 ? 'cg' : 'cr'}">${pnl}</span>
    </div>`;
  }).join('');
}

function renderICWeights() {
  if (!_icData) return;
  const el = $('ig-ic-grid');
  if (!el) return;
  const dims = _dimensions || [];
  const weights = _icData.weights || {};
  const rawIC   = _icData.raw_ic  || {};
  const hist    = _icData.history  || [];

  const rows = dims.map(dim => {
    const w   = weights[dim.key] || 0;
    const ic  = rawIC[dim.key];
    const h4  = hist.length >= 2 ? (hist[hist.length-1].raw_ic||{})[dim.key] : null;
    const wPct = (w * 100).toFixed(0);
    const icStr = ic != null ? (ic >= 0 ? '+' : '') + ic.toFixed(3) : '—';
    const h4Str = h4 != null ? (h4 >= 0 ? '+' : '') + h4.toFixed(3) : '—';
    return `<span style="color:var(--text)">${dim.label}</span>
      <div class="ic-bar-bg"><div class="ic-bar-fill" style="width:${wPct}%"></div></div>
      <span style="text-align:right;color:${ic!=null&&ic>0?'var(--green)':ic!=null&&ic<0?'var(--red)':'var(--muted2)'}">${icStr}</span>
      <span style="text-align:right;color:var(--muted2)">${h4Str}</span>`;
  });
  // Preserve header row (first 4 items), then replace data rows
  const header = el.innerHTML.split('</div>').slice(0,4).join('</div>') + '</div>';
  el.innerHTML = el.innerHTML.split('</div>')[0] + '</div>';
  // Rebuild entire grid preserving header
  const gridInner = `
    <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase">Dimension</div>
    <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase">Weight</div>
    <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase;text-align:right">IC</div>
    <div style="font-size:8px;letter-spacing:1px;color:var(--muted);text-transform:uppercase;text-align:right">4W</div>
    ${rows.join('\n')}
  `;
  el.innerHTML = gridInner;

  const status = _icData.using_equal_weights
    ? 'Equal weights (cold start — insufficient IC data)'
    : 'IC-weighted (' + (_icData.n_records||0) + ' records)';
  if ($('ig-ic-status')) $('ig-ic-status').textContent = status;
  if ($('ig-ic-updated') && _icData.updated) $('ig-ic-updated').textContent = 'Updated: ' + _icData.updated;
}

function renderAlphaChart() {
  const canvas = $('alpha-chart');
  if (!canvas || !_alphaData) return;
  if ($('ig-trade-count')) $('ig-trade-count').textContent = (_alphaData.trade_count||0) + ' trades';
  const horizons = _alphaData.horizons || [1,3,5,10];
  const groups   = _alphaData.groups   || {};
  const colors   = {'all':'#FF6B00','high_score':'#00C853','low_score':'#FF1744','bull':'#00C853','bear':'#FF1744'};
  const datasets = Object.entries(groups).slice(0,4).map(([key, g]) => ({
    label: key,
    data:  (g.median||[]).map((v,i) => ({x: horizons[i], y: v*100})),
    borderColor: colors[key] || '#888',
    borderWidth: 1.5,
    pointRadius: 3,
    tension: .3,
    fill: false,
  }));
  const cfg = {
    type:'line',
    data:{ datasets },
    options:{
      responsive:true, maintainAspectRatio:false, animation:false,
      plugins:{ legend:{ display:true, labels:{color:'#777',font:{size:9,family:'JetBrains Mono'},boxWidth:10} }},
      scales:{
        x:{ type:'linear', title:{display:true,text:'Days',color:'#555',font:{size:9}},
            ticks:{color:'#555',font:{size:9}}, grid:{color:'rgba(255,255,255,.03)'} },
        y:{ title:{display:true,text:'Return %',color:'#555',font:{size:9}},
            ticks:{color:'#555',font:{size:9},callback:v=>v.toFixed(1)+'%'},
            grid:{color:'rgba(255,255,255,.03)'} },
      }
    }
  };
  if (_alphaChart) { _alphaChart.data = cfg.data; _alphaChart.update('none'); }
  else { _alphaChart = new Chart(canvas.getContext('2d'), cfg); }
}

// ── SETTINGS TAB ───────────────────────────────────────────────
function renderSettings() {
  const s = S.settings || {};
  if (Object.keys(s).length === 0) return; // Wait for API

  // Only populate inputs if they haven't been dirtied by the user
  const inputs = {
    'cfg-risk-pct':         (s.risk_pct_per_trade||0)*100,
    'cfg-daily-limit':      (s.daily_loss_limit||0)*100,
    'cfg-max-pos':          s.max_positions,
    'cfg-cash-reserve':     (s.min_cash_reserve||0)*100,
    'cfg-max-single':       (s.max_single_position||0)*100,
    'cfg-min-score':        s.min_score_to_trade,
    'cfg-high-score':       s.high_conviction_score,
    'cfg-opt-min-score':    s.options_min_score,
    'cfg-opt-risk':         (s.options_max_risk_pct||0)*100,
    'cfg-opt-ivr':          s.options_max_ivr,
    'cfg-opt-delta':        s.options_target_delta,
    'cfg-opt-delta-range':  s.options_delta_range,
    'cfg-sentinel-poll':    s.sentinel_poll_seconds,
    'cfg-sentinel-cooldown':s.sentinel_cooldown_minutes,
    'cfg-sentinel-max-trades': s.sentinel_max_trades_per_hour,
    'cfg-sentinel-risk-mult':  s.sentinel_risk_multiplier,
    'cfg-sentinel-kw-thresh':  s.sentinel_keyword_threshold,
    'cfg-sentinel-min-conf':   s.sentinel_min_confidence,
  };
  for (const [id, val] of Object.entries(inputs)) {
    const el = $(id);
    if (el && !el.classList.contains('dirty') && val !== undefined) {
      el.value = typeof val === 'number' ? parseFloat(val.toFixed(4)) : val;
    }
  }

  // Selects
  const agreeEl = $('agree-select');
  if (agreeEl && !agreeEl.classList.contains('dirty')) agreeEl.value = s.agents_required_to_agree || 2;
  const sentEl = $('cfg-sentinel-enabled');
  if (sentEl && !sentEl.classList.contains('dirty')) sentEl.value = s.sentinel_enabled ? 'true' : 'false';
  const ibkrEl = $('cfg-sentinel-ibkr');
  if (ibkrEl && !ibkrEl.classList.contains('dirty')) ibkrEl.value = s.sentinel_use_ibkr ? 'true' : 'false';
  const finvEl = $('cfg-sentinel-finviz');
  if (finvEl && !finvEl.classList.contains('dirty')) finvEl.value = s.sentinel_use_finviz ? 'true' : 'false';

  // DTE range (read-only display)
  const dteEl = $('cfg-dte-range');
  if (dteEl) dteEl.textContent = (s.options_dte_min||7) + ' — ' + (s.options_dte_max||60) + ' days';

  // Account / status (always from API)
  if ($('cfg-account')) $('cfg-account').textContent = S.account || '—';
  if ($('cfg-status'))  $('cfg-status').textContent  = S.status  || '—';

  const effCap = S.effective_capital || 0;
  if ($('cfg-eff-cap'))    $('cfg-eff-cap').textContent    = fmt$(effCap);
  if ($('cfg-current-pnl')) {
    const pnl = S.portfolio_value - effCap;
    $('cfg-current-pnl').textContent = fmt$sign(pnl);
    $('cfg-current-pnl').className   = 's-val ' + clsSign(pnl);
  }
}

function markDirty(el) {
  el.classList.add('dirty');
  _settingsDirty = true;
  const banner = $('unsaved-banner');
  if (banner) banner.classList.add('show');
}

function showConfirmModal() {
  if (!_settingsDirty && !Object.keys(collectSettings()).length) return;
  const pending = collectSettings();
  _pendingSettings = pending;
  const s = S.settings || {};
  const labels = {
    risk_pct_per_trade:'Risk per trade (%)',
    daily_loss_limit:'Daily loss limit (%)',
    max_positions:'Max positions',
    min_cash_reserve:'Min cash reserve (%)',
    max_single_position:'Max single position (%)',
    min_score_to_trade:'Min score to trade',
    high_conviction_score:'High conviction score',
    agents_required_to_agree:'Agents required',
  };
  const el = $('modal-changes');
  if (el) {
    el.innerHTML = Object.entries(pending).map(([k,v]) => {
      const label = labels[k] || k;
      const oldV  = s[k] !== undefined ? (String(s[k]).includes('.')?parseFloat(s[k]).toFixed(2):s[k]) : '—';
      const newV  = String(v);
      return `<div class="modal-change"><span>${label}</span><span>${oldV} → <span class="new-val">${newV}</span></span></div>`;
    }).join('');
  }
  $('confirm-modal').classList.add('open');
}

function closeModal() { $('confirm-modal').classList.remove('open'); }

function collectSettings() {
  const s = S.settings || {};
  const out = {};
  const numFields = {
    'cfg-risk-pct':          ['risk_pct_per_trade',        0.01],
    'cfg-daily-limit':       ['daily_loss_limit',           0.01],
    'cfg-max-pos':           ['max_positions',              1],
    'cfg-cash-reserve':      ['min_cash_reserve',           0.01],
    'cfg-max-single':        ['max_single_position',        0.01],
    'cfg-min-score':         ['min_score_to_trade',         1],
    'cfg-high-score':        ['high_conviction_score',      1],
    'cfg-opt-min-score':     ['options_min_score',          1],
    'cfg-opt-risk':          ['options_max_risk_pct',       0.01],
    'cfg-opt-ivr':           ['options_max_ivr',            1],
    'cfg-opt-delta':         ['options_target_delta',       1],
    'cfg-opt-delta-range':   ['options_delta_range',        1],
    'cfg-sentinel-poll':     ['sentinel_poll_seconds',      1],
    'cfg-sentinel-cooldown': ['sentinel_cooldown_minutes',  1],
    'cfg-sentinel-max-trades':['sentinel_max_trades_per_hour',1],
    'cfg-sentinel-risk-mult':['sentinel_risk_multiplier',   1],
    'cfg-sentinel-kw-thresh':['sentinel_keyword_threshold', 1],
    'cfg-sentinel-min-conf': ['sentinel_min_confidence',    1],
  };
  for (const [elId, [key, div]] of Object.entries(numFields)) {
    const el = $(elId);
    if (!el || !el.classList.contains('dirty') || el.value === '') continue;
    const val = parseFloat(el.value) * div;
    if (!isNaN(val)) out[key] = val;
  }
  const agreeEl = $('agree-select');
  if (agreeEl && agreeEl.classList.contains('dirty')) out.agents_required_to_agree = parseInt(agreeEl.value);
  const boolFields = [
    ['cfg-sentinel-enabled','sentinel_enabled'],
    ['cfg-sentinel-ibkr',   'sentinel_use_ibkr'],
    ['cfg-sentinel-finviz', 'sentinel_use_finviz'],
  ];
  for (const [elId, key] of boolFields) {
    const el = $(elId);
    if (el && el.classList.contains('dirty')) out[key] = el.value === 'true';
  }
  return out;
}

async function applySettings() {
  const settings = _pendingSettings || collectSettings();
  // Validate — reject if any required numeric field is blank or invalid
  const criticalFields = ['risk_pct_per_trade','daily_loss_limit','max_positions'];
  for (const f of criticalFields) {
    if (f in settings && (settings[f] === null || isNaN(settings[f]))) {
      alert('Invalid value for ' + f + ' — cannot apply.');
      return;
    }
  }
  closeModal();
  try {
    await fetch('/api/settings', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(settings),
    });
    // Clear dirty state
    $$('.s-input.dirty, .s-select.dirty').forEach(el => el.classList.remove('dirty'));
    _settingsDirty   = false;
    _pendingSettings = {};
    const banner = $('unsaved-banner');
    if (banner) banner.classList.remove('show');
    await fetchState();
  } catch(e) { alert('Settings update failed: ' + e.message); }
}

async function recordCapitalAdjustment() {
  const type   = $('cap-type')?.value;
  const amount = parseFloat($('cap-amount')?.value||0);
  const note   = $('cap-note')?.value || '';
  if (!amount || isNaN(amount)) { alert('Enter a valid amount.'); return; }
  const adj = type === 'withdrawal' ? -amount : amount;
  try {
    await fetch('/api/capital-adjustment', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({amount: adj, note}),
    });
    if ($('cap-amount')) $('cap-amount').value = '';
    if ($('cap-note'))   $('cap-note').value   = '';
    await fetchState();
  } catch(e) {}
}

// ── NEWS TAB ───────────────────────────────────────────────────
function filterNews() {
  const kw   = ($('news-keyword')?.value||'').toLowerCase();
  const sent = $('news-sentiment')?.value || 'all';
  const sort = $('news-sort-sel')?.value  || 'time';
  let data   = [..._newsData];
  if (kw)         data = data.filter(a => (a.headline||a.title||'').toLowerCase().includes(kw) || (a.symbols||[]).some(s=>s.toLowerCase().includes(kw)));
  if (sent !== 'all') data = data.filter(a => (a.sentiment||'').toUpperCase() === sent);
  if (sort === 'score') data.sort((a,b) => (b.news_score||0) - (a.news_score||0));
  renderNews(data);
}

function renderNews(articles) {
  const el = $('news-main');
  if (!el) return;
  if (!articles.length) { el.innerHTML = '<div class="empty" style="padding:40px">No stories match filter.</div>'; return; }

  const heldSyms = new Set((S.positions||[]).map(p=>(p.symbol||'').toUpperCase()));

  const sentClass = s => {
    const su = (s||'').toUpperCase();
    return su.includes('BULL') || su === 'POSITIVE' ? 'bull' :
           su.includes('BEAR') || su === 'NEGATIVE' ? 'bear' : '';
  };
  const sentPill = s => {
    const cls = sentClass(s);
    return cls === 'bull' ? '<span class="sent-bull">▲ BULLISH</span>' :
           cls === 'bear' ? '<span class="sent-bear">▼ BEARISH</span>' :
                            '<span class="sent-neu">— NEUTRAL</span>';
  };
  const tickerBadge = (syms) => (syms||[]).map(sym => {
    const held = heldSyms.has(sym.toUpperCase());
    return `<span class="news-ticker ${held?'held'}">${sym}${held?' ●':''}</span>`;
  }).join('');
  const watchLink = (syms) => (syms||[]).length
    ? `<button class="news-watch" onclick="watchFromNews(${JSON.stringify(syms||[])})">+ Watch</button>`
    : '';

  // Lead story
  const lead = articles[0];
  const rest  = articles.slice(1);
  const half  = Math.ceil(rest.length / 2);
  const col1  = rest.slice(0, half);
  const col2  = rest.slice(half);

  const imgSrc = lead.image_url || lead.banner_image || '';
  const leadHtml = `<div class="news-lead">
    ${imgSrc
      ? `<img class="news-lead-img" src="${imgSrc}" alt="" onerror="this.style.display='none'">`
      : `<div class="news-lead-img-placeholder">NO IMAGE</div>`
    }
    <div class="news-lead-content">
      ${sentPill(lead.sentiment)}
      <div class="news-lead-headline">${(lead.headline||lead.title||'').replace(/</g,'&lt;')}</div>
      <div class="news-lead-standfirst">${(lead.summary||'').substring(0,180).replace(/</g,'&lt;')}</div>
      <div class="news-lead-meta">
        ${tickerBadge(lead.symbols)}
        <span class="news-age">${lead.age_hours != null ? (lead.age_hours<1?Math.round(lead.age_hours*60)+'m':lead.age_hours.toFixed(1)+'h') + ' ago' : ''}</span>
        ${lead.source ? `<span class="news-src">${lead.source}</span>` : ''}
        ${watchLink(lead.symbols)}
      </div>
    </div>
  </div>`;

  const storyHtml = (a) => `<div class="news-story ${sentClass(a.sentiment)}">
    <div class="news-headline">${(a.headline||a.title||'').replace(/</g,'&lt;')}</div>
    ${a.summary ? `<div class="news-standfirst">${(a.summary||'').substring(0,100).replace(/</g,'&lt;')}</div>` : ''}
    <div class="news-meta">
      ${tickerBadge(a.symbols)}
      ${sentPill(a.sentiment)}
      <span class="news-age">${a.age_hours != null ? (a.age_hours<1?Math.round(a.age_hours*60)+'m':a.age_hours.toFixed(1)+'h') + ' ago' : ''}</span>
      ${a.source ? `<span class="news-src">${a.source}</span>` : ''}
      ${watchLink(a.symbols)}
    </div>
  </div>`;

  el.innerHTML = leadHtml +
    `<div class="news-columns">
      <div class="news-col">${col1.map(storyHtml).join('')}</div>
      <div class="news-col">${col2.map(storyHtml).join('')}</div>
    </div>`;
}

function renderSentinel(triggers) {
  const el = $('sentinel-list');
  const cnt = $('sentinel-count');
  if (cnt) cnt.textContent = triggers.length;
  if (!el) return;
  if (!triggers.length) { el.innerHTML = '<div style="padding:16px 10px;font-size:10px;color:var(--muted2)">No alerts yet</div>'; return; }
  el.innerHTML = triggers.slice(0,20).map(t =>
    `<div class="sentinel-item">
      <div class="sentinel-sym">${t.symbol||t.ticker||'—'}</div>
      <div class="sentinel-reason">${(t.reason||t.catalyst||'').substring(0,80)}</div>
    </div>`
  ).join('');
}

function watchFromNews(syms) {
  const favs = new Set(S.favourites || []);
  for (const s of syms) favs.add(s.toUpperCase());
  const newFavs = [...favs];
  fetch('/api/favourites', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({favourites:newFavs})})
    .then(() => { S.favourites = newFavs; renderFavChips(); });
}

// ── POSITION MODAL ─────────────────────────────────────────────
function openPosModal(jsonStr) {
  try {
    const p = JSON.parse(jsonStr);
    const pnl = p.pnl || 0;
    const el  = $('pos-modal-content');
    if (el) el.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">
        <span style="font-family:'Syne',sans-serif;font-size:18px;font-weight:900">${p.symbol||'—'}</span>
        ${p.trade_type ? `<span class="badge badge-${p.trade_type.toLowerCase()}">${p.trade_type}</span>` : ''}
        ${p.direction ? `<span class="badge ${p.direction==='LONG'?'badge-long':'badge-short'}">${p.direction}</span>` : ''}
        <button style="margin-left:auto;padding:4px 10px;background:rgba(255,23,68,.1);border:1px solid var(--red);color:var(--red);font-size:10px;border-radius:var(--radius-sm)" onclick="closePosition('${p.symbol}')">Close Position</button>
      </div>
      ${p.entry_thesis ? `<div style="background:var(--bg3);border-radius:var(--radius-sm);padding:8px 10px;margin-bottom:10px;font-size:10px;color:var(--muted2);font-style:italic;line-height:1.5">${p.entry_thesis}</div>` : ''}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div class="metric-card"><div class="metric-label">Entry Price</div><div class="metric-val" style="font-size:15px">${fmt$(p.entry_price)}</div></div>
        <div class="metric-card"><div class="metric-label">Current Price</div><div class="metric-val" style="font-size:15px">${fmt$(p.current_price)}</div></div>
        <div class="metric-card"><div class="metric-label">Unrealised P&amp;L</div><div class="metric-val ${pnl>=0?'cg':'cr'}" style="font-size:15px">${fmt$sign(pnl)}</div></div>
        <div class="metric-card"><div class="metric-label">P&amp;L %</div><div class="metric-val ${pnl>=0?'cg':'cr'}" style="font-size:15px">${fmtPct(p.pnl_pct)}</div></div>
      </div>
      ${p.conviction ? `<div style="margin-top:8px;font-size:10px;color:var(--muted2)">Conviction: <span style="color:var(--orange)">${p.conviction}/50</span></div>` : ''}
      ${p.entry_regime ? `<div style="font-size:10px;color:var(--muted2)">Entry Regime: ${p.entry_regime}</div>` : ''}
    `;
    $('pos-modal').classList.add('open');
  } catch(e) {}
}

function closePosModal() { $('pos-modal').classList.remove('open'); }

async function closePosition(symbol) {
  if (!symbol) return;
  if (!confirm('Close position in ' + symbol + '?')) return;
  closePosModal();
  try {
    await fetch('/api/close', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({symbol})});
    await fetchState();
  } catch(e) {}
}

// ── CONTROLS ───────────────────────────────────────────────────
async function killSwitch() {
  if (!confirm('🚨 KILL SWITCH — flatten ALL positions immediately. Are you sure?')) return;
  try {
    await fetch('/api/kill', {method:'POST'});
    await fetchState();
  } catch(e) {}
}

async function togglePause() {
  try {
    await fetch('/api/pause', {method:'POST'});
    await fetchState();
  } catch(e) {}
}

async function restartBot() {
  if (!confirm('Restart the bot process?')) return;
  try { await fetch('/api/restart', {method:'POST'}); } catch(e) {}
}

async function forceScan() {
  try {
    await fetch('/api/scan', {method:'POST'});
  } catch(e) {}
}

function sortPos(by, btn) {
  _posSort = by;
  $$('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderPositions();
}

// Favourites
async function addFavFromCtrl() {
  const input = $('ctrl-fav-input');
  if (!input || !input.value.trim()) return;
  const tickers = input.value.split(/[,\s]+/).map(t=>t.toUpperCase().trim()).filter(Boolean);
  const favs = new Set(S.favourites || []);
  for (const t of tickers) favs.add(t);
  S.favourites = [...favs];
  input.value = '';
  try {
    await fetch('/api/favourites', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({favourites:S.favourites})});
  } catch(e) {}
  renderFavChips();
}

async function removeFav(ticker) {
  S.favourites = (S.favourites||[]).filter(f => f !== ticker);
  renderFavChips();
  try {
    await fetch('/api/favourites', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({favourites:S.favourites})});
  } catch(e) {}
}

// ── TAB SWITCHING ──────────────────────────────────────────────
function switchTab(id, btn) {
  _currentTab = id;
  $$('.view').forEach(v => v.classList.remove('active'));
  $$('.tab').forEach(t => t.classList.remove('active'));
  const view = $('view-' + id);
  if (view) view.classList.add('active');
  if (btn) btn.classList.add('active');

  // Lazy load per tab
  if (id === 'news' && !_newsLoaded) fetchNews();
  if (id === 'intelligence') {
    fetchDimensions().then(() => { fetchICWeights(); fetchAlphaDecay(); fetchThesisPerf(); fetchGate(); });
  }
  if (id === 'portfolio')     fetchPortfolio();
  if (id === 'performance')   renderPerformance();
  if (id === 'live')          fetchSectors();
}

// ── POLL LOOP ──────────────────────────────────────────────────
async function poll() {
  await fetchState();
  // Fetch sectors on Live tab every poll
  if (_currentTab === 'live') fetchSectors();
  // Refresh news in background if on news tab
  if (_currentTab === 'news') fetchNews();
}

// Scan progress animation
let _scanAnimFrame = null;
function animateScan() {
  const fill  = $('scan-fill');
  const label = $('scan-status');
  const eta   = $('scan-eta');
  if (!fill) return;
  const next = S.next_scan_seconds || 0;
  const intv = S.scan_interval_seconds || 300;
  const pct  = S.scanning ? 100 : (intv > 0 ? Math.max(0, Math.min(100, ((intv - next) / intv) * 100)) : 0);
  fill.style.width = pct + '%';
  fill.style.background = S.scanning ? 'var(--orange)' : 'var(--muted)';
  if (label) label.textContent = S.scanning ? 'Scanning…' : (S.last_scan ? 'Last scan: ' + S.last_scan : 'Waiting…');
  if (eta)   eta.textContent   = S.scanning ? '' : (next > 0 ? 'Next in ' + next + 's' : '—');
}
setInterval(animateScan, 1000);

// ── BOOT ───────────────────────────────────────────────────────
(async () => {
  await fetchDimensions();
  await fetchState();
  await fetchSectors();
  await fetchGate();
  setInterval(poll, POLL_MS);
})();
</script>
</body>
</html>"""
