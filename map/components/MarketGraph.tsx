"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import * as d3 from "d3";
import type { EnrichedNode, GraphEdge, Cluster } from "@/lib/types";
import { EDGE_COLORS } from "@/lib/types";

interface Props {
  nodes: EnrichedNode[];
  edges: GraphEdge[];
  clusters: Cluster[];
  onSelect: (node: EnrichedNode | null) => void;
  selected: EnrichedNode | null;
  focusId?: string | null;
}

interface SimNode extends EnrichedNode {
  x?: number; y?: number; vx?: number; vy?: number;
  fx?: number | null; fy?: number | null;
}

interface TooltipState {
  x: number; y: number; content: string;
}

export default function MarketGraph({ nodes, edges, clusters, onSelect, selected, focusId }: Props) {
  const svgRef    = useRef<SVGSVGElement>(null);
  const wrapRef   = useRef<HTMLDivElement>(null);
  const simRef    = useRef<d3.Simulation<SimNode, undefined> | null>(null);
  const zoomRef   = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const nodesRef  = useRef<SimNode[]>([]);
  const [dims, setDims]       = useState<{ w: number; h: number } | null>(null);
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    // Set dims immediately in case ResizeObserver fires with 0 at mount
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) setDims({ w: rect.width, h: rect.height });
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      if (width > 0 && height > 0) setDims({ w: width, h: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const radius = useCallback((n: EnrichedNode) => {
    const base = n.tier === 0 ? 20 : n.tier === 1 ? 14 : 10;
    return base + (n.brightness / 100) * 8;
  }, []);

  const color = useCallback((n: EnrichedNode) => {
    const base = clusters.find(c => c.id === n.cluster)?.color ?? "#6366f1";
    const p = n.price?.change_pct ?? 0;
    if (p > 2)   return "#10b981";
    if (p > 0.5) return "#34d399";
    if (p < -2)  return "#ef4444";
    if (p < -0.5) return "#f87171";
    return base;
  }, [clusters]);

  // ─── Main D3 effect ───────────────────────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current || nodes.length === 0 || !dims) return;
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    const { w: W, h: H } = dims;

    // Defs
    const defs = svg.append("defs");

    const addGlow = (id: string, std: number) => {
      const f = defs.append("filter").attr("id", id)
        .attr("x", "-80%").attr("y", "-80%").attr("width", "260%").attr("height", "260%");
      f.append("feGaussianBlur").attr("stdDeviation", std).attr("result", "b");
      const m = f.append("feMerge");
      m.append("feMergeNode").attr("in", "b");
      m.append("feMergeNode").attr("in", "SourceGraphic");
    };
    addGlow("glow-soft", 4);
    addGlow("glow-hot",  10);

    defs.append("style").text(`
      @keyframes breathe { 0%,100%{opacity:.4} 50%{opacity:.95} }
      @keyframes breathe-fast { 0%,100%{opacity:.5} 50%{opacity:1} }
      .pulse { animation: breathe 2.8s ease-in-out infinite; }
      .pulse-hot { animation: breathe-fast 1.5s ease-in-out infinite; }
    `);

    // Zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 5])
      .on("zoom", e => g.attr("transform", e.transform));
    svg.call(zoom);
    zoomRef.current = zoom;
    svg.on("click.bg", e => { if (e.target === svgRef.current) onSelect(null); });

    const g = svg.append("g");

    // Cluster layout
    const CC: Record<string, { x: number; y: number }> = {
      ai:    { x: W * 0.34, y: H * 0.46 },
      space: { x: W * 0.72, y: H * 0.46 },
    };
    const clusterMap = new Map<string, SimNode[]>();
    const sn: SimNode[] = nodes.map(n => ({ ...n }));
    sn.forEach(n => {
      const arr = clusterMap.get(n.cluster) ?? [];
      arr.push(n); clusterMap.set(n.cluster, arr);
      const cx = CC[n.cluster]?.x ?? W / 2;
      const cy = CC[n.cluster]?.y ?? H / 2;
      const spread = 80 + n.tier * 60;
      n.x = cx + (Math.random() - 0.5) * spread;
      n.y = cy + (Math.random() - 0.5) * spread;
    });
    nodesRef.current = sn;

    const ld = edges.map(e => ({
      ...e,
      source: sn.find(n => n.id === e.source)!,
      target: sn.find(n => n.id === e.target)!,
    })).filter(e => e.source && e.target);

    // Adjacency for hover
    const adj = new Map<string, Set<string>>();
    ld.forEach(l => {
      const s = (l.source as SimNode).id, t = (l.target as SimNode).id;
      if (!adj.has(s)) adj.set(s, new Set());
      if (!adj.has(t)) adj.set(t, new Set());
      adj.get(s)!.add(t); adj.get(t)!.add(s);
    });

    // ── Layers ──
    const hullG  = g.append("g").attr("class", "hulls");
    const lblG   = g.append("g").attr("class", "labels").attr("pointer-events", "none");

    const link = g.append("g")
      .selectAll<SVGLineElement, typeof ld[0]>("line")
      .data(ld).join("line")
      .attr("stroke", d => EDGE_COLORS[d.type] ?? "#555")
      .attr("stroke-opacity", 0.28)
      .attr("stroke-width",   d => Math.max(0.5, d.strength * 1.8))
      .style("cursor", "crosshair");

    // Edge tooltip
    const onEdgeMove = (event: MouseEvent, d: typeof ld[0]) => {
      const rect = wrapRef.current?.getBoundingClientRect();
      if (!rect) return;
      setTooltip({
        x: event.clientX - rect.left + 14,
        y: event.clientY - rect.top  - 14,
        content: d.label + (d.lag_weeks > 0 ? ` · ~${d.lag_weeks}w signal lag` : ""),
      });
    };
    link
      .on("mouseenter", onEdgeMove)
      .on("mousemove",  onEdgeMove)
      .on("mouseleave", () => setTooltip(null));

    // ── Node groups ──
    const nodeG = g.append("g")
      .selectAll<SVGGElement, SimNode>("g.node")
      .data(sn).join("g")
      .attr("class", "node")
      .style("cursor", "pointer");

    nodeG.call(
      d3.drag<SVGGElement, SimNode>()
        .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
        .on("drag",  (e, d) => { d.fx = e.x; d.fy = e.y; })
        .on("end",   (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; })
    );
    nodeG.on("click", (_e, d) => onSelect(d));

    // ── Hover spotlight ──
    nodeG
      .on("mouseenter", (event, d) => {
        const conn = adj.get(d.id) ?? new Set<string>();

        nodeG.transition().duration(180)
          .attr("opacity", n => n.id === d.id || conn.has(n.id) ? 1 : 0.05);

        link.transition().duration(180)
          .attr("stroke-opacity", l => {
            const s = (l.source as SimNode).id, t = (l.target as SimNode).id;
            return s === d.id || t === d.id ? 0.9 : 0.02;
          })
          .attr("stroke-width", l => {
            const s = (l.source as SimNode).id, t = (l.target as SimNode).id;
            return s === d.id || t === d.id ? Math.max(2.5, l.strength * 5) : 0.2;
          });

        // Tooltip
        const rect = wrapRef.current?.getBoundingClientRect();
        if (rect) {
          const p = d.price?.change_pct;
          const pStr = p !== undefined ? `  ${p >= 0 ? "+" : ""}${p.toFixed(2)}%` : "";
          setTooltip({
            x: event.clientX - rect.left + 16,
            y: event.clientY - rect.top  - 16,
            content: `${d.id} — ${d.label}${pStr}`,
          });
        }
      })
      .on("mousemove", event => {
        const rect = wrapRef.current?.getBoundingClientRect();
        if (rect) setTooltip(p => p ? { ...p, x: event.clientX - rect.left + 16, y: event.clientY - rect.top - 16 } : null);
      })
      .on("mouseleave", () => {
        nodeG.transition().duration(320).attr("opacity", 1);
        link.transition().duration(320)
          .attr("stroke-opacity", 0.28)
          .attr("stroke-width", l => Math.max(0.5, l.strength * 1.8));
        setTooltip(null);
      });

    // ── Node visuals ──

    // Outer animated pulse ring
    nodeG.append("circle")
      .attr("class", d => d.brightness > 72 ? "pulse-hot" : "pulse")
      .attr("r", d => radius(d) + (d.brightness > 65 ? 11 : 5))
      .attr("fill", "none")
      .attr("stroke", d => color(d))
      .attr("stroke-width", d => d.brightness > 72 ? 2.5 : d.brightness > 50 ? 1.5 : 0)
      .attr("stroke-opacity", d => Math.max(0, (d.brightness - 40) / 80))
      .attr("filter", d => d.brightness > 68 ? "url(#glow-hot)" : "url(#glow-soft)");

    // Main filled circle
    nodeG.append("circle")
      .attr("r", d => radius(d))
      .attr("fill", d => color(d))
      .attr("fill-opacity", d => 0.22 + (d.brightness / 100) * 0.68)
      .attr("stroke", d => color(d))
      .attr("stroke-width", 1)
      .attr("stroke-opacity", 0.5);

    // Ticker
    nodeG.append("text")
      .text(d => d.id)
      .attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("font-size", d => d.tier === 0 ? 11 : d.tier === 1 ? 9 : 7.5)
      .attr("font-weight", d => d.tier === 0 ? "700" : "600")
      .attr("fill", "#fff")
      .attr("fill-opacity", d => 0.55 + (d.brightness / 100) * 0.45)
      .attr("pointer-events", "none");

    // Company name (tier 0 always, tier 1+ only if bright)
    nodeG.append("text")
      .text(d => (d.tier === 0 || d.brightness > 50) ? d.label : "")
      .attr("text-anchor", "middle")
      .attr("dy", d => radius(d) + 13)
      .attr("font-size", 7.5)
      .attr("fill", "#9ca3af")
      .attr("fill-opacity", d => 0.35 + (d.brightness / 100) * 0.5)
      .attr("pointer-events", "none");

    // ── Simulation ──
    const cx = W / 2, cy = H / 2;
    const sim = d3.forceSimulation<SimNode>(sn)
      .force("link", d3.forceLink<SimNode, typeof ld[0]>(ld)
        .id(d => d.id).distance(d => 50 + (1 - d.strength) * 40))
      .force("charge", d3.forceManyBody().strength(-100))
      .force("collide", d3.forceCollide<SimNode>().radius(d => radius(d) + 12))
      .force("x", d3.forceX<SimNode>(d => CC[d.cluster]?.x ?? cx).strength(0.4))
      .force("y", d3.forceY<SimNode>(d => CC[d.cluster]?.y ?? cy).strength(0.4))
      .force("radial", d3.forceRadial<SimNode>(
        d => d.tier * 90,
        d => CC[d.cluster]?.x ?? cx,
        d => CC[d.cluster]?.y ?? cy,
      ).strength(0.35))
      .on("tick", () => {
        link
          .attr("x1", d => (d.source as SimNode).x ?? 0)
          .attr("y1", d => (d.source as SimNode).y ?? 0)
          .attr("x2", d => (d.target as SimNode).x ?? 0)
          .attr("y2", d => (d.target as SimNode).y ?? 0);
        nodeG.attr("transform", d => `translate(${d.x ?? 0},${d.y ?? 0})`);

        // Hulls
        hullG.selectAll("path").remove();
        clusterMap.forEach((cnodes, cid) => {
          const pts = cnodes.filter(n => n.x !== undefined).map(n => [n.x!, n.y!] as [number, number]);
          if (pts.length < 3) return;
          const hull = d3.polygonHull(pts); if (!hull) return;
          const cl = clusters.find(c => c.id === cid);
          const mcx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
          const mcy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
          const padded = hull.map(([x, y]) => {
            const dx = x - mcx, dy = y - mcy;
            const len = Math.sqrt(dx * dx + dy * dy) || 1;
            return [x + dx / len * 55, y + dy / len * 55] as [number, number];
          });
          hullG.append("path")
            .attr("d", `M${padded.join("L")}Z`)
            .attr("fill",         cl?.color ?? "#6366f1").attr("fill-opacity",   0.03)
            .attr("stroke",       cl?.color ?? "#6366f1").attr("stroke-opacity", 0.09)
            .attr("stroke-width", 1.5).attr("stroke-dasharray", "6 5");
        });
      });

    simRef.current = sim;

    sim.on("end", () => {
      const settled = sn.filter(n => n.x !== undefined && n.y !== undefined);
      if (!settled.length) return;
      const xs = settled.map(n => n.x!), ys = settled.map(n => n.y!);
      const pad = 90;
      const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
      const bw = Math.max(...xs) + pad - x0, bh = Math.max(...ys) + pad - y0;
      const s = Math.min(0.9, W / bw, H / bh);
      const tx = (W - bw * s) / 2 - x0 * s;
      const ty = (H - bh * s) / 2 - y0 * s;
      svg.transition().duration(900).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(s));

      // Cluster name labels at the top of each hull
      lblG.selectAll("*").remove();
      clusterMap.forEach((cnodes, cid) => {
        const cl = clusters.find(c => c.id === cid); if (!cl) return;
        const validNodes = cnodes.filter(n => n.x !== undefined && n.y !== undefined);
        if (!validNodes.length) return;
        const lcx = validNodes.reduce((s, n) => s + n.x!, 0) / validNodes.length;
        const topY  = Math.min(...validNodes.map(n => n.y!));
        lblG.append("text")
          .attr("x", lcx).attr("y", topY - 28)
          .attr("text-anchor", "middle")
          .attr("font-size", 12).attr("font-weight", "700")
          .attr("letter-spacing", "0.14em")
          .attr("fill", cl.color).attr("fill-opacity", 0.35)
          .text(cl.label.toUpperCase());
      });
    });

    return () => { sim.stop(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, clusters, onSelect, dims]);

  // ── Selection highlight (no redraw) ──────────────────────────────────────
  useEffect(() => {
    if (!svgRef.current) return;
    d3.select(svgRef.current)
      .selectAll<SVGGElement, SimNode>("g.node")
      .each(function(d) {
        const isSelected = selected?.id === d.id;
        d3.select(this).selectAll("circle")
          .filter((_, i) => i === 1)
          .attr("stroke",         isSelected ? "#ffffff" : color(d))
          .attr("stroke-width",   isSelected ? 2.5 : 1)
          .attr("stroke-opacity", isSelected ? 1 : 0.5);
      });
  }, [selected, color]);

  // ── Focus / search pan ───────────────────────────────────────────────────
  useEffect(() => {
    if (!focusId || !svgRef.current || !zoomRef.current || !dims) return;
    const target = nodesRef.current.find(n => n.id === focusId);
    if (!target?.x || !target?.y) return;
    const s = 2.0;
    const tx = dims.w / 2 - target.x * s;
    const ty = dims.h / 2 - target.y * s;
    d3.select(svgRef.current).transition().duration(700)
      .call(zoomRef.current.transform, d3.zoomIdentity.translate(tx, ty).scale(s));
  }, [focusId, dims]);

  return (
    <div ref={wrapRef} className="w-full h-full relative">
      <svg ref={svgRef} className="w-full h-full" />
      {tooltip && (
        <div
          className="absolute pointer-events-none z-50 px-2.5 py-1.5 rounded-lg text-xs text-white whitespace-nowrap"
          style={{
            left: tooltip.x, top: tooltip.y,
            background: "rgba(8,13,26,0.92)",
            border: "1px solid rgba(255,255,255,0.12)",
            backdropFilter: "blur(8px)",
          }}
        >
          {tooltip.content}
        </div>
      )}
    </div>
  );
}
