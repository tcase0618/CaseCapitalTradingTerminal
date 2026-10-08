import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowUpRight, Search, X } from "lucide-react";
import { pages, prefetchPage } from "../lib/pageRegistry";

export default function WorkspaceSearch({ open, onClose }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const input = useRef(null);
  const dialog = useRef(null);
  const navigate = useNavigate();
  const results = pages.filter(page => !page.path.includes(":") && `${page.label} ${page.group}`.toLowerCase().includes(query.toLowerCase().trim()));
  const ticker = query.trim().replace(/^\$/, "").toUpperCase();
  if (/^[A-Z][A-Z0-9.]{0,9}$/.test(ticker)) results.push({ path: `/ticker/${ticker}`, label: ticker, group: "Company profile" });
  const choose = page => { if (page) { navigate(page.path); onClose(); } };
  useEffect(() => {
    if (!open) return;
    setQuery(""); setSelected(0);
    const previous = document.activeElement;
    input.current?.focus();
    const close = event => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("keydown", close); previous?.focus?.(); };
  }, [open, onClose]);
  if (!open) return null;
  return <div className="workspace-search-backdrop" onClick={onClose}>
    <section ref={dialog} className="workspace-search" role="dialog" aria-modal="true" aria-label="Find workspace or company" onClick={event => event.stopPropagation()} onKeyDown={event => {
      if (event.key !== "Tab") return;
      const controls = dialog.current.querySelectorAll("input, button");
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
      <div className="workspace-search-input"><Search size={18} /><input ref={input} value={query} aria-label="Search workspaces or ticker" placeholder="Workspace or ticker" autoComplete="off" role="combobox" aria-expanded="true" aria-controls="workspace-results" aria-activedescendant={results[selected] ? `workspace-result-${selected}` : undefined} onChange={event => { setQuery(event.target.value); setSelected(0); }} onKeyDown={event => {
        if (event.key === "ArrowDown") { event.preventDefault(); setSelected(value => Math.min(value + 1, results.length - 1)); }
        if (event.key === "ArrowUp") { event.preventDefault(); setSelected(value => Math.max(0, value - 1)); }
        if (event.key === "Enter") { event.preventDefault(); choose(results[selected]); }
      }} /><button type="button" onClick={onClose} aria-label="Close search" title="Close search"><X size={18} /></button></div>
      <div id="workspace-results" role="listbox" className="workspace-results">
        {results.map((page, index) => <button type="button" id={`workspace-result-${index}`} role="option" aria-selected={selected === index} key={page.path} className={selected === index ? "selected" : ""} onMouseEnter={() => { setSelected(index); prefetchPage(page.path); }} onFocus={() => prefetchPage(page.path)} onClick={() => choose(page)}><span>{page.label}</span><small>{page.group}</small><ArrowUpRight size={14} /></button>)}
        {!results.length && <div className="workspace-search-empty">No matching workspace</div>}
      </div>
    </section>
  </div>;
}
