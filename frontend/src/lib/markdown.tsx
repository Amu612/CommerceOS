import React from "react";

/**
 * Minimal, dependency-free renderer for the small markdown subset the agents'
 * deterministic/LLM answers actually use: **bold**, `inline code`, "- " / "• "
 * bullet lists, "1. " numbered lists, GFM pipe tables, and blank-line
 * paragraph breaks. No npm install needed — the backend only ever emits this
 * known subset (the LLM path especially likes tables for ranked/comparison
 * answers, so these must render as a real table, not a wall of "| a | b |").
 */

/** A `| a | b |` row, split on unescaped pipes and trimmed. */
function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

/** A separator row like `|---|:--:|--:|` — dashes/colons/pipes/spaces only. */
function isTableSeparator(line: string): boolean {
  return /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(line);
}

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // Split on **bold**, *italic* (checked after bold so ** isn't split as two *'s),
  // and `code` spans, keeping the delimiters for re-matching.
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g);
  parts.forEach((part, i) => {
    if (!part) return;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      nodes.push(<strong key={`${keyPrefix}-b${i}`}>{part.slice(2, -2)}</strong>);
    } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      nodes.push(<em key={`${keyPrefix}-i${i}`}>{part.slice(1, -1)}</em>);
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      nodes.push(<code key={`${keyPrefix}-c${i}`}>{part.slice(1, -1)}</code>);
    } else {
      nodes.push(part);
    }
  });
  return nodes;
}

export function renderMarkdown(text: string): React.ReactNode {
  const lines = (text || "").split("\n");
  const blocks: React.ReactNode[] = [];
  let listBuf: string[] = [];
  let listOrdered = false;
  let paraBuf: string[] = [];

  const flushList = () => {
    if (!listBuf.length) return;
    const Tag = listOrdered ? "ol" : "ul";
    blocks.push(
      <Tag key={`l${blocks.length}`}>
        {listBuf.map((item, i) => (
          <li key={i}>{renderInline(item, `l${blocks.length}-${i}`)}</li>
        ))}
      </Tag>,
    );
    listBuf = [];
  };
  const flushPara = () => {
    if (!paraBuf.length) return;
    const joined = paraBuf.join(" ");
    blocks.push(<p key={`p${blocks.length}`}>{renderInline(joined, `p${blocks.length}`)}</p>);
    paraBuf = [];
  };

  let i = 0;
  while (i < lines.length) {
    const raw = lines[i];
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const isTableStart = line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1]);

    if (isTableStart) {
      flushList();
      flushPara();
      const headerCells = splitTableRow(line);
      const bodyRows: string[][] = [];
      let j = i + 2; // skip the header row and the separator row
      while (j < lines.length && lines[j].trim() !== "" && lines[j].includes("|")) {
        bodyRows.push(splitTableRow(lines[j]));
        j++;
      }
      const tKey = `t${blocks.length}`;
      blocks.push(
        <table key={tKey}>
          <thead>
            <tr>
              {headerCells.map((c, ci) => (
                <th key={ci}>{renderInline(c, `${tKey}-h-${ci}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bodyRows.map((row, ri) => (
              <tr key={ri}>
                {row.map((c, ci) => (
                  <td key={ci}>{renderInline(c, `${tKey}-${ri}-${ci}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      i = j;
      continue;
    }

    if (heading) {
      flushList();
      flushPara();
      const level = Math.min(4, heading[1].length) + 2; // # -> h3 .. #### -> h4 (keeps chat bubbles compact)
      const Tag = `h${level}` as keyof JSX.IntrinsicElements;
      blocks.push(<Tag key={`h${blocks.length}`}>{renderInline(heading[2], `h${blocks.length}`)}</Tag>);
    } else if (bullet) {
      flushPara();
      listOrdered = false;
      listBuf.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      listOrdered = true;
      listBuf.push(numbered[1]);
    } else if (line.trim() === "") {
      flushList();
      flushPara();
    } else {
      flushList();
      paraBuf.push(line.trim());
    }
    i++;
  }
  flushList();
  flushPara();

  if (blocks.length === 0) return null;
  return <>{blocks}</>;
}
