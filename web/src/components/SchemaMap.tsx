import type { DictTable } from "../api";

/** One join, parsed from the dictionary's "a.col -> b.col (note)" lines. */
export type Join = { from: string; fromCol: string; to: string; toCol: string; note: string };

export function parseJoins(relationships: string[]): Join[] {
  const out: Join[] = [];
  for (const r of relationships) {
    const m = r.match(/^\s*(\w+)\.(\w+)\s*(?:->|→)\s*(\w+)\.(\w+)\s*(?:\((.*)\))?\s*$/);
    if (m) out.push({ from: m[1], fromCol: m[2], to: m[3], toCol: m[4], note: m[5] ?? "" });
  }
  return out;
}

const W = 120;
const H = 40;
// Hand-placed star layout for the Olist warehouse: dimensions in a column, facts beside them, satellite
// facts to the right. Tables the layout does not know about go in a row underneath.
const LAYOUT: Record<string, [number, number]> = {
  dim_geo: [12, 24],
  dim_customer: [196, 24],
  dim_date: [196, 100],
  dim_product: [196, 176],
  dim_seller: [196, 252],
  fact_orders: [404, 62],
  fact_order_items: [404, 214],
  fact_payments: [600, 24],
  fact_reviews: [600, 100],
};

type Box = { name: string; x: number; y: number; kind: "fact" | "dimension"; rows: number };

function anchors(a: Box, b: Box): [number, number, number, number] {
  // Join lines leave a box through the side that faces the other box, so they stay in the gutters.
  const acx = a.x + W / 2, acy = a.y + H / 2, bcx = b.x + W / 2, bcy = b.y + H / 2;
  if (Math.abs(acx - bcx) < W) {
    // stacked vertically
    return acy < bcy ? [acx, a.y + H, bcx, b.y] : [acx, a.y, bcx, b.y + H];
  }
  return acx < bcx ? [a.x + W, acy, b.x, bcy] : [a.x, acy, b.x + W, bcy];
}

export function SchemaMap({ tables, joins, onSelect }: { tables: DictTable[]; joins: Join[]; onSelect?: (table: string) => void }) {
  const boxes: Record<string, Box> = {};
  let extraX = 12;
  const extraY = 318;
  for (const t of tables) {
    const pos = LAYOUT[t.name];
    if (pos) boxes[t.name] = { name: t.name, x: pos[0], y: pos[1], kind: t.kind, rows: t.row_count };
    else {
      boxes[t.name] = { name: t.name, x: extraX, y: extraY, kind: t.kind, rows: t.row_count };
      extraX += W + 20;
    }
  }
  const height = extraX > 12 ? extraY + H + 16 : 330;
  const width = 740;

  return (
    <svg className="schema-map" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="How the tables join: facts and the dimensions they reference">
      <title>How the tables join</title>
      {joins.map((j, i) => {
        const a = boxes[j.from], b = boxes[j.to];
        if (!a || !b) return null;
        const [x1, y1, x2, y2] = anchors(a, b);
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
        return (
          <g key={i} className="join">
            <title>{`${j.from}.${j.fromCol} joins ${j.to}.${j.toCol}${j.note ? ` (${j.note})` : ""}`}</title>
            <line x1={x1} y1={y1} x2={x2} y2={y2} />
            <circle cx={x2} cy={y2} r={3} className="one" />
            <text x={mx} y={my - 5} textAnchor="middle" className="key">
              {j.fromCol}
            </text>
          </g>
        );
      })}
      {Object.values(boxes).map((b) => (
        <g key={b.name} className={`node ${b.kind}`} transform={`translate(${b.x} ${b.y})`} onClick={() => onSelect?.(b.name)} style={{ cursor: onSelect ? "pointer" : "default" }} tabIndex={onSelect ? 0 : -1} role={onSelect ? "button" : undefined} aria-label={`${b.name}, ${b.rows.toLocaleString()} rows`} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelect?.(b.name); }}>
          <rect width={W} height={H} rx={7} />
          <text x={10} y={17} className="name">
            {b.name}
          </text>
          <text x={10} y={31} className="rows">
            {b.rows.toLocaleString()} rows
          </text>
        </g>
      ))}
      <g className="legend" transform={`translate(12 ${height - 10})`}>
        <rect width={10} height={10} y={-9} rx={2} className="fact-swatch" />
        <text x={16} y={0}>fact table</text>
        <rect x={84} width={10} height={10} y={-9} rx={2} className="dim-swatch" />
        <text x={100} y={0}>dimension</text>
        <circle cx={176} cy={-4} r={3} className="one" />
        <text x={184} y={0}>the “one” side of the join</text>
      </g>
    </svg>
  );
}
