import ReactMarkdown from "react-markdown";

// react-markdown disallows raw HTML by default, so the model's answer text is
// rendered safely. Links are forced to open in a new tab.
export default function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      components={{
        a: ({ ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
