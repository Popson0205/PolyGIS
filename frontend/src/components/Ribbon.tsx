import type { ReactNode } from "react";

/** A ribbon group: its buttons, with its name under them. */
export function Group({ name, children }: { name: string; children: ReactNode }) {
  return <div className="ws-group" role="group" aria-label={name}><div className="ws-group-body">{children}</div><div className="ws-group-name">{name}</div></div>;
}
/** A large ribbon button: an icon over its label. */
export function Tb({ icon, label, onClick, on, disabled, title }: { icon: ReactNode; label: string; onClick: () => void; on?: boolean; disabled?: boolean; title?: string }) {
  return <button type="button" className={`ws-tb${on ? " is-on" : ""}`} onClick={onClick} disabled={disabled} aria-pressed={on} title={title}><span aria-hidden="true" className="ws-tb-icon">{icon}</span>{label}</button>;
}
