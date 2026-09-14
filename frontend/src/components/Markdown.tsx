import type { ReactNode } from 'react'

/**
 * Minimal, deliberately bounded Markdown renderer for Project Note bodies.
 *
 * Project Note content is UNTRUSTED Project data. This renderer guarantees that
 * raw HTML/script can never execute because it NEVER uses
 * `dangerouslySetInnerHTML`: every character of the source is emitted as a React
 * text node (escaped by React) and only a small, closed Markdown subset is
 * interpreted. There is no block-editor ontology, no plugin pipeline, and no
 * dependency on a third-party Markdown engine.
 *
 * Supported: ATX headings, paragraphs, unordered/ordered lists, fenced code
 * blocks, blockquotes, horizontal rules, and inline `code`, **bold**, *italic*,
 * and safe-scheme links. Unsupported syntax stays visible as literal text.
 */

const SAFE_PROTOCOLS = ['http:', 'https:', 'mailto:']

function isSafeHref(href: string): boolean {
  const value = href.trim()
  // Reject any control/whitespace/quote/angle character outright: a URL is only
  // ever placed in `href`, but a stricter parser removes attribute-injection
  // ambiguity (React still escapes every attribute).
  if (!value || /[\s<>"'`\\\u0000-\u001f\u007f]/.test(value)) return false
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return false
  }
  return SAFE_PROTOCOLS.includes(parsed.protocol)
}

/** Inline parse: returns React nodes; all literal text is escaped by React. */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(_[^_]+_)|(\[[^\]]+\]\([^)\s]+\))/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let index = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    const token = match[0]
    const key = `${keyPrefix}-i${index++}`
    if (token.startsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>)
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('[')) {
      const linkMatch = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token)
      if (linkMatch && isSafeHref(linkMatch[2])) {
        nodes.push(
          <a key={key} href={linkMatch[2]} target="_blank" rel="noreferrer noopener">
            {linkMatch[1]}
          </a>,
        )
      } else {
        // Unsupported/unsafe link target: keep the source visible as text.
        nodes.push(token)
      }
    } else {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>)
    }
    lastIndex = match.index + token.length
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }
  return nodes
}

export function Markdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let paragraph: string[] = []
  let key = 0

  const flushParagraph = () => {
    if (paragraph.length > 0) {
      const text = paragraph.join('\n')
      blocks.push(<p key={`p${key++}`}>{renderInline(text, `p${key}`)}</p>)
      paragraph = []
    }
  }

  let index = 0
  while (index < lines.length) {
    const line = lines[index]

    if (line.trimStart().startsWith('```')) {
      flushParagraph()
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trimStart().startsWith('```')) {
        code.push(lines[index])
        index += 1
      }
      index += 1
      blocks.push(
        <pre key={`pre${key++}`} className="payload">
          <code>{code.join('\n')}</code>
        </pre>,
      )
      continue
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph()
      const level = heading[1].length
      const content = renderInline(heading[2], `h${key}`)
      const HeadingTag = (`h${Math.min(level + 2, 6)}`) as 'h3' | 'h4' | 'h5' | 'h6'
      blocks.push(<HeadingTag key={`h${key++}`}>{content}</HeadingTag>)
      index += 1
      continue
    }

    if (/^\s*([-*_])\s*\1\s*\1[\s\S]*$/.test(line) && line.trim().length >= 3) {
      flushParagraph()
      blocks.push(<hr key={`hr${key++}`} />)
      index += 1
      continue
    }

    const listMatch = /^\s*([-*+]|\d+\.)\s+(.*)$/.exec(line)
    if (listMatch) {
      flushParagraph()
      const ordered = /\d+\./.test(listMatch[1])
      const items: string[] = []
      while (index < lines.length) {
        const itemMatch = /^\s*([-*+]|\d+\.)\s+(.*)$/.exec(lines[index])
        if (!itemMatch || /\d+\./.test(itemMatch[1]) !== ordered) break
        items.push(itemMatch[2])
        index += 1
      }
      const ListTag = ordered ? 'ol' : 'ul'
      blocks.push(
        <ListTag key={`list${key++}`}>
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item, `li${key}-${itemIndex}`)}</li>
          ))}
        </ListTag>,
      )
      continue
    }

    if (line.startsWith('>')) {
      flushParagraph()
      const quote: string[] = []
      while (index < lines.length && lines[index].startsWith('>')) {
        quote.push(lines[index].replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push(<blockquote key={`bq${key++}`}>{renderInline(quote.join('\n'), `bq${key}`)}</blockquote>)
      continue
    }

    if (line.trim() === '') {
      flushParagraph()
      index += 1
      continue
    }

    paragraph.push(line)
    index += 1
  }
  flushParagraph()

  return <div className="markdown-body">{blocks}</div>
}
