// A small Markdown reader for a step's products (decision 0019, a4): headings, paragraphs, lists, quotes, fenced code,
// inline code, bold, italic and links. It returns a tree that the page renders as React elements, so a document's raw
// HTML is never HTML on the page: it stays text. A link is kept only when it is http or https.

export type Inline =
  | { t: "text"; v: string }
  | { t: "code"; v: string }
  | { t: "strong"; c: Inline[] }
  | { t: "em"; c: Inline[] }
  | { t: "link"; href: string; c: Inline[] };

export type Block =
  | { t: "h"; level: number; c: Inline[] }
  | { t: "p"; c: Inline[] }
  | { t: "ul" | "ol"; items: Inline[][] }
  | { t: "quote"; c: Inline[] }
  | { t: "pre"; v: string };

const SAFE_HREF = /^https?:\/\/[^\s]+$/i;

export function inline(src: string): Inline[] {
  const out: Inline[] = [];
  let i = 0;
  let text = "";
  const flush = () => {
    if (text) out.push({ t: "text", v: text });
    text = "";
  };
  while (i < src.length) {
    const rest = src.slice(i);
    let m: RegExpMatchArray | null;
    if ((m = rest.match(/^`([^`]+)`/))) {
      flush();
      out.push({ t: "code", v: m[1] });
    } else if ((m = rest.match(/^\*\*(.+?)\*\*/))) {
      flush();
      out.push({ t: "strong", c: inline(m[1]) });
    } else if ((m = rest.match(/^\*([^*\s][^*]*?)\*/)) || (m = rest.match(/^_([^_\s][^_]*?)_/))) {
      flush();
      out.push({ t: "em", c: inline(m[1]) });
    } else if ((m = rest.match(/^\[([^\]]+)\]\(([^)\s]+)\)/))) {
      flush();
      if (SAFE_HREF.test(m[2])) out.push({ t: "link", href: m[2], c: inline(m[1]) });
      else out.push(...inline(m[1]));
    } else {
      text += src[i];
      i += 1;
      continue;
    }
    i += m[0].length;
  }
  flush();
  return out;
}

export function parse(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  const endPara = () => {
    if (para.length) out.push({ t: "p", c: inline(para.join(" ")) });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m: RegExpMatchArray | null;
    if (/^```/.test(line)) {
      endPara();
      const body: string[] = [];
      for (i += 1; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      out.push({ t: "pre", v: body.join("\n") });
    } else if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      endPara();
      out.push({ t: "h", level: m[1].length, c: inline(m[2]) });
    } else if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      endPara();
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: Inline[][] = [];
      for (; i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i]); i++) items.push(inline(lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, "")));
      i -= 1;
      out.push({ t: ordered ? "ol" : "ul", items });
    } else if ((m = line.match(/^>\s?(.*)$/))) {
      endPara();
      out.push({ t: "quote", c: inline(m[1]) });
    } else if (!line.trim()) {
      endPara();
    } else para.push(line.trim());
  }
  endPara();
  return out;
}
