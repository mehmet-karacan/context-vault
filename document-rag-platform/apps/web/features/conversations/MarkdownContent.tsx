import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
export function MarkdownContent({ content }: { content: string }) {
  // No raw HTML, remote images or executable URL schemes from untrusted model text.
  return (
    <div className="space-y-3 break-words text-[15px] leading-relaxed">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              {children}
            </a>
          ),
          img: ({ alt }) => <span>{alt ?? "Görsel gösterilmedi"}</span>,
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded bg-paper-dim p-3 text-xs">
              {children}
            </pre>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
