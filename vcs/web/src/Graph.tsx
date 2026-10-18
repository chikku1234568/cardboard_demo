import type { Ref, Version } from "./api";

/** Branch graph: one lane per branch (peers, no main), versions left->right by time. */
export function Graph({ versions, refs, colors, selected, onSelect }: {
  versions: Version[]; refs: Record<string, Ref>; colors: Record<string, string>; selected?: string; onSelect: (v: string) => void;
}) {
  const lanes = Object.keys(refs);
  for (const v of versions) if (!lanes.includes(v.branch)) lanes.push(v.branch);
  const COL = 46, ROW = 34, PADL = 12, PADT = 18;
  const idx = new Map(versions.map((v, i) => [v.version_id, i]));
  const pos = (v: Version) => ({ x: PADL + idx.get(v.version_id)! * COL + 10, y: PADT + lanes.indexOf(v.branch) * ROW });
  const byId = new Map(versions.map((v) => [v.version_id, v]));
  const heads = new Set(Object.values(refs).map((r) => r.head));
  const width = PADL + versions.length * COL + 20;
  const height = PADT + lanes.length * ROW;
  return (
    <div className="graph-wrap">
      <svg width={width} height={height} className="graph">
        {lanes.map((b, i) => <line key={b} x1={0} x2={width} y1={PADT + i * ROW} y2={PADT + i * ROW} className="lane-line" />)}
        {versions.map((v) => v.parents.map((p) => {
          const pv = byId.get(p);
          if (!pv) return null;
          const a = pos(pv), b = pos(v);
          const d = a.y === b.y ? `M${a.x},${a.y} L${b.x},${b.y}` : `M${a.x},${a.y} C${a.x + COL * 0.6},${a.y} ${b.x - COL * 0.6},${b.y} ${b.x},${b.y}`;
          return <path key={v.version_id + p} d={d} stroke={colors[v.branch]} className="edge" />;
        }))}
        {versions.filter((v) => v.pulled_from).map((v) => {
          const src = byId.get(v.pulled_from!.version);
          if (!src) return null;
          const a = pos(src), b = pos(v);
          return <path key={"pull" + v.version_id} d={`M${a.x},${a.y} C${a.x + 30},${a.y} ${b.x - 30},${b.y} ${b.x},${b.y}`} className="edge pull" stroke={colors[v.pulled_from!.branch]} />;
        })}
        {versions.map((v) => {
          const p = pos(v);
          return (
            <g key={v.version_id} onClick={() => onSelect(v.version_id)} className="node">
              <title>{`${v.message}\n${v.author} · ${v.timestamp}\n${v.version_id.slice(0, 10)}`}</title>
              <circle cx={p.x} cy={p.y} r={selected === v.version_id ? 9 : 7} fill={heads.has(v.version_id) ? colors[v.branch] : "#12151c"}
                stroke={colors[v.branch]} strokeWidth={selected === v.version_id ? 3 : 2} />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
