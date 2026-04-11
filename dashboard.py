DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title><> Decifer 2.0</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Syne:wght@700;800;900&display=swap');
:root{
  --bg:#0A0A0A;--bg2:#111111;--bg3:#1A1A1A;
  --border:#222;--border2:#2A2A2A;
  --orange:#FF6B00;--orange2:#FF8C33;--orange_dim:rgba(255,107,0,.08);
  --green:#00C853;--red:#FF1744;--yellow:#FFD600;
  --text:#E8E8E8;--muted:#555;--muted2:#888;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:12px;height:100vh;overflow:hidden}
body::after{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(255,107,0,.006) 2px,rgba(255,107,0,.006) 4px);pointer-events:none;z-index:9999}

/* HEADER */
.hdr{display:flex;align-items:center;justify-content:space-between;padding:0 20px;height:46px;border-bottom:1px solid var(--border);background:var(--bg2);overflow:hidden}
.logo{display:flex;align-items:center;gap:8px}
.logo-sym{font-family:'Syne',sans-serif;font-size:20px;font-weight:900;color:var(--orange);letter-spacing:-2px}
.logo-name{font-family:'Syne',sans-serif;font-size:17px;font-weight:800;color:#fff}
.logo-sub{font-size:10px;color:var(--muted2);margin-left:2px}
.hdr-right{display:flex;align-items:center;gap:8px;overflow:hidden;flex-shrink:1;min-width:0}
.pill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:10px;font-weight:600;border:1px solid;white-space:nowrap;flex-shrink:0;max-width:220px;overflow:hidden;text-overflow:ellipsis}
.pg{border-color:var(--green);color:var(--green);background:rgba(0,200,83,.08)}
.pr{border-color:var(--red);color:var(--red);background:rgba(255,23,68,.08)}
.po{border-color:var(--orange);color:var(--orange);background:var(--orange_dim)}
.dot{width:5px;height:5px;border-radius:50%;background:currentColor}
.pulse{animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(.7)}}

/* STATS */
.stats{display:grid;grid-template-columns:repeat(6,1fr);height:66px;border-bottom:1px solid var(--border);overflow:hidden}
.stats2{display:grid;grid-template-columns:repeat(6,1fr);height:58px;border-bottom:1px solid var(--border);overflow:hidden;background:var(--bg2)}
.stats2 .stat{padding:6px 14px}
.stats2 .sv{font-size:15px}
.stat{padding:8px 14px;border-right:1px solid var(--border);display:flex;flex-direction:column;justify-content:center;overflow:hidden;min-width:0}
.stat:last-child{border-right:none}
.sl{font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:3px}
.sv{font-family:'Syne',sans-serif;font-size:19px;font-weight:800;line-height:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ss{font-size:10px;color:var(--muted2);margin-top:2px}
.co{color:var(--orange)}.cg{color:var(--green)}.cr{color:var(--red)}.cw{color:#fff}

/* TABS */
.tabs{display:flex;background:var(--bg2);border-bottom:1px solid var(--border);height:34px}
.tab{padding:0 18px;font-size:11px;cursor:pointer;color:var(--muted2);border-bottom:2px solid transparent;transition:.15s;display:flex;align-items:center;font-family:'JetBrains Mono',monospace}
.tab:hover{color:var(--text)}
.tab.active{color:var(--orange);border-bottom-color:var(--orange)}

/* VIEWS */
.view{display:none;height:calc(100vh - 46px - 66px - 58px - 34px);overflow:hidden}
.view.active{display:flex}

/* ── VIEW 1: LIVE ── */
.live-grid{display:grid;grid-template-columns:210px 1fr 360px;width:100%;height:100%;overflow:hidden}
.col{display:flex;flex-direction:column;border-right:1px solid var(--border);overflow:hidden}
.col:last-child{border-right:none}
.col-title{padding:7px 12px;font-size:9px;font-weight:700;letter-spacing:2px;color:var(--muted2);text-transform:uppercase;border-bottom:1px solid var(--border);background:var(--bg);flex-shrink:0;display:flex;justify-content:space-between}
.col-body{overflow-y:auto;flex:1}

/* Regime */
.regime-wrap{padding:10px}
.regime-box{border-radius:5px;padding:10px;border:1px solid}
.bull{border-color:var(--green);background:rgba(0,200,83,.07)}
.bear{border-color:var(--red);background:rgba(255,23,68,.07)}
.choppy{border-color:var(--yellow);background:rgba(255,214,0,.07)}
.panic{border-color:var(--red);background:rgba(255,23,68,.2);animation:flash 1s infinite}
.unknown{border-color:var(--muted);background:transparent}
@keyframes flash{0%,100%{opacity:1}50%{opacity:.5}}
.rl{font-family:'Syne',sans-serif;font-size:13px;font-weight:800;margin-bottom:3px}
.rm{font-size:10px;color:var(--muted2)}
.session-row{margin:0 10px 8px;padding:5px 9px;background:var(--bg3);border-radius:4px;font-size:10px;display:flex;justify-content:space-between;color:var(--muted2)}

/* Buttons */
.kill-btn{margin:8px 10px 4px;padding:9px;background:rgba(255,23,68,.1);border:1px solid var(--red);border-radius:5px;color:var(--red);font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;cursor:pointer;width:calc(100% - 20px);letter-spacing:1px;transition:.15s}
.kill-btn:hover{background:rgba(255,23,68,.25)}
.pause-btn{margin:0 10px 8px;padding:7px;background:var(--orange_dim);border:1px solid var(--orange);border-radius:5px;color:var(--orange);font-family:'JetBrains Mono',monospace;font-size:10px;cursor:pointer;width:calc(100% - 20px);transition:.15s}
.pause-btn:hover{background:rgba(255,107,0,.18)}

/* Scan progress */
.scan-wrap{padding:7px 12px;border-bottom:1px solid var(--border);flex-shrink:0}
.scan-bg{height:2px;background:var(--border2);border-radius:1px;overflow:hidden}
.scan-fill{height:100%;background:var(--orange);border-radius:1px;transition:width 1s linear}
.scan-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--muted2);margin-top:3px}

/* Logs */
.log-row{display:grid;grid-template-columns:58px 72px 1fr;gap:6px;padding:5px 12px;border-bottom:1px solid rgba(34,34,34,.5);animation:fi .2s}
@keyframes fi{from{opacity:0;transform:translateY(-2px)}to{opacity:1}}
.lt{color:var(--muted2);font-size:10px}
.lk{font-size:9px;font-weight:700;padding:2px 5px;border-radius:3px;text-align:center}
.lk-TRADE{background:rgba(255,107,0,.15);color:var(--orange)}
.lk-SIGNAL{background:rgba(0,200,83,.12);color:var(--green)}
.lk-ANALYSIS{background:rgba(255,214,0,.1);color:var(--yellow)}
.lk-ERROR{background:rgba(255,23,68,.12);color:var(--red)}
.lk-INFO{background:rgba(85,85,85,.2);color:var(--muted2)}
.lk-RISK{background:rgba(255,23,68,.08);color:var(--red)}
.lk-SCAN{background:var(--orange_dim);color:var(--orange2)}
.lm{color:var(--text);line-height:1.5;font-size:11px}

/* AI panel */
.ai-panel{border-top:1px solid var(--border);padding:6px 12px;background:var(--bg2);flex:0 1 auto;min-height:60px}
.ai-label{font-size:9px;color:var(--orange);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:4px;display:flex;align-items:center;gap:5px}
.ai-d{width:5px;height:5px;background:var(--orange);border-radius:50%;animation:pulse 2s infinite}
.ai-box{background:var(--bg3);border:1px solid var(--border2);border-left:2px solid var(--orange);border-radius:4px;padding:6px 10px;font-size:11px;color:var(--text);line-height:1.65;max-height:200px;overflow-y:auto;white-space:pre-wrap;resize:vertical}

/* Positions */
.pos-hdr{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.pos-sym{font-weight:700;font-size:13px;font-family:'Syne',sans-serif}
.pos-pnl{font-weight:600;font-size:12px}
.pos-bar-bg{height:3px;background:var(--border2);border-radius:2px;overflow:hidden;margin-bottom:4px}
.pos-bar{height:100%;border-radius:2px}
.pos-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--muted2)}

/* Trade rows */
.trade-row{display:flex;justify-content:space-between;align-items:center;padding:7px 12px;border-bottom:1px solid var(--border)}
.ts{font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px}
.tb{background:rgba(0,200,83,.14);color:var(--green)}
.ts2{background:rgba(255,23,68,.14);color:var(--red)}
.empty{padding:20px;color:var(--muted2);font-size:11px;text-align:center}

/* ── VIEW 2: TRADE HISTORY ── */
.hist-view{flex-direction:column}
.hist-filters{display:flex;gap:8px;padding:9px 14px;border-bottom:1px solid var(--border);background:var(--bg2);flex-shrink:0;flex-wrap:wrap}
.f-btn{padding:3px 10px;border-radius:3px;font-size:10px;cursor:pointer;border:1px solid var(--border2);background:transparent;color:var(--muted2);font-family:'JetBrains Mono',monospace;transition:.15s}
.f-btn.active,.f-btn:hover{border-color:var(--orange);color:var(--orange);background:var(--orange_dim)}
.hist-table{overflow-y:auto;flex:1}
.th{display:grid;grid-template-columns:90px 70px 55px 55px 85px 85px 80px 60px 1fr 40px;padding:6px 14px;background:var(--bg3);border-bottom:1px solid var(--border);font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;position:sticky;top:0}
.tr{display:grid;grid-template-columns:90px 70px 55px 55px 85px 85px 80px 60px 1fr 40px;padding:6px 14px;border-bottom:1px solid rgba(34,34,34,.5);font-size:11px}
.tr:hover{background:var(--bg3)}
.tr.tr-clickable{cursor:pointer}
.tr.tr-clickable:hover .expand-arrow{color:var(--orange)}
.expand-arrow{color:var(--muted);font-size:9px;transition:.2s;display:inline-block}
.expand-arrow.open{transform:rotate(90deg)}
.trade-explain{display:none;padding:10px 14px 12px 14px;background:rgba(255,107,0,.03);border-bottom:1px solid rgba(34,34,34,.5);border-left:3px solid var(--orange);margin:0;font-size:11px;line-height:1.6;color:var(--muted2)}
.trade-explain.open{display:block}
.trade-explain .explain-title{color:var(--orange);font-size:10px;letter-spacing:.5px;text-transform:uppercase;margin-bottom:6px;font-weight:600}
.trade-explain .explain-body{color:var(--text);opacity:.85}
.trade-explain .explain-outcome{margin-top:8px;padding-top:8px;border-top:1px solid rgba(34,34,34,.5);font-size:10px;color:var(--muted2)}
.pp{color:var(--green)}.pn{color:var(--red)}

/* ── VIEW 3: GROWTH ── */
.growth-view{flex-direction:column;overflow-y:auto;padding:14px;gap:12px;height:calc(100vh - 46px - 66px - 34px)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:14px}
.card-title{font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:10px}
.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:4px}
.tf-btn{padding:3px 10px;border-radius:3px;font-size:10px;cursor:pointer;border:1px solid var(--border2);background:transparent;color:var(--muted2);font-family:'JetBrains Mono',monospace;transition:.15s}
.tf-btn.active,.tf-btn:hover{border-color:var(--orange);color:var(--orange);background:var(--orange_dim)}
.metric{background:var(--bg2);border:1px solid var(--border);border-radius:5px;padding:10px 12px}
.metric-label{font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;margin-bottom:4px}
.metric-val{font-family:'Syne',sans-serif;font-size:20px;font-weight:800}
canvas{display:block;width:100% !important}

/* ── VIEW 4: AGENTS ── */
.agents-view{flex-direction:column;overflow-y:auto;padding:14px;gap:12px}
.agent-card{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--orange);border-radius:6px;padding:12px}
.agent-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.agent-name{font-family:'Syne',sans-serif;font-size:13px;font-weight:800;color:var(--orange)}
.agent-accuracy{font-size:11px;font-weight:700}
.agent-last{font-size:11px;color:var(--muted2);line-height:1.6;max-height:80px;overflow-y:auto;white-space:pre-wrap}

/* ── VIEW 5: RISK ── */
.risk-view{flex-direction:column;overflow-y:auto;padding:14px;gap:12px}
.risk-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.risk-meter{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:12px}
.rm-label{font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;margin-bottom:8px}
.rm-bar-bg{height:8px;background:var(--border2);border-radius:4px;overflow:hidden;margin-bottom:4px}
.rm-bar{height:100%;border-radius:4px;transition:width .5s}
.rm-meta{display:flex;justify-content:space-between;font-size:10px;color:var(--muted2)}

/* ── VIEW 7: NEWS ── */
.news-view{flex-direction:column;height:calc(100vh - 46px - 66px - 34px);overflow:hidden}
.news-hdr{flex-shrink:0;display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 14px;border-bottom:1px solid var(--border);background:var(--bg2)}
.news-feed{flex:1;overflow-y:auto;padding:12px 14px;min-height:0}
.news-badge{flex-shrink:0;padding:3px 8px;border-radius:3px;font-size:9px;font-weight:700;letter-spacing:.5px;text-align:center}
.badge-bullish{background:rgba(0,200,83,.15);color:var(--green);border:1px solid rgba(0,200,83,.3)}
.badge-bearish{background:rgba(255,23,68,.12);color:var(--red);border:1px solid rgba(255,23,68,.3)}
.badge-neutral{background:rgba(85,85,85,.15);color:var(--muted2);border:1px solid var(--border2)}
/* Hero card */
.news-hero{position:relative;border-radius:8px;overflow:hidden;margin-bottom:14px;cursor:pointer;border:1px solid var(--border);transition:border-color .2s,transform .2s}
.news-hero:hover{border-color:var(--orange);transform:translateY(-1px)}
.news-hero-bg{width:100%;height:200px;display:flex;align-items:flex-end;padding:20px 18px 14px}
.news-hero-bg.bull{background:linear-gradient(135deg,#0a2e1a 0%,#0d3b22 50%,#051a0f 100%)}
.news-hero-bg.bear{background:linear-gradient(135deg,#2e0a0a 0%,#3b0d0d 50%,#1a0505 100%)}
.news-hero-bg.neut{background:linear-gradient(135deg,#141414 0%,#1c1c1c 50%,#0e0e0e 100%)}
.news-hero-inner{width:100%}
.news-hero-tag{font-size:9px;letter-spacing:2px;font-weight:700;text-transform:uppercase;margin-bottom:8px;display:flex;align-items:center;gap:8px}
.news-hero-macro{color:var(--orange)}
.news-hero-hl{font-family:'Syne',sans-serif;font-size:16px;font-weight:900;color:#fff;line-height:1.3;margin-bottom:8px}
.news-hero-cat{font-size:11px;color:rgba(255,255,255,.7);line-height:1.5;margin-bottom:10px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.news-hero-foot{display:flex;align-items:center;gap:10px}
.news-hero-sym{font-size:10px;color:var(--orange);font-weight:700}
.news-hero-age{font-size:9px;color:rgba(255,255,255,.45)}
.news-hero-score{font-size:9px;color:rgba(255,255,255,.5)}
/* 3-col mosaic grid — hero spans 2 cols */
.news-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.news-card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;overflow:hidden;cursor:pointer;transition:border-color .15s,transform .15s,box-shadow .15s;display:flex;flex-direction:column;text-decoration:none;color:inherit}
.news-card:hover{border-color:var(--orange);transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.4)}
.news-card-hero{grid-column:span 2;flex-direction:row;border-left:3px solid var(--orange)}
.news-card-img{width:100%;aspect-ratio:3/2;overflow:hidden;flex-shrink:0;background:var(--bg3)}
.news-card-hero .news-card-img{width:45%;aspect-ratio:3/2;flex-shrink:0}
.news-card-img img{width:100%;height:100%;object-fit:cover;display:block}
.news-card-img-ph{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:800;font-family:'Syne',sans-serif;letter-spacing:-1px}
.news-card-hero .news-card-img-ph{font-size:36px}
.news-card-top{padding:10px 12px;flex:1;display:flex;flex-direction:column;gap:5px;min-width:0}
.news-card-hero .news-card-top{padding:16px 18px;justify-content:space-between}
.news-card-hero-label{font-size:9px;font-weight:700;letter-spacing:1.2px;color:var(--orange);text-transform:uppercase;margin-bottom:4px}
.news-card-tag{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.news-card-sym{font-size:10px;color:var(--orange);font-weight:700}
.news-card-hl{font-size:11px;color:var(--text);line-height:1.45;flex:1;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.news-card-hero .news-card-hl{font-size:16px;font-weight:800;font-family:'Syne',sans-serif;-webkit-line-clamp:4;letter-spacing:-.2px}
.news-card-foot{display:flex;align-items:center;justify-content:space-between;padding:6px 12px;border-top:1px solid var(--border);background:var(--bg3);flex-shrink:0}
.news-card-age{font-size:9px;color:var(--muted2)}
.news-card-score{font-size:9px;color:var(--muted2)}
.news-card-catalyst{font-size:10px;color:var(--orange);font-style:italic;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;margin-top:2px}
.news-fetch-btn{background:rgba(255,107,0,.12);border:1px solid rgba(255,107,0,.35);color:var(--orange);padding:4px 10px;border-radius:4px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;cursor:pointer;transition:.15s;white-space:nowrap}
.news-fetch-btn:hover{background:rgba(255,107,0,.22)}
.news-fetch-btn:disabled{opacity:.45;cursor:default}
/* Article drawer */
.news-drawer{position:fixed;top:0;right:-54%;width:54%;height:100vh;background:var(--bg2);border-left:1px solid var(--border);z-index:5000;display:flex;flex-direction:column;transition:right .3s cubic-bezier(.4,0,.2,1)}
.news-drawer.open{right:0}
.news-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:4999;display:none}
.news-overlay.open{display:block}
.news-drawer-hdr{display:flex;align-items:center;gap:10px;padding:12px 16px;border-bottom:1px solid var(--border);flex-shrink:0}
.news-drawer-sym{font-size:11px;color:var(--orange);font-weight:700;min-width:48px}
.news-drawer-title{flex:1;font-size:11px;color:var(--muted2);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.news-drawer-close{background:none;border:none;color:var(--muted2);font-size:18px;cursor:pointer;padding:0 2px;line-height:1}
.news-drawer-close:hover{color:var(--text)}
.news-drawer-body{flex-shrink:0;overflow-y:auto;max-height:220px;padding:20px}
.news-reader-wrap{flex:1;display:flex;flex-direction:column;border-top:1px solid var(--border);min-height:0;position:relative}
.news-reader-loading{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--muted2);background:var(--bg2);z-index:1;pointer-events:none}
.news-reader-iframe{flex:1;width:100%;border:none;background:var(--bg2)}
.news-drawer-badge-row{display:flex;align-items:center;gap:8px;margin-bottom:14px}
.news-drawer-hl{font-family:'Syne',sans-serif;font-size:18px;font-weight:900;color:var(--text);line-height:1.3;margin-bottom:12px}
.news-drawer-meta{font-size:10px;color:var(--muted2);margin-bottom:14px;display:flex;gap:14px}
.news-drawer-catalyst{background:var(--bg3);border:1px solid var(--border);border-left:3px solid var(--orange);border-radius:4px;padding:10px 14px;margin-bottom:14px}
.news-drawer-catalyst-lbl{font-size:9px;letter-spacing:1.5px;text-transform:uppercase;color:var(--orange);margin-bottom:5px}
.news-drawer-catalyst-txt{font-size:12px;color:var(--text);line-height:1.6}

/* ── AGENT CONVERSATION ── */
.convo-panel{border-top:1px solid var(--border);background:var(--bg2);flex:1;min-height:120px;overflow:hidden;display:flex;flex-direction:column}
.convo-panel.collapsed{flex:0 0 auto;min-height:auto;display:block}
.convo-toggle{padding:5px 12px;font-size:9px;letter-spacing:1.5px;color:var(--orange);text-transform:uppercase;cursor:pointer;display:flex;align-items:center;gap:5px;border-bottom:1px solid var(--border);background:var(--bg);flex-shrink:0}
.convo-toggle:hover{background:var(--bg3)}

/* ── TRADE CARD ── */
.trade-card-panel{flex:0 0 auto;border-bottom:1px solid var(--border);background:var(--bg);display:flex;flex-direction:column}
.trade-card-body{padding:10px 14px;overflow-y:auto;max-height:260px}
.tc-headline{font-family:'Syne',sans-serif;font-size:13px;font-weight:800;line-height:1.3;margin-bottom:10px}
.tc-ticker{color:#fff;font-size:15px}
.tc-sep{color:var(--muted2)}
.tc-company{color:var(--text)}
.tc-alloc{color:var(--muted2);font-size:12px}
.tc-dir-buy{color:var(--green);font-weight:800;margin-left:6px}
.tc-dir-sell{color:var(--red);font-weight:800;margin-left:6px}
.tc-row{font-size:11px;line-height:1.65;margin-bottom:5px;color:var(--text)}
.tc-label{color:var(--text);font-weight:700}
.tc-val{color:#aaa}
.tc-returns{display:flex;flex-wrap:wrap;gap:12px;font-size:11px;margin-top:4px}
.tc-ret-item .tc-label{color:var(--muted2)}
.tc-ret-pos{color:var(--green);font-weight:700}
.tc-ret-neg{color:var(--red);font-weight:700}
.tc-footer{font-size:9px;color:var(--muted);margin-top:8px;letter-spacing:.5px}
.convo-body{display:block;overflow-y:auto;flex:1;min-height:0}
.convo-body.hidden{display:none}
.convo-msg{padding:8px 12px;border-bottom:1px solid rgba(34,34,34,.4);animation:fi .2s}
.convo-agent{font-size:10px;font-weight:700;color:var(--orange);margin-bottom:2px;display:flex;justify-content:space-between}
.convo-role{font-size:9px;color:var(--muted);font-weight:400}
.convo-time{font-size:9px;color:var(--muted2)}
.convo-text{font-size:11px;color:var(--text);line-height:1.6;white-space:pre-wrap;max-height:60px;overflow-y:auto}
.convo-verdict{background:var(--bg3);border:1px solid var(--border2);border-left:3px solid var(--orange);border-radius:0 4px 4px 0;padding:8px 12px;margin:4px 0}

/* Agent conversation in Agents view */
.agent-convo-full{margin-top:12px}
.agent-convo-card{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--orange);border-radius:6px;padding:12px;margin-bottom:10px}
.agent-convo-card .agent-name{font-family:'Syne',sans-serif;font-size:13px;font-weight:800;color:var(--orange);margin-bottom:2px}
.agent-convo-card .agent-role{font-size:10px;color:var(--muted2);margin-bottom:8px}
.agent-convo-card .agent-output{font-size:11px;color:var(--text);line-height:1.65;white-space:pre-wrap;max-height:200px;overflow-y:auto}
.indicator-tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:9px;font-weight:700;margin:1px 2px}
.tag-bull{background:rgba(0,200,83,.12);color:var(--green)}
.tag-bear{background:rgba(255,23,68,.1);color:var(--red)}
.tag-neutral{background:rgba(85,85,85,.12);color:var(--muted2)}
.tag-squeeze{background:rgba(255,214,0,.1);color:var(--yellow)}

/* ── VIEW 6: SETTINGS ── */
.settings-view{flex-direction:column;overflow-y:auto;padding:14px;gap:12px}
.setting-card{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:14px}
.setting-title{font-family:'Syne',sans-serif;font-size:13px;font-weight:800;color:var(--orange);margin-bottom:10px;border-bottom:1px solid var(--border);padding-bottom:8px}
.setting-row{display:flex;justify-content:space-between;align-items:center;padding:7px 0;border-bottom:1px solid rgba(34,34,34,.4)}
.setting-row:last-child{border-bottom:none}
.setting-label{font-size:11px;color:var(--muted2)}
.setting-val{font-size:11px;color:var(--text);font-weight:600}
.setting-input{background:var(--bg3);border:1px solid var(--border2);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:11px;padding:3px 8px;border-radius:3px;width:100px}
.setting-input:focus{outline:none;border-color:var(--orange)}
.fav-tag{display:inline-flex;align-items:center;gap:5px;padding:3px 8px;background:var(--orange_dim);border:1px solid var(--orange);border-radius:3px;font-size:11px;color:var(--orange);margin:3px}
.fav-tag span{cursor:pointer;color:var(--muted2);font-size:13px;line-height:1}
.fav-tag span:hover{color:var(--red)}
.fav-input{background:var(--bg3);border:1px solid var(--border2);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:11px;padding:5px 10px;border-radius:3px;width:120px;text-transform:uppercase}
.fav-input:focus{outline:none;border-color:var(--orange)}
.fav-input::placeholder{color:var(--muted);text-transform:none}
.apply-btn{padding:8px 16px;background:var(--orange_dim);border:1px solid var(--orange);border-radius:4px;color:var(--orange);font-family:'JetBrains Mono',monospace;font-size:11px;cursor:pointer;transition:.15s}
.apply-btn:hover{background:rgba(255,107,0,.2)}

::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}

/* Position Detail Modal */
.pos-modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:10000;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.pos-modal-overlay.active{display:flex}
.pos-modal{background:var(--bg2);border:1px solid var(--orange);border-radius:8px;width:480px;max-width:90vw;max-height:85vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.6)}
.pos-modal-hdr{display:flex;justify-content:space-between;align-items:center;padding:14px 18px;border-bottom:1px solid var(--border)}
.pos-modal-hdr h3{font-family:'Syne',sans-serif;font-size:18px;font-weight:800;color:#fff;display:flex;align-items:center;gap:8px}
.pos-modal-close{background:none;border:none;color:var(--muted2);font-size:20px;cursor:pointer;padding:4px 8px;border-radius:4px;transition:.15s}
.pos-modal-close:hover{color:var(--red);background:rgba(255,23,68,.1)}
.pos-modal-body{padding:16px 18px}
.pos-modal-row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:11px}
.pos-modal-row:last-child{border-bottom:none}
.pos-modal-label{color:var(--muted2);text-transform:uppercase;letter-spacing:1px;font-size:9px}
.pos-modal-val{font-weight:600;color:var(--text)}
.pos-modal-section{margin-top:14px;padding-top:10px;border-top:1px solid var(--border2)}
.pos-modal-section h4{font-size:10px;text-transform:uppercase;letter-spacing:1.5px;color:var(--orange);margin-bottom:8px;font-family:'Syne',sans-serif}
.pos-modal-reasoning{font-size:11px;color:var(--text);line-height:1.7;white-space:pre-wrap;background:var(--bg3);border:1px solid var(--border2);border-left:2px solid var(--orange);border-radius:4px;padding:10px 12px;max-height:200px;overflow-y:auto}
.pos-card{padding:9px 12px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .15s}
.pos-card:hover{background:rgba(255,107,0,.04)}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
</head>
<body>

<!-- TWS DISCONNECTED BANNER -->
<div id="tws-banner" style="display:none;position:fixed;top:0;left:0;right:0;z-index:9999;background:#1a0a00;border-bottom:2px solid var(--orange);padding:10px 20px;display:none;align-items:center;gap:12px">
  <span style="color:var(--orange);font-size:12px;font-weight:700">⚠ TWS DISCONNECTED</span>
  <span style="color:var(--muted2);font-size:11px">Dashboard is live but trading is paused. Start TWS and reconnect.</span>
  <button id="tws-reconnect-btn" onclick="twsReconnect()" style="margin-left:auto;background:var(--orange);color:#000;border:none;padding:5px 14px;border-radius:4px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace;cursor:pointer">↺ Reconnect</button>
</div>

<!-- HEADER -->
<div class="hdr">
  <div class="logo">
    <span class="logo-sym">&lt;&gt;</span>
    <span class="logo-name">Decifer <span style="color:var(--orange);font-size:13px">2.0</span></span>
    <span class="logo-sub">Autonomous AI Trading</span>
    <span style="font-size:10px;color:var(--orange);font-weight:700;margin-left:6px;opacity:0.85;">by AMIT CHOPRA</span>
  </div>
  <div class="hdr-right">
    <div class="pill" id="bot-pill"><div class="dot pulse"></div><span id="bot-status">Connecting...</span></div>
    <div class="pill po" id="regime-pill">REGIME: —</div>
    <span style="font-size:10px;color:var(--muted2)" id="upd-time">—</span>
  </div>
</div>

<!-- STATS -->
<div class="stats">
  <div class="stat"><div class="sl">Portfolio Value</div><div class="sv co" id="s-val">—</div><div class="ss" id="s-acc">Paper</div></div>
  <div class="stat"><div class="sl">Day P&amp;L</div><div class="sv" id="s-pnl">—</div><div class="ss" id="s-pnlp">—</div></div>
  <div class="stat"><div class="sl">Session</div><div class="sv co" id="s-session">—</div><div class="ss" id="s-next">—</div></div>
  <div class="stat"><div class="sl">Scans Run</div><div class="sv co" id="s-scans">0</div><div class="ss" id="s-last">Never</div></div>
  <div class="stat"><div class="sl">Open Positions</div><div class="sv co" id="s-pos">0</div><div class="ss">bot-managed</div></div>
  <div class="stat"><div class="sl">Trades</div><div class="sv co"><span id="s-trades">0</span></div><div class="ss" id="s-wr">—</div></div>
</div>

<!-- STATS ROW 2: KPIs -->
<div class="stats2">
  <div class="stat"><div class="sl">Available Cash</div><div class="sv cg" id="s-cash">—</div><div class="ss" id="s-cash-pct">—</div></div>
  <div class="stat"><div class="sl">Buying Power</div><div class="sv cw" id="s-bp">—</div><div class="ss">IBKR margin</div></div>
  <div class="stat"><div class="sl">Unrealized P&amp;L</div><div class="sv" id="s-upnl">—</div><div class="ss" id="s-upnl-sub">open positions</div></div>
  <div class="stat"><div class="sl">Realized P&amp;L</div><div class="sv" id="s-rpnl">—</div><div class="ss">closed today</div></div>
  <div class="stat"><div class="sl">Margin Used</div><div class="sv cw" id="s-margin">—</div><div class="ss" id="s-margin-pct">—</div></div>
  <div class="stat"><div class="sl">Excess Liquidity</div><div class="sv cw" id="s-excess">—</div><div class="ss">safety buffer</div></div>
</div>

<!-- TABS -->
<div class="tabs">
  <div class="tab active" onclick="switchTab('live',this)">⚡ Live</div>
  <div class="tab" onclick="switchTab('orders',this)">📝 Orders</div>
  <div class="tab" onclick="switchTab('history',this)">📋 Closed Trades</div>
  <div class="tab" onclick="switchTab('growth',this)">📈 Account Growth</div>
  <div class="tab" onclick="switchTab('agents',this)">🧠 Agents</div>
  <div class="tab" onclick="switchTab('risk',this)">🛡 Risk</div>
  <div class="tab" onclick="switchTab('news',this)">📰 News</div>
  <div class="tab" onclick="switchTab('portfolio',this)">🏦 Portfolio</div>
  <div class="tab" onclick="switchTab('settings',this)">⚙️ Settings</div>
  <div class="tab" onclick="switchTab('alpha',this)">📉 Alpha Decay</div>
</div>

<!-- VIEW 1: LIVE -->
<div class="view active" id="view-live">
  <div class="live-grid">

    <!-- LEFT: Controls -->
    <div class="col">
      <div class="col-title">Controls</div>
      <div class="col-body">
        <div class="regime-wrap">
          <div class="regime-box unknown" id="regime-box">
            <div class="rl" id="regime-label">DETECTING...</div>
            <div class="rm" id="regime-meta">VIX: — | SPY: —</div>
          </div>
        </div>
        <div class="session-row">
          <span id="session-name">—</span>
          <span id="session-time">—</span>
        </div>
        <button class="kill-btn" onclick="killSwitch()">🚨 KILL SWITCH</button>
        <button class="pause-btn" id="pause-btn" onclick="togglePause()">⏸ PAUSE BOT</button>
      <button class="pause-btn" style="border-color:#00C853;color:#00C853;background:rgba(0,200,83,.08)" onclick="restartBot()">🔄 RESTART BOT</button>
      <button class="pause-btn" style="border-color:#FF6B00;color:#FF6B00;background:rgba(255,107,0,.08)" onclick="forceScan()">⚡ FORCE SCAN</button>
        <div style="padding:8px 10px;border-top:1px solid var(--border);margin-top:4px">
          <div style="font-size:9px;color:var(--muted2);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">Agent Agreement</div>
          <div style="font-size:11px;color:var(--muted2)">Required: <span style="color:var(--orange);font-weight:700" id="agents-req">4/6</span></div>
          <div style="font-size:11px;color:var(--muted2);margin-top:3px">Last scan: <span style="color:var(--text)" id="last-agree">—</span></div>
        </div>
        <div style="padding:8px 10px;border-top:1px solid var(--border)">
          <div style="font-size:9px;color:var(--muted2);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">Daily Risk Budget</div>
          <div class="rm-bar-bg"><div class="rm-bar" id="risk-bar" style="width:0%;background:var(--green)"></div></div>
          <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted2);margin-top:3px">
            <span id="risk-used">$0 used</span><span id="risk-left">$0 left</span>
          </div>
        </div>
        <div style="padding:8px 10px;border-top:1px solid var(--border)">
          <div style="font-size:9px;color:var(--muted2);letter-spacing:1px;text-transform:uppercase;margin-bottom:6px">Directional Skew</div>
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <div style="flex:1;height:8px;background:var(--bg);border-radius:4px;overflow:hidden;position:relative;border:1px solid var(--border2)">
              <div style="position:absolute;left:50%;width:1px;height:100%;background:var(--muted)"></div>
              <div id="skew-bar" style="position:absolute;top:0;height:100%;background:var(--orange);border-radius:4px;transition:all .3s;left:50%;width:0%"></div>
            </div>
            <span id="skew-val" style="font-size:12px;font-weight:700;color:var(--orange);min-width:40px;text-align:right">0.0</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted)">
            <span>SHORT</span>
            <span id="skew-detail">—</span>
            <span>LONG</span>
          </div>
          <div id="skew-alert" style="font-size:9px;color:var(--red);margin-top:3px;display:none"></div>
        </div>
      </div>
    </div>

    <!-- CENTRE: Last Decision (top) + Activity Log (bottom) -->
    <div class="col" style="border-right:1px solid var(--border);display:flex;flex-direction:column">

      <!-- OPUS MARKET VIEW — always visible -->
      <div class="trade-card-panel" id="opus-view-panel">
        <div class="col-title" style="flex-shrink:0">
          <span>Opus Market View</span>
          <span id="opus-view-ts" style="color:var(--muted2);font-size:9px">—</span>
        </div>
        <div id="opus-view-body" style="padding:8px 12px">
          <div style="color:var(--muted2);font-size:11px">Waiting for agents to run…</div>
        </div>
      </div>

      <!-- SCAN PROGRESS -->
      <div class="scan-wrap" style="flex-shrink:0">
        <div class="scan-bg"><div class="scan-fill" id="scan-fill" style="width:0%"></div></div>
        <div class="scan-meta"><span id="scan-status">Waiting for first scan...</span><span id="scan-eta">—</span></div>
      </div>

      <!-- ACTIVITY LOG — at the bottom, fills remaining space -->
      <div class="col-title" style="flex-shrink:0">
        <span>Activity Log</span>
        <span id="log-count" style="color:var(--muted2)">0 events</span>
      </div>
      <div class="col-body" id="log-area" style="flex:1;min-height:0;overflow-y:auto"></div>

    </div>

    <!-- RIGHT: Positions + Trades -->
    <div class="col">
      <div class="col-title">Open Positions <span style="margin-left:auto;display:flex;gap:6px"><button class="pos-sort-btn" id="pos-sort-recency" onclick="sortPositions('recency')" style="background:none;border:none;color:var(--orange);cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:9px;padding:0">Recent</button><button class="pos-sort-btn" id="pos-sort-size" onclick="sortPositions('size')" style="background:none;border:none;color:var(--muted2);cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:9px;padding:0">Size</button><button class="pos-sort-btn" id="pos-sort-pnl" onclick="sortPositions('pnl')" style="background:none;border:none;color:var(--muted2);cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:9px;padding:0">P&amp;L</button></span></div>
      <div style="flex:0 0 auto;overflow-y:auto;max-height:50%;border-bottom:1px solid var(--border)" id="pos-list">
        <div class="empty">No open positions</div>
      </div>
      <div class="col-title" style="flex-shrink:0">Today's Results</div>
      <div class="col-body" id="trades-list">
        <div class="empty">No closed trades today</div>
      </div>
    </div>

  </div>
</div>

<!-- VIEW 1B: ORDERS -->
<div class="view hist-view" id="view-orders">
  <div class="hist-filters">
    <span style="font-size:10px;color:var(--muted2);margin-right:4px">Filter:</span>
    <button class="f-btn active" onclick="filterOrders('all',this)">All</button>
    <button class="f-btn" onclick="filterOrders('submitted',this)">Pending</button>
    <button class="f-btn" onclick="filterOrders('filled',this)">Filled</button>
    <button class="f-btn" onclick="filterOrders('cancelled',this)">Cancelled</button>
    <button class="f-btn" onclick="filterOrders('stocks',this)">Stocks</button>
    <button class="f-btn" onclick="filterOrders('options',this)">Options</button>
  </div>
  <div class="hist-table">
    <div class="th"><span>Time</span><span>Symbol</span><span>Side</span><span>Type</span><span>Qty</span><span>Price</span><span>Fill Px</span><span>Status</span><span>Role</span><span></span></div>
    <div id="orders-body"><div class="empty">No orders logged yet. Orders appear here when the bot places them.</div></div>
  </div>
</div>

<!-- VIEW 2: CLOSED TRADES -->
<div class="view hist-view" id="view-history">
  <div class="hist-filters">
    <span style="font-size:10px;color:var(--muted2);margin-right:4px">Filter:</span>
    <button class="f-btn active" onclick="filterTrades('all',this)">All</button>
    <button class="f-btn" onclick="filterTrades('wins',this)">Wins</button>
    <button class="f-btn" onclick="filterTrades('losses',this)">Losses</button>
    <button class="f-btn" onclick="filterTrades('stocks',this)">Stocks</button>
    <button class="f-btn" onclick="filterTrades('options',this)">Options</button>
    <button class="f-btn" onclick="filterTrades('fx',this)">FX</button>
  </div>
  <div class="hist-table">
    <div class="th"><span>Time</span><span>Symbol</span><span>Side</span><span>Size</span><span>Entry</span><span>Exit</span><span>P&L</span><span>Hold</span><span>Reason</span></div>
    <div id="hist-body"><div class="empty">No closed trades yet. Trades appear here after positions are closed.</div></div>
  </div>
</div>

<!-- VIEW 3: GROWTH -->
<div class="view growth-view" id="view-growth">

  <!-- Metric cards -->
  <div class="metric-grid">
    <div class="metric"><div class="metric-label">Total P&L</div><div class="metric-val co" id="g-pnl">$0</div></div>
    <div class="metric"><div class="metric-label">Win Rate</div><div class="metric-val" id="g-wr">0%</div></div>
    <div class="metric"><div class="metric-label">Profit Factor</div><div class="metric-val" id="g-pf">0</div></div>
    <div class="metric"><div class="metric-label">Avg Win / Loss Ratio</div><div class="metric-val" id="g-rl">—</div></div>
    <div class="metric"><div class="metric-label">Total Trades</div><div class="metric-val co" id="g-total">0</div></div>
    <div class="metric"><div class="metric-label">Best Trade</div><div class="metric-val cg" id="g-best">—</div></div>
    <div class="metric"><div class="metric-label">Worst Trade</div><div class="metric-val cr" id="g-worst">—</div></div>
    <div class="metric"><div class="metric-label">Expectancy</div><div class="metric-val" id="g-exp">—</div></div>
  </div>

  <!-- Equity Curve -->
  <div class="card" id="equity-card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div class="card-title" style="margin-bottom:0">Equity Curve</div>
      <div style="display:flex;gap:6px">
        <button class="tf-btn active" onclick="setEquityTF('1D',this)">1D</button>
        <button class="tf-btn" onclick="setEquityTF('1W',this)">1W</button>
        <button class="tf-btn" onclick="setEquityTF('1M',this)">1M</button>
        <button class="tf-btn" onclick="setEquityTF('MTD',this)">MTD</button>
        <button class="tf-btn" onclick="setEquityTF('YTD',this)">YTD</button>
        <button class="tf-btn" onclick="setEquityTF('ALL',this)">ALL</button>
      </div>
    </div>
    <div style="position:relative;height:180px"><canvas id="equity-chart"></canvas></div>
  </div>

  <!-- Daily P&L -->
  <div class="card" id="daily-card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div class="card-title" style="margin-bottom:0">Daily P&L</div>
      <div style="display:flex;gap:6px">
        <button class="tf-btn active" id="dpnl-1w" onclick="setDailyTF('1W',this)">1W</button>
        <button class="tf-btn" id="dpnl-1m" onclick="setDailyTF('1M',this)">1M</button>
        <button class="tf-btn" id="dpnl-mtd" onclick="setDailyTF('MTD',this)">MTD</button>
        <button class="tf-btn" id="dpnl-ytd" onclick="setDailyTF('YTD',this)">YTD</button>
        <button class="tf-btn" id="dpnl-all" onclick="setDailyTF('ALL',this)">ALL</button>
      </div>
    </div>
    <div style="position:relative;height:150px"><canvas id="daily-chart"></canvas></div>
  </div>

</div>

<!-- VIEW 4: AGENTS -->
<div class="view agents-view" id="view-agents">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase">Agent Live Conversation</div>
    <div style="font-size:10px;color:var(--muted2)" id="agents-scan-time">Last scan: —</div>
  </div>
  <div id="agents-convo-full">
    <div class="empty" style="padding:30px">Agent conversation appears here after the first scan completes.</div>
  </div>
  <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
    <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;margin-bottom:10px">Raw Agent Outputs</div>
    <div id="agents-grid">
      <div class="empty">Raw outputs appear here after the first scan.</div>
    </div>
  </div>
</div>

<!-- VIEW 5: RISK -->
<div class="view risk-view" id="view-risk">
  <div class="risk-grid">
    <div class="risk-meter">
      <div class="rm-label">Daily Loss Budget Used</div>
      <div class="rm-bar-bg"><div class="rm-bar" id="r-daily-bar" style="width:0%;background:var(--green)"></div></div>
      <div class="rm-meta"><span id="r-daily-used">$0 of $0</span><span id="r-daily-pct">0%</span></div>
    </div>
    <div class="risk-meter">
      <div class="rm-label">Portfolio Exposure</div>
      <div class="rm-bar-bg"><div class="rm-bar" id="r-exp-bar" style="width:0%;background:var(--orange)"></div></div>
      <div class="rm-meta"><span id="r-exp-used">0 positions</span><span id="r-exp-pct">0% deployed</span></div>
    </div>
    <div class="risk-meter">
      <div class="rm-label">Consecutive Losses</div>
      <div class="rm-bar-bg"><div class="rm-bar" id="r-loss-bar" style="width:0%;background:var(--red)"></div></div>
      <div class="rm-meta"><span id="r-loss-n">0 of 3</span><span id="r-loss-status">OK</span></div>
    </div>
    <div class="risk-meter">
      <div class="rm-label">Cash Reserve</div>
      <div class="rm-bar-bg"><div class="rm-bar" id="r-cash-bar" style="width:0%;background:var(--green)"></div></div>
      <div class="rm-meta"><span id="r-cash-pct">100% cash</span><span>Min: 40%</span></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Open Position Risk</div>
    <div id="r-pos-detail"><div class="empty">No open positions</div></div>
  </div>
</div>

<!-- VIEW 7: NEWS -->
<div class="view news-view" id="view-news">
  <div class="news-hdr">
    <span id="news-count" style="font-weight:600;color:var(--orange)">0 stories</span>
    <input type="text" id="news-keyword" placeholder="Filter keyword..." oninput="filterNews()" style="background:var(--bg1);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-size:11px;font-family:'JetBrains Mono',monospace;width:140px">
    <input type="text" id="news-ticker" placeholder="Ticker..." oninput="filterNews()" style="background:var(--bg1);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-size:11px;font-family:'JetBrains Mono',monospace;width:80px">
    <select id="news-sort" onchange="filterNews()" style="background:var(--bg1);border:1px solid var(--border);color:var(--text);padding:4px 6px;border-radius:4px;font-size:11px;font-family:'JetBrains Mono',monospace">
      <option value="time">Newest</option>
      <option value="score">Score</option>
      <option value="macro">Macro Impact</option>
    </select>
    <select id="news-sentiment-filter" onchange="filterNews()" style="background:var(--bg1);border:1px solid var(--border);color:var(--text);padding:4px 6px;border-radius:4px;font-size:11px;font-family:'JetBrains Mono',monospace">
      <option value="all">All</option>
      <option value="BULLISH">Bullish</option>
      <option value="BEARISH">Bearish</option>
    </select>
    <span id="news-updated" style="color:var(--muted2);font-size:10px;margin-left:auto"></span>
    <button class="news-fetch-btn" id="news-fetch-btn" onclick="loadNews()">⟳ Fetch News</button>
  </div>
  <div id="market-events-strip" style="display:none;padding:10px 14px 0"></div>
  <div class="news-feed" id="news-feed">
    <div class="empty" style="padding:40px 0;text-align:center">
      <div style="font-size:28px;opacity:.3;margin-bottom:12px">📰</div>
      <div style="font-size:12px;color:var(--muted2);margin-bottom:14px">No stories loaded yet</div>
      <button class="news-fetch-btn" onclick="loadNews()">Fetch News Now</button>
    </div>
  </div>
</div>
<!-- News article drawer -->
<div class="news-overlay" id="news-overlay" onclick="closeNewsDrawer()"></div>
<div class="news-drawer" id="news-drawer">
  <div class="news-drawer-hdr">
    <span class="news-drawer-sym" id="nd-sym">—</span>
    <span class="news-drawer-title" id="nd-title">—</span>
    <button class="news-drawer-close" onclick="closeNewsDrawer()">×</button>
  </div>
  <div class="news-drawer-body">
    <div class="news-drawer-badge-row" id="nd-badge-row"></div>
    <div class="news-drawer-hl" id="nd-hl"></div>
    <div class="news-drawer-meta" id="nd-meta"></div>
    <div id="nd-catalyst-wrap" style="display:none">
      <div class="news-drawer-catalyst">
        <div class="news-drawer-catalyst-lbl">AI Analysis</div>
        <div class="news-drawer-catalyst-txt" id="nd-catalyst"></div>
      </div>
    </div>
  </div>
  <div class="news-reader-wrap" id="nd-reader-wrap">
    <div class="news-reader-loading" id="nd-reader-loading">Loading article…</div>
    <iframe id="nd-iframe" src="about:blank" class="news-reader-iframe"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      onload="document.getElementById('nd-reader-loading').style.display='none'"></iframe>
  </div>
</div>

<!-- VIEW 7: PORTFOLIO (multi-account aggregation) -->
<div class="view" id="view-portfolio" style="flex-direction:column;overflow-y:auto;padding:16px;gap:14px">

  <!-- Last Decision (full card with navigation) -->
  <div class="setting-card" style="margin:0;padding:0;overflow:hidden">
    <div class="col-title" style="flex-shrink:0">
      <span>Last Decision</span>
      <span style="display:flex;align-items:center;gap:6px">
        <span id="trade-card-age" style="color:var(--muted2);font-size:9px"></span>
        <button id="tc-copy-btn" onclick="copyDecision()" style="background:none;border:none;color:var(--muted2);cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:9px;padding:0;letter-spacing:1px" title="Copy to clipboard">Copy</button>
        <button id="tc-prev-btn" onclick="prevDecision()" style="background:none;border:none;color:var(--muted2);cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:12px;padding:0;line-height:1" title="Previous decision">&#8592;</button>
        <span id="tc-nav-pos" style="color:var(--muted2);font-size:9px"></span>
        <button id="tc-next-btn" onclick="nextDecision()" style="background:none;border:none;color:var(--muted2);cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:12px;padding:0;line-height:1" title="Next decision">&#8594;</button>
      </span>
    </div>
    <div class="trade-card-body" id="trade-card-body" style="max-height:none">
      <div style="color:var(--muted2);font-size:11px">No trades taken yet.</div>
    </div>
  </div>

  <!-- Summary KPI strip -->
  <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px">
    <div class="setting-card" style="padding:10px 14px;margin:0">
      <div class="sl">Gross Exposure</div>
      <div class="sv co" id="pf-gross">—</div>
    </div>
    <div class="setting-card" style="padding:10px 14px;margin:0">
      <div class="sl">Net Exposure</div>
      <div class="sv" id="pf-net">—</div>
    </div>
    <div class="setting-card" style="padding:10px 14px;margin:0">
      <div class="sl">Unrealised P&amp;L</div>
      <div class="sv" id="pf-unreal">—</div>
    </div>
    <div class="setting-card" style="padding:10px 14px;margin:0">
      <div class="sl">Realised P&amp;L</div>
      <div class="sv" id="pf-real">—</div>
    </div>
    <div class="setting-card" style="padding:10px 14px;margin:0">
      <div class="sl">Long / Short</div>
      <div class="sv" id="pf-ls">—</div>
    </div>
    <div class="setting-card" style="padding:10px 14px;margin:0">
      <div class="sl">Accounts</div>
      <div class="sv cw" id="pf-accts">—</div>
    </div>
  </div>

  <!-- Exposure bar -->
  <div class="setting-card" style="margin:0;padding:12px 16px">
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted2);margin-bottom:6px">
      <span>LONG EXPOSURE</span><span id="pf-long-pct">0%</span>
    </div>
    <div style="height:6px;background:var(--border2);border-radius:3px;overflow:hidden;margin-bottom:4px">
      <div id="pf-long-bar" style="height:100%;background:var(--green);border-radius:3px;transition:width .4s;width:0%"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted2);margin-bottom:6px;margin-top:8px">
      <span>SHORT EXPOSURE</span><span id="pf-short-pct">0%</span>
    </div>
    <div style="height:6px;background:var(--border2);border-radius:3px;overflow:hidden">
      <div id="pf-short-bar" style="height:100%;background:var(--red);border-radius:3px;transition:width .4s;width:0%"></div>
    </div>
  </div>

  <!-- Position table -->
  <div class="setting-card" style="margin:0;padding:0;overflow:hidden">
    <div style="padding:10px 14px;border-bottom:1px solid var(--border);font-size:9px;font-weight:700;letter-spacing:2px;color:var(--muted2);display:flex;justify-content:space-between">
      <span>AGGREGATED POSITIONS</span>
      <span id="pf-count" style="color:var(--orange)">0 positions</span>
    </div>
    <div id="pf-table" style="overflow-y:auto;max-height:340px">
      <div class="empty" style="padding:20px;text-align:center;color:var(--muted2)">Click the Portfolio tab to load aggregated positions.</div>
    </div>
  </div>
</div>

<!-- VIEW 6: SETTINGS -->
<div class="view settings-view" id="view-settings">
  <div class="setting-card">
    <div class="setting-title">&lt;&gt; Bot Control</div>
    <div class="setting-row"><span class="setting-label">Active Account</span><span class="setting-val" id="cfg-account">DUP481326</span></div>
    <div class="setting-row"><span class="setting-label">Bot Status</span><span class="setting-val" id="cfg-status">Running</span></div>
    <div style="display:flex;gap:8px;padding-top:10px">
      <button class="apply-btn" onclick="applySettings()" style="flex:1">✅ Apply Settings</button>
      <button class="apply-btn" onclick="restartBot()" style="flex:1;border-color:var(--red);color:var(--red);background:rgba(255,23,68,.08)">🔄 Restart Bot</button>
    </div>
  </div>
  <div class="setting-card">
    <div class="setting-title">Risk Parameters</div>
    <div class="setting-row"><span class="setting-label">Risk per trade (%)</span><input class="setting-input" id="cfg-risk-pct" type="number" step="0.5" min="0.5" max="10" value="4"></div>
    <div class="setting-row"><span class="setting-label">Daily loss limit (%)</span><input class="setting-input" id="cfg-daily-limit" type="number" step="0.5" min="1" max="15" value="6"></div>
    <div class="setting-row"><span class="setting-label">Max positions</span><input class="setting-input" id="cfg-max-pos" type="number" step="1" min="1" max="30" value="12"></div>
    <div class="setting-row"><span class="setting-label">Min cash reserve (%)</span><input class="setting-input" id="cfg-cash-reserve" type="number" step="5" min="0" max="80" value="10"></div>
    <div class="setting-row"><span class="setting-label">Max single position (%)</span><input class="setting-input" id="cfg-max-single" type="number" step="1" min="1" max="30" value="15"></div>
  </div>
  <div class="setting-card">
    <div class="setting-title">Scoring &amp; Agents</div>
    <div class="setting-row"><span class="setting-label">Min score to trade (/50)</span><input class="setting-input" id="cfg-min-score" type="number" step="1" min="10" max="45" value="28"></div>
    <div class="setting-row"><span class="setting-label">High conviction score</span><input class="setting-input" id="cfg-high-score" type="number" step="1" min="20" max="50" value="38"></div>
    <div class="setting-row">
      <span class="setting-label">Agents required to agree</span>
      <select id="agree-select" class="setting-input" style="width:60px">
        <option value="2">2/6</option>
        <option value="3" selected>3/6</option>
        <option value="4">4/6</option>
        <option value="5">5/6</option>
        <option value="6">6/6</option>
      </select>
    </div>
  </div>
  <div class="setting-card">
    <div class="setting-title">Options</div>
    <div class="setting-row"><span class="setting-label">Min score for options</span><input class="setting-input" id="cfg-opt-min-score" type="number" step="1" min="20" max="50" value="35"></div>
    <div class="setting-row"><span class="setting-label">Options risk per trade (%)</span><input class="setting-input" id="cfg-opt-risk" type="number" step="0.5" min="0.5" max="5" value="2.5"></div>
    <div class="setting-row"><span class="setting-label">Max IV Rank</span><input class="setting-input" id="cfg-opt-ivr" type="number" step="5" min="20" max="100" value="65"></div>
    <div class="setting-row"><span class="setting-label">Target delta</span><input class="setting-input" id="cfg-opt-delta" type="number" step="0.05" min="0.2" max="0.7" value="0.50"></div>
    <div class="setting-row"><span class="setting-label">Delta range (±)</span><input class="setting-input" id="cfg-opt-delta-range" type="number" step="0.05" min="0.10" max="0.45" value="0.35"></div>
    <div class="setting-row"><span class="setting-label">DTE range</span><span class="setting-val">5 — 45 days</span></div>
  </div>

  <div class="setting-card">
    <div class="setting-title">📡 News Sentinel</div>
    <div class="setting-row"><span class="setting-label">Sentinel enabled</span>
      <select id="cfg-sentinel-enabled" class="setting-input" style="width:70px"><option value="true">On</option><option value="false">Off</option></select>
    </div>
    <div class="setting-row"><span class="setting-label">Poll interval (sec)</span><input class="setting-input" id="cfg-sentinel-poll" type="number" step="5" min="15" max="120" value="45"></div>
    <div class="setting-row"><span class="setting-label">Cooldown per symbol (min)</span><input class="setting-input" id="cfg-sentinel-cooldown" type="number" step="1" min="1" max="60" value="10"></div>
    <div class="setting-row"><span class="setting-label">Max trades / hour</span><input class="setting-input" id="cfg-sentinel-max-trades" type="number" step="1" min="1" max="10" value="3"></div>
    <div class="setting-row"><span class="setting-label">Position size multiplier</span><input class="setting-input" id="cfg-sentinel-risk-mult" type="number" step="0.05" min="0.25" max="1.5" value="0.75"></div>
    <div class="setting-row"><span class="setting-label">Keyword threshold</span><input class="setting-input" id="cfg-sentinel-kw-thresh" type="number" step="1" min="1" max="10" value="3"></div>
    <div class="setting-row"><span class="setting-label">Min confidence to trade</span><input class="setting-input" id="cfg-sentinel-min-conf" type="number" step="1" min="1" max="10" value="5"></div>
    <div class="setting-row"><span class="setting-label">Use IBKR news</span>
      <select id="cfg-sentinel-ibkr" class="setting-input" style="width:70px"><option value="true">On</option><option value="false">Off</option></select>
    </div>
    <div class="setting-row"><span class="setting-label">Use Finviz news</span>
      <select id="cfg-sentinel-finviz" class="setting-input" style="width:70px"><option value="true">On</option><option value="false">Off</option></select>
    </div>
  </div>
  <div class="setting-card">
    <div class="setting-title">💰 Capital Management</div>
    <p style="font-size:11px;color:var(--muted2);margin-bottom:10px">Record deposits or withdrawals so P&L reflects true trading performance. P&L = NetLiquidation - (Starting Capital + Adjustments)</p>
    <div class="setting-row"><span class="setting-label">Starting Capital</span><span class="setting-val" id="cfg-start-cap">$1,000,000</span></div>
    <div class="setting-row"><span class="setting-label">Effective Capital</span><span class="setting-val" id="cfg-eff-cap">$1,000,000</span></div>
    <div class="setting-row"><span class="setting-label">Current P&L</span><span class="setting-val" id="cfg-current-pnl">—</span></div>
    <div style="display:flex;gap:8px;align-items:center;padding-top:10px">
      <select id="cap-type" class="setting-input" style="width:120px">
        <option value="deposit">Deposit</option>
        <option value="withdrawal">Withdrawal</option>
      </select>
      <input id="cap-amount" class="setting-input" type="number" step="1000" min="0" placeholder="Amount ($)" style="flex:1">
      <input id="cap-note" class="setting-input" type="text" placeholder="Note (optional)" style="flex:1">
    </div>
    <div style="padding-top:8px">
      <button class="apply-btn" onclick="recordCapitalAdjustment()" style="width:100%">💰 Record Adjustment</button>
    </div>
    <div id="cap-history" style="margin-top:10px;font-size:10px;color:var(--muted2)"></div>
  </div>

  <div class="setting-card">
    <div class="setting-title">⭐ Favourites Watchlist</div>
    <p style="font-size:11px;color:var(--muted2);margin-bottom:10px">Tickers added here are always scanned regardless of scanner results. Stocks, ETFs, commodities, anything IBKR supports.</p>
    <div id="fav-tags" style="margin-bottom:10px;min-height:32px"></div>
    <div style="display:flex;gap:8px;align-items:center">
      <input id="fav-input" class="fav-input" placeholder="e.g. NVDA, GLD, BTC" maxlength="10"
             onkeydown="if(event.key==='Enter')addFavourite()">
      <button class="apply-btn" onclick="addFavourite()">+ Add</button>
    </div>
    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      <span style="font-size:10px;color:var(--muted2)">Quick add:</span>
      <button class="f-btn" onclick="addFavTicker('GLD')">GLD</button>
      <button class="f-btn" onclick="addFavTicker('IBIT')">IBIT</button>
      <button class="f-btn" onclick="addFavTicker('USO')">USO</button>
      <button class="f-btn" onclick="addFavTicker('SPY')">SPY</button>
      <button class="f-btn" onclick="addFavTicker('QQQ')">QQQ</button>
      <button class="f-btn" onclick="addFavTicker('NVDA')">NVDA</button>
      <button class="f-btn" onclick="addFavTicker('TSLA')">TSLA</button>
      <button class="f-btn" onclick="addFavTicker('AAPL')">AAPL</button>
    </div>
    <div style="margin-top:10px">
      <button class="apply-btn" onclick="saveFavourites()" style="width:100%">💾 Save & Apply to Bot</button>
    </div>
  </div>

</div>

<!-- VIEW: ALPHA DECAY -->
<div class="view growth-view" id="view-alpha">

  <!-- IC Weights Panel -->
  <div class="card" id="ic-weights-card" style="margin-bottom:0">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div class="card-title" style="margin-bottom:0">IC-Weighted Signal Composite</div>
      <div id="ic-status-pill" style="font-size:9px;padding:2px 8px;border-radius:10px;border:1px solid var(--muted);color:var(--muted2)">Loading…</div>
    </div>
    <div style="font-size:10px;color:var(--muted2);margin-bottom:10px">
      Spearman IC (rank correlation) between each dimension and 5-day forward return — rolling 60-trade window.
      Negative IC dimensions receive zero weight. Updated weekly.
    </div>
    <div id="ic-bars" style="display:grid;grid-template-columns:80px 1fr 52px 52px;gap:5px 8px;align-items:center;font-size:11px">
      <div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase">Dim</div>
      <div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase">Weight</div>
      <div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;text-align:right">IC</div>
      <div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;text-align:right">4w</div>
    </div>
    <div style="font-size:10px;color:var(--muted2);margin-top:8px" id="ic-updated">—</div>
  </div>

  <!-- Summary KPIs -->
  <div class="metric-grid" id="ad-kpi-row" style="grid-template-columns:repeat(4,1fr)">
    <div class="metric"><div class="metric-label">Trades Analysed</div><div class="metric-val co" id="ad-count">—</div></div>
    <div class="metric"><div class="metric-label">Optimal Hold</div><div class="metric-val cg" id="ad-optimal">—</div></div>
    <div class="metric"><div class="metric-label">T+1 Median</div><div class="metric-val" id="ad-t1">—</div></div>
    <div class="metric"><div class="metric-label">T+10 Median</div><div class="metric-val" id="ad-t10">—</div></div>
  </div>

  <!-- Forward return curve -->
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div class="card-title" style="margin-bottom:0">Forward Return Distribution by Horizon</div>
      <div style="display:flex;gap:4px">
        <button id="ad-view-conviction" onclick="setAdView('conviction')" style="font-size:9px;padding:2px 8px;border-radius:10px;border:1px solid var(--orange);background:var(--orange);color:#000;cursor:pointer;font-family:inherit;letter-spacing:.5px">All / Regime</button>
        <button id="ad-view-dims" onclick="setAdView('dims')" style="font-size:9px;padding:2px 8px;border-radius:10px;border:1px solid var(--muted);background:transparent;color:var(--muted2);cursor:pointer;font-family:inherit;letter-spacing:.5px">By Dimension</button>
      </div>
    </div>
    <div style="font-size:10px;color:var(--muted2);margin-bottom:10px">
      Median direction-adjusted return (%) for closed trades at T+N bars after entry.
      Positive = favourable outcome for the trade direction. Shaded band = P25–P75 for all trades.
    </div>
    <div style="position:relative;height:220px"><canvas id="alpha-decay-chart"></canvas></div>
  </div>

  <!-- Per-segment breakdown table -->
  <div class="card">
    <div class="card-title">Segment Breakdown</div>
    <div id="ad-segment-table" style="font-size:11px">
      <div style="display:grid;grid-template-columns:120px repeat(5,1fr);gap:4px;padding:5px 0;border-bottom:1px solid var(--border);font-size:9px;letter-spacing:1.2px;color:var(--muted2);text-transform:uppercase">
        <div>Segment</div><div>n</div><div>T+1</div><div>T+3</div><div>T+5</div><div>T+10</div>
      </div>
      <div id="ad-seg-rows" style="color:var(--muted2);padding:12px 0">Loading…</div>
    </div>
  </div>

  <!-- Data quality note -->
  <div style="font-size:10px;color:var(--muted);padding:4px 2px">
    ⚠ Forward returns fetched live via yfinance. Horizons not yet reached for recent trades are omitted.
    Signal half-life analysis requires ≥20 agent-scored trades for statistical significance.
  </div>

</div>

<script>
// ── State ──────────────────────────────────────────────────
let allTrades = [];
let equityHistory = [];
let _decisionHistory = [];
let _decisionIdx = 0;
let currentFilter = 'all';
let _liveSettings = {}; // latest settings from /api/state — avoids hardcoded defaults
let scanElapsed = 0;
let scanTimer;
let alphaDecayChart = null;
let _adData = null;
let _adView = 'conviction';

// ── Alpha decay view toggle ────────────────────────────────
function setAdView(v) {
  _adView = v;
  const b1 = document.getElementById('ad-view-conviction');
  const b2 = document.getElementById('ad-view-dims');
  if (b1) {
    b1.style.background  = v === 'conviction' ? 'var(--orange)' : 'transparent';
    b1.style.color       = v === 'conviction' ? '#000' : 'var(--muted2)';
    b1.style.borderColor = v === 'conviction' ? 'var(--orange)' : 'var(--muted)';
  }
  if (b2) {
    b2.style.background  = v === 'dims' ? 'var(--orange)' : 'transparent';
    b2.style.color       = v === 'dims' ? '#000' : 'var(--muted2)';
    b2.style.borderColor = v === 'dims' ? 'var(--orange)' : 'var(--muted)';
  }
  if (_adData) renderAlphaDecay(_adData);
}

// ── Portfolio aggregation ──────────────────────────────────
async function loadPortfolio() {
  const tableEl = document.getElementById('pf-table');
  tableEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted2)">Loading…</div>';
  try {
    const resp = await fetch('/api/portfolio');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const d = await resp.json();

    // KPI strip
    const t = d.totals || {};
    const pnlColor = v => v >= 0 ? 'var(--green)' : 'var(--red)';
    document.getElementById('pf-gross').textContent   = '$' + fmt$(t.gross_exposure   || 0);
    document.getElementById('pf-gross').className     = 'sv co';

    const netEl = document.getElementById('pf-net');
    netEl.textContent = (t.net_exposure >= 0 ? '+' : '') + '$' + fmt$(t.net_exposure || 0);
    netEl.style.color = pnlColor(t.net_exposure || 0);

    const urEl = document.getElementById('pf-unreal');
    urEl.textContent = (t.unrealized_pnl >= 0 ? '+' : '') + '$' + fmt$(t.unrealized_pnl || 0);
    urEl.style.color = pnlColor(t.unrealized_pnl || 0);

    const rlEl = document.getElementById('pf-real');
    rlEl.textContent = (t.realized_pnl >= 0 ? '+' : '') + '$' + fmt$(t.realized_pnl || 0);
    rlEl.style.color = pnlColor(t.realized_pnl || 0);

    document.getElementById('pf-ls').textContent =
      (t.long_count || 0) + 'L / ' + (t.short_count || 0) + 'S';
    document.getElementById('pf-accts').textContent =
      (d.accounts || []).length;

    // Exposure bars
    const lp = t.long_exposure_pct  || 0;
    const sp = t.short_exposure_pct || 0;
    document.getElementById('pf-long-pct').textContent  = lp.toFixed(1) + '%';
    document.getElementById('pf-short-pct').textContent = sp.toFixed(1) + '%';
    document.getElementById('pf-long-bar').style.width  = Math.min(lp, 100) + '%';
    document.getElementById('pf-short-bar').style.width = Math.min(sp, 100) + '%';

    // Position table
    const positions = Object.values(d.positions || {});
    document.getElementById('pf-count').textContent = positions.length + ' position' + (positions.length !== 1 ? 's' : '');

    if (!positions.length) {
      tableEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted2)">No open positions across all accounts.</div>';
      return;
    }

    // Sort by |market_value| desc
    positions.sort((a, b) => Math.abs(b.market_value) - Math.abs(a.market_value));

    tableEl.innerHTML = positions.map(p => {
      const dirColor = p.direction === 'LONG' ? 'var(--green)' : p.direction === 'SHORT' ? 'var(--red)' : 'var(--muted2)';
      const pnlColor2 = p.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)';
      const pnlSign   = p.unrealized_pnl >= 0 ? '+' : '';
      const acctList  = Object.keys(p.accounts || {}).join(', ') || '—';
      const isOpt = p.sec_type === 'OPT';
      const label = isOpt
        ? `${p.symbol} ${p.right === 'C' ? 'CALL' : 'PUT'} @${p.strike}`
        : p.symbol;
      return `<div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr 1.5fr;gap:8px;padding:8px 14px;border-bottom:1px solid var(--border);align-items:center;font-size:11px">
        <div>
          <span style="font-weight:700;font-family:'Syne',sans-serif;font-size:13px">${label}</span>
          ${isOpt ? '<span style="font-size:9px;color:var(--muted2);margin-left:4px">OPT</span>' : ''}
        </div>
        <div style="color:${dirColor};font-weight:600">${p.direction}</div>
        <div style="color:var(--text)">${p.net_position > 0 ? '+' : ''}${p.net_position}</div>
        <div style="color:var(--orange)">$${fmt$(p.market_value)}</div>
        <div style="color:${pnlColor2}">${pnlSign}$${fmt$(p.unrealized_pnl)}</div>
        <div style="color:var(--muted2);font-size:10px">${acctList}</div>
      </div>`;
    }).join('');

    // Column header (prepend)
    tableEl.insertAdjacentHTML('afterbegin',
      `<div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr 1.5fr;gap:8px;padding:6px 14px;font-size:9px;letter-spacing:1.5px;color:var(--muted2);text-transform:uppercase;border-bottom:1px solid var(--border)">
        <div>Symbol</div><div>Dir</div><div>Qty</div><div>Mkt Val</div><div>Unreal P&L</div><div>Accounts</div>
      </div>`
    );

  } catch (err) {
    tableEl.innerHTML = `<div style="padding:20px;color:var(--red)">⚠ Could not load portfolio data: ${esc(err.message)}</div>`;
  }
}

// ── IC Weights ─────────────────────────────────────────────
const _IC_DIMS = ['trend','momentum','squeeze','flow','breakout','mtf','news','social','reversion'];
const _IC_LABELS = {
  trend:'TREND', momentum:'MOM', squeeze:'SQZ', flow:'FLOW', breakout:'BRK',
  mtf:'MTF', news:'NEWS', social:'SOC', reversion:'REV'
};

async function loadICWeights() {
  try {
    const resp = await fetch('/api/ic_weights');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const d = await resp.json();
    if (d.error) throw new Error(d.error);
    renderICWeights(d);
  } catch (err) {
    document.getElementById('ic-status-pill').textContent = '⚠ ' + err.message;
  }
}

function renderICWeights(d) {
  const weights = d.weights || {};
  const rawIC   = d.raw_ic  || {};
  const history = d.history || [];
  const isEqual = d.using_equal_weights;

  const pill = document.getElementById('ic-status-pill');
  if (isEqual) {
    pill.textContent = 'Equal weights (insufficient data)';
    pill.style.borderColor = 'var(--yellow)';
    pill.style.color = 'var(--yellow)';
  } else {
    const nr = d.n_records || 0;
    pill.textContent = `IC-weighted · n=${nr}`;
    pill.style.borderColor = 'var(--green)';
    pill.style.color = 'var(--green)';
  }

  if (d.updated) {
    const updDt = new Date(d.updated);
    document.getElementById('ic-updated').textContent =
      'Updated ' + updDt.toLocaleDateString() + ' ' + updDt.toTimeString().slice(0,5);
  }

  // Build per-dimension bars
  const maxW  = Math.max(...Object.values(weights), 1/9);
  const barsEl = document.getElementById('ic-bars');

  // Header row already in HTML; clear and re-render data rows
  barsEl.innerHTML =
    `<div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase">Dim</div>
     <div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase">Weight (${isEqual?'equal':'IC-weighted'})</div>
     <div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;text-align:right">IC</div>
     <div style="font-size:9px;letter-spacing:1px;color:var(--muted2);text-transform:uppercase;text-align:right">4w trend</div>`;

  for (const dim of _IC_DIMS) {
    const w    = weights[dim] || 0;
    const ic   = rawIC[dim];
    const pct  = (w * 100).toFixed(1) + '%';
    const barW = Math.round((w / maxW) * 100);
    const barCol = isEqual ? 'var(--muted)' : (w > 1/9 * 1.1 ? 'var(--green)' : w < 1/9 * 0.9 ? 'var(--red)' : 'var(--orange)');

    // 4-week trend: compare most recent vs 4-weeks-ago weight for this dim
    let trendTxt = '—';
    if (history.length >= 2) {
      const old_w = (history[0].weights || {})[dim] || 0;
      const new_w = (history[history.length - 1].weights || {})[dim] || 0;
      const delta = new_w - old_w;
      if (Math.abs(delta) < 0.005) trendTxt = '<span style="color:var(--muted2)">→</span>';
      else if (delta > 0) trendTxt = '<span style="color:var(--green)">↑</span>';
      else trendTxt = '<span style="color:var(--red)">↓</span>';
    }

    const icTxt = (ic === null || ic === undefined)
      ? '<span style="color:var(--muted2)">—</span>'
      : `<span style="color:${ic >= 0 ? 'var(--green)' : 'var(--red)'}">${(ic*100).toFixed(1)}%</span>`;

    barsEl.innerHTML +=
      `<div style="color:var(--orange);font-size:10px;font-weight:600">${_IC_LABELS[dim]||dim}</div>
       <div style="display:flex;align-items:center;gap:6px">
         <div style="flex:1;background:var(--bg3);border-radius:2px;height:8px;overflow:hidden">
           <div style="width:${barW}%;height:100%;background:${barCol};border-radius:2px;transition:width .4s"></div>
         </div>
         <span style="font-size:10px;color:var(--text);min-width:36px;text-align:right">${pct}</span>
       </div>
       <div style="text-align:right;font-size:10px">${icTxt}</div>
       <div style="text-align:right;font-size:11px">${trendTxt}</div>`;
  }
}

// ── Alpha Decay ────────────────────────────────────────────
async function loadAlphaDecay() {
  document.getElementById('ad-seg-rows').textContent = 'Fetching forward returns…';
  document.getElementById('ad-count').textContent    = '…';
  document.getElementById('ad-optimal').textContent  = '…';
  document.getElementById('ad-t1').textContent       = '…';
  document.getElementById('ad-t10').textContent      = '…';
  try {
    const resp = await fetch('/api/alpha_decay');
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const d = await resp.json();
    if (d.error) throw new Error(d.error);
    renderAlphaDecay(d);
  } catch (err) {
    document.getElementById('ad-seg-rows').innerHTML =
      `<div style="color:var(--red);padding:8px 0">⚠ ${err.message}</div>`;
  }
}

function _fmtPct(v) {
  if (v === null || v === undefined) return '—';
  const pct = (v * 100).toFixed(2);
  const col  = v >= 0 ? 'var(--green)' : 'var(--red)';
  return `<span style="color:${col}">${v >= 0 ? '+' : ''}${pct}%</span>`;
}

// Inline Chart.js plugin: draws a vertical dashed line at the optimal horizon.
const _adOptimalLinePlugin = {
  id: 'adOptimalLine',
  afterDraw(chart, _args, opts) {
    if (opts.xIndex == null) return;
    const meta = chart.getDatasetMeta(chart.data.datasets.findIndex(ds => ds._isMedianAll));
    const pt   = meta && meta.data && meta.data[opts.xIndex];
    if (!pt) return;
    const {top, bottom} = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(pt.x, top);
    ctx.lineTo(pt.x, bottom);
    ctx.lineWidth   = 1.5;
    ctx.strokeStyle = 'rgba(255,214,0,0.5)';
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.restore();
  },
};
if (typeof Chart !== 'undefined') Chart.register(_adOptimalLinePlugin);

function renderAlphaDecay(d) {
  _adData = d;
  const horizons = d.horizons || [1, 3, 5, 10];
  const groups   = d.groups  || {};
  const all      = groups.all || {};

  // KPI strip
  document.getElementById('ad-count').textContent =
    d.trade_count > 0 ? d.trade_count : '0';
  document.getElementById('ad-optimal').textContent =
    d.optimal_horizon != null ? `T+${d.optimal_horizon}d` : '—';

  const t1val  = all.median && all.median[0]  != null ? all.median[0]  : null;
  const t10val = all.median && all.median[horizons.length - 1] != null
                   ? all.median[horizons.length - 1] : null;
  document.getElementById('ad-t1').innerHTML  = _fmtPct(t1val);
  document.getElementById('ad-t10').innerHTML = _fmtPct(t10val);

  // Chart
  const labels   = horizons.map(h => `T+${h}d`);
  const optIndex = d.optimal_horizon != null ? horizons.indexOf(d.optimal_horizon) : null;

  const datasets = [];

  if (_adView === 'conviction') {
    // ── Conviction & Regime view ──────────────────────────────────────────
    const COLORS = {
      all:        'rgba(255,107,0,1)',
      high_score: 'rgba(0,200,83,1)',
      low_score:  'rgba(255,214,0,1)',
      bull:       'rgba(0,150,255,1)',
      bear:       'rgba(255,23,68,1)',
    };

    // P25–P75 shaded band for "all"
    if (all.p75 && all.p25) {
      datasets.push({
        label:           'P75 (all)',
        data:            all.p75.map(v => v != null ? parseFloat((v * 100).toFixed(4)) : null),
        borderColor:     'transparent',
        backgroundColor: 'rgba(255,107,0,0.10)',
        fill:            '+1',
        tension:         0.35,
        pointRadius:     0,
        spanGaps:        true,
      });
      datasets.push({
        label:           'P25 (all)',
        data:            all.p25.map(v => v != null ? parseFloat((v * 100).toFixed(4)) : null),
        borderColor:     'transparent',
        backgroundColor: 'rgba(255,107,0,0.10)',
        fill:            false,
        tension:         0.35,
        pointRadius:     0,
        spanGaps:        true,
      });
    }

    const visibleGroups = ['all', 'high_score', 'low_score', 'bull', 'bear'];
    const groupLabels   = {
      all: 'All', high_score: 'Hi-Conv (≥38)', low_score: 'Lo-Conv (<38)',
      bull: 'Bull Regime', bear: 'Bear Regime'
    };
    for (const key of visibleGroups) {
      const g = groups[key];
      if (!g || !g.n) continue;
      const ds = {
        label:           `${groupLabels[key]} (n=${g.n})`,
        data:            (g.median || []).map(v => v != null ? parseFloat((v * 100).toFixed(4)) : null),
        borderColor:     COLORS[key],
        backgroundColor: 'transparent',
        borderWidth:     key === 'all' ? 2.5 : 1.5,
        borderDash:      key === 'all' ? [] : [4, 3],
        tension:         0.35,
        pointRadius:     key === 'all' ? 4 : 3,
        pointBackgroundColor: COLORS[key],
        spanGaps:        true,
      };
      if (key === 'all') ds._isMedianAll = true;
      datasets.push(ds);
    }
  } else {
    // ── By Signal Dimension view ──────────────────────────────────────────
    const DIM_COLORS = [
      'rgba(255,107,0,1)',   // trend
      'rgba(0,200,83,1)',    // momentum
      'rgba(0,150,255,1)',   // squeeze
      'rgba(255,214,0,1)',   // flow
      'rgba(200,80,255,1)',  // breakout
      'rgba(255,160,0,1)',   // mtf
      'rgba(0,230,200,1)',   // news
      'rgba(255,80,180,1)',  // social
      'rgba(120,200,80,1)',  // reversion
    ];
    const DIM_NAMES = [
      'dim_trend','dim_momentum','dim_squeeze','dim_flow','dim_breakout',
      'dim_mtf','dim_news','dim_social','dim_reversion',
    ];
    const DIM_LABELS_SHORT = {
      dim_trend:'Trend', dim_momentum:'Momentum', dim_squeeze:'Squeeze',
      dim_flow:'Flow', dim_breakout:'Breakout', dim_mtf:'MTF',
      dim_news:'News', dim_social:'Social', dim_reversion:'Reversion',
    };
    let first = true;
    DIM_NAMES.forEach((k, i) => {
      const g = groups[k];
      if (!g || !g.n) return;
      const ds = {
        label:           `${DIM_LABELS_SHORT[k]} (n=${g.n})`,
        data:            (g.median || []).map(v => v != null ? parseFloat((v * 100).toFixed(4)) : null),
        borderColor:     DIM_COLORS[i],
        backgroundColor: 'transparent',
        borderWidth:     first ? 2.5 : 1.5,
        borderDash:      first ? [] : [4, 3],
        tension:         0.35,
        pointRadius:     first ? 4 : 3,
        pointBackgroundColor: DIM_COLORS[i],
        spanGaps:        true,
      };
      if (first) { ds._isMedianAll = true; first = false; }
      datasets.push(ds);
    });
  }

  const alphaCanvas = document.getElementById('alpha-decay-chart');
  if (!alphaCanvas) return;
  const ctx = alphaCanvas.getContext('2d');
  if (alphaDecayChart) alphaDecayChart.destroy();
  alphaDecayChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      interaction:         { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'top',
          labels: {
            color:     '#888',
            font:      { family: "'JetBrains Mono', monospace", size: 10 },
            boxWidth:  12,
            filter:    item => !item.text.startsWith('P75') && !item.text.startsWith('P25'),
          },
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.parsed.y;
              if (v === null) return `${ctx.dataset.label}: —`;
              return `${ctx.dataset.label}: ${v >= 0 ? '+' : ''}${v.toFixed(3)}%`;
            },
            title: items => {
              const lbl = items[0].label;
              return optIndex != null && items[0].dataIndex === optIndex
                ? `${lbl}  ← optimal exit`
                : lbl;
            },
          },
        },
        adOptimalLine: { xIndex: optIndex != null && optIndex >= 0 ? optIndex : null },
      },
      scales: {
        x: {
          grid:  { color: 'rgba(255,255,255,0.04)' },
          ticks: {
            color: (ctx2) => {
              if (optIndex != null && ctx2.index === optIndex) return '#FFD600';
              return '#888';
            },
            font: { family: "'JetBrains Mono',monospace", size: 10 },
          },
        },
        y: {
          grid:  { color: 'rgba(255,255,255,0.04)' },
          ticks: {
            color: '#888',
            font:  { family: "'JetBrains Mono',monospace", size: 10 },
            callback: v => (v >= 0 ? '+' : '') + v.toFixed(2) + '%',
          },
        },
      },
    },
  });

  // Segment table
  const segRows = [
    ['All Trades',       'all'],
    ['Hi-Conv (≥38)',    'high_score'],
    ['Lo-Conv (<38)',    'low_score'],
    ['Bull Regime',      'bull'],
    ['Bear Regime',      'bear'],
    ['Long Only',        'long_only'],
    ['Short Only',       'short_only'],
  ];
  const hi1  = d.horizons.indexOf(1);
  const hi3  = d.horizons.indexOf(3);
  const hi5  = d.horizons.indexOf(5);
  const hi10 = d.horizons.indexOf(10);

  function _segRow(label, g, indent) {
    if (!g || !g.n) return '';
    const m   = g.median || [];
    const v1  = hi1  >= 0 ? m[hi1]  : null;
    const v3  = hi3  >= 0 ? m[hi3]  : null;
    const v5  = hi5  >= 0 ? m[hi5]  : null;
    const v10 = hi10 >= 0 ? m[hi10] : null;
    const pl  = indent ? 'padding-left:10px' : '';
    return `<div style="display:grid;grid-template-columns:120px repeat(5,1fr);gap:4px;padding:5px 0;border-bottom:1px solid var(--border)">
      <div style="color:var(--text);${pl}">${label}</div>
      <div style="color:var(--muted2)">${g.n}</div>
      <div>${_fmtPct(v1)}</div>
      <div>${_fmtPct(v3)}</div>
      <div>${_fmtPct(v5)}</div>
      <div>${_fmtPct(v10)}</div>
    </div>`;
  }

  const rowsHtml = segRows.map(([label, key]) => _segRow(label, groups[key], false)).join('');

  // Dimension rows — only shown when at least one dimension has data
  const DIM_LABELS = {
    dim_trend: 'Trend', dim_momentum: 'Momentum', dim_squeeze: 'Squeeze',
    dim_flow: 'Flow', dim_breakout: 'Breakout', dim_mtf: 'MTF',
    dim_news: 'News', dim_social: 'Social', dim_reversion: 'Reversion',
  };
  const dimKeys = Object.keys(DIM_LABELS).filter(k => groups[k] && groups[k].n > 0);
  const dimHtml = dimKeys.length === 0 ? '' :
    `<div style="padding:6px 0 2px;font-size:9px;letter-spacing:1px;color:var(--muted);text-transform:uppercase">— by dominant dimension —</div>` +
    dimKeys.map(k => _segRow(DIM_LABELS[k], groups[k], true)).join('');

  document.getElementById('ad-seg-rows').innerHTML = (rowsHtml + dimHtml) ||
    '<div style="padding:8px 0;color:var(--muted2)">No data with usable forward returns yet.</div>';
}

// ── Tab switching ──────────────────────────────────────────
function switchTab(id, el) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('view-' + id).classList.add('active');
  el.classList.add('active');

  // Load portfolio aggregation when switching to that tab
  if (id === 'portfolio') loadPortfolio();

  // Load news when switching to news tab
  if (id === 'news') loadNews();

  // Alpha decay + IC weights: load fresh data each time the tab opens
  if (id === 'alpha') {
    if (alphaDecayChart) { alphaDecayChart.destroy(); alphaDecayChart = null; }
    setTimeout(() => { loadICWeights(); loadAlphaDecay(); }, 50);
  }

  // Charts render with 0 dimensions when their container is display:none.
  // Force redraw when switching to growth tab.
  if (id === 'growth') {
    _lastEquityFingerprint = '';
    _lastDailyFingerprint  = '';
    // Destroy existing charts so they recreate with correct dimensions
    if (equityChart) { equityChart.destroy(); equityChart = null; }
    if (dailyChart)  { dailyChart.destroy();  dailyChart  = null; }
    // Slight delay to let the DOM layout recalculate after display:flex kicks in
    setTimeout(() => {
      if (allEquityData && allEquityData.length >= 2) {
        renderEquityChart(filterByTF(allEquityData, equityTF));
        renderDailyChart(buildDailyPnL(allEquityData, dailyTF));
      }
    }, 50);
  }
}

// ── Kill switch ────────────────────────────────────────────
function forceScan() {
  fetch('/api/scan', {method: 'POST'}).then(() => {
    document.getElementById('bot-status').textContent = 'Scanning...';
    document.getElementById('trades-list').innerHTML = '<div class="empty">Scanning...</div>';
  });
}
function restartBot() {
  if (confirm('Restart Decifer? Bot will stop, then restart in 3 seconds. Positions are held in IBKR.')) {
    fetch('/api/restart', {method: 'POST'}).then(() => {
      document.getElementById('bot-status').textContent = 'Restarting...';
      setTimeout(() => { window.location.reload(); }, 5000);
    });
  }
}

function killSwitch() {
  if (confirm('🚨 KILL SWITCH: This will stop all trading and close all positions. Are you sure?')) {
    fetch('/api/kill', {method:'POST'}).then(r => r.json()).then(d => {
      if (d.ok) alert('🚨 Kill switch executed. ' + d.detail);
      else alert('❌ Kill switch failed: ' + (d.error || 'unknown'));
    }).catch(() => alert('Kill switch sent.'));
  }
}

let paused = false;
function togglePause() {
  paused = !paused;
  const btn = document.getElementById('pause-btn');
  btn.textContent = paused ? '▶ RESUME BOT' : '⏸ PAUSE BOT';
  btn.style.borderColor = paused ? 'var(--green)' : 'var(--orange)';
  btn.style.color = paused ? 'var(--green)' : 'var(--orange)';
  fetch('/api/pause', {method:'POST', body: JSON.stringify({paused}), headers:{'Content-Type':'application/json'}});
}

// ── Filter trades ──────────────────────────────────────────
function filterTrades(filter, btn) {
  currentFilter = filter;
  document.querySelectorAll('.f-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderHistory();
}

// ── Helpers ────────────────────────────────────────────────
function fmt$(n) {
  const v = typeof n === 'number' ? n : parseFloat(n);
  return (v == null || isNaN(v) || !isFinite(v)) ? '—' : '$' + v.toLocaleString('en', {minimumFractionDigits:2, maximumFractionDigits:2});
}
function fmtPct(n) { return (n == null || isNaN(n)) ? '—' : (n >= 0 ? '+' : '') + n.toFixed(1) + '%'; }
// HTML-escape user/external data before inserting into innerHTML
function esc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── Render positions ───────────────────────────────────────
function closePosition(idx) {
  const p = lastPositions[idx];
  const key = p ? (p._trade_key || p.symbol) : String(idx);
  if (confirm('Close ' + key + '? Executes immediately via aggressive limit order.')) {
    fetch('/api/close', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({symbol: key})})
      .then(r => r.json())
      .then(d => {
        if (d.ok) alert('✅ ' + d.detail);
        else alert('❌ ' + (d.error || 'unknown'));
      })
      .catch(e => alert('Error: ' + e));
  }
}

function cancelOrder(orderId, idx) {
  const p = lastPositions[idx];
  const sym = p ? p.symbol : String(idx);
  if (confirm('Cancel pending order #' + orderId + ' (' + sym + ')?')) {
    fetch('/api/cancel-order', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({order_id: orderId})})
      .then(r => r.json())
      .then(d => {
        if (d.ok) { alert('✅ ' + d.detail); poll(); }
        else alert('❌ ' + (d.error || 'unknown'));
      })
      .catch(e => alert('Error: ' + e));
  }
}

let posSort = 'recency'; // 'recency' | 'size' | 'pnl'
let lastPositions = [];

function sortPositions(mode) {
  posSort = mode;
  // Update button styles
  document.querySelectorAll('.pos-sort-btn').forEach(b => b.style.color = 'var(--muted2)');
  const active = document.getElementById('pos-sort-' + mode);
  if (active) active.style.color = 'var(--orange)';
  renderPositions(lastPositions);
}

function renderPositions(positions) {
  const el = document.getElementById('pos-list');
  if (!positions || !positions.length) { el.innerHTML = '<div class="empty">No open positions</div>'; return; }
  lastPositions = positions;

  // Enrich with computed fields
  let enriched = positions.map((p, idx) => {
    const dir = (p.direction === 'SHORT' || p.qty < 0) ? 'SHORT' : 'LONG';
    const isOpt = p.instrument === 'option';
    // Options: multiply by 100 (contract multiplier) for correct P&L and position value
    const mult = isOpt ? 100 : 1;
    const pnl = dir === 'SHORT'
      ? (p.entry - p.current) * Math.abs(p.qty) * mult
      : (p.current - p.entry) * Math.abs(p.qty) * mult;
    const pct = (p.entry && p.entry !== 0)
      ? (dir === 'SHORT'
        ? ((p.entry - p.current) / p.entry) * 100
        : ((p.current - p.entry) / p.entry) * 100)
      : 0;
    const posValue = Math.abs(p.current * p.qty * mult);
    return {...p, dir, pnl, pct, posValue, isOpt, _idx: idx};
  });

  // Sort
  if (posSort === 'size') enriched.sort((a, b) => b.posValue - a.posValue);
  else if (posSort === 'pnl') enriched.sort((a, b) => a.pnl - b.pnl);
  // 'recency' = original order (most recent entries first)

  el.innerHTML = enriched.map(p => {
    const bw  = Math.min(Math.abs(p.pct) * 10, 100);
    const col = p.pnl >= 0 ? 'var(--green)' : 'var(--red)';
    const isPending = p.status === 'PENDING';
    const cardOpacity = isPending ? 'opacity:0.55' : '';
    // Option subtitle: show strike + expiry + right
    const optSub = p.isOpt ? `<div style="font-size:9px;color:var(--cyan);margin-top:1px">${p.right === 'C' ? 'CALL' : 'PUT'} $${p.strike} exp ${p.expiry_str || p.expiry || ''}</div>` : '';
    // Pending badge
    const pendingBadge = isPending ? ' <span style="font-size:8px;color:var(--yellow);background:rgba(255,214,0,.12);border:1px solid var(--yellow);padding:1px 5px;border-radius:8px;font-weight:600;letter-spacing:0.5px">PENDING</span>' : '';
    // Tranche badge: shows T1 OPEN / T1 FILLED when dual-tranche mode is active
    const trancheBadge = (!isPending && p.tranche_mode)
      ? ` <span style="font-size:8px;color:var(--cyan);background:rgba(0,229,255,.12);border:1px solid var(--cyan);padding:1px 5px;border-radius:8px;font-weight:600;letter-spacing:0.5px">${p.t1_status === 'FILLED' ? 'T1 FILLED' : 'T1 OPEN'}</span>`
      : '';
    // Action button: Cancel for pending, Close for active
    const actionBtn = isPending && p.order_id
      ? `<button onclick="event.stopPropagation();cancelOrder(${p.order_id},${p._idx})" style="background:rgba(255,214,0,.12);border:1px solid var(--yellow);color:var(--yellow);font-size:9px;padding:2px 6px;border-radius:3px;cursor:pointer;font-family:'JetBrains Mono',monospace;font-weight:600" title="Cancel pending order">CANCEL</button>`
      : `<button onclick="event.stopPropagation();closePosition(${p._idx})" style="background:rgba(255,23,68,.12);border:1px solid var(--red);color:var(--red);font-size:9px;padding:2px 6px;border-radius:3px;cursor:pointer;font-family:'JetBrains Mono',monospace;font-weight:600" title="Close this position">✕</button>`;
    return `<div class="pos-card" data-symbol="${p.symbol||''}" data-entry="${p.entry||0}" data-qty="${p.qty||0}" data-direction="${p.dir||'LONG'}" onclick="showPositionDetail(${p._idx})" title="Click for details" style="${cardOpacity}">
      <div class="pos-hdr">
        <span class="pos-sym">${p.symbol}${p.instrument === 'option' ? ' <span style="font-size:9px;color:var(--cyan);font-weight:600">OPT</span>' : ''}${pendingBadge}${trancheBadge} <span style="font-size:10px;color:var(--muted2);font-weight:400">${p.dir} ×${Math.abs(p.qty)}</span></span>
        <span style="display:flex;align-items:center;gap:6px">
          ${isPending ? '<span style="font-size:10px;color:var(--yellow)">Awaiting fill</span>' : `<span class="pos-pnl" style="color:${col}">${p.pnl >= 0 ? '+' : ''}${fmt$(p.pnl)}</span>`}
          ${actionBtn}
        </span>
      </div>
      ${optSub}
      ${isPending ? '' : `<div class="pos-bar-bg"><div class="pos-bar" style="width:${bw}%;background:${col}"></div></div>`}
      <div class="pos-meta">
        ${isPending ? `<span style="color:var(--yellow)">Limit ${fmt$(p.entry)}</span>` : `<span style="color:var(--orange);font-weight:600">${fmt$(p.posValue)}</span>`}
        ${isPending ? '' : `<span>${p.pct >= 0 ? '+' : ''}${p.pct.toFixed(2)}%</span>`}
        ${isPending ? '' : `<span>Entry ${fmt$(p.entry)}</span>`}
        ${isPending ? '' : `<span title="${p._price_sources || 'unknown'}">Now ${fmt$(p.current)}</span>`}
      </div>
      ${isPending ? '' : `<div class="pos-meta"><span>SL ${fmt$(p.sl)}</span><span>TP ${fmt$(p.tp)}</span></div>`}
    </div>`;
  }).join('');
}

// ── Today's Results ────────────────────────────────────────
// Shows closed trades from today with P&L, direction, exit reason.
function renderTodaysTrades(allTrades) {
  const el = document.getElementById('trades-list');
  if (!allTrades || !allTrades.length) {
    el.innerHTML = '<div class="empty">No closed trades today</div>';
    return;
  }

  const todayStr = new Date().toISOString().slice(0, 10); // YYYY-MM-DD
  const todayTrades = allTrades
    .filter(t => {
      const ts = t.timestamp || t.exit_time || '';
      return ts.slice(0, 10) === todayStr && t.exit_price != null;
    })
    .sort((a, b) => new Date(b.timestamp || b.exit_time || 0) - new Date(a.timestamp || a.exit_time || 0));

  if (!todayTrades.length) {
    el.innerHTML = '<div class="empty">No closed trades today</div>';
    return;
  }

  const exitLabels = {
    'stop_loss':     'SL',
    'take_profit':   'TP',
    'agent_sell':    'Exit',
    'trailing_stop': 'Trail',
    'manual':        'Manual',
    'kill':          'Kill'
  };

  el.innerHTML = todayTrades.map(t => {
    const dir = t.direction || (t.action === 'BUY' ? 'LONG' : 'SHORT');
    const dirColor = dir === 'LONG' ? 'var(--green)' : 'var(--red)';
    const pnl = t.pnl || 0;
    const pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    const pnlSign = pnl >= 0 ? '+' : '';
    const exitLabel = exitLabels[t.exit_reason] || (t.exit_reason || '—');
    const exitColor = t.exit_reason === 'stop_loss' ? 'var(--red)' :
                      t.exit_reason === 'take_profit' ? 'var(--green)' : 'var(--muted2)';
    const entry = t.entry_price || 0;
    const exit  = t.exit_price  || 0;
    const holdMin = t.hold_minutes ? (t.hold_minutes >= 60
      ? (t.hold_minutes / 60).toFixed(1) + 'h'
      : t.hold_minutes + 'm') : '';
    return `<div class="trade-row" style="padding:6px 10px;display:flex;flex-direction:column;gap:2px;border-bottom:1px solid var(--border)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:700;font-size:12px">${t.symbol || '—'} <span style="font-size:9px;color:${dirColor};font-weight:600">${dir}</span></span>
        <span style="font-weight:700;color:${pnlColor}">${pnlSign}${fmt$(pnl)}</span>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--muted2)">
        <span>${fmt$(entry)} → ${fmt$(exit)}</span>
        <span style="color:${exitColor}">${exitLabel}${holdMin ? ' · ' + holdMin : ''}</span>
      </div>
    </div>`;
  }).join('');
}

// ── Order rendering ───────────────────────────────────────
let allOrders = [];
let currentOrderFilter = 'all';

function filterOrders(filter, btn) {
  currentOrderFilter = filter;
  const parent = btn.parentElement;
  parent.querySelectorAll('.f-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderOrders();
}

function renderOrders() {
  const el = document.getElementById('orders-body');
  let filtered = [...allOrders];

  if (currentOrderFilter === 'submitted') filtered = filtered.filter(o => ['SUBMITTED','PRESUBMITTED'].includes((o.status||'').toUpperCase()));
  if (currentOrderFilter === 'filled')    filtered = filtered.filter(o => (o.status||'').toUpperCase() === 'FILLED');
  if (currentOrderFilter === 'cancelled') filtered = filtered.filter(o => (o.status||'').toUpperCase() === 'CANCELLED');
  if (currentOrderFilter === 'stocks')    filtered = filtered.filter(o => (o.instrument||'stock') === 'stock');
  if (currentOrderFilter === 'options')   filtered = filtered.filter(o => (o.instrument||'') === 'option');

  // Sort newest first
  filtered.sort((a,b) => new Date(b.timestamp||0) - new Date(a.timestamp||0));

  if (!filtered.length) { el.innerHTML = '<div class="empty">No orders match this filter.</div>'; return; }

  const sanitizePrice = p => (typeof p === 'number' && isFinite(p) && Math.abs(p) < 1e10) ? p : 0;

  el.innerHTML = filtered.map(o => {
    const ts = o.timestamp || '';
    const side = o.side || '—';
    const sideClass = side === 'BUY' ? 'tb' : 'ts2';
    const status = (o.status || 'UNKNOWN').toUpperCase();
    const statusColor = status === 'FILLED' ? 'var(--green)' :
                        status === 'CANCELLED' ? 'var(--red)' :
                        ['SUBMITTED','PRESUBMITTED'].includes(status) ? 'var(--yellow)' : 'var(--muted2)';
    const role = o.role || '—';
    const roleLabel = role === 'stop_loss' ? 'SL' :
                      role === 'take_profit' ? 'TP' :
                      role === 'close' ? 'CLOSE' :
                      role === 'emergency_flatten' ? 'KILL' : role === '—' ? 'ENTRY' : role.toUpperCase();
    const fillPx = o.fill_price ? fmt$(sanitizePrice(o.fill_price)) : '—';
    const sym = o.symbol || '—';
    const optLabel = o.instrument === 'option' ? ` ${o.right||''}${o.strike ? ' $'+o.strike : ''}` : '';
    const px = sanitizePrice(o.price);

    return `<div class="tr">
      <span>${ts ? ts.slice(0,16).replace('T',' ') : '—'}</span>
      <span>${sym}${optLabel}</span>
      <span><span class="ts ${sideClass}">${side}</span></span>
      <span style="color:var(--muted2)">${o.order_type || '—'}</span>
      <span style="color:var(--orange)">${o.qty || '—'}</span>
      <span>${px ? fmt$(px) : '—'}</span>
      <span>${fillPx}</span>
      <span style="color:${statusColor};font-weight:700">${status}</span>
      <span style="color:var(--muted2)">${roleLabel}</span>
      <span>${['SUBMITTED','PRESUBMITTED'].includes(status) && o.order_id ? `<button onclick="cancelOrder(${o.order_id}, ${JSON.stringify(sym)})" style="background:rgba(255,23,68,.15);border:1px solid var(--red);color:var(--red);border-radius:3px;cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:10px;padding:2px 8px;font-weight:700" title="Cancel order #${o.order_id}">✕</button>` : ''}</span>
    </div>`;
  }).join('');
}

// ── Instrument type detection ──────────────────────────────
function getInstrumentType(t) {
  // Explicit field takes priority
  if (t.instrument) return t.instrument.toLowerCase();
  if (t.asset_class) return t.asset_class.toLowerCase();
  // Options: has strike, expiry, right, contracts, or option-like symbol
  if (t.strike || t.expiry || t.right || t.contracts || t.option_type) return 'option';
  if (t.symbol && /\d{6}[CP]\d+/.test(t.symbol)) return 'option';
  // FX: currency pair patterns
  if (t.symbol && /^[A-Z]{3}\.?[A-Z]{3}$/.test(t.symbol)) return 'fx';
  const fxPairs = ['EUR','GBP','JPY','CHF','AUD','NZD','CAD'];
  if (t.symbol && fxPairs.some(p => t.symbol.startsWith(p) || t.symbol.endsWith(p))) return 'fx';
  // Default: stock
  return 'stock';
}

// ── Render history view ────────────────────────────────────
function renderHistory() {
  const el = document.getElementById('hist-body');
  // Show all closed trades — any trade with an exit_price
  let filtered = allTrades.filter(t => t.exit_price != null);
  if (currentFilter === 'wins')    filtered = filtered.filter(t => t.pnl > 0);
  if (currentFilter === 'losses')  filtered = filtered.filter(t => t.pnl <= 0);
  if (currentFilter === 'stocks')  filtered = filtered.filter(t => getInstrumentType(t) === 'stock');
  if (currentFilter === 'options') filtered = filtered.filter(t => getInstrumentType(t) === 'option');
  if (currentFilter === 'fx')      filtered = filtered.filter(t => getInstrumentType(t) === 'fx');
  // Sort newest first
  filtered = filtered.sort((a,b) => new Date(b.timestamp||b.exit_time||0) - new Date(a.timestamp||a.exit_time||0));
  if (!filtered.length) { el.innerHTML = '<div class="empty">No closed trades yet.</div>'; return; }
  el.innerHTML = filtered.map((t, idx) => {
    const pnlClass = t.pnl >= 0 ? 'pp' : 'pn';
    const direction = t.direction || (t.action === 'BUY' ? 'LONG' : 'SHORT');
    const ts = t.timestamp || t.exit_time || '';
    const qty = t.qty || t.shares || t.total_shares || '—';
    const uid = 'te-' + idx;
    const tKey = _explainKey(t);
    const wasOpen = _openExplains.has(tKey);
    const explanation = buildTradeExplanation(t);
    return `<div class="tr tr-clickable" onclick="toggleExplain('${uid}','${tKey.replace(/'/g,"\\'")}')">
      <span><span class="expand-arrow${wasOpen ? ' open' : ''}" id="arr-${uid}">▶</span> ${ts ? ts.slice(0,16).replace('T',' ') : '—'}</span>
      <span>${t.symbol || '—'}${getInstrumentType(t) === 'option' ? ' <span style="font-size:9px;color:var(--cyan);font-weight:600">' + (t.right||'') + (t.strike ? ' $'+t.strike : '') + '</span>' : ''}</span>
      <span><span class="ts ${direction === 'LONG' ? 'tb' : 'ts2'}">${direction}</span></span>
      <span style="color:var(--orange)">${qty}</span>
      <span>${fmt$(t.entry_price)}</span>
      <span>${fmt$(t.exit_price)}</span>
      <span class="${pnlClass}">${t.pnl != null ? (t.pnl >= 0 ? '+' : '') + fmt$(t.pnl) : '—'}</span>
      <span>${t.hold_minutes ? t.hold_minutes + 'm' : '—'}</span>
      <span style="color:var(--muted2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.exit_reason || '—'}</span>
    </div>
    <div class="trade-explain${wasOpen ? ' open' : ''}" id="${uid}">
      <div class="explain-title">Why This Trade Was Taken</div>
      <div class="explain-body">${explanation}</div>
    </div>`;
  }).join('');
}

// Track which trade explanations are open (by symbol+timestamp key for stability across re-renders)
const _openExplains = new Set();

function _explainKey(t) {
  return (t.symbol || '') + '|' + (t.timestamp || t.exit_time || '');
}

function toggleExplain(uid, tradeKey) {
  const el = document.getElementById(uid);
  const arr = document.getElementById('arr-' + uid);
  if (!el) return;
  const opening = !el.classList.contains('open');
  el.classList.toggle('open');
  if (arr) arr.classList.toggle('open');
  if (opening) _openExplains.add(tradeKey);
  else _openExplains.delete(tradeKey);
}

function buildTradeExplanation(t) {
  const sym = t.symbol || 'this stock';
  const dir = t.direction || (t.action === 'BUY' ? 'LONG' : (t.action === 'SELL' || t.action === 'CLOSE') ? 'SHORT' : 'LONG');
  const isLong = dir === 'LONG';
  const isSentinel = (t.reasoning || '').includes('[SENTINEL]') || (t.source === 'sentinel');
  const isCatalyst = (t.reasoning || '').includes('[CATALYST') || (t.source === 'catalyst');
  const isBackfill = (t.source === 'ibkr_backfill') || (t.source === 'manual_backfill') ||
                     (t.reasoning || '').toLowerCase().includes('backfill') ||
                     (t.reasoning || '').toLowerCase().includes('reconciled');
  const rawReasoning = (t.reasoning || '')
    .replace(/\[SENTINEL\]/g, '')
    .replace(/\[CATALYST:[^\]]*\]/g, '')
    .trim();

  let story = '';

  // ── AGENT REASONING QUOTE — shown first if substantive ──
  const isSubstantive = rawReasoning.length > 60 && !isBackfill;
  if (isSubstantive) {
    const sourceTag = isSentinel ? '<span style="font-size:9px;color:var(--orange);letter-spacing:1px">NEWS SENTINEL</span> '
                    : isCatalyst ? '<span style="font-size:9px;color:var(--orange);letter-spacing:1px">CATALYST</span> '
                    : '<span style="font-size:9px;color:var(--muted2);letter-spacing:1px">AGENT REASONING</span> ';
    story += `<div style="border-left:2px solid var(--orange);padding:6px 10px;margin-bottom:10px;background:rgba(255,107,0,.04)">
      ${sourceTag}
      <div style="margin-top:4px;font-size:11px;color:var(--text);line-height:1.6">${esc(rawReasoning)}</div>
    </div>`;
  }

  // ── SCORE BREAKDOWN BARS (if available) ──
  if (t.score_breakdown && Object.keys(t.score_breakdown).length > 0) {
    const dims = Object.entries(t.score_breakdown);
    const maxVal = Math.max(...dims.map(([,v]) => Math.abs(v || 0)), 1);
    story += `<div style="margin-bottom:10px">
      <div style="font-size:9px;letter-spacing:1.5px;color:var(--muted2);margin-bottom:4px">SIGNAL DIMENSIONS</div>
      ${dims.map(([dim, val]) => {
        const pct = Math.min(Math.abs((val || 0) / maxVal) * 100, 100);
        const barColor = (val || 0) >= 0 ? 'var(--green)' : 'var(--red)';
        return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
          <div style="width:60px;font-size:9px;color:var(--muted2);text-transform:uppercase">${dim}</div>
          <div style="flex:1;height:4px;background:var(--border2);border-radius:2px">
            <div style="width:${pct}%;height:100%;background:${barColor};border-radius:2px"></div>
          </div>
          <div style="width:32px;font-size:9px;color:${barColor};text-align:right">${(val||0).toFixed(1)}</div>
        </div>`;
      }).join('')}
    </div>`;
  }

  // ── CONVICTION ──
  if (t.score && t.score > 0) {
    const hiConv  = _liveSettings.high_conviction_score || 38;
    const minConv = _liveSettings.min_score_to_trade   || 28;
    const convLabel = t.score >= hiConv ? 'very high' : t.score >= minConv ? 'moderate' : 'borderline';
    story += `Conviction: <strong>${convLabel}</strong> — scored ${t.score}/50. `;
  }

  // ── MARKET CONTEXT ──
  if (t.regime && t.regime !== 'UNKNOWN') {
    const regimeMap = {
      'TRENDING_UP':   'trending up — broad participation',
      'TRENDING_DOWN': 'trending down — broad selling',
      'RELIEF_RALLY':  'relief rally — bear-market bounce',
      'RANGE_BOUND':   'range bound — no clear direction',
      'CAPITULATION':  'capitulation — extreme fear'
    };
    const regimeDesc = regimeMap[t.regime] || t.regime;
    story += `Market regime: <strong>${regimeDesc}</strong>`;
    if (t.vix) story += ` | VIX: ${Number(t.vix).toFixed(0)}`;
    story += '. ';
  }

  // ── OUTCOME ──
  if (t.pnl != null) {
    const won = t.pnl >= 0;
    const holdStr = t.hold_minutes
      ? (t.hold_minutes >= 60 ? (t.hold_minutes / 60).toFixed(1) + 'h' : t.hold_minutes + 'm')
      : null;
    const exitMap = {
      'stop_loss':     'stop-loss triggered',
      'take_profit':   'take-profit hit',
      'agent_sell':    'agents voted to exit',
      'trailing_stop': 'trailing stop triggered',
      'manual':        'manually closed'
    };
    const exitDesc = exitMap[t.exit_reason] || t.exit_reason || 'position closed';
    story += won
      ? `<span class="pp"><strong>WIN: +${fmt$(t.pnl)}</strong></span>`
      : `<span class="pn"><strong>LOSS: ${fmt$(t.pnl)}</strong></span>`;
    if (holdStr) story += ` held ${holdStr}`;
    story += ` — ${exitDesc}.`;
  }

  // ── BACKFILL NOTICE ──
  if (isBackfill) {
    story += '<div style="color:var(--muted2);font-size:10px;margin-top:6px">Imported from IBKR history — no agent reasoning captured for this trade.</div>';
  }

  return story || '<div style="color:var(--muted2)">No reasoning recorded for this trade.</div>';
}

// ── Render agents view — dynamic from agent_conversation ───
// agent_conversation is [{agent, role, time, output}] built by bot_trading.py.
// No hardcoded agent names, count, or keys — all read from state.
function renderAgents(convo) {
  if (!convo || !convo.length) return;
  const el = document.getElementById('agents-grid');
  if (!el) return;
  el.innerHTML = convo.map((msg, i) => {
    const isFinal = i === convo.length - 1;
    const borderColor = isFinal ? 'var(--green)' : `hsl(${25 + i * 55}, 85%, 55%)`;
    return `<div class="agent-card" style="border-left:3px solid ${borderColor}">
      <div class="agent-header">
        <span class="agent-name" style="color:${borderColor}">${esc(msg.agent || '—')}</span>
        <span class="agent-accuracy" style="color:var(--muted2)">${esc(msg.time || '')}</span>
      </div>
      <div class="agent-role" style="font-size:10px;color:var(--muted2);margin-bottom:6px">${esc(msg.role || '')}</div>
      <div class="agent-last">${(msg.output || 'No output yet').slice(0, 400)}</div>
    </div>`;
  }).join('');
}

// ── Chart instances ────────────────────────────────────────
let equityChart = null;
let dailyChart  = null;
let allEquityData = [];
let allDailyData  = [];
let equityTF = '1D';
let dailyTF  = '1W';
// Fingerprints — only redraw charts when data actually changes
let _lastEquityFingerprint = '';
let _lastDailyFingerprint  = '';

const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: { duration: 400 },
  plugins: {
    legend: { display: false },
    tooltip: {
      backgroundColor: '#1A1A1A',
      borderColor: '#FF6B00',
      borderWidth: 1,
      titleColor: '#FF6B00',
      bodyColor: '#E8E8E8',
      titleFont: { family: 'JetBrains Mono', size: 11 },
      bodyFont:  { family: 'JetBrains Mono', size: 11 },
      callbacks: {
        label: ctx => ' $' + Number(ctx.parsed.y).toLocaleString('en', {minimumFractionDigits: 2})
      }
    }
  },
  scales: {
    x: {
      ticks: { color: '#555', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 8, maxRotation: 0 },
      grid:  { color: 'rgba(42,42,42,0.5)', drawBorder: false }
    },
    y: {
      ticks: { color: '#555', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 6,
               callback: v => '$' + Number(v).toLocaleString('en', {minimumFractionDigits: 0}) },
      grid:  { color: 'rgba(42,42,42,0.5)', drawBorder: false }
    }
  }
};

function filterByTF(data, tf) {
  if (!data || !data.length) return data;
  const now = new Date();
  let cutoff;
  if (tf === '1D')  cutoff = new Date(now - 1   * 86400000);
  else if (tf === '1W')  cutoff = new Date(now - 7   * 86400000);
  else if (tf === '1M')  cutoff = new Date(now - 30  * 86400000);
  else if (tf === 'MTD') cutoff = new Date(now.getFullYear(), now.getMonth(), 1);
  else if (tf === 'YTD') cutoff = new Date(now.getFullYear(), 0, 1);
  else return data; // ALL
  return data.filter(d => new Date(d.date.replace(/ [A-Z]{2,3}$/, '')) >= cutoff);
}

function setEquityTF(tf, btn) {
  equityTF = tf;
  _lastEquityFingerprint = '';  // Force redraw on explicit timeframe change
  document.querySelectorAll('#equity-card .tf-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderEquityChart(filterByTF(allEquityData, tf));
}

function setDailyTF(tf, btn) {
  dailyTF = tf;
  _lastDailyFingerprint = '';  // Force redraw on explicit timeframe change
  document.querySelectorAll('#daily-card .tf-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderDailyChart(buildDailyPnL(allEquityData, tf));
}

function renderEquityChart(data) {
  if (!data || data.length < 2) return;
  // Only redraw if data or timeframe actually changed
  const fp = equityTF + '_' + data.length + '_' + data[0].date + '_' + data[0].value + '_' + data[data.length-1].date + '_' + data[data.length-1].value;
  if (fp === _lastEquityFingerprint && equityChart) return;
  _lastEquityFingerprint = fp;

  const ctx = document.getElementById('equity-chart').getContext('2d');
  const labels = data.map(d => d.date);
  const values = data.map(d => d.value);
  const startVal = values[0];
  const isUp = values[values.length - 1] >= startVal;
  const lineColor = isUp ? '#00C853' : '#FF1744';
  const fillColor = isUp ? 'rgba(0,200,83,0.08)' : 'rgba(255,23,68,0.08)';

  if (equityChart) {
    // Update in-place — no flicker, no destroy
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = values;
    equityChart.data.datasets[0].borderColor = lineColor;
    equityChart.data.datasets[0].backgroundColor = fillColor;
    equityChart.data.datasets[0].pointBackgroundColor = lineColor;
    equityChart.data.datasets[0].pointBorderColor = lineColor;
    equityChart.data.datasets[0].pointRadius = data.length > 50 ? 0 : 3;
    equityChart.update('none');
    return;
  }
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: lineColor,
        borderWidth: 2,
        pointRadius: data.length > 50 ? 0 : 3,
        pointBackgroundColor: lineColor,
        pointBorderColor: lineColor,
        fill: true,
        backgroundColor: fillColor,
        tension: 0.3,
      }]
    },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        tooltip: {
          ...CHART_DEFAULTS.plugins.tooltip,
          callbacks: {
            title: ctx => ctx[0].label,
            label: ctx => ' Portfolio: $' + Number(ctx.parsed.y).toLocaleString('en', {minimumFractionDigits: 2}),
            afterLabel: ctx => {
              const pnl = ctx.parsed.y - startVal;
              const pct = startVal > 0 ? ((pnl / startVal) * 100).toFixed(2) : '0.00';
              return ' P&L: ' + (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) + ' (' + pct + '%)';
            }
          }
        }
      }
    }
  });
}

function buildDailyPnL(equityData, tf) {
  if (!equityData || equityData.length < 2) return [];

  // Group by date, take first and last value per day
  const byDate = {};
  equityData.forEach(d => {
    const date = d.date.split(' ')[0];
    if (!byDate[date]) byDate[date] = { open: d.value, close: d.value };
    byDate[date].close = d.value;
  });
  let days = Object.entries(byDate).map(([date, v]) => ({ date, pnl: v.close - v.open }));

  // If only 1 day of data, show HOURLY P&L bars instead of one giant bar
  if (days.length <= 1) {
    const byHour = {};
    equityData.forEach(d => {
      const hour = d.date.substring(0, 13) + ':00'; // "2026-03-25 14:00"
      if (!byHour[hour]) byHour[hour] = { open: d.value, close: d.value };
      byHour[hour].close = d.value;
    });
    const hours = Object.entries(byHour).map(([hour, v]) => ({
      date: hour.substring(11),  // just show "14:00" etc.
      pnl: v.close - v.open
    }));
    if (hours.length >= 2) return hours;
  }

  const now = new Date();
  let cutoff = null;
  if      (tf === '1W')  cutoff = new Date(now - 7  * 86400000);
  else if (tf === '1M')  cutoff = new Date(now - 30 * 86400000);
  else if (tf === 'MTD') cutoff = new Date(now.getFullYear(), now.getMonth(), 1);
  else if (tf === 'YTD') cutoff = new Date(now.getFullYear(), 0, 1);
  if (cutoff) days = days.filter(d => new Date(d.date) >= cutoff);
  return days;
}

function renderDailyChart(days) {
  if (!days || !days.length) return;
  // Only redraw if data or timeframe actually changed
  const fp = dailyTF + '_' + days.length + '_' + days[0].date + '_' + days[0].pnl + '_' + days[days.length-1].date + '_' + days[days.length-1].pnl;
  if (fp === _lastDailyFingerprint && dailyChart) return;
  _lastDailyFingerprint = fp;

  const ctx = document.getElementById('daily-chart').getContext('2d');
  const labels = days.map(d => d.date);
  const values = days.map(d => d.pnl);
  const colors = values.map(v => v >= 0 ? 'rgba(0,200,83,0.8)' : 'rgba(255,23,68,0.8)');
  const borders = values.map(v => v >= 0 ? '#00C853' : '#FF1744');

  if (dailyChart) {
    dailyChart.data.labels = labels;
    dailyChart.data.datasets[0].data = values;
    dailyChart.data.datasets[0].backgroundColor = colors;
    dailyChart.data.datasets[0].borderColor = borders;
    dailyChart.data.datasets[0].maxBarThickness = 60;
    dailyChart.update('none');
    return;
  }
  dailyChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderColor: borders,
        borderWidth: 1,
        borderRadius: 3,
        maxBarThickness: 60,
      }]
    },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        tooltip: {
          ...CHART_DEFAULTS.plugins.tooltip,
          callbacks: {
            title: ctx => ctx[0].label,
            label: ctx => ' P&L: ' + (ctx.parsed.y >= 0 ? '+' : '') + '$' + ctx.parsed.y.toFixed(2)
          }
        }
      }
    }
  });
}

// ── Render growth ──────────────────────────────────────────
function renderGrowth(perf, equity) {
  if (!perf) return;

  // Metrics
  const pnlEl = document.getElementById('g-pnl');
  pnlEl.textContent = (perf.total_pnl >= 0 ? '+' : '') + fmt$(perf.total_pnl);
  pnlEl.className = 'metric-val ' + (perf.total_pnl >= 0 ? 'cg' : 'cr');

  const wrEl = document.getElementById('g-wr');
  const wr = perf.win_rate != null ? perf.win_rate : null;
  wrEl.textContent = wr != null ? wr + '%' : '—';
  wrEl.className = 'metric-val ' + (wr >= 52 ? 'cg' : wr >= 45 ? 'co' : 'cr');

  const pfEl = document.getElementById('g-pf');
  const pf = perf.profit_factor;
  // profit_factor=0 means no losing trades (backend returns 0 when gross_loss=0) → show ∞
  pfEl.textContent = pf == null ? '—' : pf === 0 ? '∞' : pf;
  pfEl.className = 'metric-val ' + (pf === 0 || pf >= 1.5 ? 'cg' : 'co');

  document.getElementById('g-rl').textContent = perf.avg_win && perf.avg_loss
    ? Math.abs(perf.avg_win / perf.avg_loss).toFixed(2) + ':1' : '—';
  document.getElementById('g-total').textContent  = perf.total_trades || 0;
  document.getElementById('g-best').textContent   = perf.best_trade  ? '+' + fmt$(perf.best_trade)  : '—';
  document.getElementById('g-worst').textContent  = perf.worst_trade ? fmt$(perf.worst_trade) : '—';
  document.getElementById('g-exp').textContent    = perf.expectancy  ? (perf.expectancy >= 0 ? '+' : '') + fmt$(perf.expectancy) : '—';

  // Store full data for timeframe filtering
  if (equity && equity.length > 0) {
    allEquityData = equity;
    // Only render charts if the growth view is currently visible
    // (Chart.js needs a visible container to calculate dimensions)
    const growthView = document.getElementById('view-growth');
    if (growthView && growthView.classList.contains('active')) {
      renderEquityChart(filterByTF(equity, equityTF));
      renderDailyChart(buildDailyPnL(equity, dailyTF));
    }
  }
}

// ── Regime UI ─────────────────────────────────────────────
function updateRegime(regime) {
  const box   = document.getElementById('regime-box');
  const label = document.getElementById('regime-label');
  const meta  = document.getElementById('regime-meta');
  const pill  = document.getElementById('regime-pill');

  const classMap = {
    'TRENDING_UP':'bull','TRENDING_DOWN':'bear',
    'RELIEF_RALLY':'choppy','RANGE_BOUND':'choppy','CAPITULATION':'panic','UNKNOWN':'unknown'
  };
  box.className = 'regime-box ' + (classMap[regime.regime] || 'unknown');
  label.textContent = regime.regime || 'UNKNOWN';
  const routerStr = regime.regime_router && regime.regime_router !== 'disabled'
    ? ` | ROUTER: ${regime.regime_router.replace('_', '-').toUpperCase()}` : '';
  const vixRankStr  = regime.vix_rank   != null ? (regime.vix_rank * 100).toFixed(0) + '%' : '—';
  const kellyStr    = regime.kelly_fraction != null ? regime.kelly_fraction.toFixed(2) : '—';
  meta.textContent  = `VIX: ${regime.vix || '—'} | Rank: ${vixRankStr} | Kelly: ${kellyStr} | SPY: $${regime.spy_price || '—'}${routerStr}`;
  pill.textContent  = 'REGIME: ' + (regime.regime || '—');
}

// ── Main poll ──────────────────────────────────────────────
async function poll() {
  try {
    const d = await (await fetch('/api/state')).json();

    // TWS disconnected banner
    const twsBanner = document.getElementById('tws-banner');
    if (d.ibkr_disconnected) {
      twsBanner.style.display = 'flex';
      document.body.style.paddingTop = '46px';
    } else {
      twsBanner.style.display = 'none';
      document.body.style.paddingTop = '';
    }

    // Header
    const pill = document.getElementById('bot-pill');
    const stat = document.getElementById('bot-status');
    if (d.status === 'running') { pill.className = 'pill pg'; stat.textContent = 'Running ●'; }
    else if (d.ibkr_disconnected) { pill.className = 'pill pr'; stat.textContent = 'TWS Disconnected'; }
    else { pill.className = 'pill pr'; stat.textContent = 'Stopped'; }
    document.getElementById('upd-time').textContent = 'Updated ' + new Date().toTimeString().slice(0, 8);

    // Stats
    document.getElementById('s-val').textContent    = d.portfolio_value ? fmt$(d.portfolio_value) : '—';
    document.getElementById('s-acc').textContent    = d.account || '';
    const pnl = d.daily_pnl || 0;
    const pnlEl = document.getElementById('s-pnl');
    pnlEl.textContent = (pnl >= 0 ? '+' : '-') + fmt$(Math.abs(pnl));
    pnlEl.className   = 'sv ' + (pnl >= 0 ? 'cg' : 'cr');
    const pnlPct = (d.portfolio_value > 0) ? (pnl / d.portfolio_value * 100).toFixed(3) : '0.000';
    const pnlPctEl = document.getElementById('s-pnlp');
    pnlPctEl.textContent = (pnl >= 0 ? '+' : '') + pnlPct + '% today';
    pnlPctEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    document.getElementById('s-session').textContent = d.session || '—';
    document.getElementById('s-scans').textContent   = d.scan_count || 0;
    document.getElementById('s-last').textContent    = d.last_scan ? 'Last: ' + d.last_scan : 'Never';
    document.getElementById('s-pos').textContent     = (d.positions || []).length;
    document.getElementById('s-trades').textContent  = (d.all_trades || []).length;
    if (d.performance) {
      document.getElementById('s-wr').textContent = 'Win: ' + d.performance.win_rate + '%';
    }

    // KPI Row 2 — Account details
    if (d.account_details) {
      const ad = d.account_details;
      const pv = d.portfolio_value || 1;

      // Available Cash
      const cash = ad.total_cash || ad.available_cash || 0;
      document.getElementById('s-cash').textContent = fmt$(cash);
      document.getElementById('s-cash').className = 'sv ' + (cash > 0 ? 'cg' : 'cr');
      const cashPct = (cash / pv * 100).toFixed(1);
      document.getElementById('s-cash-pct').textContent = cashPct + '% of portfolio';

      // Buying Power
      document.getElementById('s-bp').textContent = ad.buying_power ? fmt$(ad.buying_power) : '—';

      // Unrealized P&L
      const upnl = ad.unrealized_pnl || 0;
      const upnlEl = document.getElementById('s-upnl');
      upnlEl.textContent = (upnl >= 0 ? '+' : '-') + fmt$(Math.abs(upnl));
      upnlEl.className = 'sv ' + (upnl >= 0 ? 'cg' : 'cr');

      // Realized P&L
      const rpnl = ad.realized_pnl || 0;
      const rpnlEl = document.getElementById('s-rpnl');
      rpnlEl.textContent = (rpnl >= 0 ? '+' : '-') + fmt$(Math.abs(rpnl));
      rpnlEl.className = 'sv ' + (rpnl >= 0 ? 'cg' : 'cr');

      // Margin Used
      const margin = ad.margin_used || 0;
      document.getElementById('s-margin').textContent = fmt$(margin);
      const marginPct = (margin / pv * 100).toFixed(1);
      document.getElementById('s-margin-pct').textContent = marginPct + '% utilization';

      // Excess Liquidity
      document.getElementById('s-excess').textContent = ad.excess_liquidity ? fmt$(ad.excess_liquidity) : '—';
    }

    // Regime
    if (d.regime) updateRegime(d.regime);

    // Session
    document.getElementById('session-name').textContent = d.session || '—';

    // Agents required — store globals for Last Decision card and color-code the vote
    const _req = d.agents_required || 4;
    const _agreed = d.last_agents_agreed;
    window._agentsRequired   = _req;
    window._lastAgentsAgreed = _agreed;
    window._lastScanTime     = d.last_scan || '';
    const _total = window._totalAgents || (d.agent_conversation || []).length || '';
    document.getElementById('agents-req').textContent = _req + (_total ? '/' + _total : '');
    const agreeEl = document.getElementById('last-agree');
    agreeEl.textContent = _agreed != null ? _agreed + (_total ? '/' + _total : '') : '—';
    if (_agreed != null) agreeEl.style.color = _agreed >= _req ? 'var(--green)' : 'var(--red)';
    else agreeEl.style.color = '';

    // Risk budget — limit from settings, not hardcoded
    if (d.portfolio_value) {
      const limit = d.portfolio_value * (d.settings?.daily_loss_limit || 0);
      const used  = Math.abs(Math.min(pnl, 0));
      const pct   = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
      document.getElementById('risk-bar').style.width     = pct + '%';
      document.getElementById('risk-bar').style.background = pct > 75 ? 'var(--red)' : pct > 50 ? 'var(--yellow)' : 'var(--green)';
      document.getElementById('risk-used').textContent    = fmt$(used) + ' used';
      document.getElementById('risk-left').textContent    = fmt$(limit - used) + ' left';
    }

    // Directional Skew (roadmap #07)
    if (d.skew) {
      const sk = d.skew['48h'] || d.skew;
      const sv = (isFinite(sk.skew) && sk.skew != null) ? sk.skew : 0;
      const skBar = document.getElementById('skew-bar');
      const pctWidth = Math.min(Math.abs(sv) * 50, 50); // cap at 50% each side
      if (sv >= 0) {
        skBar.style.left = '50%';
        skBar.style.width = pctWidth + '%';
        skBar.style.background = sv > 0.8 ? 'var(--red)' : sv > 0.5 ? 'var(--yellow)' : 'var(--green)';
      } else {
        skBar.style.left = (50 - pctWidth) + '%';
        skBar.style.width = pctWidth + '%';
        skBar.style.background = sv < -0.8 ? 'var(--red)' : sv < -0.5 ? 'var(--yellow)' : 'var(--green)';
      }
      const skValEl = document.getElementById('skew-val');
      skValEl.textContent = (sv >= 0 ? '+' : '') + sv.toFixed(2);
      skValEl.style.color = sk.regime_aligned === false ? 'var(--red)' : sk.regime_aligned === true ? 'var(--green)' : 'var(--orange)';
      document.getElementById('skew-detail').textContent = sk.long_count + 'L / ' + sk.short_count + 'S (48h)';
      const alertEl = document.getElementById('skew-alert');
      if (sk.alert) { alertEl.textContent = sk.alert; alertEl.style.display = 'block'; }
      else { alertEl.style.display = 'none'; }
    }

    // Logs — only rebuild DOM when count changes (preserves scroll position)
    const logArea = document.getElementById('log-area');
    if (d.logs && d.logs.length && d.logs.length !== _lastLogCount) {
      _lastLogCount = d.logs.length;
      logArea.innerHTML = d.logs.map(l =>
        `<div class="log-row"><span class="lt">${esc(l.time)}</span><span class="lk lk-${esc(l.type)}">${esc(l.type)}</span><span class="lm">${esc(l.msg)}</span></div>`
      ).join('');
      document.getElementById('log-count').textContent = d.logs.length + ' events';
    }

    // AI box (element removed from LIVE view — no-op)
    // if (d.claude_analysis) document.getElementById('ai-box').textContent = d.claude_analysis;
    renderOpusView(d);

    // Positions and today's results
    renderPositions(d.positions);
    if (d.all_trades) renderTodaysTrades(d.all_trades);

    // History view
    if (d.all_trades) { allTrades = d.all_trades; renderHistory(); }

    // Orders view
    if (d.all_orders) { allOrders = d.all_orders; renderOrders(); }

    // Growth view
    if (d.performance) renderGrowth(d.performance, d.equity_history);

    // Agents view — rendered from conversation (dynamic, no hardcoded names)
    if (d.agent_conversation && d.agent_conversation.length) {
      window._totalAgents = d.agent_conversation.length;
      renderAgents(d.agent_conversation);
    }

    // Trade card — last decision with history navigation
    const incoming = d.decision_history && d.decision_history.length
      ? d.decision_history.slice().reverse()   // most-recent first
      : (d.last_decision ? [d.last_decision] : []);
    if (incoming.length && (!_decisionHistory.length ||
        (incoming[0] && _decisionHistory[0] && incoming[0].timestamp !== _decisionHistory[0].timestamp))) {
      _decisionIdx = 0;
    }
    _decisionHistory = incoming;
    renderTradeCard(_decisionHistory[_decisionIdx] || null);

    // Agent conversation (full agents view only — live panel replaced by trade card)
    if (d.agent_conversation && d.agent_conversation.length) {
      renderAgentConvoFull(d.agent_conversation, d.last_scan);
    }

    // News view
    if (d.news_data) renderNews(d.news_data);

    // Risk view — daily limit from settings
    if (d.portfolio_value) {
      const pv   = d.portfolio_value;
      const limit = pv * (d.settings?.daily_loss_limit || 0);
      const used  = Math.abs(Math.min(pnl, 0));
      const expPct = ((d.positions||[]).length / 6) * 100;
      const deployed = (d.positions||[]).reduce((s, p) => {
        const mult = p.instrument === 'option' ? 100 : 1;
        return s + Math.abs(p.current * p.qty * mult);
      }, 0) / pv * 100;

      document.getElementById('r-daily-bar').style.width   = Math.min(used/limit*100,100) + '%';
      document.getElementById('r-daily-used').textContent  = `${fmt$(used)} of ${fmt$(limit)}`;
      document.getElementById('r-daily-pct').textContent   = (used/limit*100).toFixed(1) + '%';
      document.getElementById('r-exp-bar').style.width     = Math.min(deployed,100) + '%';
      document.getElementById('r-exp-used').textContent    = (d.positions||[]).length + ' positions';
      document.getElementById('r-exp-pct').textContent     = deployed.toFixed(1) + '% deployed';
      document.getElementById('r-cash-bar').style.width    = Math.min(100 - deployed, 100) + '%';
      document.getElementById('r-cash-pct').textContent    = (100 - deployed).toFixed(1) + '% cash';
    }

    // Cache latest settings so functions without `d` scope can read them
    if (d.settings) _liveSettings = d.settings;

    // Settings — populate form inputs from live CONFIG values
    document.getElementById('cfg-account').textContent = d.account || '—';
    document.getElementById('cfg-status').textContent  = d.status  || '—';
    if (d.settings && !document.activeElement?.classList.contains('setting-input')) {
      // Only update form values when user is NOT actively editing an input
      const s = d.settings;
      document.getElementById('cfg-risk-pct').value    = (s.risk_pct_per_trade * 100).toFixed(1);
      document.getElementById('cfg-daily-limit').value = (s.daily_loss_limit * 100).toFixed(1);
      document.getElementById('cfg-max-pos').value     = s.max_positions;
      document.getElementById('cfg-cash-reserve').value = (s.min_cash_reserve * 100).toFixed(0);
      document.getElementById('cfg-max-single').value  = (s.max_single_position * 100).toFixed(0);
      document.getElementById('cfg-min-score').value   = s.min_score_to_trade;
      document.getElementById('cfg-high-score').value  = s.high_conviction_score;
      document.getElementById('agree-select').value    = s.agents_required_to_agree;
      document.getElementById('cfg-opt-min-score').value = s.options_min_score;
      document.getElementById('cfg-opt-risk').value    = (s.options_max_risk_pct * 100).toFixed(1);
      document.getElementById('cfg-opt-ivr').value     = s.options_max_ivr;
      document.getElementById('cfg-opt-delta').value   = s.options_target_delta;
      document.getElementById('cfg-opt-delta-range').value = s.options_delta_range;
      // Sentinel settings
      if (s.sentinel_enabled != null) {
        document.getElementById('cfg-sentinel-enabled').value  = String(s.sentinel_enabled);
        document.getElementById('cfg-sentinel-poll').value     = s.sentinel_poll_seconds;
        document.getElementById('cfg-sentinel-cooldown').value = s.sentinel_cooldown_minutes;
        document.getElementById('cfg-sentinel-max-trades').value = s.sentinel_max_trades_per_hour;
        document.getElementById('cfg-sentinel-risk-mult').value  = s.sentinel_risk_multiplier;
        document.getElementById('cfg-sentinel-kw-thresh').value  = s.sentinel_keyword_threshold;
        document.getElementById('cfg-sentinel-min-conf').value   = s.sentinel_min_confidence;
        document.getElementById('cfg-sentinel-ibkr').value     = String(s.sentinel_use_ibkr);
        document.getElementById('cfg-sentinel-finviz').value   = String(s.sentinel_use_finviz);
      }
    }

    // Capital management display
    if (d.effective_capital) {
      document.getElementById('cfg-start-cap').textContent = fmt$(d.effective_capital || 1000000);
      document.getElementById('cfg-eff-cap').textContent   = fmt$(d.effective_capital);
      const cpnl = (d.portfolio_value || 0) - d.effective_capital;
      const cpnlEl = document.getElementById('cfg-current-pnl');
      cpnlEl.textContent = (cpnl >= 0 ? '+' : '') + fmt$(cpnl);
      cpnlEl.style.color = cpnl >= 0 ? 'var(--green)' : 'var(--red)';
    }

    // Hot reload indicator
    if (d.last_reload && d.last_reload_files && d.last_reload_files.length > 0) {
      const pill = document.getElementById('reload-pill');
      const info = document.getElementById('reload-info');
      if (pill && info) {
        pill.style.display = 'flex';
        info.textContent = d.last_reload_files.join(', ') + ' @ ' + d.last_reload;
        setTimeout(() => { pill.style.display = 'none'; }, 30000);
      }
    }

    // Scan progress
    if (d.next_scan_seconds != null) {
      const total = d.scan_interval_seconds || 300;
      const pct   = Math.max(0, Math.min(100, ((total - d.next_scan_seconds) / total) * 100));
      document.getElementById('scan-fill').style.width   = pct + '%';
      document.getElementById('scan-status').textContent = d.scanning ? '⚡ Scanning...' : 'Next scan in';
      document.getElementById('scan-eta').textContent    = d.next_scan_seconds + 's';
    }

  } catch(e) {
    document.getElementById('bot-status').textContent = 'Connection Error';
  }
}

// ── Live price polling (1s) ────────────────────────────────────
// Hits /api/prices (QUOTE_CACHE mid-prices) every second.
// Mutates position card price/P&L display without a full re-render.
async function fetchPrices() {
  try {
    const r = await fetch('/api/prices');
    if (!r.ok) return;
    const data = await r.json();
    if (data.prices) applyLivePrices(data.prices);
  } catch(e) {}
}

function applyLivePrices(prices) {
  document.querySelectorAll('.pos-card[data-symbol]').forEach(card => {
    const sym = card.dataset.symbol;
    const p   = prices[sym];
    if (!p || p.source === 'stale' || p.mid <= 0) return;

    const entry = parseFloat(card.dataset.entry) || 0;
    const qty   = parseFloat(card.dataset.qty)   || 0;
    const dir   = card.dataset.direction || 'LONG';
    if (!entry || !qty) return;

    const mid  = p.mid;
    const pnl  = dir === 'SHORT' ? (entry - mid) * qty : (mid - entry) * qty;

    // Update P&L display
    const pnlEl = card.querySelector('.pos-pnl');
    if (pnlEl) {
      pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt$(Math.abs(pnl));
      pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
    }

    // Show live indicator dot next to symbol
    let liveEl = card.querySelector('.live-dot');
    if (!liveEl) {
      liveEl = document.createElement('span');
      liveEl.className = 'live-dot';
      liveEl.style.cssText = 'font-size:7px;margin-left:4px;vertical-align:middle';
      const symEl = card.querySelector('.pos-sym');
      if (symEl) symEl.appendChild(liveEl);
    }
    liveEl.textContent = '●';
    liveEl.style.color = p.source === 'stream' ? 'var(--green)' : 'var(--muted)';
  });

  // Update regime SPY price if present
  const spyP = prices['SPY'];
  if (spyP && spyP.mid > 0) {
    const el = document.getElementById('regime-spy');
    if (el) el.textContent = '$' + spyP.mid.toFixed(2);
  }
}

// ── Poll guard: skip tick if previous fetch is still in flight ─
async function twsReconnect() {
  const btn = document.getElementById('tws-reconnect-btn');
  btn.disabled = true; btn.textContent = '↺ Connecting…';
  try {
    const r = await fetch('/api/reconnect', {method:'POST'});
    const j = await r.json();
    if (!j.ok) throw new Error(j.msg || 'failed');
    btn.textContent = '✓ Signal sent';
    setTimeout(() => { btn.disabled = false; btn.textContent = '↺ Reconnect'; }, 5000);
  } catch(e) {
    btn.textContent = '✗ Failed';
    setTimeout(() => { btn.disabled = false; btn.textContent = '↺ Reconnect'; }, 3000);
  }
}

let _pollInFlight = false;
let _lastLogCount = 0;
poll();
setInterval(async () => {
  if (_pollInFlight) return;
  _pollInFlight = true;
  try { await poll(); } finally { _pollInFlight = false; }
}, 2000);
setInterval(fetchPrices, 1000);  // live price updates between full polls
setInterval(loadNews, 90_000);   // auto-refresh news every 90 s
loadNews();                       // load immediately on page open

// ── News rendering ─────────────────────────────────────────
let _allNewsItems = [];
window._newsItems  = [];  // filtered set, used by drawer

const _MACRO_KW = ['fed','fomc','federal reserve','interest rate','rate cut','rate hike',
  'inflation','cpi','pce','gdp','recession','yield','treasury','tariff','trade war',
  'geopolit','china','russia','iran','war','sanction','imf','ecb','boj','central bank',
  'powell','yellen','lagarde','economy','jobs report','nonfarm','payroll','unemployment',
  'opec','oil price','gas price','nuclear','nato','ukraine','taiwan','debt ceiling',
  'fiscal','deficit','earnings','guidance','merger','acquisition','ipo'];

function _macroScore(item) {
  const txt = ((item.headline||'')+' '+(item.catalyst||'')).toLowerCase();
  return _MACRO_KW.filter(kw => txt.includes(kw)).length * 2 + (item.news_score || 0);
}

function _ageStr(h) {
  if (h < 1)  return 'Just now';
  if (h < 24) return Math.round(h) + 'h ago';
  return Math.round(h / 24) + 'd ago';
}

function _imgPh(sym, sentiment) {
  let h = 0;
  for (let i = 0; i < sym.length; i++) h = (h * 31 + sym.charCodeAt(i)) & 0xffff;
  h = h % 360;
  const tint = sentiment === 'BULLISH' ? '0,200,83' : sentiment === 'BEARISH' ? '255,23,68' : '255,107,0';
  return `background:linear-gradient(135deg,hsl(${h},55%,14%) 0%,hsl(${h},35%,8%) 100%);box-shadow:inset 0 0 0 1000px rgba(${tint},.18);color:hsl(${h},75%,72%);text-shadow:0 1px 4px rgba(0,0,0,.6)`;
}

function _imgHtml(item, cls='news-card-img') {
  const ph = `<div class="news-card-img-ph" style="${_imgPh(item.symbol||'?', item.sentiment)}">${esc(item.symbol||'?')}</div>`;
  if (!item.image_url) return `<div class="${cls}">${ph}</div>`;
  // Route through local proxy to avoid hotlink blocks and CORS issues
  const src = '/api/img-proxy?url=' + encodeURIComponent(item.image_url);
  return `<div class="${cls}"><img src="${src}" alt="" loading="lazy" onerror="this.style.display='none';this.nextSibling.style.display='flex'">${ph}</div>`;
}

function _renderHero(item, idx) {
  const badgeCls = item.sentiment === 'BULLISH' ? 'badge-bullish' : item.sentiment === 'BEARISH' ? 'badge-bearish' : 'badge-neutral';
  const badgeTxt = item.sentiment === 'BULLISH' ? '▲ BULLISH' : item.sentiment === 'BEARISH' ? '▼ BEARISH' : '— NEUTRAL';
  const ms = _macroScore(item);
  const macroTag = ms >= 4 ? `<span class="news-card-hero-label">● MACRO IMPACT</span>` : `<span class="news-card-hero-label">Top Story</span>`;
  return `<div class="news-card news-card-hero" onclick="openNewsDrawer(${idx})">
    ${_imgHtml(item)}
    <div class="news-card-top">
      <div>
        ${macroTag}
        <div class="news-card-tag">
          <span class="news-badge ${badgeCls}">${badgeTxt}</span>
          <span class="news-card-sym">${esc(item.symbol)}</span>
        </div>
        <div class="news-card-hl" style="margin-top:8px">${esc(item.headline)}</div>
        ${item.catalyst ? `<div class="news-card-catalyst">${esc(item.catalyst)}</div>` : ''}
      </div>
      <div class="news-card-foot" style="border-top:1px solid var(--border);margin:0 -18px -18px;padding:6px 18px">
        <span class="news-card-age">${_ageStr(item.recency)}</span>
        <span class="news-card-score" style="color:${item.news_score>=7?'var(--green)':item.news_score>=4?'var(--orange)':'var(--muted2)'}">Score ${item.news_score}/10</span>
      </div>
    </div>
  </div>`;
}

function _renderCard(item, idx) {
  const badgeCls = item.sentiment === 'BULLISH' ? 'badge-bullish' : item.sentiment === 'BEARISH' ? 'badge-bearish' : 'badge-neutral';
  const badgeTxt = item.sentiment === 'BULLISH' ? '▲ BULL' : item.sentiment === 'BEARISH' ? '▼ BEAR' : '— NEUT';
  return `<div class="news-card" onclick="openNewsDrawer(${idx})">
    ${_imgHtml(item)}
    <div class="news-card-top">
      <div class="news-card-tag">
        <span class="news-card-sym">${esc(item.symbol)}</span>
        <span class="news-badge ${badgeCls}" style="font-size:8px;padding:2px 6px">${badgeTxt}</span>
      </div>
      <div class="news-card-hl">${esc(item.headline)}</div>
      ${item.catalyst ? `<div class="news-card-catalyst">${esc(item.catalyst)}</div>` : ''}
    </div>
    <div class="news-card-foot">
      <span class="news-card-age">${_ageStr(item.recency)}</span>
      <span class="news-card-score" style="color:${item.news_score>=7?'var(--green)':item.news_score>=4?'var(--orange)':'var(--muted2)'}">Score ${item.news_score}/10</span>
    </div>
  </div>`;
}

async function loadNews() {
  const btn = document.getElementById('news-fetch-btn');
  const upd = document.getElementById('news-updated');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ Loading…'; }
  try {
    const r = await fetch('/api/news');
    const j = await r.json();
    const articles = j.articles || [];
    _allNewsItems = articles.map(a => ({
      symbol:           (a.symbols || [])[0] || '—',
      headline:         a.headline || '',
      summary:          a.summary  || '',
      sentiment:        a.sentiment  || 'NEUTRAL',
      keyword_score:    a.keyword_score || 0,
      catalyst:         a.catalyst  || '',
      recency:          a.age_hours || 999,
      news_score:       a.news_score || 0,
      image_url:        a.image_url  || '',
      url:              a.url        || '#',
      macro_event:      a.macro_event      || false,
      macro_type:       a.macro_type       || '',
      macro_label:      a.macro_label      || '',
      macro_color:      a.macro_color      || '',
      macro_impact:     a.macro_impact     || 0,
      macro_direction:  a.macro_direction  || '',
      macro_implication:a.macro_implication|| '',
    }));
    window._newsItems = _allNewsItems;
    filterNews();
    if (upd) upd.textContent = 'Updated ' + new Date().toTimeString().slice(0,8);
  } catch(e) {
    if (upd) upd.textContent = 'Fetch failed';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⟳ Fetch News'; }
  }
}

function renderNews(newsData) {
  // Called by poll() when scan data arrives — merge into existing items
  if (!newsData || !Object.keys(newsData).length) return;
  const scanItems = [];
  for (const [sym, nd] of Object.entries(newsData)) {
    if (!nd.headlines || !nd.headlines.length) continue;
    scanItems.push({
      symbol: sym,
      headline: nd.headlines[0],
      sentiment: nd.claude_sentiment || 'NEUTRAL',
      keyword_score: nd.keyword_score || 0,
      catalyst: nd.claude_catalyst || '',
      recency: nd.recency_hours || 999,
      news_score: nd.news_score || 0,
      image_url: nd.image_url || '',
      url: 'https://finance.yahoo.com/quote/' + encodeURIComponent(sym) + '/news/',
    });
  }
  if (!scanItems.length) return;
  // Merge: deduplicate by headline, scan items take precedence
  const seen = new Set(scanItems.map(i => i.headline));
  const merged = [...scanItems, ..._allNewsItems.filter(i => !seen.has(i.headline))];
  _allNewsItems = merged;
  window._newsItems = _allNewsItems;
  filterNews();
}

function _renderMacroStrip(allItems) {
  const strip = document.getElementById('market-events-strip');
  const macroItems = allItems.filter(i => i.macro_event && i.macro_impact >= 3)
                             .sort((a, b) => b.macro_impact - a.macro_impact);
  if (!macroItems.length) {
    strip.style.display = 'block';
    strip.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;padding-bottom:10px;border-bottom:1px solid var(--border)">
        <span style="font-size:10px;font-weight:800;letter-spacing:.08em;color:var(--muted2)">⚡ MARKET EVENTS</span>
        <span style="font-size:10px;color:var(--muted2);font-style:italic">— No market-moving events identified by Sonnet</span>
      </div>`;
    return;
  }

  const dirIcon = d => d === 'BULLISH' ? '▲' : d === 'BEARISH' ? '▼' : d === 'MIXED' ? '⇅' : '—';
  const dirColor = d => d === 'BULLISH' ? 'var(--green)' : d === 'BEARISH' ? 'var(--red)' : d === 'MIXED' ? 'var(--orange)' : 'var(--muted2)';
  const impactBar = n => {
    const pct = Math.round((n / 10) * 100);
    const c = n >= 8 ? '#ff2222' : n >= 6 ? 'var(--orange)' : 'var(--yellow,#ffd700)';
    return `<div style="height:3px;background:var(--border);border-radius:2px;margin-top:6px"><div style="height:3px;width:${pct}%;background:${c};border-radius:2px"></div></div>`;
  };

  window._macroDrawerItems = macroItems;
  const cards = macroItems.map((item, i) => `
    <div onclick="openMacroDrawerItem(${i})" style="flex-shrink:0;width:240px;background:var(--bg1);border:1px solid ${item.macro_color};border-top:3px solid ${item.macro_color};border-radius:6px;padding:10px 12px;display:block;cursor:pointer">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
        <span style="background:${item.macro_color};color:#000;font-size:9px;font-weight:800;padding:2px 6px;border-radius:3px;letter-spacing:.05em">${esc(item.macro_label)}</span>
        <span style="color:${dirColor(item.macro_direction)};font-size:11px;font-weight:700">${dirIcon(item.macro_direction)}</span>
        <span style="margin-left:auto;font-size:10px;color:var(--muted2)">Impact ${item.macro_impact}/10</span>
      </div>
      <div style="font-size:11px;color:var(--text);font-weight:600;line-height:1.35;margin-bottom:4px">${esc(item.headline)}</div>
      <div style="font-size:10px;color:var(--muted2);line-height:1.4">${esc(item.macro_implication)}</div>
      ${impactBar(item.macro_impact)}
    </div>`).join('');

  strip.style.display = 'block';
  strip.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
      <span style="font-size:10px;font-weight:800;letter-spacing:.08em;color:var(--orange)">⚡ MARKET EVENTS</span>
      <span style="font-size:10px;color:var(--muted2)">${macroItems.length} identified by Sonnet</span>
    </div>
    <div style="display:flex;gap:10px;overflow-x:auto;padding-bottom:8px;scrollbar-width:thin">${cards}</div>`;
}

function filterNews() {
  const feed    = document.getElementById('news-feed');
  const keyword = (document.getElementById('news-keyword').value || '').toLowerCase();
  const ticker  = (document.getElementById('news-ticker').value || '').toUpperCase().trim();
  const sortBy  = document.getElementById('news-sort').value;
  const sentFilter = document.getElementById('news-sentiment-filter').value;

  let items = [..._allNewsItems];
  if (keyword)            items = items.filter(i => i.headline.toLowerCase().includes(keyword) || i.catalyst.toLowerCase().includes(keyword));
  if (ticker)             items = items.filter(i => i.symbol.includes(ticker));
  if (sentFilter !== 'all') items = items.filter(i => i.sentiment === sentFilter);

  if (sortBy === 'time')  items.sort((a, b) => a.recency - b.recency);
  if (sortBy === 'score') items.sort((a, b) => b.news_score - a.news_score);
  if (sortBy === 'macro') items.sort((a, b) => _macroScore(b) - _macroScore(a));

  // Render macro events strip (always from full unfiltered set so events never disappear)
  _renderMacroStrip(_allNewsItems);

  document.getElementById('news-count').textContent = items.length + ' stories';
  window._newsItems = items;

  if (!items.length) {
    feed.innerHTML = '<div class="empty" style="padding:40px 0;text-align:center"><div style="font-size:24px;opacity:.3;margin-bottom:10px">📰</div><div style="font-size:12px;color:var(--muted2)">No matching stories</div></div>';
    return;
  }

  // Hero = highest macro score; goes first in grid (spans 2 cols)
  let heroIdx = 0, bestScore = -1;
  items.forEach((item, i) => {
    const s = _macroScore(item);
    if (s > bestScore) { bestScore = s; heroIdx = i; }
  });

  const hero = items[heroIdx];
  const rest = items.filter((_, i) => i !== heroIdx);
  // All cards in one grid — hero spans 2 cols, rest are 1 col each
  const gridHtml = '<div class="news-grid">'
    + _renderHero(hero, heroIdx)
    + rest.map((item, i) => {
        const origIdx = i < heroIdx ? i : i + 1;
        return _renderCard(item, origIdx);
      }).join('')
    + '</div>';

  feed.innerHTML = gridHtml;
}

function openNewsDrawer(idx) {
  const item = (window._newsItems || [])[idx];
  if (!item) return;
  _populateNewsDrawer(item);
}

function openNewsDrawerAll(idx) {
  const item = _allNewsItems[idx];
  if (!item) return;
  _populateNewsDrawer(item);
}

function openMacroDrawerItem(i) {
  const item = window._macroDrawerItems && window._macroDrawerItems[i];
  if (!item) return;
  const allIdx = _allNewsItems.findIndex(n => n.headline === item.headline);
  if (allIdx >= 0) { _populateNewsDrawer(_allNewsItems[allIdx]); return; }
  _populateNewsDrawer(item);
}

function _populateNewsDrawer(item) {
  const badgeCls = item.sentiment === 'BULLISH' ? 'badge-bullish' : item.sentiment === 'BEARISH' ? 'badge-bearish' : 'badge-neutral';
  const badgeTxt = item.sentiment === 'BULLISH' ? '▲ BULLISH' : item.sentiment === 'BEARISH' ? '▼ BEARISH' : '— NEUTRAL';
  document.getElementById('nd-sym').textContent   = item.symbol;
  document.getElementById('nd-title').textContent = item.headline;
  document.getElementById('nd-hl').textContent    = item.headline;
  document.getElementById('nd-badge-row').innerHTML =
    `<span class="news-badge ${badgeCls}">${badgeTxt}</span>
     <span style="font-size:10px;color:var(--muted2)">Score ${item.news_score}/10</span>
     ${item.keyword_score !== 0 ? `<span style="font-size:10px;color:var(--orange)">kw ${item.keyword_score > 0 ? '+' : ''}${item.keyword_score}</span>` : ''}`;
  document.getElementById('nd-meta').innerHTML =
    `<span>${_ageStr(item.recency)}</span><span>${esc(item.source || 'News')}</span>`;
  const cw = document.getElementById('nd-catalyst-wrap');
  if (item.catalyst) {
    document.getElementById('nd-catalyst').textContent = item.catalyst;
    cw.style.display = '';
  } else {
    cw.style.display = 'none';
  }
  const loading = document.getElementById('nd-reader-loading');
  const iframe  = document.getElementById('nd-iframe');
  if (item.url && item.url.startsWith('http')) {
    loading.style.display = 'flex';
    iframe.src = '/api/article-proxy?url=' + encodeURIComponent(item.url);
  } else {
    loading.style.display = 'none';
    iframe.src = 'about:blank';
  }
  document.getElementById('news-overlay').classList.add('open');
  document.getElementById('news-drawer').classList.add('open');
}

function closeNewsDrawer() {
  document.getElementById('news-overlay').classList.remove('open');
  document.getElementById('news-drawer').classList.remove('open');
  document.getElementById('nd-iframe').src = 'about:blank';
}

// ── Agent Conversation ─────────────────────────────────────
function toggleConvo() { /* no-op — convo panel replaced by trade card */ }

function annotateIndicators(text) {
  // Escape raw text first so agent output can't inject HTML, then add our own annotation spans.
  let result = esc(text);
  const annotations = {
    'ADX': '<span class="indicator-tag tag-neutral" title="Average Directional Index — measures trend strength. &gt;25 = strong trend, &lt;20 = no trend">ADX</span>',
    'MFI': '<span class="indicator-tag tag-neutral" title="Money Flow Index — volume-weighted RSI. &gt;65 = overbought, &lt;35 = oversold">MFI</span>',
    'VWAP': '<span class="indicator-tag tag-neutral" title="Volume-Weighted Average Price — institutional benchmark. Above VWAP = bullish, below = bearish">VWAP</span>',
    'OBV': '<span class="indicator-tag tag-neutral" title="On-Balance Volume — cumulative volume direction. Rising = accumulation, falling = distribution">OBV</span>',
    'Donchian': '<span class="indicator-tag tag-neutral" title="Donchian Channel — 20-period high/low. Breakout = potential new trend">Donchian</span>',
    'Squeeze': '<span class="indicator-tag tag-squeeze" title="BB Squeeze — Bollinger Bands inside Keltner Channels = volatility compression. Explosive move incoming">Squeeze</span>',
    'EMA': '<span class="indicator-tag tag-neutral" title="Exponential Moving Average — trend direction. Bull aligned = 9&gt;21&gt;50, Bear = opposite">EMA</span>',
    'MACD': '<span class="indicator-tag tag-neutral" title="Moving Average Convergence Divergence — momentum and trend changes">MACD</span>',
    'RSI': '<span class="indicator-tag tag-neutral" title="Relative Strength Index — momentum oscillator. &gt;70 = overbought, &lt;30 = oversold">RSI</span>',
    'Bollinger': '<span class="indicator-tag tag-neutral" title="Bollinger Bands — volatility envelope around price. Squeeze = low volatility before big move">Bollinger</span>',
    'Keltner': '<span class="indicator-tag tag-neutral" title="Keltner Channel — ATR-based envelope. When BB is inside KC = squeeze">Keltner</span>',
  };
  for (const [term, html] of Object.entries(annotations)) {
    // Only annotate first occurrence per text block (avoids double-wrapping on re-render)
    const regex = new RegExp('\\b' + term + '\\b', 'i');
    result = result.replace(regex, html);
  }
  return result;
}

function prevDecision() {
  if (_decisionIdx < _decisionHistory.length - 1) {
    _decisionIdx++;
    renderTradeCard(_decisionHistory[_decisionIdx] || null);
  }
}

function nextDecision() {
  if (_decisionIdx > 0) {
    _decisionIdx--;
    renderTradeCard(_decisionHistory[_decisionIdx] || null);
  }
}

function copyDecision() {
  const ld = _decisionHistory[_decisionIdx];
  if (!ld) return;
  const lines = [];
  if (ld.symbol)        lines.push((ld.symbol) + (ld.company_name ? ' — ' + ld.company_name : '') + (ld.direction ? ' | ' + ld.direction : ''));
  if (ld.thesis)        lines.push('Thesis: ' + ld.thesis);
  if (ld.edge_why_now)  lines.push('Edge: ' + ld.edge_why_now);
  if (ld.risk)          lines.push('Risk: ' + ld.risk);
  if (ld.timestamp)     lines.push(ld.timestamp.replace('T',' ').slice(0,16));
  navigator.clipboard.writeText(lines.join('\n')).then(() => {
    const btn = document.getElementById('tc-copy-btn');
    if (btn) { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); }
  });
}

function renderOpusView(d) {
  const el   = document.getElementById('opus-view-body');
  const tsEl = document.getElementById('opus-view-ts');
  if (!el) return;

  const analystText = (d.agent_outputs || {}).trading_analyst || '';
  const summary     = d.claude_analysis || '';

  if (!analystText && !summary) {
    el.innerHTML = '<div style="color:var(--muted2);font-size:11px">Waiting for agents to run…</div>';
    return;
  }
  if (tsEl) tsEl.textContent = d.last_scan || '—';

  const macroMatch   = analystText.match(/MACRO:\s*(BULLISH|BEARISH|NEUTRAL|UNCERTAIN)/i);
  const macroVerdict = macroMatch ? macroMatch[1].toUpperCase() : null;


  let macroText = '';
  if (macroVerdict && analystText) {
    const after = analystText.split(/MACRO:\s*(?:BULLISH|BEARISH|NEUTRAL|UNCERTAIN)/i)[1] || '';
    macroText = after.split('\n')
      .map(l => l.trim())
      .filter(l => l && !/^(OPPORTUNITIES|SYMBOL|DIRECTION|CONVICTION|RATIONALE|INSTRUMENT|KEY RISK|COUNTER)/.test(l))
      .slice(0, 3).join(' ').slice(0, 300);
  }

  const verdictColor = macroVerdict === 'BULLISH'   ? '#00C853' :
                       macroVerdict === 'BEARISH'   ? '#FF1744' :
                       macroVerdict === 'UNCERTAIN' ? '#FFD600' : 'var(--muted2)';

  el.innerHTML =
    (macroVerdict
      ? `<span style="font-size:9px;font-weight:700;letter-spacing:1px;padding:2px 7px;border-radius:3px;` +
        `background:${verdictColor}22;border:1px solid ${verdictColor};color:${verdictColor};` +
        `display:inline-block;margin-bottom:5px">MACRO: ${macroVerdict}</span>`
      : '') +
    (macroText
      ? `<div style="font-size:10px;color:var(--muted2);line-height:1.55;margin-bottom:4px">${esc(macroText)}</div>`
      : '') +
    (summary
      ? `<div style="font-size:9px;color:var(--orange);letter-spacing:0.3px">${esc(summary)}</div>`
      : '');
}

function renderTradeCard(ld) {
  // Render the rich last-decision trade card (thesis / edge / risk / returns).
  const body = document.getElementById('trade-card-body');
  const ageEl = document.getElementById('trade-card-age');
  if (!body) return;

  // Nav counter + arrow colour
  const navPos  = document.getElementById('tc-nav-pos');
  const prevBtn = document.getElementById('tc-prev-btn');
  const nextBtn = document.getElementById('tc-next-btn');
  const total   = _decisionHistory.length;
  if (navPos)  navPos.textContent  = total ? (_decisionIdx + 1) + ' / ' + total : '';
  if (prevBtn) prevBtn.style.color = _decisionIdx < total - 1 ? 'var(--text)' : 'var(--muted)';
  if (nextBtn) nextBtn.style.color = _decisionIdx > 0          ? 'var(--text)' : 'var(--muted)';

  if (!ld || !ld.symbol) {
    body.innerHTML = '<div style="color:var(--muted2);font-size:11px">No trades taken yet.</div>';
    if (ageEl) ageEl.textContent = '';
    return;
  }

  const sym     = esc(ld.symbol);
  const co      = esc(ld.company_name || ld.symbol);
  const dir     = esc(ld.direction || 'BUY');
  const dirCls  = dir === 'BUY' ? 'tc-dir-buy' : 'tc-dir-sell';
  const alloc   = ld.allocation_pct != null ? ld.allocation_pct.toFixed(0) + '%' : '';
  const thesis  = esc(ld.thesis || '');
  const edge    = esc(ld.edge_why_now || '');
  const risk    = esc(ld.risk || '');
  const exp     = ld.expected_returns || {};
  const _tot    = window._totalAgents || '';
  const agents  = ld.agents_agreed != null ? ld.agents_agreed + (_tot ? '/' + _tot : '') + ' agents agreed' : '';
  const ts      = ld.timestamp ? esc(ld.timestamp.replace('T', ' ').slice(0, 16)) : '';

  // Age label
  let age = ts;
  if (ld.timestamp) {
    try {
      const diff = (Date.now() - new Date(ld.timestamp).getTime()) / 1000;
      if (diff < 300)       age = 'just now';
      else if (diff < 3600) age = Math.floor(diff / 60) + 'm ago';
      else if (diff < 86400) age = Math.floor(diff / 3600) + 'h ago';
      else                  age = Math.floor(diff / 86400) + 'd ago';
    } catch(e) {}
  }
  if (ageEl) ageEl.textContent = age;

  // Expected returns row
  let retHtml = '';
  const retKeys = [['1M','1m'],['3M','3m'],['12M','12m']];
  for (const [label, key] of retKeys) {
    const v = exp[key];
    if (v != null) {
      const sign = v >= 0 ? '+' : '';
      const cls  = v >= 0 ? 'tc-ret-pos' : 'tc-ret-neg';
      retHtml += `<span class="tc-ret-item"><span class="tc-label">${label}: </span><span class="${cls}">${sign}${v.toFixed(1)}%</span></span>`;
    }
  }

  body.innerHTML = `
    <div class="tc-headline">
      <span class="tc-ticker">${sym}</span>
      <span class="tc-sep"> — </span>
      <span class="tc-company">${co}</span>
      ${alloc ? `<span class="tc-alloc"> | ${alloc}</span>` : ''}
      <span class="${dirCls}"> | ${dir}</span>
    </div>
    ${thesis ? `<div class="tc-row"><span class="tc-label">Thesis: </span><span class="tc-val">${thesis}</span></div>` : ''}
    ${edge   ? `<div class="tc-row"><span class="tc-label">Edge (why now): </span><span class="tc-val">${edge}</span></div>` : ''}
    ${risk   ? `<div class="tc-row"><span class="tc-label">Risk: </span><span class="tc-val">${risk}</span></div>` : ''}
    ${retHtml ? `<div class="tc-returns"><span class="tc-label" style="margin-right:4px">Expected Returns:</span>${retHtml}</div>` : ''}
    <div class="tc-footer">${agents}${agents && ts ? '  ·  ' : ''}${ts}</div>`;
}

function renderAgentConversation(convo) {
  // Keep for backwards compat — no-op since the convo-body element is removed.
  // Full agent debate is still visible in the Agents tab.
}

function renderAgentConvoFull(convo, lastScan) {
  // Full conversation in Agents view with indicator annotations
  const el = document.getElementById('agents-convo-full');
  document.getElementById('agents-scan-time').textContent = 'Last scan: ' + (lastScan || '—');
  if (!convo || !convo.length) return;

  el.innerHTML = convo.map((msg, i) => {
    const isFinal = msg.agent === 'Final Decision Maker';
    const borderColor = isFinal ? 'var(--green)' : `hsl(${25 + i * 40}, 85%, 55%)`;
    return `<div class="agent-convo-card" style="border-left-color:${borderColor}">
      <div class="agent-name" style="color:${borderColor}">${isFinal ? '⚡' : 'Agent ' + (i+1) + ':'} ${esc(msg.agent)}</div>
      <div class="agent-role">${esc(msg.role)}</div>
      <div class="agent-output">${annotateIndicators(msg.output || '')}</div>
    </div>`;
  }).join('');
}

// ── Capital Management ────────────────────────────────────
function recordCapitalAdjustment() {
  const type = document.getElementById('cap-type').value;
  const raw  = parseFloat(document.getElementById('cap-amount').value);
  const note = document.getElementById('cap-note').value.trim();
  if (!raw || raw <= 0) { alert('Enter a valid amount'); return; }
  const amount = type === 'withdrawal' ? -raw : raw;
  fetch('/api/capital-adjustment', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({amount, note: note || type})
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      document.getElementById('cfg-eff-cap').textContent = fmt$(d.effective_capital);
      document.getElementById('cap-amount').value = '';
      document.getElementById('cap-note').value = '';
      alert((type === 'deposit' ? 'Deposit' : 'Withdrawal') + ' of ' + fmt$(raw) + ' recorded. New capital base: ' + fmt$(d.effective_capital));
    }
  });
}

// ── Settings Apply ────────────────────────────────────────
function applySettings() {
  const settings = {
    risk_pct_per_trade:       parseFloat(document.getElementById('cfg-risk-pct').value) / 100,
    daily_loss_limit:         parseFloat(document.getElementById('cfg-daily-limit').value) / 100,
    max_positions:            parseInt(document.getElementById('cfg-max-pos').value),
    min_cash_reserve:         parseFloat(document.getElementById('cfg-cash-reserve').value) / 100,
    max_single_position:      parseFloat(document.getElementById('cfg-max-single').value) / 100,
    min_score_to_trade:       parseInt(document.getElementById('cfg-min-score').value),
    high_conviction_score:    parseInt(document.getElementById('cfg-high-score').value),
    agents_required_to_agree: parseInt(document.getElementById('agree-select').value),
    options_min_score:        parseInt(document.getElementById('cfg-opt-min-score').value),
    options_max_risk_pct:     parseFloat(document.getElementById('cfg-opt-risk').value) / 100,
    options_max_ivr:          parseInt(document.getElementById('cfg-opt-ivr').value),
    options_target_delta:     parseFloat(document.getElementById('cfg-opt-delta').value),
    options_delta_range:      parseFloat(document.getElementById('cfg-opt-delta-range').value),
    // Sentinel
    sentinel_enabled:             document.getElementById('cfg-sentinel-enabled').value === 'true',
    sentinel_poll_seconds:        parseInt(document.getElementById('cfg-sentinel-poll').value),
    sentinel_cooldown_minutes:    parseInt(document.getElementById('cfg-sentinel-cooldown').value),
    sentinel_max_trades_per_hour: parseInt(document.getElementById('cfg-sentinel-max-trades').value),
    sentinel_risk_multiplier:     parseFloat(document.getElementById('cfg-sentinel-risk-mult').value),
    sentinel_keyword_threshold:   parseInt(document.getElementById('cfg-sentinel-kw-thresh').value),
    sentinel_min_confidence:      parseInt(document.getElementById('cfg-sentinel-min-conf').value),
    sentinel_use_ibkr:            document.getElementById('cfg-sentinel-ibkr').value === 'true',
    sentinel_use_finviz:          document.getElementById('cfg-sentinel-finviz').value === 'true',
  };

  const btn = document.querySelector('.setting-card .apply-btn');
  const orig = btn.textContent;
  fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(settings)
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      btn.textContent = '✅ Applied!';
      btn.style.borderColor = 'var(--green)';
      btn.style.color = 'var(--green)';
      setTimeout(() => { btn.textContent = orig; btn.style.borderColor = ''; btn.style.color = ''; }, 2000);
    } else {
      btn.textContent = '⚠ Error';
      btn.style.borderColor = 'var(--red)';
      btn.style.color = 'var(--red)';
      setTimeout(() => { btn.textContent = orig; btn.style.borderColor = ''; btn.style.color = ''; }, 3000);
    }
  }).catch(() => {
    btn.textContent = '⚠ Failed';
    btn.style.borderColor = 'var(--red)';
    btn.style.color = 'var(--red)';
    setTimeout(() => { btn.textContent = orig; btn.style.borderColor = ''; btn.style.color = ''; }, 3000);
  });
}

// ── Favourites ─────────────────────────────────────────────
let favourites = JSON.parse(localStorage.getItem('decifer_favourites') || '[]');

function renderFavTags() {
  const el = document.getElementById('fav-tags');
  if (!favourites.length) {
    el.innerHTML = '<span style="font-size:11px;color:var(--muted2)">No favourites yet — add tickers below</span>';
    return;
  }
  el.innerHTML = favourites.map(t =>
    `<span class="fav-tag">${esc(t)}<span onclick="removeFav(${JSON.stringify(t)})" title="Remove">×</span></span>`
  ).join('');
}

function addFavourite() {
  const input = document.getElementById('fav-input');
  const val = input.value.trim().toUpperCase().replace(/[^A-Z0-9.]/g,'');
  if (!val) return;
  addFavTicker(val);
  input.value = '';
}

function addFavTicker(ticker) {
  if (!favourites.includes(ticker)) {
    favourites.push(ticker);
    renderFavTags();
  }
}

function removeFav(ticker) {
  favourites = favourites.filter(t => t !== ticker);
  renderFavTags();
}

function saveFavourites() {
  localStorage.setItem('decifer_favourites', JSON.stringify(favourites));
  fetch('/api/favourites', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({favourites})
  }).then(r => r.json()).then(d => {
    alert('✅ Favourites saved! Bot will include these on next scan.\n\nActive: ' + favourites.join(', '));
  }).catch(() => {
    alert('✅ Favourites saved locally. Will apply on next scan.');
  });
}

// Load favourites on page load
renderFavTags();

// ── Position Detail Modal ─────────────────────────────────
function showPositionDetail(idx) {
  const p = lastPositions[idx];
  if (!p) return;

  const dir = (p.direction === 'SHORT' || p.qty < 0) ? 'SHORT' : 'LONG';
  const isOpt = p.instrument === 'option';
  const mult = isOpt ? 100 : 1;
  const pnl = dir === 'SHORT'
    ? (p.entry - p.current) * Math.abs(p.qty) * mult
    : (p.current - p.entry) * Math.abs(p.qty) * mult;
  const pct = (p.entry && p.entry !== 0)
    ? (dir === 'SHORT'
      ? ((p.entry - p.current) / p.entry) * 100
      : ((p.current - p.entry) / p.entry) * 100)
    : 0;
  const posValue = Math.abs(p.current * p.qty * mult);
  const pnlCol = pnl >= 0 ? 'var(--green)' : 'var(--red)';

  // Build option details section
  let optRows = '';
  if (isOpt) {
    const right = p.right === 'C' ? 'CALL' : 'PUT';
    optRows = `
      <div class="pos-modal-row"><span class="pos-modal-label">Type</span><span class="pos-modal-val" style="color:var(--cyan)">${right} Option</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Strike</span><span class="pos-modal-val">${fmt$(p.strike)}</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Expiry</span><span class="pos-modal-val">${p.expiry_str || p.expiry || '—'}</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Contracts</span><span class="pos-modal-val">${p.contracts || p.qty}</span></div>
      ${p.delta != null ? `<div class="pos-modal-row"><span class="pos-modal-label">Delta</span><span class="pos-modal-val">${Number(p.delta).toFixed(3)}</span></div>` : ''}
      ${p.theta != null ? `<div class="pos-modal-row"><span class="pos-modal-label">Theta</span><span class="pos-modal-val">${Number(p.theta).toFixed(4)}</span></div>` : ''}
      ${p.iv != null ? `<div class="pos-modal-row"><span class="pos-modal-label">IV</span><span class="pos-modal-val">${(Number(p.iv)*100).toFixed(1)}%</span></div>` : ''}
      ${p.iv_rank != null ? `<div class="pos-modal-row"><span class="pos-modal-label">IV Rank</span><span class="pos-modal-val">${p.iv_rank}%</span></div>` : ''}
    `;
  } else {
    optRows = `<div class="pos-modal-row"><span class="pos-modal-label">Type</span><span class="pos-modal-val">Common Stock</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Shares</span><span class="pos-modal-val">${Math.abs(p.qty)}</span></div>`;
  }

  const badge = isOpt ? ' <span style="color:var(--cyan);font-size:12px">OPT</span>' : '';
  const dirBadge = `<span style="font-size:11px;color:${dir==='LONG'?'var(--green)':'var(--red)'};font-weight:600;background:${dir==='LONG'?'rgba(0,200,83,.1)':'rgba(255,23,68,.1)'};padding:2px 8px;border-radius:10px">${dir}</span>`;

  const RECONCILED_MARKER = 'Reconciled from IBKR on startup';
  let reasoningText;
  if (!p.reasoning) {
    reasoningText = 'No reasoning recorded for this position.';
  } else if (p.reasoning === RECONCILED_MARKER) {
    reasoningText = 'Position loaded from broker at startup \u2014 entry reasoning not available.';
  } else {
    reasoningText = p.reasoning;
  }

  let agentSection = '';
  if (p.agent_outputs && p.agent_outputs.opportunity) {
    const raw = String(p.agent_outputs.opportunity);
    const truncated = raw.length > 300 ? raw.slice(0, 300) + '\u2026' : raw;
    agentSection = `
      <div class="pos-modal-section">
        <h4>Agent Analysis</h4>
        <div class="pos-modal-reasoning" style="font-size:11px;color:var(--muted2)">${esc(truncated)}</div>
      </div>`;
  }

  document.getElementById('pos-modal-content').innerHTML = `
    <div class="pos-modal-hdr">
      <h3>${p.symbol}${badge} ${dirBadge}</h3>
      <button class="pos-modal-close" onclick="closePositionModal()">&times;</button>
    </div>
    <div class="pos-modal-body">
      <div class="pos-modal-row"><span class="pos-modal-label">Position Value</span><span class="pos-modal-val" style="color:var(--orange)">${fmt$(posValue)}</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">P&L</span><span class="pos-modal-val" style="color:${pnlCol}">${pnl >= 0 ? '+' : ''}${fmt$(pnl)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Entry Price</span><span class="pos-modal-val">${fmt$(p.entry)}</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Current Price</span><span class="pos-modal-val">${fmt$(p.current)}</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Stop Loss</span><span class="pos-modal-val" style="color:var(--red)">${fmt$(p.sl)}</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Take Profit</span><span class="pos-modal-val" style="color:var(--green)">${fmt$(p.tp)}</span></div>
      ${optRows}
      <div class="pos-modal-row"><span class="pos-modal-label">Score</span><span class="pos-modal-val">${p.score || '—'}</span></div>
      <div class="pos-modal-row"><span class="pos-modal-label">Status</span><span class="pos-modal-val">${p.status || '—'}</span></div>
      ${p._price_sources ? `<div class="pos-modal-row"><span class="pos-modal-label">Price Source</span><span class="pos-modal-val" style="font-size:10px">${p._price_sources}</span></div>` : ''}
      <div class="pos-modal-section">
        <h4>Why Decifer Took This Position</h4>
        <div class="pos-modal-reasoning">${esc(reasoningText)}</div>
      </div>
      ${agentSection}
    </div>
  `;
  document.getElementById('pos-modal-overlay').classList.add('active');
}

function closePositionModal() {
  document.getElementById('pos-modal-overlay').classList.remove('active');
}

// Close modal on overlay click or Escape
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePositionModal(); });
</script>

<!-- Position Detail Modal -->
<div id="pos-modal-overlay" class="pos-modal-overlay" onclick="if(event.target===this)closePositionModal()">
  <div class="pos-modal" id="pos-modal-content"></div>
</div>

<!-- FOOTER -->
<div style="text-align:center;padding:18px 0 12px;font-size:11px;color:var(--muted2);border-top:1px solid var(--border);margin-top:32px;">
  <span style="color:var(--orange);font-weight:700;">DECIFER 2.0</span> &nbsp;|&nbsp; Invented &amp; built by <span style="color:#fff;font-weight:700;">AMIT CHOPRA</span>
</div>

<!-- VOICE ASSISTANT -->
<style>
#voice-btn{
  position:fixed;bottom:28px;right:28px;z-index:10000;
  width:52px;height:52px;border-radius:50%;border:none;cursor:pointer;
  background:var(--bg3);border:1px solid var(--border2);
  display:flex;align-items:center;justify-content:center;
  font-size:22px;transition:all .2s;box-shadow:0 2px 12px rgba(0,0,0,.5);
}
#voice-btn:hover{border-color:var(--orange);background:var(--orange_dim);}
#voice-btn.listening{background:rgba(255,23,68,.15);border-color:var(--red);animation:pulse-ring .8s ease infinite;}
#voice-btn.waiting{opacity:.6;cursor:not-allowed;}
@keyframes pulse-ring{0%{box-shadow:0 0 0 0 rgba(255,23,68,.5)}70%{box-shadow:0 0 0 10px rgba(255,23,68,0)}100%{box-shadow:0 0 0 0 rgba(255,23,68,0)}}
#voice-toast{
  position:fixed;bottom:92px;right:24px;z-index:10001;
  max-width:320px;background:var(--bg3);border:1px solid var(--border2);
  border-radius:10px;padding:12px 16px;font-size:12px;line-height:1.5;
  display:none;box-shadow:0 4px 24px rgba(0,0,0,.6);
}
#voice-toast.show{display:block;}
#voice-q{color:var(--muted2);margin-bottom:6px;font-size:11px;}
#voice-a{color:var(--text);}
</style>

<button id="voice-btn" title="Ask Decifer a question">🎤</button>
<div id="voice-toast">
  <div id="voice-q"></div>
  <div id="voice-a"></div>
</div>

<script>
(function(){
  const btn   = document.getElementById('voice-btn');
  const toast = document.getElementById('voice-toast');
  const qEl   = document.getElementById('voice-q');
  const aEl   = document.getElementById('voice-a');

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    btn.title = 'Voice not supported in this browser (use Chrome)';
    btn.style.opacity = '0.35';
    btn.onclick = () => alert('Voice requires Chrome or Safari.');
    return;
  }

  const rec = new SpeechRecognition();
  rec.lang = 'en-US';
  rec.interimResults = false;
  rec.maxAlternatives = 1;

  let busy = false;

  function showToast(q, a) {
    qEl.textContent = q ? '\u201C' + q + '\u201D' : '';
    aEl.textContent = a || '';
    toast.classList.add('show');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove('show'), 12000);
  }

  btn.addEventListener('click', () => {
    if (busy) return;
    rec.start();
  });

  rec.onstart = () => {
    busy = true;
    btn.classList.add('listening');
    btn.title = 'Listening…';
    showToast('Listening…', '');
  };

  rec.onresult = (e) => {
    const question = e.results[0][0].transcript;
    btn.classList.remove('listening');
    btn.classList.add('waiting');
    btn.title = 'Thinking…';
    showToast(question, 'Thinking…');

    fetch('/api/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question})
    })
    .then(r => r.json())
    .then(d => {
      const answer = d.answer || d.error || 'No response.';
      showToast(question, answer);
      btn.classList.remove('waiting');
      btn.title = 'Ask Decifer a question';
      busy = false;
    })
    .catch(err => {
      showToast(question, 'Request failed.');
      btn.classList.remove('waiting');
      btn.title = 'Ask Decifer a question';
      busy = false;
    });
  };

  rec.onerror = (e) => {
    btn.classList.remove('listening', 'waiting');
    btn.title = 'Ask Decifer a question';
    if (e.error !== 'no-speech') showToast('', 'Could not hear you. Try again.');
    busy = false;
  };

  rec.onend = () => {
    if (btn.classList.contains('listening')) {
      btn.classList.remove('listening');
      busy = false;
    }
  };
})();
</script>
</body>
</html>"""
