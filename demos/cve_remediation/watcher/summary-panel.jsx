// summary-panel.jsx — reusable primitives for FinalSummaryPanel (and others)
// Buildless React 18 + Babel-standalone. All exports via window.* globals.

function Collapsible({ title, children }) {
  return (
    <details className="collapsible">
      <summary className="collapsible-title">{title}</summary>
      <div className="collapsible-body">{children}</div>
    </details>
  );
}

function CopyButton({ value }) {
  const [copied, setCopied] = React.useState(false);

  const handleClick = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1000);
    });
  };

  return (
    <button className="copy-btn" onClick={handleClick} title="Copy to clipboard">
      {copied ? "copied" : "copy"}
    </button>
  );
}

function DiagnosticBlock({ title, rows }) {
  return (
    <div className="diagnostic-block">
      {title && <h4 className="diagnostic-block-title">{title}</h4>}
      <dl className="diagnostic-block-dl">
        {rows.map((row, i) => (
          <React.Fragment key={i}>
            <dt className={row.danger ? "diagnostic-danger" : ""}>{row.label}</dt>
            <dd className={row.danger ? "diagnostic-danger" : ""}>{row.value}</dd>
          </React.Fragment>
        ))}
      </dl>
    </div>
  );
}

function EmptyState({ text }) {
  return <p className="empty-state">{text}</p>;
}

window.Collapsible = Collapsible;
window.CopyButton = CopyButton;
window.DiagnosticBlock = DiagnosticBlock;
window.EmptyState = EmptyState;
