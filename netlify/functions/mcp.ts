import type { Config, Context } from "@netlify/functions";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { timingSafeEqual } from "node:crypto";
import { z } from "zod";
import data from "../../data/offers.json" with { type: "json" };

type Offer = {
  brand: string | null;
  product: string;
  description: string | null;
  unit: string | null;
  price_eur: number;
  old_price_eur: number | null;
  discount_pct: number | null;
  lidl_plus_price_eur?: number | null;
  limited_stock: boolean;
  valid: string | null;
  page: number;
};

const OFFERS = (data as { offers: Offer[] }).offers;

// ---------- помощни ----------

const norm = (s: string | null | undefined) => (s ?? "").toLowerCase().replace(/ё/g, "е");
const hay = (o: Offer) => norm([o.brand, o.product, o.description].filter(Boolean).join(" "));
// грубо стемване за български: "ябълки" -> "ябъл"
const hit = (tok: string, h: string) => h.includes(tok.length <= 4 ? tok : tok.slice(0, Math.max(4, tok.length - 2)));
const matches = (query: string) => {
  const toks = norm(query).split(/\s+/).filter(Boolean);
  return (o: Offer) => toks.every((t) => hit(t, hay(o)));
};

function period(valid: string | null): [Date | null, Date | null] {
  if (!valid) return [null, null];
  const d = [...valid.matchAll(/(\d{1,2})\.(\d{1,2})\./g)];
  const y = new Date().getFullYear();
  const mk = (m: RegExpMatchArray) => new Date(Date.UTC(y, Number(m[2]) - 1, Number(m[1])));
  const start = d[0] ? mk(d[0]) : null;
  let end = d[1] ? mk(d[1]) : null;
  if (start && end && end < start) end = new Date(Date.UTC(y + 1, end.getUTCMonth(), end.getUTCDate()));
  return [start, end];
}

const active = (o: Offer, on: Date) => {
  const [s, e] = period(o.valid);
  return (!s || s <= on) && (!e || on <= e);
};

const clean = (o: Offer) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== false));
const reply = (x: unknown) => ({ content: [{ type: "text" as const, text: JSON.stringify(x, null, 1) }] });

// ---------- сървър ----------

function buildServer() {
  const server = new McpServer({ name: "lidl-brochure", version: "1.0.0" },
    { instructions: "Цени и отстъпки от седмичната брошура на Лидл България. Цените са в евро." });

  server.registerTool("search_offers", {
    description: "Търси продукт в брошурата на Лидл (на български или по марка, напр. \"кашкавал\", \"PILOS\", \"бира\"). " +
      "Връща цена, стара цена, отстъпка %, Lidl Plus цена, грамаж, период на валидност и страница.",
    inputSchema: {
      query: z.string().describe("Продукт или марка"),
      max_price_eur: z.number().optional().describe("Максимална цена в евро"),
      active_on: z.string().optional().describe("Дата YYYY-MM-DD – само оферти, валидни този ден"),
      limit: z.number().int().optional().describe("Брой резултати (по подразбиране 20)"),
    },
  }, async ({ query, max_price_eur, active_on, limit }) => {
    let res = OFFERS.filter(matches(query));
    if (max_price_eur !== undefined) res = res.filter((o) => o.price_eur <= max_price_eur);
    if (active_on) res = res.filter((o) => active(o, new Date(active_on + "T00:00:00Z")));
    res.sort((a, b) => a.price_eur - b.price_eur);
    return reply(res.slice(0, limit ?? 20).map(clean));
  });

  server.registerTool("top_discounts", {
    description: "Най-големите отстъпки в брошурата, подредени от най-голямата. query по желание стеснява до продукт или категория.",
    inputSchema: {
      min_discount_pct: z.number().optional().describe("Минимална отстъпка в % (по подразбиране 20)"),
      query: z.string().optional().describe("Продукт или категория, напр. \"сирене\", \"кафе\""),
      limit: z.number().int().optional(),
    },
  }, async ({ min_discount_pct, query, limit }) => {
    let res = query ? OFFERS.filter(matches(query)) : OFFERS;
    res = res.filter((o) => (o.discount_pct ?? 0) >= (min_discount_pct ?? 20));
    res = [...res].sort((a, b) => (b.discount_pct ?? 0) - (a.discount_pct ?? 0));
    return reply(res.slice(0, limit ?? 20).map(clean));
  });

  server.registerTool("offers_by_brand", {
    description: "Всички оферти на дадена марка (напр. \"PILOS\", \"SILVERCREST\", \"PARKSIDE\").",
    inputSchema: { brand: z.string(), limit: z.number().int().optional() },
  }, async ({ brand, limit }) => {
    const b = norm(brand);
    return reply(OFFERS.filter((o) => norm(o.brand).includes(b)).slice(0, limit ?? 50).map(clean));
  });

  server.registerTool("brochure_info", {
    description: "Коя брошура е заредена: файл, кога е обработена, брой оферти и периоди на валидност.",
    inputSchema: {},
  }, async () => {
    const periods: Record<string, number> = {};
    for (const o of OFFERS) periods[o.valid ?? "?"] = (periods[o.valid ?? "?"] ?? 0) + 1;
    const d = data as Record<string, unknown>;
    return reply({ source: d.source, parsed_on: d.parsed_on, count: OFFERS.length, periods });
  });

  return server;
}

// Ключът е в URL-а (?key=...), защото custom connector в claude.ai не праща собствен header.
// Приема се и "Authorization: Bearer <key>" за Claude Code / Cursor.
function authorized(req: Request): boolean {
  const expected = Netlify.env.get("MCP_KEY");
  if (!expected) return false;
  const url = new URL(req.url);
  const given = url.searchParams.get("key") ?? req.headers.get("authorization")?.match(/^Bearer\s+(.+)$/i)?.[1];
  if (!given) return false;
  const a = Buffer.from(given), b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export default async (req: Request, _context: Context) => {
  if (!authorized(req)) return new Response("Unauthorized", { status: 401 });
  if (req.method !== "POST") return new Response("Method not allowed", { status: 405 });

  const server = buildServer();
  const transport = new WebStandardStreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
  });
  await server.connect(transport);
  return transport.handleRequest(req);
};

export const config: Config = {
  path: "/mcp",
  rateLimit: { windowSize: 60, windowLimit: 120, aggregateBy: ["ip", "domain"] },
};
