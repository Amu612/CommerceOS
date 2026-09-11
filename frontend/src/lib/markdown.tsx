import React from "react";

/**
 * Minimal, dependency-free renderer for the small markdown subset the agents'
 * deterministic/LLM answers actually use: **bold**, `inline code`, "- " / "• "
 * bullet lists, "1. " numbered lists, and blank-line paragraph breaks. No
 * npm install needed — the backend only ever emits this known subset.
 */

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

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
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
  }
  flushList();
  flushPara();

  if (blocks.length === 0) return null;
  return <>{blocks}</>;
}
